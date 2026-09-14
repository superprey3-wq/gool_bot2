from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import telegram
from . import xbet_market_pressure as market
from .providers.common import norm_team
from .providers.flashscore import FlashscoreProvider, _as_int, _fields
from .storage_runtime import trim_file_tail
from .xbet_multisport_card import render_multisport_steam_card


@dataclass(frozen=True)
class SportConfig:
    key: str
    sport_id: int
    icon: str
    title: str
    probability_scale: float
    min_metric_delta: float
    extreme_metric_delta: float
    min_moves: int
    min_age_seconds: float
    score_guard_seconds: float
    cooldown_seconds: float
    window_seconds: float


SPORTS: dict[str, SportConfig] = {
    "hockey": SportConfig(
        key="hockey",
        sport_id=2,
        icon="🏒",
        title="HOCKEY STEAM",
        probability_scale=4.0,
        min_metric_delta=0.45,
        extreme_metric_delta=0.75,
        min_moves=3,
        min_age_seconds=28.0,
        score_guard_seconds=16.0,
        cooldown_seconds=12 * 60.0,
        window_seconds=4 * 60.0,
    ),
    "basketball": SportConfig(
        key="basketball",
        sport_id=3,
        icon="🏀",
        title="BASKETBALL STEAM",
        probability_scale=40.0,
        min_metric_delta=3.5,
        extreme_metric_delta=6.0,
        min_moves=3,
        min_age_seconds=24.0,
        score_guard_seconds=6.0,
        cooldown_seconds=8 * 60.0,
        window_seconds=3 * 60.0,
    ),
}

# Flashscore: basketball=3, ice hockey=4. 1xBet uses hockey=2, basketball=3.
FLASHSCORE_SPORT_IDS = {"basketball": 3, "hockey": 4}

_EXCLUDED_MARKERS = (
    "esports",
    "e-sports",
    "cyber",
    "virtual",
    "ebasketball",
    "ehockey",
    "nba2k",
    "2x2",
    "3x3",
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().casefold() not in {"0", "false", "no", "off"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _score(game: dict[str, Any]) -> tuple[int, int] | None:
    sc = game.get("SC") or {}
    fs = sc.get("FS") or {}
    try:
        if fs.get("S1") is not None and fs.get("S2") is not None:
            return int(float(fs.get("S1"))), int(float(fs.get("S2")))
    except (TypeError, ValueError):
        pass
    try:
        if game.get("S1") is not None and game.get("S2") is not None:
            return int(float(game.get("S1"))), int(float(game.get("S2")))
    except (TypeError, ValueError):
        pass
    return None


def _period(game: dict[str, Any]) -> str:
    sc = game.get("SC") or {}
    value = sc.get("CPS") or sc.get("CP") or sc.get("I") or "LIVE"
    return str(value).strip() or "LIVE"


def _clock_seconds(game: dict[str, Any]) -> int | None:
    sc = game.get("SC") or {}
    try:
        raw = sc.get("TS")
        return None if raw is None else max(0, int(float(raw)))
    except (TypeError, ValueError):
        return None


def _fair(over: float, under: float) -> float:
    a = 1.0 / float(over)
    b = 1.0 / float(under)
    return a / (a + b)


def _balanced_total(game: dict[str, Any]) -> dict[str, float] | None:
    rows = market.decode_markets(game).get("match_total") or []
    candidates: list[dict[str, float]] = []
    for row in rows:
        try:
            line = float(row.get("line"))
            over = float(row.get("over"))
            under = float(row.get("under"))
        except (TypeError, ValueError):
            continue
        if not (1.08 <= over <= 8.0 and 1.08 <= under <= 8.0):
            continue
        probability = _fair(over, under)
        candidates.append({"line": line, "over": over, "under": under, "probability": probability})
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["probability"]) - 0.5))


def _metric(total: dict[str, float], score: tuple[int, int], cfg: SportConfig) -> float:
    current_points = int(score[0]) + int(score[1])
    remaining_line = float(total["line"]) - float(current_points)
    probability_bias = (float(total["probability"]) - 0.5) * float(cfg.probability_scale)
    return remaining_line + probability_bias


