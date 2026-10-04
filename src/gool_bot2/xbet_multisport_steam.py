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
from zoneinfo import ZoneInfo

from . import telegram
from . import xbet_market_pressure as market
from .providers.common import norm_team
from .providers.flashscore import FlashscoreProvider, _as_int, _fields
from .storage_runtime import trim_file_tail
from .multisport_journal import append_unique, load_journal, save_journal
from .hockey_signal_card import render_hockey_live_card, render_hockey_prematch_card
from .basketball_signal_card import render_basketball_live_card, render_basketball_prematch_card
from .xbet_multisport_markets import (
    SCOPE_FULL,
    balanced_total as sport_balanced_total,
    decode_core_markets,
    lane_key,
    lane_phase_policy,
    lane_score,
    market_lanes,
    period_scores,
    raw_catalog,
    scope_from_subgame,
    selection_label,
)


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


def _display_tz():
    try:
        return ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return timezone.utc


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


def _lane_threshold_scale(rows: list[dict[str, Any]], cfg: SportConfig) -> float:
    if not rows:
        return 1.0
    row = rows[-1]
    scope = str(row.get("scope") or SCOPE_FULL)
    family = str(row.get("market_family") or "match_total")
    scale = 1.0
    if cfg.key == "basketball":
        if scope.startswith("QUARTER_"):
            scale *= _float_env("GOOL_BASKETBALL_QUARTER_THRESHOLD_SCALE", 0.45)
        elif scope in {"FIRST_HALF", "SECOND_HALF"}:
            scale *= _float_env("GOOL_BASKETBALL_HALF_THRESHOLD_SCALE", 0.68)
        if family in {"home_total", "away_total"}:
            scale *= _float_env("GOOL_BASKETBALL_TEAM_TOTAL_THRESHOLD_SCALE", 0.72)
    else:
        if scope.startswith("PERIOD_"):
            scale *= _float_env("GOOL_HOCKEY_PERIOD_THRESHOLD_SCALE", 0.65)
        if family in {"home_total", "away_total"}:
            scale *= _float_env("GOOL_HOCKEY_TEAM_TOTAL_THRESHOLD_SCALE", 0.75)
    return max(0.25, min(1.0, scale))


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
    threshold_scale = _lane_threshold_scale(eligible, cfg)
    min_metric = cfg.min_metric_delta * threshold_scale
    extreme_metric = cfg.extreme_metric_delta * threshold_scale
    move_epsilon = max(0.001, cfg.move_epsilon * threshold_scale)
    moves = _one_way_moves(eligible, direction, move_epsilon)
    extreme = metric_delta >= extreme_metric
    if metric_delta < min_metric or (moves < cfg.min_moves and not extreme):
        return None

    try:
        odd = float(end[direction])
    except (TypeError, ValueError, KeyError):
        return None
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None

    over_probability_delta = (float(end["probability"]) - float(start["probability"])) * 100.0
    probability_delta_pp = over_probability_delta if direction == "over" else -over_probability_delta
    raw_line_delta = float(end["line"]) - float(start["line"])
    line_delta = raw_line_delta if direction == "over" else -raw_line_delta
    base_line_floor = _float_env(
        f"GOOL_{cfg.key.upper()}_LIVE_MIN_LINE_DELTA",
        0.5 if cfg.key == "hockey" else 2.5,
    )
    line_floor = base_line_floor * threshold_scale
    if (
        abs(over_probability_delta) < _float_env("GOOL_MULTISPORT_MIN_FAIR_EDGE_PP", 3.0)
        and line_delta < line_floor
        and not extreme
    ):
        return None
    fair_probability = float(end["probability"]) if direction == "over" else 1.0 - float(end["probability"])
    strength = min(100.0, 58.0 + metric_delta / max(1e-6, min_metric) * 13.0 + moves * 3.0 + (7.0 if extreme else 0.0))
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
        "threshold_scale": round(threshold_scale, 3),
        "line_floor": round(line_floor, 3),
        "start": start,
        "end": end,
    }


