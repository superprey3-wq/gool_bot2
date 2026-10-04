from __future__ import annotations

import argparse
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
from .xbet_multisport_card import render_multisport_prematch_card, render_multisport_steam_card


@dataclass(frozen=True)
class SportConfig:
    key: str
    sport_id: int
    flashscore_id: int
    icon: str
    title: str
    probability_scale: float
    min_metric_delta: float
    extreme_metric_delta: float
    min_moves: int
    min_age_seconds: float
    score_guard_seconds: float
    window_seconds: float
    move_epsilon: float


SPORTS: dict[str, SportConfig] = {
    "hockey": SportConfig("hockey", 2, 4, "🏒", "HOCKEY", 4.0, 0.45, 0.75, 3, 28.0, 16.0, 4 * 60.0, 0.035),
    "basketball": SportConfig("basketball", 3, 3, "🏀", "BASKETBALL", 40.0, 3.5, 6.0, 3, 24.0, 6.0, 3 * 60.0, 0.30),
}

FLASHSCORE_SPORT_IDS = {key: cfg.flashscore_id for key, cfg in SPORTS.items()}
_EXCLUDED_MARKERS = ("esports", "e-sports", "cyber", "virtual", "ebasketball", "ehockey", "nba2k", "2x2", "3x3")
_FINAL_RESULTS = {"won", "lost", "void"}
PREMATCH_ROOTS = (
    "https://1xbet.com/service-api/LineFeed",
    "https://1xbet.com/LineFeed",
    "https://1xbet.fi/service-api/LineFeed",
    "https://1xbet.fi/LineFeed",
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


def _mode() -> str:
    raw = os.getenv("GOOL_MULTISPORT_MODE", os.getenv("GOOL_SPORT_MODE", "shadow"))
    return "active" if str(raw).strip().casefold() == "active" else "shadow"


def _sport_enabled(key: str) -> bool:
    legacy = _truthy(f"XBET_{key.upper()}_STEAM_ENABLED", True)
    return _truthy(f"GOOL_{key.upper()}_ENABLED", legacy)


def _runtime_path(env_name: str, legacy_name: str, runtime: Path, filename: str) -> Path:
    raw = os.getenv(env_name, "").strip() or os.getenv(legacy_name, "").strip()
    return Path(raw) if raw else runtime / "live" / filename


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _save_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _score(game: dict[str, Any]) -> tuple[int, int] | None:
    sc = game.get("SC") or {}
    fs = sc.get("FS") or {}
    for root in (fs, game):
        try:
            if root.get("S1") is not None and root.get("S2") is not None:
                return int(float(root.get("S1"))), int(float(root.get("S2")))
        except (TypeError, ValueError):
            pass
    return None


def _period(game: dict[str, Any]) -> str:
    sc = game.get("SC") or {}
    return str(sc.get("CPS") or sc.get("CP") or sc.get("I") or "LIVE").strip() or "LIVE"


def _clock_seconds(game: dict[str, Any]) -> int | None:
    try:
        raw = (game.get("SC") or {}).get("TS")
        return None if raw is None else max(0, int(float(raw)))
    except (TypeError, ValueError):
        return None


def _fair(over: float, under: float) -> float:
    a, b = 1.0 / float(over), 1.0 / float(under)
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
        if 1.08 <= over <= 8.0 and 1.08 <= under <= 8.0:
            candidates.append({"line": line, "over": over, "under": under, "probability": _fair(over, under)})
    return min(candidates, key=lambda row: abs(float(row["probability"]) - 0.5)) if candidates else None


def _metric(total: dict[str, float], score: tuple[int, int], cfg: SportConfig) -> float:
    current = int(score[0]) + int(score[1])
    remaining = float(total["line"]) - current
    return remaining + (float(total["probability"]) - 0.5) * cfg.probability_scale


def _event_allowed(game: dict[str, Any]) -> bool:
    text = " ".join(str(game.get(key) or "") for key in ("L", "LE", "SN", "O1", "O2")).casefold()
    return not any(marker in text for marker in _EXCLUDED_MARKERS)


def _one_way_moves(rows: list[dict[str, Any]], direction: str, epsilon: float) -> int:
    count = 0
    for left, right in zip(rows, rows[1:]):
        delta = float(right.get("metric") or 0.0) - float(left.get("metric") or 0.0)
        if direction == "over" and delta >= epsilon:
            count += 1
        elif direction == "under" and delta <= -epsilon:
            count += 1
    return count


def detect_steam(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
    score_changed_at: float | None,
) -> dict[str, Any] | None:
    eligible = [row for row in rows if now - float(row.get("ts") or 0.0) <= cfg.window_seconds]
    if len(eligible) < max(4, cfg.min_moves + 1):
        return None
    age = float(eligible[-1]["ts"]) - float(eligible[0]["ts"])
    if age < cfg.min_age_seconds:
        return None
    if score_changed_at is not None and now - score_changed_at < cfg.score_guard_seconds:
        return None

    start, end = eligible[0], eligible[-1]
    raw_delta = float(end["metric"]) - float(start["metric"])
    direction = "over" if raw_delta > 0 else "under"
    metric_delta = abs(raw_delta)
    moves = _one_way_moves(eligible, direction, cfg.move_epsilon)
    extreme = metric_delta >= cfg.extreme_metric_delta
    if metric_delta < cfg.min_metric_delta or (moves < cfg.min_moves and not extreme):
        return None

    try:
        odd = float(end[direction])
    except (TypeError, ValueError, KeyError):
        return None
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None

    over_probability_delta = (float(end["probability"]) - float(start["probability"])) * 100.0
    probability_delta_pp = over_probability_delta if direction == "over" else -over_probability_delta
    if abs(over_probability_delta) < _float_env("GOOL_MULTISPORT_MIN_FAIR_EDGE_PP", 3.0) and not extreme:
        return None

    raw_line_delta = float(end["line"]) - float(start["line"])
    line_delta = raw_line_delta if direction == "over" else -raw_line_delta
    fair_probability = float(end["probability"]) if direction == "over" else 1.0 - float(end["probability"])
    strength = min(100.0, 58.0 + metric_delta / max(1e-6, cfg.min_metric_delta) * 13.0 + moves * 3.0 + (7.0 if extreme else 0.0))
    return {
        "direction": direction,
        "line": float(end["line"]),
        "odd": odd,
        "fair_probability": round(fair_probability, 6),
        "metric_delta": round(metric_delta, 3),
        "probability_delta_pp": round(probability_delta_pp, 2),
        "line_delta": round(line_delta, 2),
        "moves": moves,
        "age_seconds": round(age, 1),
        "strength": round(strength, 1),
        "extreme": extreme,
        "start": start,
        "end": end,
    }


def detect_prematch_steam(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
) -> dict[str, Any] | None:
    window = max(5 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_WINDOW_SECONDS", 60 * 60.0))
    eligible = [row for row in rows if now - float(row.get("ts") or 0.0) <= window]
    if len(eligible) < 3:
        return None
    age = float(eligible[-1]["ts"]) - float(eligible[0]["ts"])
    if age < _float_env("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", 60.0):
        return None

    start, end = eligible[0], eligible[-1]
    raw_delta = float(end["metric"]) - float(start["metric"])
    direction = "over" if raw_delta > 0 else "under"
    metric_delta = abs(raw_delta)
    min_metric = _float_env(
        f"GOOL_{cfg.key.upper()}_PREMATCH_MIN_METRIC_DELTA",
        0.35 if cfg.key == "hockey" else 2.0,
    )
    line_floor = _float_env(
        f"GOOL_{cfg.key.upper()}_PREMATCH_MIN_LINE_DELTA",
        0.5 if cfg.key == "hockey" else 2.5,
    )
    moves = _one_way_moves(eligible, direction, max(cfg.move_epsilon, min_metric / 6.0))
    raw_probability_delta = (float(end["probability"]) - float(start["probability"])) * 100.0
    probability_delta_pp = raw_probability_delta if direction == "over" else -raw_probability_delta
    raw_line_delta = float(end["line"]) - float(start["line"])
    line_delta = raw_line_delta if direction == "over" else -raw_line_delta
    extreme = metric_delta >= min_metric * 1.75

    if metric_delta < min_metric:
        return None
    if moves < 2 and not extreme:
        return None
    if (
        probability_delta_pp < _float_env("GOOL_MULTISPORT_PREMATCH_MIN_FAIR_EDGE_PP", 2.0)
        and line_delta < line_floor
        and not extreme
    ):
        return None

    odd = float(end[direction])
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None

    fair_probability = float(end["probability"]) if direction == "over" else 1.0 - float(end["probability"])
    strength = min(
        100.0,
        55.0
        + metric_delta / max(1e-6, min_metric) * 14.0
        + max(0.0, probability_delta_pp) * 1.5
        + moves * 3.0
        + (8.0 if extreme else 0.0),
    )
    return {
        "phase": "PREMATCH",
        "direction": direction,
        "line": float(end["line"]),
        "odd": odd,
        "fair_probability": round(fair_probability, 6),
        "metric_delta": round(metric_delta, 3),
        "probability_delta_pp": round(probability_delta_pp, 2),
        "line_delta": round(line_delta, 2),
        "moves": moves,
        "age_seconds": round(age, 1),
        "strength": round(strength, 1),
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


def parse_flashscore_events(body: str) -> list[dict[str, Any]]:
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
        home = str(fields.get("AE") or fields.get("CX") or "").strip()
        away = str(fields.get("AF") or "").strip()
        if not home or not away:
            continue
        rows[event_id] = {
            "flashscore_event_id": event_id,
            "home": home,
            "away": away,
            "score": [_as_int(fields.get("AG"), _as_int(fields.get("AT"))), _as_int(fields.get("AH"), _as_int(fields.get("AU")))],
            "league": league,
            "status_code": str(fields.get("AC") or ""),
            "coarse_status": str(fields.get("AB") or ""),
            "start_ts": _as_int(fields.get("AD") or fields.get("AO"), 0),
        }
    return list(rows.values())


def parse_flashscore_live(body: str) -> list[dict[str, Any]]:
    return [row for row in parse_flashscore_events(body) if str(row.get("coarse_status") or "") == "2"]


def _match_quality(xbet: dict[str, Any], fs: dict[str, Any]) -> tuple[float, bool, float]:
    xh, xa = str(xbet.get("O1") or ""), str(xbet.get("O2") or "")
    fh, fa = str(fs.get("home") or ""), str(fs.get("away") or "")
    direct_sides = (_team_similarity(xh, fh), _team_similarity(xa, fa))
    reverse_sides = (_team_similarity(xh, fa), _team_similarity(xa, fh))
    direct, reverse = sum(direct_sides) / 2.0, sum(reverse_sides) / 2.0
    return (reverse, True, min(reverse_sides)) if reverse > direct else (direct, False, min(direct_sides))


def map_xbet_to_flashscore(
    xbet_events: list[dict[str, Any]],
    flashscore_events: list[dict[str, Any]],
    *,
    min_score: float | None = None,
    min_side: float | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], bool, float]]:
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
    used_xbet, used_fs, out = set(), set(), []
    for quality, xi, fi, reversed_order in candidates:
        if xi in used_xbet or fi in used_fs:
            continue
        used_xbet.add(xi)
        used_fs.add(fi)
        out.append((xbet_events[xi], flashscore_events[fi], reversed_order, quality))
    return out


def settle_multisport_pick(row: dict[str, Any], home_score: int, away_score: int) -> str:
    total = int(home_score) + int(away_score)
    line = float(row.get("line") or 0.0)
    if abs(total - line) < 1e-9:
        return "void"
    if str(row.get("direction") or "over") == "under":
        return "won" if total < line else "lost"
    return "won" if total > line else "lost"


class MultiSportSteamWorker:
    """Basketball + hockey market-movement worker ported from basket_hokkey.

    Flashscore owns identity/status/score. 1xBet contributes the live total and
    its movement. In shadow mode signals are journaled but not pushed.
    """

    def __init__(self, runtime: Path | None = None) -> None:
        runtime = runtime or Path(os.getenv("RUNTIME_DATA_DIR", "data"))
        self.state_path = _runtime_path("GOOL_MULTISPORT_STATE", "XBET_MULTISPORT_STATE", runtime, "gool_multisport_state.json")
        self.history_path = _runtime_path("GOOL_MULTISPORT_HISTORY", "XBET_MULTISPORT_HISTORY", runtime, "gool_multisport_history.jsonl")
        self.journal_path = _runtime_path("GOOL_MULTISPORT_JOURNAL", "XBET_MULTISPORT_JOURNAL", runtime, "gool_multisport_signals.json")
        self._stop = threading.Event()
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
        self._prematch_history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=180))
        self._last_score: dict[str, tuple[int, int]] = {}
        self._score_changed_at: dict[str, float] = {}
        self._last_period: dict[str, str] = {}
        self._roots: dict[str, str] = {key: market.ROOTS[0] for key in SPORTS}
        self._prematch_roots: dict[str, str] = {key: PREMATCH_ROOTS[0] for key in SPORTS}
        self._index_diag: dict[str, dict[str, Any]] = {}
        self._prematch_index_diag: dict[str, dict[str, Any]] = {}
        self._flashscore = FlashscoreProvider()
        self._restore_history()

    def stop(self) -> None:
        self._stop.set()

    def _restore_history(self) -> None:
        try:
            size = self.history_path.stat().st_size
            with self.history_path.open("rb") as fh:
                fh.seek(max(0, size - 4 * 1024 * 1024))
                data = fh.read()
            if size > 4 * 1024 * 1024 and b"\n" in data:
                data = data.split(b"\n", 1)[1]
        except FileNotFoundError:
            return
        restored = 0
        for raw in data.splitlines():
            try:
                state = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            for key, cfg in SPORTS.items():
                sport_state = ((state.get("sports") or {}).get(key) or {})
                for row in (sport_state.get("matches") or []):
                    if not isinstance(row, dict) or not row.get("event_id") or row.get("ts") is None:
                        continue
                    self._append_history(row, cfg)
                    restored += 1
                for row in (sport_state.get("prematch_matches") or []):
                    if not isinstance(row, dict) or not row.get("event_id") or row.get("ts") is None:
                        continue
                    self._append_prematch_history(row, cfg)
                    restored += 1
        if restored:
            print(f"GOOL_MULTISPORT_MEMORY restored_snapshots={restored}", flush=True)

    def _flashscore_today(self, cfg: SportConfig) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for path in (f"f_{cfg.flashscore_id}_0_3_en_1", f"f_{cfg.flashscore_id}_0_0_en_1"):
            body = self._flashscore._feed(path)
            if not body:
                continue
            for row in parse_flashscore_events(body):
                merged[str(row["flashscore_event_id"])] = row
        return list(merged.values())

    def _xbet_queries(self, cfg: SportConfig) -> list[str]:
        count = max(50, _int_env("XBET_MULTISPORT_INDEX_COUNT", 1000))
        base = {"sports": cfg.sport_id, "count": count, "lng": "en", "mode": 4}
        return [
            urllib.parse.urlencode({**base, "country": 1, "getEmpty": "true"}),
            urllib.parse.urlencode({**base, "country": 137, "gr": 285, "virtualSports": "true", "noFilterBlockEvent": "true", "getEmpty": "true"}),
        ]

    def _xbet_index(self, cfg: SportConfig) -> list[dict[str, Any]]:
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        attempts: list[dict[str, Any]] = []
        for root in dict.fromkeys(roots):
            for query_no, query in enumerate(self._xbet_queries(cfg), 1):
                payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=7.0)
                values = payload.get("Value") if isinstance(payload, dict) else None
                raw_count = len(values) if isinstance(values, list) else 0
                attempts.append({"root": root, "query": query_no, "raw": raw_count, "payload": bool(payload)})
                if isinstance(values, list) and values:
                    rows = [row for row in values if isinstance(row, dict) and row.get("I") and row.get("O1") and row.get("O2")]
                    if rows:
                        self._roots[cfg.key] = root
                        self._index_diag[cfg.key] = {"ok": True, "root": root, "query": query_no, "raw": raw_count, "usable": len(rows)}
                        return rows
        self._index_diag[cfg.key] = {"ok": False, "attempts": attempts[-8:]}
        return []

    def _xbet_prematch_queries(self, cfg: SportConfig) -> list[str]:
        count = max(100, _int_env("GOOL_MULTISPORT_PREMATCH_INDEX_COUNT", 1000))
        base = {"sports": cfg.sport_id, "count": count, "lng": "en", "cfview": 2, "mode": 4}
        return [
            urllib.parse.urlencode({**base, "country": 1, "getEmpty": "true"}),
            urllib.parse.urlencode({**base, "country": 19, "getEmpty": "true"}),
            urllib.parse.urlencode({**base, "country": 1, "tf": 2200000, "tz": 0, "getEmpty": "true"}),
        ]

    def _xbet_prematch_index(self, cfg: SportConfig) -> list[dict[str, Any]]:
        roots = [self._prematch_roots[cfg.key], *[root for root in PREMATCH_ROOTS if root != self._prematch_roots[cfg.key]]]
        attempts: list[dict[str, Any]] = []
        for root in dict.fromkeys(roots):
            merged: dict[str, dict[str, Any]] = {}
            for query_no, query in enumerate(self._xbet_prematch_queries(cfg), 1):
                payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=8.0)
                values = payload.get("Value") if isinstance(payload, dict) else None
                raw_count = len(values) if isinstance(values, list) else 0
                attempts.append({"root": root, "query": query_no, "raw": raw_count, "payload": bool(payload)})
                if isinstance(values, list):
                    for row in values:
                        if isinstance(row, dict) and row.get("I") and row.get("O1") and row.get("O2"):
                            merged[str(row.get("I"))] = row
            if merged:
                self._prematch_roots[cfg.key] = root
                self._prematch_index_diag[cfg.key] = {
                    "ok": True,
                    "root": root,
                    "raw": len(merged),
                    "usable": len(merged),
                    "attempts": attempts[-6:],
                }
                return list(merged.values())
        self._prematch_index_diag[cfg.key] = {"ok": False, "attempts": attempts[-9:]}
        return []

    def _prematch_game(self, event_id: str, cfg: SportConfig) -> dict[str, Any] | None:
        params = {
            "id": event_id,
            "lng": "en",
            "cfview": 0,
            "isSubGames": "true",
            "GroupEvents": "true",
            "allEventsGroupSubGames": "true",
            "countevents": 250,
            "grMode": 4,
            "marketType": 1,
            "isNewBuilder": "true",
        }
        roots = [self._prematch_roots[cfg.key], *[root for root in PREMATCH_ROOTS if root != self._prematch_roots[cfg.key]]]
        for root in dict.fromkeys(roots):
            payload = market._http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=8.0)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._prematch_roots[cfg.key] = root
                return value
        return None

    def _game(self, event_id: str, cfg: SportConfig) -> dict[str, Any] | None:
        params = {"id": event_id, "lng": "en", "cfview": 0, "isSubGames": "true", "GroupEvents": "true", "allEventsGroupSubGames": "true", "countevents": 250, "grMode": 2}
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        for root in dict.fromkeys(roots):
            payload = market._http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=7.0)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._roots[cfg.key] = root
                return value
        return None

    def _prematch_snapshot(self, event: dict[str, Any], fs: dict[str, Any], reversed_order: bool, match_score: float, cfg: SportConfig) -> tuple[dict[str, Any] | None, str | None]:
        event_id = str(event.get("I") or "").strip()
        game = event
        total = _balanced_total(game)
        if total is None:
            game = self._prematch_game(event_id, cfg) or {}
            total = _balanced_total(game)
        if total is None or not _event_allowed(game):
            return None, "prematch_market_decode"
        now = time.time()
        start_ts = float(fs.get("start_ts") or 0.0)
        if start_ts <= now:
            return None, "prematch_started"
        horizon = max(15 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", 6 * 60 * 60.0))
        if start_ts - now > horizon:
            return None, "prematch_outside_horizon"
        return {
            "ts": now,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "phase": "PREMATCH",
            "origin": "multisport_prematch",
            "phase": "LIVE",
            "origin": "multisport_live",
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": str(fs.get("flashscore_event_id") or ""),
            "home": str(fs.get("home") or "?"),
            "away": str(fs.get("away") or "?"),
            "league": str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            "start_ts": start_ts,
            "scheduled_start": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
            "line": float(total["line"]),
            "over": float(total["over"]),
            "under": float(total["under"]),
            "probability": float(total["probability"]),
            "metric": _metric(total, (0, 0), cfg),
            "flashscore_match_score": round(float(match_score), 4),
        }, None

    def _snapshot(self, event: dict[str, Any], fs: dict[str, Any], reversed_order: bool, match_score: float, cfg: SportConfig) -> tuple[dict[str, Any] | None, str | None]:
        event_id = str(event.get("I") or "").strip()
        game = event
        total, xbet_score = _balanced_total(game), _score(game)
        if total is None or xbet_score is None:
            game = self._game(event_id, cfg) or {}
            total, xbet_score = _balanced_total(game), _score(game)
        if total is None or xbet_score is None or not _event_allowed(game):
            return None, "market_decode"
        canonical = (xbet_score[1], xbet_score[0]) if reversed_order else xbet_score
        fs_score_raw = list(fs.get("score") or [0, 0])
        fs_score = (int(fs_score_raw[0]), int(fs_score_raw[1]))
        if canonical != fs_score:
            return None, "score_mismatch"
        now = time.time()
        return {
            "ts": now,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": str(fs.get("flashscore_event_id") or ""),
            "home": str(fs.get("home") or "?"),
            "away": str(fs.get("away") or "?"),
            "league": str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            "score": [*fs_score],
            "period": _period(game),
            "clock_seconds": _clock_seconds(game),
            "line": float(total["line"]),
            "over": float(total["over"]),
            "under": float(total["under"]),
            "probability": float(total["probability"]),
            "metric": _metric(total, fs_score, cfg),
            "flashscore_match_score": round(float(match_score), 4),
            "flashscore_score_verified": True,
        }, None

    def _append_history(self, row: dict[str, Any], cfg: SportConfig | None = None) -> tuple[list[dict[str, Any]], float | None]:
        cfg = cfg or SPORTS.get(str(row.get("sport") or "").casefold())
        if cfg is None:
            raise ValueError(f"unknown_multisport={row.get('sport')}")
        key = f"{cfg.key}:{row['event_id']}"
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
            if cfg.key == "hockey":
                self._history[key].clear()
        self._last_score[key] = score
        self._history[key].append(dict(row))
        return list(self._history[key]), self._score_changed_at.get(key)

    def _append_prematch_history(self, row: dict[str, Any], cfg: SportConfig) -> list[dict[str, Any]]:
        key = f"{cfg.key}:{row['event_id']}"
        self._prematch_history[key].append(dict(row))
        return list(self._prematch_history[key])

    def _settle(self, cfg: SportConfig, states: dict[str, dict[str, Any]]) -> int:
        rows = _load_rows(self.journal_path)
        changed = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            if row.get("sport") != cfg.key or str(row.get("result") or "pending") in _FINAL_RESULTS:
                continue
            state = states.get(str(row.get("flashscore_event_id") or ""))
            if not state or str(state.get("coarse_status") or "") != "3":
                continue
            score = list(state.get("score") or [0, 0])
            result = settle_multisport_pick(row, int(score[0]), int(score[1]))
            profit = 0.0 if result == "void" else (float(row.get("odd") or 0.0) - 1.0 if result == "won" else -1.0)
            row.update({"result": result, "profit_units": round(profit, 4), "settled_at": now, "settled_score": [int(score[0]), int(score[1])]})
            changed += 1
        if changed:
            _save_rows(self.journal_path, rows)
        return changed

    def _already_seen(self, sport: str, event_id: str, phase: str) -> bool:
        wanted_phase = str(phase or "LIVE").upper()
        for row in _load_rows(self.journal_path):
            row_phase = str(row.get("phase") or ("PREMATCH" if row.get("origin") == "multisport_prematch" else "LIVE")).upper()
            if str(row.get("sport") or "") == sport and str(row.get("event_id") or "") == event_id and row_phase == wanted_phase:
                return True
        return False

    def _format_clock(self, row: dict[str, Any]) -> str:
        raw = row.get("clock_seconds")
        if raw is None:
            return str(row.get("period") or "LIVE")
        seconds = max(0, int(raw))
        return f"{row.get('period') or 'LIVE'} · {seconds // 60:02d}:{seconds % 60:02d}"

    def _message(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> str:
        score = list(row.get("score") or [0, 0])
        direction = str(signal.get("direction") or "over")
        market_label = ("ТБ" if direction == "over" else "ТМ") + f" {float(signal.get('line') or 0):g}"
        arrow = "⬆️" if direction == "over" else "⬇️"
        if str(row.get("phase") or "LIVE").upper() == "PREMATCH":
            start_ts = float(row.get("start_ts") or 0.0)
            start_label = datetime.fromtimestamp(start_ts, timezone.utc).strftime("%d.%m %H:%M UTC") if start_ts else "до старта"
            return (
                f"{cfg.icon} <b>GOOL MULTI · PREMATCH · {cfg.title}</b>\n"
                f"<b>{row.get('home','?')} — {row.get('away','?')}</b>\n"
                f"🏆 {row.get('league') or 'PREMATCH'}\n"
                f"🕐 {start_label} · ✅ Flashscore + 1xBet LineFeed\n"
                f"{arrow} <b>{market_label} @ {float(signal.get('odd') or 0):.2f}</b>\n"
                f"🧠 сила {float(signal.get('strength') or 0):.0f}/100 · движение {float(signal.get('metric_delta') or 0):.2f}\n"
                f"📈 Δp {float(signal.get('probability_delta_pp') or 0):+.1f} п.п. · линия {float(signal.get('line_delta') or 0):+.1f}"
            )
        return (
            f"{cfg.icon} <b>GOOL MULTI · LIVE · {cfg.title}</b>\n"
            f"<b>{row.get('home','?')} — {row.get('away','?')}</b> · {int(score[0])}:{int(score[1])}\n"
            f"🏆 {row.get('league') or 'LIVE'}\n"
            f"⏱ {self._format_clock(row)} · ✅ Flashscore\n"
            f"{arrow} <b>{market_label} @ {float(signal.get('odd') or 0):.2f}</b>\n"
            f"🧠 сила {float(signal.get('strength') or 0):.0f}/100 · движение {float(signal.get('metric_delta') or 0):.2f}\n"
            f"📈 Δp {float(signal.get('probability_delta_pp') or 0):+.1f} п.п. · импульсов {int(signal.get('moves') or 0)}"
        )

    def _deliver(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> int:
        if not _truthy("XBET_MULTISPORT_TELEGRAM_ENABLED", True):
            return 0
        message = self._message(row, signal, cfg)
        if _truthy("XBET_MULTISPORT_CARDS_ENABLED", True):
            try:
                png = (
                    render_multisport_prematch_card(row, signal, cfg)
                    if str(row.get("phase") or "LIVE").upper() == "PREMATCH"
                    else render_multisport_steam_card(row, signal, cfg)
                )
                sent = telegram.broadcast_photo(png, caption=message)
                if sent:
                    return int(sent)
            except Exception as exc:
                print(f"GOOL_{cfg.key.upper()}_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        return int(telegram.broadcast(message) or 0)

    def _record_signal(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> tuple[bool, int]:
        event_id = str(row.get("event_id") or "")
        phase = str(row.get("phase") or "LIVE").upper()
        if self._already_seen(cfg.key, event_id, phase):
            return False, 0
        mode = _mode()
        sent = self._deliver(row, signal, cfg) if mode == "active" else 0
        entry = {
            "entry_id": f"{cfg.key}:{phase.lower()}:{event_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "origin": str(row.get("origin") or ("multisport_prematch" if phase == "PREMATCH" else "multisport_live")),
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": row.get("flashscore_event_id"),
            "home": row.get("home"),
            "away": row.get("away"),
            "league": row.get("league"),
            "score": list(row.get("score") or [0, 0]) if phase == "LIVE" else None,
            "period": row.get("period") if phase == "LIVE" else None,
            "start_ts": float(row.get("start_ts") or 0.0),
            "scheduled_start": row.get("scheduled_start"),
            "clock_seconds": row.get("clock_seconds"),
            "direction": signal.get("direction"),
            "line": float(signal.get("line") or 0.0),
            "odd": float(signal.get("odd") or 0.0),
            "fair_probability": float(signal.get("fair_probability") or 0.0),
            "metric_delta": float(signal.get("metric_delta") or 0.0),
            "probability_delta_pp": float(signal.get("probability_delta_pp") or 0.0),
            "line_delta": float(signal.get("line_delta") or 0.0),
            "moves": int(signal.get("moves") or 0),
            "strength": float(signal.get("strength") or 0.0),
            "extreme": bool(signal.get("extreme")),
            "mapping_score": float(row.get("flashscore_match_score") or 0.0),
            "result": "pending",
            "profit_units": 0.0,
            "mode": mode,
            "telegram_sent": sent > 0,
        }
        rows = _load_rows(self.journal_path)
        rows.append(entry)
        _save_rows(self.journal_path, rows)
        print(
            f"GOOL_MULTISPORT_SIGNAL phase={phase} sport={cfg.key} match={row.get('home')}--{row.get('away')} "
            f"selection={entry['direction']}:{entry['line']:g} odd={entry['odd']:.2f} "
            f"strength={entry['strength']:.0f} mode={mode}",
            flush=True,
        )
        return True, sent

    def _scan_prematch(self, cfg: SportConfig, fs_today: list[dict[str, Any]]) -> dict[str, Any]:
        if not _truthy("GOOL_MULTISPORT_PREMATCH_ENABLED", True):
            return {"enabled": False, "matches": []}
        now = time.time()
        horizon = max(15 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", 6 * 60 * 60.0))
        fs_upcoming = [
            row for row in fs_today
            if str(row.get("coarse_status") or "") == "1"
            and float(row.get("start_ts") or 0.0) > now
            and float(row.get("start_ts") or 0.0) - now <= horizon
        ]
        xbet_prematch = self._xbet_prematch_index(cfg)
        mapped = map_xbet_to_flashscore(xbet_prematch, fs_upcoming)[:max(1, _int_env("GOOL_MULTISPORT_PREMATCH_MAX_MAPPED_PER_SPORT", 80))]
        decoded = failed = detected = delivered = 0
        latest: list[dict[str, Any]] = []
        workers = max(2, min(12, _int_env("GOOL_MULTISPORT_PREMATCH_GAME_WORKERS", 6)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._prematch_snapshot, event, fs, reversed_order, score, cfg) for event, fs, reversed_order, score in mapped]
            for future in as_completed(futures):
                try:
                    row, error = future.result(timeout=18)
                except Exception:
                    row, error = None, "prematch_market_decode"
                if row is None:
                    if error not in {"prematch_outside_horizon", "prematch_started"}:
                        failed += 1
                    continue
                decoded += 1
                history = self._append_prematch_history(row, cfg)
                signal = detect_prematch_steam(history, cfg, now=float(row["ts"]))
                if signal is not None:
                    recorded, sent = self._record_signal(row, signal, cfg)
                    detected += int(recorded)
                    delivered += int(bool(sent))
                    row["signal"] = signal
                latest.append(row)
        return {
            "enabled": True,
            "flashscore_prematch": len(fs_upcoming),
            "xbet_prematch": len(xbet_prematch),
            "prematch_mapped": len(mapped),
            "prematch_decoded": decoded,
            "prematch_market_decode_failed": failed,
            "prematch_detected": detected,
            "prematch_delivered": delivered,
            "xbet_prematch_diag": self._prematch_index_diag.get(cfg.key) or {},
            "matches": sorted(latest, key=lambda row: float(row.get("start_ts") or 0.0))[:80],
        }

    def _scan_sport(self, cfg: SportConfig) -> dict[str, Any]:
        fs_today = self._flashscore_today(cfg)
        states = {str(row["flashscore_event_id"]): row for row in fs_today}
        settled = self._settle(cfg, states)
        prematch = self._scan_prematch(cfg, fs_today)
        fs_live = [row for row in fs_today if str(row.get("coarse_status") or "") == "2"]
        xbet_live = self._xbet_index(cfg)
        mapped = map_xbet_to_flashscore(xbet_live, fs_live)[:max(1, _int_env("XBET_MULTISPORT_MAX_MAPPED_PER_SPORT", 120))]

        decoded = mismatch = failed = detected = delivered = 0
        latest: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        workers = max(2, min(16, _int_env("XBET_MULTISPORT_GAME_WORKERS", 8)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._snapshot, event, fs, reversed_order, score, cfg) for event, fs, reversed_order, score in mapped]
            for future in as_completed(futures):
                try:
                    row, error = future.result(timeout=18)
                except Exception:
                    row, error = None, "market_decode"
                if row is None:
                    if error == "score_mismatch":
                        mismatch += 1
                    else:
                        failed += 1
                    if len(diagnostics) < 6:
                        diagnostics.append(str(error or "unknown"))
                    continue
                decoded += 1
                history, score_changed_at = self._append_history(row, cfg)
                signal = detect_steam(history, cfg, now=float(row["ts"]), score_changed_at=score_changed_at)
                if signal is not None:
                    recorded, sent = self._record_signal(row, signal, cfg)
                    if recorded:
                        detected += 1
                    if sent:
                        delivered += 1
                    row["signal"] = signal
                    row["steam"] = signal
                latest.append(row)

        return {
            "enabled": True,
            "settled": settled,
            "prematch": prematch,
            "flashscore_prematch": int(prematch.get("flashscore_prematch") or 0),
            "xbet_prematch": int(prematch.get("xbet_prematch") or 0),
            "prematch_mapped": int(prematch.get("prematch_mapped") or 0),
            "prematch_decoded": int(prematch.get("prematch_decoded") or 0),
            "prematch_detected": int(prematch.get("prematch_detected") or 0),
            "prematch_delivered": int(prematch.get("prematch_delivered") or 0),
            "prematch_matches": list(prematch.get("matches") or []),
            "flashscore_live": len(fs_live),
            "xbet_live": len(xbet_live),
            "mapped": len(mapped),
            "decoded": decoded,
            "score_mismatch": mismatch,
            "market_decode_failed": failed,
            "detected": detected,
            "delivered": delivered,
            "diagnostics": diagnostics,
            "xbet_diag": self._index_diag.get(cfg.key) or {},
            "matches": latest[:80],
        }

    def collect_once(self) -> dict[str, Any]:
        started = time.time()
        sports: dict[str, Any] = {}
        for key, cfg in SPORTS.items():
            if not _sport_enabled(key):
                sports[key] = {"enabled": False}
                continue
            stats = self._scan_sport(cfg)
            sports[key] = stats
            print(
                f"GOOL_{key.upper()} fs={stats['flashscore_live']} xbet={stats['xbet_live']} mapped={stats['mapped']} "
                f"decoded={stats['decoded']} mismatch={stats['score_mismatch']} decode_fail={stats['market_decode_failed']} "
                f"live_signals={stats['detected']} prematch_signals={stats['prematch_detected']} "
                f"prematch={stats['flashscore_prematch']}/{stats['prematch_decoded']} "
                f"delivered={stats['delivered'] + stats['prematch_delivered']} settled={stats['settled']}",
                flush=True,
            )
            if not (stats.get("xbet_diag") or {}).get("ok"):
                print(f"GOOL_{key.upper()}_XBET_DIAG " + json.dumps(stats.get("xbet_diag") or {}, ensure_ascii=False, separators=(",", ":")), flush=True)

        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": int((time.time() - started) * 1000),
            "mode": _mode(),
            "flashscore_whitelist_required": True,
            "score_sync_required": True,
            "sports": sports,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(self.state_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        trim_file_tail(self.history_path, max(1024 * 1024, _int_env("XBET_MULTISPORT_HISTORY_KEEP_BYTES", 8 * 1024 * 1024)))
        return state

    def run(self, interval: float = 20.0) -> None:
        interval = max(8.0, float(interval))
        print(
            f"GOOL_MULTISPORT started mode={_mode()} sports=hockey,basketball interval={interval:g}s "
            "flashscore_whitelist=required score_sync=required",
            flush=True,
        )
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.collect_once()
            except Exception as exc:
                print(f"GOOL_MULTISPORT_ERROR {type(exc).__name__}:{exc}", flush=True)
            self._stop.wait(max(1.0, interval - (time.monotonic() - started)))


def main() -> None:
    parser = argparse.ArgumentParser(description="GOOL multisport hockey+basketball worker")
    parser.add_argument("--interval", type=float, default=_float_env("GOOL_MULTISPORT_INTERVAL_SECONDS", 20.0))
    args = parser.parse_args()
    worker = MultiSportSteamWorker()
    try:
        worker.run(args.interval)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
