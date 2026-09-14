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
from pathlib import Path
from typing import Any

from . import telegram
from . import xbet_market_pressure as market


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
        candidates.append(
            {
                "line": line,
                "over": over,
                "under": under,
                "probability": probability,
            }
        )
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["probability"]) - 0.5))


def _metric(total: dict[str, float], score: tuple[int, int], cfg: SportConfig) -> float:
    current_points = int(score[0]) + int(score[1])
    remaining_line = float(total["line"]) - float(current_points)
    probability_bias = (float(total["probability"]) - 0.5) * float(cfg.probability_scale)
    return remaining_line + probability_bias


def _event_allowed(game: dict[str, Any]) -> bool:
    text = " ".join(
        str(game.get(key) or "")
        for key in ("L", "LE", "SN", "O1", "O2")
    ).casefold()
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


class MultiSportSteamWorker:
    """Independent 1xBet steam scanner for real ice hockey and basketball.

    It does not touch GOOL football Brain or football STEAM state. The signal is
    based on the bookmaker's implied *remaining* total, so ordinary score changes
    are largely removed from the pressure metric instead of being treated as
    money movement. A short post-score guard suppresses transient repricing.
    """

    def __init__(self, runtime: Path | None = None) -> None:
        runtime = runtime or Path(os.getenv("RUNTIME_DATA_DIR", "data"))
        self.state_path = Path(
            os.getenv(
                "XBET_MULTISPORT_STATE",
                str(runtime / "live" / "xbet_multisport_steam_state.json"),
            )
        )
        self.history_path = Path(
            os.getenv(
                "XBET_MULTISPORT_HISTORY",
                str(runtime / "live" / "xbet_multisport_steam_history.jsonl"),
            )
        )
        self._stop = threading.Event()
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=40))
        self._last_score: dict[str, tuple[int, int]] = {}
        self._score_changed_at: dict[str, float] = {}
        self._last_period: dict[str, str] = {}
        self._last_alert_at: dict[str, float] = {}
        self._roots: dict[str, str] = {key: market.ROOTS[0] for key in SPORTS}

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _query(sport_id: int) -> str:
        return urllib.parse.urlencode(
            {
                "sports": int(sport_id),
                "count": max(50, _int_env("XBET_MULTISPORT_INDEX_COUNT", 1000)),
                "lng": "en",
                "mode": 4,
                "country": 1,
                "getEmpty": "true",
            }
        )

    def _index(self, cfg: SportConfig) -> list[dict[str, Any]]:
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        query = self._query(cfg.sport_id)
        for root in roots:
            payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=7.0)
            values = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(values, list) and values:
                self._roots[cfg.key] = root
                return [row for row in values if isinstance(row, dict) and row.get("I")]
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
            payload = market._http_json(
                f"{root}/GetGameZip?{urllib.parse.urlencode(params)}",
                timeout=7.0,
            )
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._roots[cfg.key] = root
                return value
        return None

    def _event_snapshot(self, event: dict[str, Any], cfg: SportConfig) -> dict[str, Any] | None:
        event_id = str(event.get("I") or "").strip()
        if not event_id:
            return None
        game = event
        total = _balanced_total(game)
        score = _score(game)
        if total is None or score is None:
            fetched = self._game(event_id, cfg)
            if not fetched:
                return None
            game = fetched
            total = _balanced_total(game)
            score = _score(game)
        if total is None or score is None or not _event_allowed(game):
            return None

        home = str(game.get("O1") or event.get("O1") or "?").strip()
        away = str(game.get("O2") or event.get("O2") or "?").strip()
        league = str(game.get("LE") or game.get("L") or event.get("LE") or event.get("L") or "").strip()
        period = _period(game)
        clock = _clock_seconds(game)
        return {
            "event_id": event_id,
            "sport": cfg.key,
            "home": home,
            "away": away,
            "league": league,
            "score": [score[0], score[1]],
            "period": period,
            "clock_seconds": clock,
            "line": float(total["line"]),
            "over": float(total["over"]),
            "under": float(total["under"]),
            "probability": float(total["probability"]),
            "metric": _metric(total, score, cfg),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
        }

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
        end = signal["end"]
        score = row.get("score") or [0, 0]
        strength = "EXTREME" if signal.get("extreme") else "STRONG"
        unit = "гола" if cfg.key == "hockey" else "очка"
        return (
            f"{cfg.icon} <b>1xBet {cfg.title} · {strength}</b>\n\n"
            f"<b>{row.get('home','?')} — {row.get('away','?')}</b>\n"
            f"{int(score[0])}:{int(score[1])} · {self._format_clock(row)}\n"
            f"🏆 {row.get('league') or 'LIVE'}\n\n"
            f"📈 Тотал: <b>{float(end['line']):g}</b> · ТБ {float(end['over']):.2f}\n"
            f"🔥 Давление на будущий тотал: <b>+{float(signal['metric_delta']):.2f} {unit}</b>\n"
            f"📊 Δ fair P(ТБ): {float(signal['probability_delta_pp']):+.1f} п.п. · "
            f"линия {float(signal['line_delta']):+.1f} · импульсов {int(signal['moves'])}\n"
            "⚠️ Это отдельный рыночный STEAM-сигнал; футбольный GOOL Brain он не меняет."
        )

    def _emit(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> int:
        if not _truthy("XBET_MULTISPORT_TELEGRAM_ENABLED", True):
            return 0
        sent = telegram.broadcast(self._message(row, signal, cfg))
        print(
            f"XBET_{cfg.key.upper()}_STEAM_SENT event={row.get('event_id')} "
            f"delta={signal.get('metric_delta')} moves={signal.get('moves')} sent={sent}",
            flush=True,
        )
        return int(sent or 0)

    def collect_once(self) -> dict[str, Any]:
        started = time.time()
        state: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "sports": {},
            "alerts": [],
        }
        workers = max(2, min(24, _int_env("XBET_MULTISPORT_GAME_WORKERS", 12)))
        max_games = max(10, _int_env("XBET_MULTISPORT_MAX_GAMES_PER_SPORT", 160))

        for sport_key, base_cfg in SPORTS.items():
            enabled = _truthy(f"XBET_{sport_key.upper()}_STEAM_ENABLED", True)
            if not enabled:
                state["sports"][sport_key] = {"enabled": False, "indexed": 0, "sampled": 0}
                continue
            cfg = SportConfig(
                **{
                    **base_cfg.__dict__,
                    "min_metric_delta": _float_env(
                        f"XBET_{sport_key.upper()}_STEAM_MIN_DELTA",
                        base_cfg.min_metric_delta,
                    ),
                    "min_moves": max(
                        2,
                        _int_env(f"XBET_{sport_key.upper()}_STEAM_MIN_MOVES", base_cfg.min_moves),
                    ),
                }
            )
            index = self._index(cfg)
            rows: list[dict[str, Any]] = []
            alerts = 0
            selected = index[:max_games]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(self._event_snapshot, event, cfg) for event in selected]
                for future in as_completed(futures):
                    try:
                        row = future.result()
                    except Exception as exc:
                        print(f"XBET_{sport_key.upper()}_STEAM_EVENT_ERROR {type(exc).__name__}:{exc}", flush=True)
                        continue
                    if not row:
                        continue
                    history, score_changed_at = self._append_history(row)
                    signal = detect_steam(history, cfg, now=float(row["ts"]), score_changed_at=score_changed_at)
                    if signal and self._cooldown_ok(row, cfg, float(row["ts"])):
                        sent = self._emit(row, signal, cfg)
                        if sent > 0 or not _truthy("XBET_MULTISPORT_TELEGRAM_ENABLED", True):
                            self._mark_alert(row, float(row["ts"]))
                        alerts += 1
                        state["alerts"].append({**row, "signal": signal, "sent": sent})
                    rows.append(row)
            state["sports"][sport_key] = {
                "enabled": True,
                "sport_id": cfg.sport_id,
                "indexed": len(index),
                "sampled": len(selected),
                "decoded": len(rows),
                "alerts": alerts,
                "root": self._roots[cfg.key],
                "matches": rows,
            }
            print(
                f"XBET_{sport_key.upper()}_STEAM indexed={len(index)} sampled={len(selected)} "
                f"decoded={len(rows)} alerts={alerts}",
                flush=True,
            )

        state["latency_ms"] = int((time.time() - started) * 1000)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(self.state_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        return state

    def run(self, interval: float = 20.0) -> None:
        interval = max(8.0, float(interval))
        print(
            "XBET_MULTISPORT_STEAM started sports=hockey,basketball "
            f"interval={interval:.1f}s telegram={int(_truthy('XBET_MULTISPORT_TELEGRAM_ENABLED', True))}",
            flush=True,
        )
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.collect_once()
            except Exception as exc:
                print(f"XBET_MULTISPORT_STEAM_ERROR {type(exc).__name__}:{exc}", flush=True)
            delay = max(0.5, interval - (time.monotonic() - started))
            self._stop.wait(delay)


__all__ = [
    "MultiSportSteamWorker",
    "SPORTS",
    "SportConfig",
    "_balanced_total",
    "_metric",
    "_score",
    "detect_steam",
]