def _event_allowed(game: dict[str, Any]) -> bool:
    text = " ".join(str(game.get(key) or "") for key in ("L", "LE", "SN", "O1", "O2")).casefold()
    return not any(marker in text for marker in _EXCLUDED_MARKERS)


def _one_way_moves(rows: list[dict[str, Any]], epsilon: float) -> int:
    moves = 0
    for left, right in zip(rows, rows[1:]):
        if float(right.get("metric") or 0.0) >= float(left.get("metric") or 0.0) + epsilon:
            moves += 1
    return moves


def detect_steam(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
    score_changed_at: float | None,
) -> dict[str, Any] | None:
    if len(rows) < max(4, cfg.min_moves + 1):
        return None
    eligible = [row for row in rows if now - float(row.get("ts") or 0.0) <= cfg.window_seconds]
    if len(eligible) < max(4, cfg.min_moves + 1):
        return None
    age = float(eligible[-1]["ts"]) - float(eligible[0]["ts"])
    if age < cfg.min_age_seconds:
        return None
    if score_changed_at is not None and now - score_changed_at < cfg.score_guard_seconds:
        return None

    start = eligible[0]
    end = eligible[-1]
    delta = float(end["metric"]) - float(start["metric"])
    probability_delta_pp = (float(end["probability"]) - float(start["probability"])) * 100.0
    line_delta = float(end["line"]) - float(start["line"])
    epsilon = 0.035 if cfg.key == "hockey" else 0.30
    moves = _one_way_moves(eligible, epsilon)
    extreme = delta >= cfg.extreme_metric_delta

    if delta < cfg.min_metric_delta or (moves < cfg.min_moves and not extreme):
        return None
    try:
        odd = float(end["over"])
    except (TypeError, ValueError):
        return None
    if not (1.20 <= odd <= 3.50):
        return None

    return {
        "metric_delta": round(delta, 3),
        "probability_delta_pp": round(probability_delta_pp, 2),
        "line_delta": round(line_delta, 2),
        "moves": moves,
        "age_seconds": round(age, 1),
        "extreme": extreme,
        "start": start,
        "end": end,
    }


def _team_similarity(left: str, right: str) -> float:
    a, b = norm_team(left), norm_team(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def parse_flashscore_live(body: str) -> list[dict[str, Any]]:
    """Parse a generic Flashscore sport master feed and keep LIVE events only."""
    league = ""
    rows: dict[str, dict[str, Any]] = {}
    for chunk in (body or "").split("~"):
        if not chunk:
            continue
        if chunk.startswith("ZA÷"):
            league = str(_fields(chunk).get("ZA") or "").strip()
            continue
        if not chunk.startswith("AA÷"):
            continue
        event_id, sep, rest = chunk[3:].partition("¬")
        if not sep or len(event_id) != 8 or not event_id.isalnum():
            continue
        fields = _fields(rest)
        if str(fields.get("AB") or "") != "2":
            continue
        home = str(fields.get("AE") or fields.get("CX") or "").strip()
        away = str(fields.get("AF") or "").strip()
        if not home or not away:
            continue
        rows[event_id] = {
            "flashscore_event_id": event_id,
            "home": home,
            "away": away,
            "score": [
                _as_int(fields.get("AG"), _as_int(fields.get("AT"))),
                _as_int(fields.get("AH"), _as_int(fields.get("AU"))),
            ],
            "league": league,
            "status_code": str(fields.get("AC") or ""),
            "coarse_status": "2",
        }
    return list(rows.values())


def _match_quality(xbet: dict[str, Any], fs: dict[str, Any]) -> tuple[float, bool, float]:
    xh = str(xbet.get("O1") or "")
    xa = str(xbet.get("O2") or "")
    fh = str(fs.get("home") or "")
    fa = str(fs.get("away") or "")
    direct_sides = (_team_similarity(xh, fh), _team_similarity(xa, fa))
    reverse_sides = (_team_similarity(xh, fa), _team_similarity(xa, fh))
    direct = sum(direct_sides) / 2.0
    reverse = sum(reverse_sides) / 2.0
    if reverse > direct:
        return reverse, True, min(reverse_sides)
    return direct, False, min(direct_sides)


def map_xbet_to_flashscore(
    xbet_events: list[dict[str, Any]],
    flashscore_events: list[dict[str, Any]],
    *,
    min_score: float | None = None,
    min_side: float | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], bool, float]]:
    """One-to-one whitelist mapping. Unmatched 1xBet events are never scanned."""
    threshold = _float_env("XBET_MULTISPORT_FS_MATCH_MIN", 0.70) if min_score is None else float(min_score)
    side_floor = _float_env("XBET_MULTISPORT_FS_SIDE_MIN", 0.52) if min_side is None else float(min_side)
    candidates: list[tuple[float, int, int, bool]] = []
    for xi, xbet in enumerate(xbet_events):
        if not _event_allowed(xbet):
            continue
        for fi, fs in enumerate(flashscore_events):
            quality, reversed_order, weakest = _match_quality(xbet, fs)
            if quality >= threshold and weakest >= side_floor:
                candidates.append((quality, xi, fi, reversed_order))

    candidates.sort(key=lambda item: item[0], reverse=True)
    used_xbet: set[int] = set()
    used_fs: set[int] = set()
    out: list[tuple[dict[str, Any], dict[str, Any], bool, float]] = []
    for quality, xi, fi, reversed_order in candidates:
        if xi in used_xbet or fi in used_fs:
            continue
        used_xbet.add(xi)
        used_fs.add(fi)
        out.append((xbet_events[xi], flashscore_events[fi], reversed_order, quality))
    return out