def detect_prematch_steam(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
) -> dict[str, Any] | None:
    window = max(5 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_WINDOW_SECONDS", 6 * 60 * 60.0))
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
    threshold_scale = _lane_threshold_scale(eligible, cfg)
    min_metric = _float_env(
        f"GOOL_{cfg.key.upper()}_PREMATCH_MIN_METRIC_DELTA",
        0.35 if cfg.key == "hockey" else 2.0,
    ) * threshold_scale
    line_floor = _float_env(
        f"GOOL_{cfg.key.upper()}_PREMATCH_MIN_LINE_DELTA",
        0.5 if cfg.key == "hockey" else 2.5,
    ) * threshold_scale
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
        "threshold_scale": round(threshold_scale, 3),
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
    family = str(row.get("market_family") or "match_total")
    if family == "home_total":
        total = int(home_score)
    elif family == "away_total":
        total = int(away_score)
    else:
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
        self._last_index: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._last_prematch_index: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._subgame_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._scope_scores: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
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
                    event_scope_key = f"{cfg.key}:{row.get('event_id')}"
                    for scope, score in (row.get("scoped_scores") or {}).items():
                        try:
                            self._scope_scores[event_scope_key][str(scope)] = (int(score[0]), int(score[1]))
                        except (TypeError, ValueError, IndexError):
                            continue
                    lanes = [lane for lane in (row.get("market_lanes") or []) if isinstance(lane, dict)]
                    if lanes:
                        for lane in lanes:
                            self._append_history(self._lane_row(row, lane), cfg)
                            restored += 1
                    else:
                        self._append_history(row, cfg)
                        restored += 1
                for row in (sport_state.get("prematch_matches") or []):
                    if not isinstance(row, dict) or not row.get("event_id") or row.get("ts") is None:
                        continue
                    lanes = [lane for lane in (row.get("market_lanes") or []) if isinstance(lane, dict)]
                    if lanes:
                        for lane in lanes:
                            self._append_prematch_history(self._lane_row(row, lane), cfg)
                            restored += 1
                    else:
                        self._append_prematch_history(row, cfg)
                        restored += 1
        if restored:
            print(f"GOOL_MULTISPORT_MEMORY restored_snapshots={restored}", flush=True)

    def _flashscore_today(self, cfg: SportConfig) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for path in (
            f"f_{cfg.flashscore_id}_-1_3_en_1",
            f"f_{cfg.flashscore_id}_0_3_en_1",
            f"f_{cfg.flashscore_id}_0_0_en_1",
        ):
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
        merged: dict[str, dict[str, Any]] = {}
        root_counts: dict[str, int] = {}
        event_roots: dict[str, list[str]] = {}
        for root in dict.fromkeys(roots):
            root_ids: set[str] = set()
            for query_no, query in enumerate(self._xbet_queries(cfg), 1):
                payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=7.0)
                values = payload.get("Value") if isinstance(payload, dict) else None
                raw_count = len(values) if isinstance(values, list) else 0
                attempts.append({"root": root, "query": query_no, "raw": raw_count, "payload": bool(payload)})
                if not isinstance(values, list):
                    continue
                for row in values:
                    if not isinstance(row, dict) or not row.get("I") or not row.get("O1") or not row.get("O2"):
                        continue
                    event_id = str(row["I"])
                    root_ids.add(event_id)
                    event_roots.setdefault(event_id, []).append(root)
                    merged.setdefault(event_id, row)
            root_counts[root] = len(root_ids)

        now = time.monotonic()
        if merged:
            rows = list(merged.values())
            best_root = max(root_counts, key=lambda root: root_counts.get(root, 0), default=self._roots[cfg.key])
            if root_counts.get(best_root, 0):
                self._roots[cfg.key] = best_root
            self._last_index[cfg.key] = (now, [dict(row) for row in rows])
            self._index_diag[cfg.key] = {
                "ok": True,
                "root": self._roots[cfg.key],
                "raw": len(rows),
                "usable": len(rows),
                "root_counts": root_counts,
                "cache": False,
                "attempts": attempts[-8:],
            }
            return rows

        cached_at, cached_rows = self._last_index.get(cfg.key, (0.0, []))
        age = now - cached_at if cached_at else 10**9
        max_age = max(0.0, _float_env("GOOL_MULTISPORT_INDEX_CACHE_SECONDS", 180.0))
        if cached_rows and age <= max_age:
            self._index_diag[cfg.key] = {
                "ok": True,
                "root": self._roots[cfg.key],
                "raw": len(cached_rows),
                "usable": len(cached_rows),
                "cache": True,
                "cache_age_seconds": round(age, 1),
                "attempts": attempts[-8:],
            }
            return [dict(row) for row in cached_rows]

        self._index_diag[cfg.key] = {"ok": False, "root_counts": root_counts, "attempts": attempts[-8:]}
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
        merged: dict[str, dict[str, Any]] = {}
        root_counts: dict[str, int] = {}
        for root in dict.fromkeys(roots):
            root_ids: set[str] = set()
            for query_no, query in enumerate(self._xbet_prematch_queries(cfg), 1):
                payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=8.0)
                values = payload.get("Value") if isinstance(payload, dict) else None
                raw_count = len(values) if isinstance(values, list) else 0
                attempts.append({"root": root, "query": query_no, "raw": raw_count, "payload": bool(payload)})
                if not isinstance(values, list):
                    continue
                for row in values:
                    if not isinstance(row, dict) or not row.get("I") or not row.get("O1") or not row.get("O2"):
                        continue
                    event_id = str(row["I"])
                    root_ids.add(event_id)
                    merged.setdefault(event_id, row)
            root_counts[root] = len(root_ids)

        now = time.monotonic()
        if merged:
            rows = list(merged.values())
            best_root = max(root_counts, key=lambda root: root_counts.get(root, 0), default=self._prematch_roots[cfg.key])
            if root_counts.get(best_root, 0):
                self._prematch_roots[cfg.key] = best_root
            self._last_prematch_index[cfg.key] = (now, [dict(row) for row in rows])
            self._prematch_index_diag[cfg.key] = {
                "ok": True,
                "root": self._prematch_roots[cfg.key],
                "raw": len(rows),
                "usable": len(rows),
                "root_counts": root_counts,
                "cache": False,
                "attempts": attempts[-10:],
            }
            return rows

        cached_at, cached_rows = self._last_prematch_index.get(cfg.key, (0.0, []))
        age = now - cached_at if cached_at else 10**9
        max_age = max(0.0, _float_env("GOOL_MULTISPORT_PREMATCH_INDEX_CACHE_SECONDS", 600.0))
        if cached_rows and age <= max_age:
            self._prematch_index_diag[cfg.key] = {
                "ok": True,
                "root": self._prematch_roots[cfg.key],
                "raw": len(cached_rows),
                "usable": len(cached_rows),
                "cache": True,
                "cache_age_seconds": round(age, 1),
                "attempts": attempts[-10:],
            }
            return [dict(row) for row in cached_rows]

        self._prematch_index_diag[cfg.key] = {"ok": False, "root_counts": root_counts, "attempts": attempts[-10:]}
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

    def _cached_subgame(self, sub_id: str, cfg: SportConfig, *, prematch: bool) -> dict[str, Any]:
        phase = "PREMATCH" if prematch else "LIVE"
        key = f"{phase}:{cfg.key}:{sub_id}"
        now = time.monotonic()
        ttl = max(
            5.0,
            _float_env(
                "GOOL_MULTISPORT_PREMATCH_SUBGAME_CACHE_SECONDS" if prematch else "GOOL_MULTISPORT_LIVE_SUBGAME_CACHE_SECONDS",
                180.0 if prematch else 28.0,
            ),
        )
        stale_ttl = max(ttl, _float_env("GOOL_MULTISPORT_SUBGAME_STALE_SECONDS", 600.0))
        cached_at, cached = self._subgame_cache.get(key, (0.0, {}))
        if cached and now - cached_at <= ttl:
            return dict(cached)
        game = self._prematch_game(sub_id, cfg) if prematch else self._game(sub_id, cfg)
        if isinstance(game, dict) and game:
            self._subgame_cache[key] = (now, dict(game))
            return game
        if cached and now - cached_at <= stale_ttl:
            return dict(cached)
        return {}

    @staticmethod
    def _compact_decoded(decoded: dict[str, Any]) -> dict[str, Any]:
        moneyline = dict(decoded.get("moneyline") or {})
        return {
            "scope": decoded.get("scope"),
            "match_total": list(decoded.get("match_total") or []),
            "home_total": list(decoded.get("home_total") or []),
            "away_total": list(decoded.get("away_total") or []),
            "handicap": list(decoded.get("handicap") or []),
            "moneyline": moneyline,
            "raw_market_count": len(decoded.get("raw") or []),
        }

    def _market_tree(
        self,
        game: dict[str, Any],
        cfg: SportConfig,
        *,
        prematch: bool,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        decoded: dict[str, dict[str, Any]] = {
            SCOPE_FULL: decode_core_markets(game, cfg.key, scope=SCOPE_FULL)
        }
        fetch_status: dict[str, str] = {SCOPE_FULL: "fetched"}
        unknown_catalog: dict[str, list[dict[str, Any]]] = {
            SCOPE_FULL: raw_catalog(decoded[SCOPE_FULL])
        }

        wanted: list[tuple[str, str]] = []
        seen: set[str] = set()
        for sg in game.get("SG") or []:
            if not isinstance(sg, dict):
                continue
            scope = scope_from_subgame(sg, cfg.key)
            sub_id = str(sg.get("I") or "").strip()
            if not scope or not sub_id or scope in seen:
                continue
            seen.add(scope)
            wanted.append((scope, sub_id))

        maximum = max(0, _int_env("GOOL_MULTISPORT_MAX_SUBGAMES_PER_EVENT", 8))
        wanted = wanted[:maximum]
        workers = max(1, min(4, _int_env("GOOL_MULTISPORT_SUBGAME_WORKERS", 3)))
        if wanted:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._cached_subgame, sub_id, cfg, prematch=prematch): (scope, sub_id)
                    for scope, sub_id in wanted
                }
                for future in as_completed(futures):
                    scope, _sub_id = futures[future]
                    try:
                        sub = future.result(timeout=10)
                    except Exception:
                        sub = {}
                    if not sub:
                        fetch_status[scope] = "failed"
                        continue
                    scope_decoded = decode_core_markets(sub, cfg.key, scope=scope)
                    decoded[scope] = scope_decoded
                    unknown_catalog[scope] = raw_catalog(scope_decoded)
                    fetch_status[scope] = "fetched"

        core_groups = {1, 2, 3, 4, 5, 6, 15, 16, 17, 62, 101, 102}
        unknown: list[dict[str, Any]] = []
        for scope, rows in unknown_catalog.items():
            for item in rows:
                if int(item.get("G") or -1) in core_groups:
                    continue
                unknown.append({"scope": scope, **item})
        unknown.sort(key=lambda row: (str(row.get("scope") or ""), int(row.get("G") or -1), int(row.get("T") or -1)))

        coverage: dict[str, Any] = {}
        for scope, item in decoded.items():
            coverage[scope] = {
                "match_total_lines": len(item.get("match_total") or []),
                "home_total_lines": len(item.get("home_total") or []),
                "away_total_lines": len(item.get("away_total") or []),
                "handicap_lines": len(item.get("handicap") or []),
                "moneyline": any((item.get("moneyline") or {}).values()),
                "raw_market_count": len(item.get("raw") or []),
                "fetch": fetch_status.get(scope, "fetched"),
            }
        return decoded, {
            "coverage": coverage,
            "unknown_market_catalog": unknown[:max(20, _int_env("GOOL_MULTISPORT_UNKNOWN_MARKET_CATALOG_MAX", 160))],
            "subgame_fetch": fetch_status,
        }

    def _prematch_snapshot(self, event: dict[str, Any], fs: dict[str, Any], reversed_order: bool, match_score: float, cfg: SportConfig) -> tuple[dict[str, Any] | None, str | None]:
        event_id = str(event.get("I") or "").strip()
        game = self._prematch_game(event_id, cfg) or event
        if not _event_allowed(game):
            return None, "prematch_market_decode"
        now = time.time()
        start_ts = float(fs.get("start_ts") or 0.0)
        if start_ts <= now:
            return None, "prematch_started"
        horizon = max(15 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", 30 * 60 * 60.0))
        if start_ts - now > horizon:
            return None, "prematch_outside_horizon"

        decoded, market_meta = self._market_tree(game, cfg, prematch=True)
        lanes = market_lanes(decoded)
        for lane in lanes:
            lane["lane_key"] = lane_key(lane)
            lane["metric"] = _metric(lane, (0, 0), cfg)
            lane["selection"] = selection_label(lane, "over")
        raw_count = sum(len(item.get("raw") or []) for item in decoded.values())
        if raw_count <= 0:
            return None, "prematch_market_decode"

        primary = next(
            (lane for lane in lanes if lane.get("scope") == SCOPE_FULL and lane.get("market_family") == "match_total"),
            lanes[0] if lanes else None,
        )
        return {
            "ts": now,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "phase": "PREMATCH",
            "origin": "multisport_prematch",
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": str(fs.get("flashscore_event_id") or ""),
            "home": str(fs.get("home") or "?"),
            "away": str(fs.get("away") or "?"),
            "league": str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            "start_ts": start_ts,
            "scheduled_start": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
            "line": float((primary or {}).get("line") or 0.0),
            "over": float((primary or {}).get("over") or 0.0),
            "under": float((primary or {}).get("under") or 0.0),
            "probability": float((primary or {}).get("probability") or 0.5),
            "metric": float((primary or {}).get("metric") or 0.0),
            "market_lanes": lanes,
            "markets_by_scope": {scope: self._compact_decoded(item) for scope, item in decoded.items()},
            "market_coverage": market_meta.get("coverage") or {},
            "unknown_market_catalog": market_meta.get("unknown_market_catalog") or [],
            "subgame_fetch": market_meta.get("subgame_fetch") or {},
            "flashscore_match_score": round(float(match_score), 4),
        }, None

    def _snapshot(self, event: dict[str, Any], fs: dict[str, Any], reversed_order: bool, match_score: float, cfg: SportConfig) -> tuple[dict[str, Any] | None, str | None]:
        event_id = str(event.get("I") or "").strip()
        game = self._game(event_id, cfg) or event
        xbet_score = _score(game) or _score(event)
        if xbet_score is None or not _event_allowed(game):
            return None, "market_decode"
        canonical = (xbet_score[1], xbet_score[0]) if reversed_order else xbet_score
        fs_score_raw = list(fs.get("score") or [0, 0])
        fs_score = (int(fs_score_raw[0]), int(fs_score_raw[1]))
        if canonical != fs_score:
            return None, "score_mismatch"

        decoded, market_meta = self._market_tree(game, cfg, prematch=False)
        raw_count = sum(len(item.get("raw") or []) for item in decoded.values())
        if raw_count <= 0:
            return None, "market_decode"
        scoped_scores = period_scores(game, cfg.key)
        event_scope_key = f"{cfg.key}:{event_id}"
        for scope, score in scoped_scores.items():
            self._scope_scores[event_scope_key][scope] = score

        lanes = market_lanes(decoded)
        for lane in lanes:
            score = lane_score(lane, fs_score, scoped_scores)
            lane["lane_key"] = lane_key(lane)
            lane["score"] = [int(score[0]), int(score[1])]
            lane["metric"] = _metric(lane, score, cfg)
            lane["selection"] = selection_label(lane, "over")

        primary = next(
            (lane for lane in lanes if lane.get("scope") == SCOPE_FULL and lane.get("market_family") == "match_total"),
            lanes[0] if lanes else None,
        )
        now = time.time()
        return {
            "ts": now,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "phase": "LIVE",
            "origin": "multisport_live",
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": str(fs.get("flashscore_event_id") or ""),
            "home": str(fs.get("home") or "?"),
            "away": str(fs.get("away") or "?"),
            "league": str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            "score": [*fs_score],
            "scoped_scores": {scope: [score[0], score[1]] for scope, score in scoped_scores.items()},
            "period": _period(game),
            "clock_seconds": _clock_seconds(game),
            "line": float((primary or {}).get("line") or 0.0),
            "over": float((primary or {}).get("over") or 0.0),
            "under": float((primary or {}).get("under") or 0.0),
            "probability": float((primary or {}).get("probability") or 0.5),
            "metric": float((primary or {}).get("metric") or 0.0),
            "market_lanes": lanes,
            "markets_by_scope": {scope: self._compact_decoded(item) for scope, item in decoded.items()},
            "market_coverage": market_meta.get("coverage") or {},
            "unknown_market_catalog": market_meta.get("unknown_market_catalog") or [],
            "subgame_fetch": market_meta.get("subgame_fetch") or {},
            "flashscore_match_score": round(float(match_score), 4),
            "flashscore_score_verified": True,
        }, None

    @staticmethod
    def _lane_row(row: dict[str, Any], lane: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: value
            for key, value in row.items()
            if key not in {"market_lanes", "markets_by_scope", "unknown_market_catalog", "signals", "signal", "steam"}
        }
        if row.get("score") is not None:
            compact["match_score"] = list(row.get("score") or [0, 0])
        compact.update(dict(lane))
        compact["scope"] = str(lane.get("scope") or SCOPE_FULL)
        compact["market_family"] = str(lane.get("market_family") or "match_total")
        compact["lane_key"] = str(lane.get("lane_key") or lane_key(lane))
        return compact

    def _append_history(self, row: dict[str, Any], cfg: SportConfig | None = None) -> tuple[list[dict[str, Any]], float | None]:
        cfg = cfg or SPORTS.get(str(row.get("sport") or "").casefold())
        if cfg is None:
            raise ValueError(f"unknown_multisport={row.get('sport')}")
        lane = str(row.get("lane_key") or f"{SCOPE_FULL}:match_total")
        key = f"{cfg.key}:{row['event_id']}:{lane}"
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
        lane = str(row.get("lane_key") or f"{SCOPE_FULL}:match_total")
        key = f"{cfg.key}:{row['event_id']}:{lane}"
        self._prematch_history[key].append(dict(row))
        return list(self._prematch_history[key])

    def _settle(self, cfg: SportConfig, states: dict[str, dict[str, Any]]) -> int:
        rows = load_journal(self.journal_path)
        changed = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            if row.get("sport") != cfg.key or str(row.get("result") or "pending") in _FINAL_RESULTS:
                continue
            state = states.get(str(row.get("flashscore_event_id") or ""))
            if not state or str(state.get("coarse_status") or "") != "3":
                continue
            full_score = list(state.get("score") or [0, 0])
            scope = str(row.get("scope") or SCOPE_FULL)
            if scope == SCOPE_FULL:
                score = (int(full_score[0]), int(full_score[1]))
            else:
                score = self._scope_scores.get(f"{cfg.key}:{row.get('event_id')}", {}).get(scope)
                if score is None:
                    continue
            result = settle_multisport_pick(row, int(score[0]), int(score[1]))
            profit = 0.0 if result == "void" else (float(row.get("odd") or 0.0) - 1.0 if result == "won" else -1.0)
            row.update({
                "result": result,
                "profit_units": round(profit, 4),
                "settled_at": now,
                "settled_score": [int(score[0]), int(score[1])],
                "settled_match_score": [int(full_score[0]), int(full_score[1])],
            })
            changed += 1
        if changed:
            save_journal(self.journal_path, rows)
        return changed

    def _already_seen(
        self,
        sport: str,
        event_id: str,
        phase: str,
        scope: str = SCOPE_FULL,
        market_family: str = "match_total",
    ) -> bool:
        wanted_phase = str(phase or "LIVE").upper()
        wanted_scope = str(scope or SCOPE_FULL)
        wanted_family = str(market_family or "match_total")
        for row in load_journal(self.journal_path):
            row_phase = str(row.get("phase") or ("PREMATCH" if row.get("origin") == "multisport_prematch" else "LIVE")).upper()
            row_scope = str(row.get("scope") or SCOPE_FULL)
            row_family = str(row.get("market_family") or "match_total")
            if (
                str(row.get("sport") or "") == sport
                and str(row.get("event_id") or "") == event_id
                and row_phase == wanted_phase
                and row_scope == wanted_scope
                and row_family == wanted_family
            ):
                return True
        return False

    def _format_clock(self, row: dict[str, Any]) -> str:
        raw = row.get("clock_seconds")
        if raw is None:
            return str(row.get("period") or "LIVE")
        seconds = max(0, int(raw))
        return f"{row.get('period') or 'LIVE'} · {seconds // 60:02d}:{seconds % 60:02d}"

    def _message(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> str:
        score = list(row.get("match_score") or row.get("score") or [0, 0])
        direction = str(signal.get("direction") or "over")
        market_label = str(signal.get("selection") or row.get("selection") or selection_label(row, direction, float(signal.get("line") or 0)))
        arrow = "⬆️" if direction == "over" else "⬇️"
        if str(row.get("phase") or "LIVE").upper() == "PREMATCH":
            start_ts = float(row.get("start_ts") or 0.0)
            start_label = datetime.fromtimestamp(start_ts, _display_tz()).strftime("%d.%m %H:%M МСК") if start_ts else "до старта"
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
                prematch = str(row.get("phase") or "LIVE").upper() == "PREMATCH"
                if cfg.key == "hockey":
                    png = render_hockey_prematch_card(row, signal, cfg) if prematch else render_hockey_live_card(row, signal, cfg)
                else:
                    png = render_basketball_prematch_card(row, signal, cfg) if prematch else render_basketball_live_card(row, signal, cfg)
                sent = telegram.broadcast_photo(png, caption=message)
                if sent:
                    return int(sent)
            except Exception as exc:
                print(f"GOOL_{cfg.key.upper()}_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        return int(telegram.broadcast(message) or 0)

    def _record_signal(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> tuple[bool, int]:
        event_id = str(row.get("event_id") or "")
        phase = str(row.get("phase") or "LIVE").upper()
        scope = str(row.get("scope") or SCOPE_FULL)
        family = str(row.get("market_family") or "match_total")
        if self._already_seen(cfg.key, event_id, phase, scope, family):
            return False, 0
        mode = _mode()
        direction = str(signal.get("direction") or "over")
        pick_label = selection_label(row, direction, float(signal.get("line") or row.get("line") or 0.0))
        row_for_delivery = {**row, "selection": pick_label, "scope": scope, "market_family": family}
        sent = self._deliver(row_for_delivery, signal, cfg) if mode == "active" else 0
        entry = {
            "entry_id": f"{cfg.key}:{phase.lower()}:{event_id}:{scope}:{family}",
            "journal_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "origin": str(row.get("origin") or ("multisport_prematch" if phase == "PREMATCH" else "multisport_live")),
            "signal_type": f"{phase.lower()}_{family}_movement",
            "market_family": family,
            "phase_policy": str(row.get("phase_policy") or ""),
            "card_profile": f"{cfg.key}_{phase.lower()}",
            "scope": scope,
            "lane_key": str(row.get("lane_key") or f"{scope}:{family}"),
            "selection": pick_label,
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": row.get("flashscore_event_id"),
            "home": row.get("home"),
            "away": row.get("away"),
            "league": row.get("league"),
            "score": list(row.get("score") or [0, 0]) if phase == "LIVE" else None,
            "match_score": list(row.get("match_score") or row.get("score") or [0, 0]) if phase == "LIVE" else None,
            "period": row.get("period") if phase == "LIVE" else None,
            "start_ts": float(row.get("start_ts") or 0.0),
            "scheduled_start_ts": float(row.get("start_ts") or 0.0),
            "scheduled_start": row.get("scheduled_start"),
            "clock_seconds": row.get("clock_seconds"),
            "direction": direction,
            "line": float(signal.get("line") or 0.0),
            "odd": float(signal.get("odd") or 0.0),
            "opening_line": float(((signal.get("start") or {}).get("line") or signal.get("line") or 0.0)) if phase == "PREMATCH" else None,
            "opening_odd": float(((signal.get("start") or {}).get(direction) or signal.get("odd") or 0.0)) if phase == "PREMATCH" else None,
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
        if not append_unique(self.journal_path, entry):
            return False, 0
        print(
            f"GOOL_MULTISPORT_SIGNAL phase={phase} sport={cfg.key} scope={scope} family={family} "
            f"match={row.get('home')}--{row.get('away')} selection={pick_label} odd={entry['odd']:.2f} "
            f"strength={entry['strength']:.0f} mode={mode}",
            flush=True,
        )
        return True, sent

    def _scan_prematch(self, cfg: SportConfig, fs_today: list[dict[str, Any]]) -> dict[str, Any]:
        if not _truthy("GOOL_MULTISPORT_PREMATCH_ENABLED", True):
            return {"enabled": False, "matches": []}
        now = time.time()
        horizon = max(15 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", 30 * 60 * 60.0))
        fs_upcoming = [
            row for row in fs_today
            if str(row.get("coarse_status") or "") == "1"
            and float(row.get("start_ts") or 0.0) > now
            and float(row.get("start_ts") or 0.0) - now <= horizon
        ]
        xbet_prematch = self._xbet_prematch_index(cfg)
        mapped = map_xbet_to_flashscore(xbet_prematch, fs_upcoming)[:max(1, _int_env("GOOL_MULTISPORT_PREMATCH_MAX_MAPPED_PER_SPORT", 160))]
        decoded = failed = detected = delivered = policy_blocked = 0
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
                signals: list[dict[str, Any]] = []
                for lane in row.get("market_lanes") or []:
                    allowed, policy_reason = lane_phase_policy(cfg.key, "PREMATCH", lane, row.get("period"))
                    if not allowed:
                        policy_blocked += 1
                        continue
                    lane_row = self._lane_row(row, {**lane, "phase_policy": policy_reason})
                    history = self._append_prematch_history(lane_row, cfg)
                    signal = detect_prematch_steam(history, cfg, now=float(lane_row["ts"]))
                    if signal is None:
                        continue
                    signal = {
                        **signal,
                        "scope": lane_row.get("scope"),
                        "market_family": lane_row.get("market_family"),
                        "selection": selection_label(lane_row, str(signal.get("direction") or "over"), float(signal.get("line") or 0.0)),
                    }
                    recorded, sent = self._record_signal(lane_row, signal, cfg)
                    detected += int(recorded)
                    delivered += int(bool(sent))
                    if recorded:
                        signals.append(signal)
                if signals:
                    signals.sort(key=lambda item: float(item.get("strength") or 0.0), reverse=True)
                    row["signals"] = signals
                    row["signal"] = signals[0]
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
            "prematch_policy_blocked": policy_blocked,
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

        decoded = mismatch = failed = detected = delivered = policy_blocked = 0
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
                signals: list[dict[str, Any]] = []
                for lane in row.get("market_lanes") or []:
                    allowed, policy_reason = lane_phase_policy(cfg.key, "LIVE", lane, row.get("period"))
                    if not allowed:
                        policy_blocked += 1
                        continue
                    lane_row = self._lane_row(row, {**lane, "phase_policy": policy_reason})
                    history, score_changed_at = self._append_history(lane_row, cfg)
                    signal = detect_steam(history, cfg, now=float(lane_row["ts"]), score_changed_at=score_changed_at)
                    if signal is None:
                        continue
                    signal = {
                        **signal,
                        "scope": lane_row.get("scope"),
                        "market_family": lane_row.get("market_family"),
                        "selection": selection_label(lane_row, str(signal.get("direction") or "over"), float(signal.get("line") or 0.0)),
                    }
                    recorded, sent = self._record_signal(lane_row, signal, cfg)
                    if recorded:
                        detected += 1
                        signals.append(signal)
                    if sent:
                        delivered += 1
                if signals:
                    signals.sort(key=lambda item: float(item.get("strength") or 0.0), reverse=True)
                    row["signals"] = signals
                    row["signal"] = signals[0]
                    row["steam"] = signals[0]
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
            "prematch_policy_blocked": int(prematch.get("prematch_policy_blocked") or 0),
            "prematch_matches": list(prematch.get("matches") or []),
            "flashscore_live": len(fs_live),
            "xbet_live": len(xbet_live),
            "mapped": len(mapped),
            "decoded": decoded,
            "score_mismatch": mismatch,
            "market_decode_failed": failed,
            "detected": detected,
            "delivered": delivered,
            "policy_blocked": policy_blocked,
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