class MultiSportSteamWorker:
    """Flashscore-whitelisted hockey/basketball 1xBet STEAM scanner.

    Flashscore is the canonical LIVE universe and score source. 1xBet contributes
    market movement only. No Flashscore match -> no scan -> no signal.
    """

    def __init__(self, runtime: Path | None = None) -> None:
        runtime = runtime or Path(os.getenv("RUNTIME_DATA_DIR", "data"))
        self.state_path = Path(os.getenv("XBET_MULTISPORT_STATE", str(runtime / "live" / "xbet_multisport_steam_state.json")))
        self.history_path = Path(os.getenv("XBET_MULTISPORT_HISTORY", str(runtime / "live" / "xbet_multisport_steam_history.jsonl")))
        self._stop = threading.Event()
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=40))
        self._last_score: dict[str, tuple[int, int]] = {}
        self._score_changed_at: dict[str, float] = {}
        self._last_period: dict[str, str] = {}
        self._last_alert_at: dict[str, float] = {}
        self._roots: dict[str, str] = {key: market.ROOTS[0] for key in SPORTS}
        self._flashscore = FlashscoreProvider()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _query(sport_id: int) -> str:
        return urllib.parse.urlencode({
            "sports": int(sport_id),
            "count": max(50, _int_env("XBET_MULTISPORT_INDEX_COUNT", 1000)),
            "lng": "en",
            "mode": 4,
            "country": 1,
            "getEmpty": "true",
        })

    def _flashscore_live(self, cfg: SportConfig) -> list[dict[str, Any]]:
        sport_id = FLASHSCORE_SPORT_IDS[cfg.key]
        merged: dict[str, dict[str, Any]] = {}
        for path in (f"f_{sport_id}_0_3_en_1", f"f_{sport_id}_0_0_en_1"):
            body = self._flashscore._feed(path)
            if not body:
                continue
            for row in parse_flashscore_live(body):
                merged[str(row["flashscore_event_id"])] = row
        return list(merged.values())

    def _xbet_index(self, cfg: SportConfig) -> list[dict[str, Any]]:
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        query = self._query(cfg.sport_id)
        for root in roots:
            payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=7.0)
            values = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(values, list) and values:
                self._roots[cfg.key] = root
                return [row for row in values if isinstance(row, dict) and row.get("I") and row.get("O1") and row.get("O2")]
        return []

    def _game(self, event_id: str, cfg: SportConfig) -> dict[str, Any] | None:
        params = {
            "id": event_id,
            "lng": "en",
            "cfview": 0,
            "isSubGames": "true",
            "GroupEvents": "true",
            "allEventsGroupSubGames": "true",
            "countevents": 250,
            "grMode": 2,
        }
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        for root in roots:
            payload = market._http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=7.0)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._roots[cfg.key] = root
                return value
        return None

    def _snapshot(
        self,
        event: dict[str, Any],
        fs: dict[str, Any],
        reversed_order: bool,
        match_score: float,
        cfg: SportConfig,
    ) -> tuple[dict[str, Any] | None, str | None]:
        event_id = str(event.get("I") or "").strip()
        if not event_id:
            return None, "missing_event_id"
        game = event
        total = _balanced_total(game)
        xbet_score = _score(game)
        if total is None or xbet_score is None:
            game = self._game(event_id, cfg) or {}
            total = _balanced_total(game)
            xbet_score = _score(game)
        if total is None or xbet_score is None or not _event_allowed(game):
            return None, "market_decode"

        canonical_xbet = (xbet_score[1], xbet_score[0]) if reversed_order else xbet_score
        fs_score_raw = list(fs.get("score") or [0, 0])
        fs_score = (int(fs_score_raw[0]), int(fs_score_raw[1]))
        if canonical_xbet != fs_score:
            return None, "score_mismatch"

        return {
            "event_id": event_id,
            "sport": cfg.key,
            "home": str(fs.get("home") or "?"),
            "away": str(fs.get("away") or "?"),
            "league": str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            "score": [fs_score[0], fs_score[1]],
            "period": _period(game),
            "clock_seconds": _clock_seconds(game),
            "line": float(total["line"]),
            "over": float(total["over"]),
            "under": float(total["under"]),
            "probability": float(total["probability"]),
            "metric": _metric(total, fs_score, cfg),
            "flashscore_event_id": str(fs.get("flashscore_event_id") or ""),
            "flashscore_match_score": round(float(match_score), 4),
            "flashscore_score_verified": True,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
        }, None

    def _append_history(self, row: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
        key = f"{row['sport']}:{row['event_id']}"
        score = (int(row["score"][0]), int(row["score"][1]))
        period = str(row.get("period") or "LIVE")
        now = float(row["ts"])

        previous_period = self._last_period.get(key)
        if previous_period is not None and previous_period != period:
            self._history[key].clear()
        self._last_period[key] = period

        previous_score = self._last_score.get(key)
        if previous_score is not None and previous_score != score:
            self._score_changed_at[key] = now
        self._last_score[key] = score
        self._history[key].append(dict(row))
        return list(self._history[key]), self._score_changed_at.get(key)

    def _cooldown_ok(self, row: dict[str, Any], cfg: SportConfig, now: float) -> bool:
        key = f"{row['sport']}:{row['event_id']}"
        return now - float(self._last_alert_at.get(key, 0.0)) >= cfg.cooldown_seconds

    def _mark_alert(self, row: dict[str, Any], now: float) -> None:
        self._last_alert_at[f"{row['sport']}:{row['event_id']}"] = now

    @staticmethod
    def _format_clock(row: dict[str, Any]) -> str:
        raw = row.get("clock_seconds")
        if raw is None:
            return str(row.get("period") or "LIVE")
        seconds = int(raw)
        return f"{row.get('period') or 'LIVE'} · {seconds // 60:02d}:{seconds % 60:02d}"

    def _message(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> str:
        end = dict(signal.get("end") or {})
        score = list(row.get("score") or [0, 0])
        strength = "EXTREME" if signal.get("extreme") else "STRONG"
        return (
            f"{cfg.icon} <b>1xBet {cfg.title} · {strength}</b>\n"
            f"<b>{row.get('home','?')} — {row.get('away','?')}</b> · {int(score[0])}:{int(score[1])}\n"
            f"⏱ {self._format_clock(row)} · ✅ Flashscore LIVE\n"
            f"📈 ТБ {float(end.get('line') or row.get('line') or 0):g} @ {float(end.get('over') or row.get('over') or 0):.2f}\n"
            f"🔥 движение +{float(signal.get('metric_delta') or 0):.2f} · импульсов {int(signal.get('moves') or 0)}"
        )

    def _deliver(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> int:
        if not _truthy("XBET_MULTISPORT_TELEGRAM_ENABLED", True):
            return 0
        message = self._message(row, signal, cfg)
        if _truthy("XBET_MULTISPORT_CARDS_ENABLED", True):
            try:
                png = render_multisport_steam_card(row, signal, cfg)
                sent = telegram.broadcast_photo(png, caption=message)
                if sent:
                    return sent
            except Exception as exc:
                print(f"XBET_{cfg.key.upper()}_CARD_ERROR error={type(exc).__name__}:{exc}", flush=True)
        return telegram.broadcast(message)

    def _scan_sport(self, cfg: SportConfig) -> dict[str, Any]:
        fs_live = self._flashscore_live(cfg)
        xbet_live = self._xbet_index(cfg)
        mapped = map_xbet_to_flashscore(xbet_live, fs_live)
        max_events = max(1, _int_env("XBET_MULTISPORT_MAX_MAPPED_PER_SPORT", 120))
        mapped = mapped[:max_events]

        decoded = 0
        score_mismatch = 0
        market_decode = 0
        alerts = 0
        latest: list[dict[str, Any]] = []
        workers = max(2, min(16, _int_env("XBET_MULTISPORT_GAME_WORKERS", 8)))
        futures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for event, fs, reversed_order, match_score in mapped:
                futures.append(pool.submit(self._snapshot, event, fs, reversed_order, match_score, cfg))
            for future in as_completed(futures):
                try:
                    row, error = future.result(timeout=18)
                except Exception:
                    row, error = None, "market_decode"
                if row is None:
                    if error == "score_mismatch":
                        score_mismatch += 1
                    else:
                        market_decode += 1
                    continue
                decoded += 1
                history, score_changed_at = self._append_history(row)
                signal = detect_steam(history, cfg, now=float(row["ts"]), score_changed_at=score_changed_at)
                if signal is not None and self._cooldown_ok(row, cfg, float(row["ts"])):
                    sent = self._deliver(row, signal, cfg)
                    if sent > 0 or not _truthy("XBET_MULTISPORT_TELEGRAM_ENABLED", True):
                        self._mark_alert(row, float(row["ts"]))
                    alerts += 1
                    row["steam"] = signal
                latest.append(row)

        return {
            "flashscore_live": len(fs_live),
            "xbet_live": len(xbet_live),
            "mapped": len(mapped),
            "decoded": decoded,
            "score_mismatch": score_mismatch,
            "market_decode_failed": market_decode,
            "alerts": alerts,
            "matches": latest,
        }

    def collect_once(self) -> dict[str, Any]:
        started = time.time()
        sports: dict[str, Any] = {}
        for key, cfg in SPORTS.items():
            if not _truthy(f"XBET_{key.upper()}_STEAM_ENABLED", True):
                sports[key] = {"enabled": False}
                continue
            stats = self._scan_sport(cfg)
            sports[key] = {"enabled": True, **stats}
            print(
                f"XBET_{key.upper()}_STEAM flashscore={stats['flashscore_live']} xbet={stats['xbet_live']} "
                f"mapped={stats['mapped']} decoded={stats['decoded']} score_mismatch={stats['score_mismatch']} "
                f"alerts={stats['alerts']} cards={'on' if _truthy('XBET_MULTISPORT_CARDS_ENABLED', True) else 'off'}",
                flush=True,
            )

        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": int((time.time() - started) * 1000),
            "flashscore_whitelist_required": True,
            "sports": sports,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.state_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        trim_file_tail(self.history_path, max(1024 * 1024, _int_env("XBET_MULTISPORT_HISTORY_KEEP_BYTES", 4 * 1024 * 1024)))
        return state

    def run(self, interval: float = 20.0) -> None:
        interval = max(8.0, float(interval))
        print(
            f"XBET_MULTISPORT_STEAM started sports=hockey,basketball interval={interval:g}s "
            "flashscore_whitelist=required score_sync=required cards=on",
            flush=True,
        )
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.collect_once()
            except Exception as exc:
                print(f"XBET_MULTISPORT_STEAM_ERROR error={type(exc).__name__}:{exc}", flush=True)
            self._stop.wait(max(1.0, interval - (time.monotonic() - started)))
