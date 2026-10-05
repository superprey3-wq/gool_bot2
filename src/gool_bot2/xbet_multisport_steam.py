from __future__ import annotations

import argparse
import json
import http.cookiejar
import os
import threading
import time
import urllib.parse
import urllib.request
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
from .multisport_parlay import build_sport_parlays
from .multisport_parlay_card import render_multisport_parlay_card
from .hockey_signal_card import render_hockey_live_card, render_hockey_prematch_card, render_hockey_result_card
from .basketball_signal_card import render_basketball_live_card, render_basketball_prematch_card, render_basketball_result_card
from .xbet_multisport_markets import (
    SCOPE_FULL,
    balanced_total as sport_balanced_total,
    decode_core_markets,
    lane_key,
    lane_phase_policy,
    live_scopes_from_period,
    lane_score,
    market_lanes,
    prematch_market_lanes,
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


def _sport_http_json(url: str, timeout: float = 8.0) -> dict[str, Any] | None:
    """1xBet request profile for hockey/basketball with low-cost header fallback."""
    base_headers = dict(getattr(market, "HEADERS", {}) or {})
    base_headers["Referer"] = "https://1xbet.com/live/"
    attempts = max(1, min(3, _int_env("GOOL_MULTISPORT_HTTP_ATTEMPTS", 2)))
    profiles = [
        # Exact profile used by the original working hockey/basketball bot.
        {"Origin": "https://1xbet.com", "Referer": "https://1xbet.com/live/"},
        # Some .fi mirrors occasionally prefer a host-matched Origin.
        {
            "Origin": "https://1xbet.fi" if "1xbet.fi/" in url else "https://1xbet.com",
            "Referer": "https://1xbet.com/live/",
        },
    ]
    for attempt in range(attempts):
        headers = dict(base_headers)
        headers.update(profiles[min(attempt, len(profiles) - 1)])
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict):
                    return payload
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(max(0.05, _float_env("GOOL_MULTISPORT_HTTP_RETRY_DELAY", 0.25)))
    return None


def _team_sport_exact_json(url: str, timeout: float = 8.0) -> dict[str, Any] | None:
    """Minimal request profile for 1xBet Basketball/Hockey only."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Python/3 aiohttp-compatible",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


_V3_HTTP_LOCK = threading.Lock()
_V3_HTTP_OPENERS: dict[str, urllib.request.OpenerDirector] = {}
_V3_HTTP_BOOTED: set[str] = set()


def _sport_v3_json(host: str, path: str, ordered_query: list[tuple[str, str]], timeout: float = 8.0) -> Any:
    """Current 1xBet frontend v3 request profile with per-host cookies/bootstrap."""
    host = str(host or "").rstrip("/")
    if not host:
        return None
    with _V3_HTTP_LOCK:
        opener = _V3_HTTP_OPENERS.get(host)
        if opener is None:
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            _V3_HTTP_OPENERS[host] = opener
        booted = host in _V3_HTTP_BOOTED

    headers = dict(getattr(market, "HEADERS", {}) or {})
    headers.update({
        "User-Agent": headers.get("User-Agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": host,
        "Referer": host + "/live/",
    })

    if not booted:
        try:
            req = urllib.request.Request(host + "/", headers={
                "User-Agent": headers["User-Agent"],
                "Accept": "text/html,application/xhtml+xml",
            })
            with opener.open(req, timeout=min(max(3.0, timeout), 8.0)) as response:
                response.read(256 * 1024)
            with _V3_HTTP_LOCK:
                _V3_HTTP_BOOTED.add(host)
        except Exception:
            # Some mirrors serve the API even when homepage bootstrap is blocked.
            pass

    url = f"{host}{path}?{urllib.parse.urlencode(ordered_query)}"
    req = urllib.request.Request(url, headers=headers)
    attempts = max(1, min(3, _int_env("GOOL_MULTISPORT_HTTP_ATTEMPTS", 2)))
    for attempt in range(attempts):
        try:
            with opener.open(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, (dict, list)):
                    return payload
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(max(0.05, _float_env("GOOL_MULTISPORT_HTTP_RETRY_DELAY", 0.25)))
    return None


def _v3_hosts_from_roots(roots: list[str]) -> list[str]:
    hosts: list[str] = []
    for root in roots:
        parsed = urllib.parse.urlsplit(root)
        if parsed.scheme and parsed.netloc:
            hosts.append(f"{parsed.scheme}://{parsed.netloc}")
    return list(dict.fromkeys(hosts))


def _v3_to_legacy_market_game(payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    """Adapt main-live-feed/v3/gameEvents into legacy-like game/market shapes."""
    groups = []
    for group in payload.get("eventGroups") or []:
        if not isinstance(group, dict):
            continue
        try:
            gid = int(group.get("groupId"))
        except (TypeError, ValueError):
            continue
        selections = []
        for bucket in group.get("events") or []:
            rows = bucket if isinstance(bucket, list) else [bucket]
            for item in rows:
                if not isinstance(item, dict) or item.get("type") is None:
                    continue
                try:
                    row = {
                        "T": int(item.get("type")),
                        "C": float(item.get("cf")),
                        "B": bool(item.get("blocked") or group.get("blocked") or False),
                    }
                except (TypeError, ValueError):
                    continue
                parameter = item.get("parameter")
                try:
                    p = float(parameter)
                except (TypeError, ValueError):
                    p = 0.0
                if gid not in {1, 101, 102} or abs(p) > 1e-12:
                    row["P"] = p
                selections.append(row)
        if selections:
            groups.append({"G": gid, "ME": selections})

    scores = payload.get("scores") or {}
    sc: dict[str, Any] = {}
    try:
        sc["FS"] = {
            "S1": int(scores.get("scoreOpp1")),
            "S2": int(scores.get("scoreOpp2")),
        }
    except (TypeError, ValueError):
        pass
    current_period = str(scores.get("currentPeriodName") or "").strip()
    if current_period:
        sc["CPS"] = current_period
    timer = scores.get("timer") or {}
    try:
        if timer.get("timeSec") is not None:
            sc["TS"] = int(float(timer.get("timeSec")))
    except (TypeError, ValueError, AttributeError):
        pass
    period_rows = []
    for item in scores.get("periodScores") or []:
        if not isinstance(item, dict):
            continue
        try:
            period_rows.append({
                "Key": int(item.get("period") or 0),
                "Value": {
                    "S1": int(item.get("scoreOpp1") or 0),
                    "S2": int(item.get("scoreOpp2") or 0),
                    "NF": str(item.get("periodNameFull") or ""),
                },
            })
        except (TypeError, ValueError):
            continue
    if period_rows:
        sc["PS"] = period_rows

    subgames = []
    for item in payload.get("subGamesForMainGame") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        name = str(item.get("subGameName") or "").strip()
        embedded_groups = []
        for group in item.get("eventGroups") or []:
            if not isinstance(group, dict):
                continue
            try:
                gid = int(group.get("groupId"))
            except (TypeError, ValueError):
                continue
            selections = []
            for bucket in group.get("events") or []:
                rows = bucket if isinstance(bucket, list) else [bucket]
                for outcome in rows:
                    if not isinstance(outcome, dict) or outcome.get("type") is None:
                        continue
                    try:
                        selection = {
                            "T": int(outcome.get("type")),
                            "C": float(outcome.get("cf")),
                            "B": bool(outcome.get("blocked") or group.get("blocked") or False),
                        }
                    except (TypeError, ValueError):
                        continue
                    try:
                        parameter = float(outcome.get("parameter"))
                    except (TypeError, ValueError):
                        parameter = 0.0
                    if gid not in {1, 101, 102} or abs(parameter) > 1e-12:
                        selection["P"] = parameter
                    selections.append(selection)
            if selections:
                embedded_groups.append({"G": gid, "ME": selections})
        subgames.append({
            "I": str(item.get("id")),
            "PN": name,
            "TG": name,
            "P": item.get("period"),
            "AE": embedded_groups,
            "_market_source": "main-live-feed-v3-embedded-subgame",
        })

    game: dict[str, Any] = {
        "I": str(payload.get("id") or event_id),
        "AE": groups,
        "SG": subgames,
        "_market_source": "main-live-feed-v3",
    }
    if sc:
        game["SC"] = sc
    return game


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
    # Legacy LiveFeed/GetGameZip shape.
    for root in (fs, game):
        try:
            if root.get("S1") is not None and root.get("S2") is not None:
                return int(float(root.get("S1"))), int(float(root.get("S2")))
        except (TypeError, ValueError, AttributeError):
            pass

    # Current main-live-feed/v3 shape used by the 1xBet frontend.
    scores = game.get("scores") or game.get("Scores") or {}
    try:
        if scores.get("scoreOpp1") is not None and scores.get("scoreOpp2") is not None:
            return int(float(scores.get("scoreOpp1"))), int(float(scores.get("scoreOpp2")))
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def _score_candidates(game: dict[str, Any], cfg: SportConfig) -> list[tuple[int, int]]:
    """Return every credible full-score representation carried by 1xBet.

    Basketball feeds can update SC.FS and SC.PS a few seconds apart. Summing
    quarter scores gives us a second, independent full-score candidate instead
    of throwing away a correctly matched game on a transient feed lag.
    """
    out: list[tuple[int, int]] = []
    direct = _score(game)
    if direct is not None:
        out.append(direct)

    scoped = period_scores(game, cfg.key)
    if cfg.key == "basketball":
        quarter_scores = [
            scoped.get(f"QUARTER_{idx}") for idx in (1, 2, 3, 4)
            if scoped.get(f"QUARTER_{idx}") is not None
        ]
        if quarter_scores:
            summed = (
                sum(int(score[0]) for score in quarter_scores),
                sum(int(score[1]) for score in quarter_scores),
            )
            if summed not in out:
                out.append(summed)
    else:
        period_rows = [
            scoped.get(f"PERIOD_{idx}") for idx in (1, 2, 3)
            if scoped.get(f"PERIOD_{idx}") is not None
        ]
        if period_rows:
            summed = (
                sum(int(score[0]) for score in period_rows),
                sum(int(score[1]) for score in period_rows),
            )
            if summed not in out:
                out.append(summed)
    return out


def _flashscore_scoped_scores(fs: dict[str, Any], cfg: SportConfig) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    parts = [p for p in (fs.get("score_parts") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if cfg.key == "hockey":
        for idx, score in enumerate(parts[:3], 1):
            out[f"PERIOD_{idx}"] = (int(score[0]), int(score[1]))
    else:
        for idx, score in enumerate(parts[:4], 1):
            out[f"QUARTER_{idx}"] = (int(score[0]), int(score[1]))

    # At a segment transition Flashscore can expose the new match minute before
    # BA/BB/BC... contains a row for the new period/quarter. Recover the current
    # segment score as full score minus all completed segment parts.
    current_scope = _infer_flashscore_scope(fs, cfg)
    if current_scope and current_scope not in out:
        try:
            idx = int(current_scope.rsplit("_", 1)[1])
            full = list(fs.get("score") or [0, 0])
            completed = [
                out.get(f"{'PERIOD' if cfg.key == 'hockey' else 'QUARTER'}_{part_idx}")
                for part_idx in range(1, idx)
            ]
            known = [score for score in completed if score is not None]
            if len(known) == max(0, idx - 1):
                derived = (
                    max(0, int(full[0]) - sum(int(score[0]) for score in known)),
                    max(0, int(full[1]) - sum(int(score[1]) for score in known)),
                )
                out[current_scope] = derived
        except (TypeError, ValueError, IndexError):
            pass

    if cfg.key == "basketball":
        q1, q2 = out.get("QUARTER_1"), out.get("QUARTER_2")
        q3, q4 = out.get("QUARTER_3"), out.get("QUARTER_4")
        if q1 and q2:
            out["FIRST_HALF"] = (q1[0] + q2[0], q1[1] + q2[1])
        if q3 and q4:
            out["SECOND_HALF"] = (q3[0] + q4[0], q3[1] + q4[1])
    return out


def _numeric_live_segment_index(fs: dict[str, Any], cfg: SportConfig) -> int:
    """Return current segment index only when Flashscore exposes a numeric whole-match minute."""
    try:
        minute = int(float(str(fs.get("status_code") or "").strip()))
    except (TypeError, ValueError):
        return 0
    if minute <= 0:
        return 0
    if cfg.key == "hockey":
        return max(1, min(3, (minute - 1) // 20 + 1))
    league = str(fs.get("league") or "").casefold()
    segment_minutes = 12 if ("nba" in league or "g league" in league) and "wnba" not in league else 10
    return max(1, min(4, (minute - 1) // segment_minutes + 1))


def multisport_scope_is_complete(fs: dict[str, Any], sport: str, scope: str) -> bool:
    """True once a scoped market's segment has definitely ended."""
    cfg = SPORTS.get(str(sport or "").casefold())
    if cfg is None:
        return False
    scope = str(scope or SCOPE_FULL)
    if str(fs.get("coarse_status") or "") == "3":
        return True
    current = _numeric_live_segment_index(fs, cfg)
    if current <= 0:
        return False
    if cfg.key == "hockey" and scope.startswith("PERIOD_"):
        try:
            target = int(scope.rsplit("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            return False
        return current > target
    if cfg.key == "basketball" and scope.startswith("QUARTER_"):
        try:
            target = int(scope.rsplit("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            return False
        return current > target
    if cfg.key == "basketball" and scope == "FIRST_HALF":
        return current > 2
    return False


def _infer_flashscore_scope(
    fs: dict[str, Any],
    cfg: SportConfig,
    section_keys: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Infer current segment from Flashscore minute + score-part evidence.

    For hockey/basketball the master-feed AC value frequently behaves like a
    whole-match minute (e.g. hockey 46 => P3, basketball 23/38 => later quarters),
    not a universal status enum. Score-parts are used as a second authority.
    """
    prefix = "PERIOD_" if cfg.key == "hockey" else "QUARTER_"
    maximum = 3 if cfg.key == "hockey" else 4
    parts = [
        row for row in (fs.get("score_parts") or [])
        if isinstance(row, (list, tuple)) and len(row) >= 2
    ]
    parts_idx = max(1, min(maximum, len(parts) or 1))

    minute_idx = 0
    try:
        minute = int(float(str(fs.get("status_code") or "").strip()))
    except (TypeError, ValueError):
        minute = 0
    if minute > 0:
        if cfg.key == "hockey":
            minute_idx = max(1, min(3, (minute - 1) // 20 + 1))
        else:
            league = str(fs.get("league") or "").casefold()
            segment_minutes = 12 if ("nba" in league or "g league" in league) and "wnba" not in league else 10
            minute_idx = max(1, min(4, (minute - 1) // segment_minutes + 1))

    idx = max(parts_idx, minute_idx or 1)
    guessed = f"{prefix}{idx}"
    available = [str(value) for value in (section_keys or []) if str(value).startswith(prefix)]
    if guessed in available:
        return guessed
    if available:
        def number(value: str) -> int:
            try:
                return int(value.rsplit("_", 1)[1])
            except (TypeError, ValueError, IndexError):
                return 0
        eligible = [value for value in available if number(value) <= idx]
        return max(eligible or available, key=number)
    return guessed


def _flashscore_period_label(scope: str, status_code: str = "") -> str:
    labels = {
        "PERIOD_1": "1-й период",
        "PERIOD_2": "2-й период",
        "PERIOD_3": "3-й период",
        "QUARTER_1": "1-я четверть",
        "QUARTER_2": "2-я четверть",
        "QUARTER_3": "3-я четверть",
        "QUARTER_4": "4-я четверть",
    }
    return labels.get(str(scope or ""), str(status_code or "LIVE") or "LIVE")


def _score_sync_allowed(
    cfg: SportConfig,
    fs_score: tuple[int, int],
    xbet_score: tuple[int, int],
    match_quality: float,
) -> bool:
    """Allow only a bounded provider-lag drift after a strong identity match.

    Flashscore remains authoritative for the score used by the LIVE brain.
    The tolerance only prevents 1xBet's slightly older scoreboard snapshot from
    deleting an otherwise correctly matched basketball/hockey event.
    """
    if fs_score == xbet_score:
        return True
    # Hockey is low-scoring and an exact score remains a valuable identity
    # guard. The bounded lag exception is basketball-only.
    if cfg.key != "basketball":
        return False

    min_quality = _float_env("GOOL_MULTISPORT_SCORE_DRIFT_MIN_MATCH", 0.80)
    quality = float(match_quality)
    if quality < min_quality:
        return False

    dh = abs(int(fs_score[0]) - int(xbet_score[0]))
    da = abs(int(fs_score[1]) - int(xbet_score[1]))

    # Basketball can move several possessions while Flashscore and 1xBet are
    # fetched sequentially. Use a wider sanity window only when team identity
    # is very strong; weaker fuzzy matches keep the tighter guard.
    strong_quality = _float_env("GOOL_BASKETBALL_SCORE_DRIFT_STRONG_MATCH", 0.92)
    if quality >= strong_quality:
        side_max = max(0, _int_env("GOOL_BASKETBALL_SCORE_DRIFT_STRONG_SIDE_MAX", 16))
        total_max = max(0, _int_env("GOOL_BASKETBALL_SCORE_DRIFT_STRONG_TOTAL_MAX", 24))
    else:
        side_max = max(0, _int_env("GOOL_BASKETBALL_SCORE_DRIFT_SIDE_MAX", 10))
        total_max = max(0, _int_env("GOOL_BASKETBALL_SCORE_DRIFT_TOTAL_MAX", 14))
    return dh <= side_max and da <= side_max and (dh + da) <= total_max


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


def _segment_duration_seconds(row: dict[str, Any], cfg: SportConfig) -> float:
    if cfg.key == "hockey":
        return 20.0 * 60.0
    league = str(row.get("league") or "").casefold()
    if any(token in league for token in ("nba", "g league")) and "wnba" not in league:
        return 12.0 * 60.0
    return 10.0 * 60.0


def _segment_clock_seconds(
    game: dict[str, Any],
    cfg: SportConfig,
    *,
    period: str,
    league: str,
) -> int | None:
    """Convert 1xBet's cumulative match clock to current-period elapsed time.

    Real basketball GetGameZip snapshots expose SC.TS as 600 at the start of
    Q2, 1200 at half-time and ~2200 during Q4. The segment brain must see
    0..600 (or 0..720 in NBA), not the cumulative match value.
    """
    raw = _clock_seconds(game)
    if raw is None:
        return None

    allowed = live_scopes_from_period(cfg.key, period)
    if cfg.key == "basketball":
        scope = next((value for value in allowed if value.startswith("QUARTER_")), "")
        if not scope:
            return raw
        try:
            idx = int(scope.rsplit("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            return raw
        duration = int(_segment_duration_seconds({"league": league}, cfg))
    else:
        scope = next((value for value in allowed if value.startswith("PERIOD_")), "")
        if not scope:
            return raw
        try:
            idx = int(scope.rsplit("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            return raw
        duration = int(20 * 60)

    offset = max(0, idx - 1) * duration
    # For cumulative elapsed clocks this yields the local segment clock.
    # If a mirror already returns a segment-local value, keep it unchanged.
    local = raw - offset if raw >= offset else raw
    return max(0, min(duration, int(local)))


def detect_live_segment_stats(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
    score_changed_at: float | None,
) -> dict[str, Any] | None:
    """Stat-first LIVE brain for the current hockey period/basketball quarter.

    The decision is driven by segment score + game-clock pace. 1xBet total/fair
    price is a confirmation/veto layer, not the source of the projection.
    """
    eligible = [row for row in rows if now - float(row.get("ts") or 0.0) <= cfg.window_seconds]
    if len(eligible) < 4:
        return None
    age = float(eligible[-1]["ts"]) - float(eligible[0]["ts"])
    if age < cfg.min_age_seconds:
        return None
    if score_changed_at is not None and now - score_changed_at < cfg.score_guard_seconds:
        return None

    clock_rows = []
    for row in eligible:
        try:
            clock = float(row.get("clock_seconds"))
        except (TypeError, ValueError):
            continue
        if clock >= 0:
            clock_rows.append((row, clock))
    if len(clock_rows) < 2:
        return None

    first, first_clock = clock_rows[0]
    end, end_clock = clock_rows[-1]
    duration = _segment_duration_seconds(end, cfg)
    observed_max = max(clock for _, clock in clock_rows)
    if observed_max > duration * 1.05 and observed_max <= 15 * 60:
        duration = max(duration, observed_max)

    delta_clock = end_clock - first_clock
    if abs(delta_clock) < _float_env("GOOL_MULTISPORT_LIVE_MIN_CLOCK_DELTA_SECONDS", 35.0):
        return None
    if delta_clock > 0:
        elapsed = min(duration, end_clock)
    else:
        elapsed = min(duration, max(0.0, duration - end_clock))
    if elapsed < _float_env("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_ELAPSED_SECONDS", 75.0):
        return None
    remaining = max(0.0, duration - elapsed)
    if remaining < _float_env("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_REMAINING_SECONDS", 45.0):
        return None

    def total_score(row: dict[str, Any]) -> int:
        score = list(row.get("score") or [0, 0])
        return int(score[0] or 0) + int(score[1] or 0)

    current = total_score(end)
    first_total = total_score(first)
    score_delta = max(0, current - first_total)
    game_clock_delta = max(1.0, abs(delta_clock))
    recent_rate = score_delta * 60.0 / game_clock_delta
    overall_rate = current * 60.0 / max(1.0, elapsed)

    if cfg.key == "basketball":
        recent_weight = _float_env("GOOL_BASKETBALL_LIVE_RECENT_PACE_WEIGHT", 0.55)
        max_rate = _float_env("GOOL_BASKETBALL_LIVE_MAX_POINTS_PER_MINUTE", 8.5)
    else:
        recent_weight = _float_env("GOOL_HOCKEY_LIVE_RECENT_PACE_WEIGHT", 0.30)
        max_rate = _float_env("GOOL_HOCKEY_LIVE_MAX_GOALS_PER_MINUTE", 0.45)
    recent_rate = min(max_rate, max(0.0, recent_rate))
    overall_rate = min(max_rate, max(0.0, overall_rate))
    blended_rate = overall_rate * (1.0 - recent_weight) + recent_rate * recent_weight
    goal_pace_remaining = blended_rate * (remaining / 60.0)
    stat_projection = current + goal_pace_remaining

    hockey_stats = dict(end.get("live_game_stats") or {}) if cfg.key == "hockey" else {}
    hockey_pressure: dict[str, Any] = {}
    if cfg.key == "hockey" and hockey_stats:
        end_shots = list(hockey_stats.get("shots_on_goal") or [])
        first_stats = dict(first.get("live_game_stats") or {})
        first_shots = list(first_stats.get("shots_on_goal") or [])
        if len(end_shots) >= 2:
            shot_total = max(0, int(end_shots[0])) + max(0, int(end_shots[1]))
            overall_shot_rate = shot_total * 60.0 / max(1.0, elapsed)
            recent_shot_rate = overall_shot_rate
            if len(first_shots) >= 2:
                first_shot_total = max(0, int(first_shots[0])) + max(0, int(first_shots[1]))
                recent_shot_rate = max(0, shot_total - first_shot_total) * 60.0 / game_clock_delta
            shot_recent_weight = _float_env("GOOL_HOCKEY_LIVE_RECENT_SHOTS_WEIGHT", 0.60)
            blended_shot_rate = (
                overall_shot_rate * (1.0 - shot_recent_weight)
                + recent_shot_rate * shot_recent_weight
            )
            projected_remaining_shots = max(0.0, blended_shot_rate * (remaining / 60.0))
            goal_per_shot = _float_env("GOOL_HOCKEY_LIVE_GOAL_PER_SHOT", 0.08)
            shot_goal_remaining = projected_remaining_shots * goal_per_shot
            shot_weight = max(0.0, min(0.80, _float_env("GOOL_HOCKEY_LIVE_SHOTS_PROJECTION_WEIGHT", 0.55)))
            fused_remaining = goal_pace_remaining * (1.0 - shot_weight) + shot_goal_remaining * shot_weight

            penalties = list(hockey_stats.get("penalties_2m") or [])
            first_penalties = list(first_stats.get("penalties_2m") or [])
            penalty_delta = 0
            if len(penalties) >= 2:
                penalty_total = max(0, int(penalties[0])) + max(0, int(penalties[1]))
                first_penalty_total = (
                    max(0, int(first_penalties[0])) + max(0, int(first_penalties[1]))
                    if len(first_penalties) >= 2 else penalty_total
                )
                penalty_delta = max(0, penalty_total - first_penalty_total)
            pp_goals = list(hockey_stats.get("powerplay_goals") or [])
            pp_total = sum(max(0, int(value)) for value in pp_goals[:2]) if len(pp_goals) >= 2 else 0
            volatility_boost = min(
                _float_env("GOOL_HOCKEY_LIVE_SPECIAL_TEAMS_MAX_BOOST", 0.18),
                penalty_delta * _float_env("GOOL_HOCKEY_LIVE_PENALTY_GOAL_BOOST", 0.05)
                + pp_total * _float_env("GOOL_HOCKEY_LIVE_PP_GOAL_BOOST", 0.02),
            )
            stat_projection = current + fused_remaining + volatility_boost
            hockey_pressure = {
                "shots_on_goal": [int(end_shots[0]), int(end_shots[1])],
                "overall_shots_per_min": round(overall_shot_rate, 3),
                "recent_shots_per_min": round(recent_shot_rate, 3),
                "projected_remaining_shots": round(projected_remaining_shots, 2),
                "penalties_2m": [int(x) for x in penalties[:2]] if len(penalties) >= 2 else None,
                "powerplay_goals": [int(x) for x in pp_goals[:2]] if len(pp_goals) >= 2 else None,
                "special_teams_boost": round(volatility_boost, 3),
            }

    line = float(end.get("line") or 0.0)
    if line <= 0:
        return None
    market_prior_weight = _float_env("GOOL_MULTISPORT_LIVE_MARKET_PRIOR_WEIGHT", 0.20)
    projection = stat_projection * (1.0 - market_prior_weight) + line * market_prior_weight
    raw_edge = projection - line
    direction = "over" if raw_edge > 0 else "under"
    stat_edge = abs(raw_edge)
    min_edge = _float_env(
        f"GOOL_{cfg.key.upper()}_LIVE_SEGMENT_MIN_STAT_EDGE",
        0.35 if cfg.key == "hockey" else 2.5,
    )
    if stat_edge < min_edge:
        return None

    over_probability = float(end.get("probability") or 0.5)
    chosen_market_probability = over_probability if direction == "over" else 1.0 - over_probability
    opposition_floor = _float_env("GOOL_MULTISPORT_LIVE_MARKET_OPPOSITION_FLOOR", 0.42)
    if chosen_market_probability < opposition_floor:
        return None

    start_over_probability = float(first.get("probability") or 0.5)
    raw_prob_delta = (over_probability - start_over_probability) * 100.0
    probability_delta_pp = raw_prob_delta if direction == "over" else -raw_prob_delta
    raw_line_delta = float(end.get("line") or 0.0) - float(first.get("line") or 0.0)
    line_delta = raw_line_delta if direction == "over" else -raw_line_delta
    market_confirmed = (
        chosen_market_probability >= 0.50
        or probability_delta_pp >= 0.75
        or line_delta >= (0.25 if cfg.key == "hockey" else 1.0)
    )

    odd = float(end.get(direction) or 0.0)
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None

    edge_ratio = stat_edge / max(1e-6, min_edge)
    strength = min(
        100.0,
        58.0
        + edge_ratio * 12.0
        + min(10.0, abs(probability_delta_pp) * 1.2)
        + (7.0 if market_confirmed else 0.0),
    )
    return {
        "brain_mode": "segment_stats",
        "direction": direction,
        "line": line,
        "odd": odd,
        "fair_probability": round(chosen_market_probability, 6),
        "metric_delta": round(stat_edge, 3),
        "stat_edge": round(stat_edge, 3),
        "projected_total": round(projection, 2),
        "raw_stat_projection": round(stat_projection, 2),
        "current_segment_total": current,
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(remaining, 1),
        "overall_rate_per_min": round(overall_rate, 3),
        "recent_rate_per_min": round(recent_rate, 3),
        "probability_delta_pp": round(probability_delta_pp, 2),
        "line_delta": round(line_delta, 2),
        "moves": max(0, len(eligible) - 1),
        "age_seconds": round(age, 1),
        "strength": round(strength, 1),
        "market_confirmed": bool(market_confirmed),
        "market_probability": round(chosen_market_probability, 4),
        "hockey_pressure": hockey_pressure,
        "start": first,
        "end": end,
    }


def _expected_live_segment_total(cfg: SportConfig, league: str) -> float:
    if cfg.key == "hockey":
        return _float_env("GOOL_HOCKEY_LIVE_EXPECTED_PERIOD_TOTAL", 1.90)
    text = str(league or "").casefold()
    if ("nba" in text or "g league" in text) and "wnba" not in text:
        return _float_env("GOOL_BASKETBALL_LIVE_EXPECTED_NBA_QUARTER_TOTAL", 56.0)
    return _float_env("GOOL_BASKETBALL_LIVE_EXPECTED_QUARTER_TOTAL", 48.0)


def _priced_segment_projection(
    brain: dict[str, Any],
    lane: dict[str, Any],
    cfg: SportConfig,
) -> tuple[float, float, float] | None:
    """Project current segment only after Brain selected the game.

    Flashscore stats choose the candidate. Once selected, the already-fetched
    1xBet game snapshot contributes a reliable local segment clock. That clock
    is not used for discovery; it is used only to price the chosen candidate.
    """
    try:
        elapsed = float(lane.get("clock_seconds"))
    except (TypeError, ValueError):
        return None
    duration = float(_segment_duration_seconds(lane, cfg))
    if elapsed <= 0 or elapsed > duration:
        return None
    remaining = max(0.0, duration - elapsed)
    if remaining < _float_env("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_REMAINING_SECONDS", 45.0):
        return None

    score = list(lane.get("score") or brain.get("current_segment_score") or [0, 0])
    try:
        current = max(0.0, float(score[0])) + max(0.0, float(score[1]))
    except (TypeError, ValueError, IndexError):
        return None

    prior = _expected_live_segment_total(cfg, str(lane.get("league") or brain.get("league") or ""))
    observed_weight = max(0.20, min(0.85, elapsed / max(1.0, duration)))

    if cfg.key == "basketball":
        raw_pace_total = current * duration / max(1.0, elapsed)
        projection = prior * (1.0 - observed_weight) + raw_pace_total * observed_weight
    else:
        stats_payload = dict(lane.get("live_game_stats") or brain.get("live_game_stats") or {})
        stats = dict(stats_payload.get("segment_stats") or {})
        stats_mode = str(stats_payload.get("stats_mode") or "")
        shots = list(stats.get("shots_on_goal") or stats.get("shots") or [])
        shot_total = (
            max(0.0, float(shots[0])) + max(0.0, float(shots[1]))
            if len(shots) >= 2 else 0.0
        )
        raw_goal_total = current * duration / max(1.0, elapsed)
        if stats_mode == "cumulative_through_current_segment":
            # Cumulative P1+P2(+P3) shots are excellent candidate evidence but
            # must not be treated as if every shot happened in the current
            # period. For pricing, use only the fresh shot-rate delta already
            # measured by the Flashscore Brain across recent snapshots.
            try:
                recent_shot_rate = max(0.0, float(brain.get("recent_shot_rate") or 0.0))
            except (TypeError, ValueError):
                recent_shot_rate = 0.0
            if recent_shot_rate > 0:
                projected_remaining_shots = recent_shot_rate * remaining / 60.0
                shot_projection = current + projected_remaining_shots * _float_env("GOOL_HOCKEY_LIVE_GOAL_PER_SHOT", 0.08)
                observed_total = raw_goal_total * 0.55 + shot_projection * 0.45
            else:
                observed_total = raw_goal_total
        elif shot_total > 0:
            projected_shots = shot_total * duration / max(1.0, elapsed)
            shot_goal_total = projected_shots * _float_env("GOOL_HOCKEY_LIVE_GOAL_PER_SHOT", 0.08)
            observed_total = raw_goal_total * 0.45 + shot_goal_total * 0.55
        else:
            observed_total = raw_goal_total
        projection = prior * (1.0 - observed_weight) + observed_total * observed_weight

    # A projection can never finish below the score already recorded.
    projection = max(current, projection)
    return float(projection), float(elapsed), float(remaining)


def price_flashscore_live_candidate(
    brain: dict[str, Any],
    lane: dict[str, Any],
    cfg: SportConfig,
) -> dict[str, Any] | None:
    """Attach current 1xBet segment price after Flashscore Brain selection."""
    if str(brain.get("brain_state") or "") not in {"PASS", "BORDERLINE"}:
        return None
    if str(lane.get("market_family") or "") != "match_total":
        return None
    scope = str(brain.get("scope") or "")
    if str(lane.get("scope") or "") != scope:
        return None

    projected = _priced_segment_projection(brain, lane, cfg)
    if projected is None:
        return None
    projection, elapsed, remaining = projected
    try:
        line = float(lane.get("line"))
    except (TypeError, ValueError):
        return None

    raw_edge = projection - line
    direction = "over" if raw_edge > 0 else "under"
    stat_edge = abs(raw_edge)

    # Hockey P3 UNDER protection: a trailing team may pull the goalie late,
    # sharply increasing goal / empty-net risk. Do not issue a late UNDER when
    # the match is within two goals. OVER is not auto-blocked by this rule.
    if cfg.key == "hockey" and str(brain.get("scope") or "") == "PERIOD_3" and direction == "under":
        # Use the pricing clock calculated above; it comes from the matched
        # 1xBet game only after Flashscore Brain has selected the candidate.
        match_score = list(lane.get("match_score") or brain.get("score") or [0, 0])
        try:
            margin = abs(int(match_score[0]) - int(match_score[1]))
        except (TypeError, ValueError, IndexError):
            margin = 99
        guard_seconds = _float_env("GOOL_HOCKEY_EMPTY_NET_UNDER_GUARD_SECONDS", 300.0)
        guard_margin = max(1, _int_env("GOOL_HOCKEY_EMPTY_NET_UNDER_GUARD_MARGIN", 2))
        if 0 < remaining <= guard_seconds and 1 <= margin <= guard_margin:
            return None
    min_edge = _float_env(
        f"GOOL_{cfg.key.upper()}_LIVE_SEGMENT_MIN_STAT_EDGE",
        0.35 if cfg.key == "hockey" else 2.5,
    )
    if stat_edge < min_edge:
        return None

    try:
        odd = float(lane.get(direction) or 0.0)
        over_probability = float(lane.get("probability") or 0.5)
    except (TypeError, ValueError):
        return None
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None
    market_probability = over_probability if direction == "over" else 1.0 - over_probability
    if market_probability < _float_env("GOOL_MULTISPORT_LIVE_MARKET_OPPOSITION_FLOOR", 0.42):
        return None

    brain_score = float(brain.get("brain_score") or 0.0)
    edge_ratio = stat_edge / max(1e-6, min_edge)
    strength = min(100.0, brain_score * 0.72 + 20.0 + min(18.0, edge_ratio * 8.0))
    score = list(lane.get("score") or brain.get("current_segment_score") or [0, 0])
    current_total = int(score[0] or 0) + int(score[1] or 0)
    return {
        "brain_mode": "flashscore_stat_first",
        "direction": direction,
        "line": line,
        "odd": odd,
        "fair_probability": round(market_probability, 6),
        "metric_delta": round(stat_edge, 3),
        "stat_edge": round(stat_edge, 3),
        "projected_total": round(projection, 2),
        "raw_stat_projection": round(projection, 2),
        "current_segment_total": current_total,
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(remaining, 1),
        "recent_rate_per_min": (
            brain.get("recent_shot_rate")
            if cfg.key == "hockey"
            else brain.get("recent_score_rate")
        ),
        "probability_delta_pp": 0.0,
        "line_delta": 0.0,
        "moves": int(brain.get("history_points") or 1) - 1,
        "age_seconds": 0.0,
        "strength": round(strength, 1),
        "market_confirmed": market_probability >= 0.50,
        "market_probability": round(market_probability, 4),
        "projection_clock_source": "1xbet_after_flashscore_brain",
        "flashscore_brain_score": round(brain_score, 1),
        "flashscore_brain_state": str(brain.get("brain_state") or ""),
        "flashscore_brain_reason": str(brain.get("brain_reason") or ""),
        "start": {},
        "end": dict(lane),
    }


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


def select_prematch_primary(
    candidates: list[tuple[dict[str, Any], dict[str, Any]]],
    recent_families: list[str] | None = None,
    sport: str = "",
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Pick one PREMATCH market without letting one family dominate by accident."""
    if not candidates:
        return None
    family_bias = {
        "match_total": 0.8,
        "home_total": 0.6,
        "away_total": 0.6,
        "moneyline": 0.3,
        "handicap": 0.0,
    }
    if str(sport or "").casefold() == "hockey":
        # Choice-market strength grows much faster than total-market strength in
        # hockey. Normalise that scale so a close total/IT candidate is not
        # permanently hidden by handicaps, while a materially stronger handicap
        # can still win.
        family_bias.update({
            "match_total": _float_env("GOOL_HOCKEY_PREMATCH_MATCH_TOTAL_BIAS", 4.0),
            "home_total": _float_env("GOOL_HOCKEY_PREMATCH_TEAM_TOTAL_BIAS", 3.0),
            "away_total": _float_env("GOOL_HOCKEY_PREMATCH_TEAM_TOTAL_BIAS", 3.0),
            "moneyline": _float_env("GOOL_HOCKEY_PREMATCH_MONEYLINE_BIAS", 0.5),
            "handicap": _float_env("GOOL_HOCKEY_PREMATCH_HANDICAP_BIAS", -3.0),
        })
    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item[1].get("strength") or 0.0)
            + family_bias.get(str(item[0].get("market_family") or ""), 0.0),
            float(item[1].get("fair_probability") or 0.0),
        ),
        reverse=True,
    )
    best_row, best_signal = ranked[0]
    best_family = str(best_row.get("market_family") or "")
    recent = [str(x or "") for x in (recent_families or []) if str(x or "")]
    streak = max(2, _int_env("GOOL_MULTISPORT_PREMATCH_FAMILY_STREAK", 3))
    if len(recent) < streak or any(family != best_family for family in recent[-streak:]):
        return best_row, best_signal
    alternative = next(
        ((row, signal) for row, signal in ranked if str(row.get("market_family") or "") != best_family),
        None,
    )
    if alternative is None:
        return best_row, best_signal
    alt_row, alt_signal = alternative
    max_gap = max(
        0.0,
        _float_env(
            "GOOL_HOCKEY_PREMATCH_FAMILY_DIVERSITY_MAX_GAP"
            if str(sport or "").casefold() == "hockey"
            else "GOOL_MULTISPORT_PREMATCH_FAMILY_DIVERSITY_MAX_GAP",
            10.0 if str(sport or "").casefold() == "hockey" else 8.0,
        ),
    )
    if float(alt_signal.get("strength") or 0.0) >= float(best_signal.get("strength") or 0.0) - max_gap:
        return alt_row, alt_signal
    return best_row, best_signal


def detect_prematch_choice(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
) -> dict[str, Any] | None:
    """Detect sustained PREMATCH support for handicap/moneyline selections."""
    window = max(5 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_WINDOW_SECONDS", 6 * 60 * 60.0))
    eligible = [row for row in rows if now - float(row.get("ts") or 0.0) <= window]
    if len(eligible) < 3:
        return None
    age = float(eligible[-1]["ts"]) - float(eligible[0]["ts"])
    if age < _float_env("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", 60.0):
        return None

    start, end = eligible[0], eligible[-1]
    probability_delta_pp = (float(end.get("probability") or 0.0) - float(start.get("probability") or 0.0)) * 100.0
    if probability_delta_pp <= 0:
        return None
    min_pp = _float_env("GOOL_MULTISPORT_PREMATCH_CHOICE_MIN_FAIR_EDGE_PP", 2.0)
    moves = sum(
        1
        for left, right in zip(eligible, eligible[1:])
        if float(right.get("probability") or 0.0) - float(left.get("probability") or 0.0) >= 0.002
    )
    extreme = probability_delta_pp >= min_pp * 1.8
    if probability_delta_pp < min_pp or (moves < 2 and not extreme):
        return None

    try:
        odd = float(end.get("odd") or 0.0)
    except (TypeError, ValueError):
        return None
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None

    line_delta = float(end.get("line") or 0.0) - float(start.get("line") or 0.0)
    strength = min(
        100.0,
        58.0 + probability_delta_pp * 4.0 + moves * 3.0 + (8.0 if extreme else 0.0),
    )
    return {
        "phase": "PREMATCH",
        "direction": str(end.get("selection_side") or end.get("choice_key") or "choice"),
        "selection_side": str(end.get("selection_side") or end.get("choice_key") or ""),
        "selection": str(end.get("selection") or "?"),
        "line": float(end.get("line") or 0.0),
        "odd": odd,
        "fair_probability": round(float(end.get("probability") or 0.0), 6),
        "metric_delta": round(probability_delta_pp, 3),
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
        score_parts: list[list[int]] = []
        for home_key, away_key in (("BA","BB"),("BC","BD"),("BE","BF"),("BG","BH"),("BI","BJ")):
            hv, av = fields.get(home_key), fields.get(away_key)
            if hv is None and av is None:
                continue
            score_parts.append([_as_int(hv), _as_int(av)])
        rows[event_id] = {
            "flashscore_event_id": event_id,
            "home": home,
            "away": away,
            "score": [_as_int(fields.get("AG"), _as_int(fields.get("AT"))), _as_int(fields.get("AH"), _as_int(fields.get("AU")))],
            "score_parts": score_parts,
            "league": league,
            "status_code": str(fields.get("AC") or ""),
            "coarse_status": str(fields.get("AB") or ""),
            "match_start_ts": _as_int(fields.get("AD"), 0),
            "period_start_ts": _as_int(fields.get("AO"), 0),
            "start_ts": _as_int(fields.get("AD") or fields.get("AO"), 0),
            "home_team_id": str(fields.get("JA") or "").strip(),
            "away_team_id": str(fields.get("JB") or "").strip(),
            "home_team_slug": str(fields.get("WU") or "").strip(),
            "away_team_slug": str(fields.get("WV") or "").strip(),
            "home_logo_file": str(fields.get("OA") or "").strip(),
            "away_logo_file": str(fields.get("OB") or "").strip(),
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


def _displayed_total_direction(row: dict[str, Any]) -> str | None:
    """Return the total direction the user actually saw on the signal card."""
    family = str(row.get("market_family") or "")
    if family not in {"match_total", "home_total", "away_total"}:
        return None
    text = str(row.get("selection") or "").strip().casefold()
    # Russian labels emitted by selection_label().
    if "итб" in text or text.startswith("тб") or ": тб" in text:
        return "over"
    if "итм" in text or text.startswith("тм") or ": тм" in text:
        return "under"
    # Defensive support for legacy/English journal rows.
    if " over " in f" {text} " or text.startswith("over"):
        return "over"
    if " under " in f" {text} " or text.startswith("under"):
        return "under"
    return None


def settle_multisport_pick(row: dict[str, Any], home_score: int, away_score: int) -> str:
    family = str(row.get("market_family") or "match_total")
    side = str(row.get("selection_side") or row.get("direction") or "").lower()
    line = float(row.get("line") or 0.0)

    if family == "moneyline":
        if home_score == away_score:
            return "void"
        winner = "home" if home_score > away_score else "away"
        return "won" if side == winner else "lost"

    if family == "handicap":
        if side == "away":
            margin = float(away_score - home_score) + line
        else:
            margin = float(home_score - away_score) + line
        if abs(margin) < 1e-9:
            return "void"
        return "won" if margin > 0 else "lost"

    if family == "home_total":
        total = int(home_score)
    elif family == "away_total":
        total = int(away_score)
    else:
        total = int(home_score) + int(away_score)
    if abs(total - line) < 1e-9:
        return "void"
    # Settlement must grade the exact bet shown to the user. Older PREMATCH
    # rows could contain selection="ТБ ..." while direction="under" because the
    # display label was prebuilt before Brain chose a side.
    direction = _displayed_total_direction(row) or str(row.get("direction") or "over").casefold()
    if direction == "under":
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
        self.parlay_delivery_path = _runtime_path(
            "GOOL_MULTISPORT_PARLAY_DELIVERY_STATE",
            "XBET_MULTISPORT_PARLAY_DELIVERY_STATE",
            runtime,
            "gool_multisport_parlays_sent.json",
        )
        self._stop = threading.Event()
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
        self._prematch_history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=180))
        # Flashscore-only Brain history is intentionally independent from 1xBet.
        # It decides which LIVE games are interesting before bookmaker pricing.
        self._fs_brain_history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=30))
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
        self._prematch_cursor: dict[str, int] = defaultdict(int)
        self._prematch_latest: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self._flashscore = FlashscoreProvider()
        self._fs_live_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._fs_scope_stat_baseline: dict[str, dict[str, Any]] = {}
        self._fs_scope_stat_samples: dict[str, int] = defaultdict(int)
        self._fs_history_cache: dict[str, tuple[float, dict[str, Any]]] = {}
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
                    self._prematch_latest[cfg.key][str(row.get("event_id"))] = dict(row)
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

    def _flashscore_live_stats(
        self,
        fs: dict[str, Any],
        cfg: SportConfig,
        *,
        current_period: str = "",
    ) -> dict[str, Any]:
        """Flashscore-first LIVE stats, selecting the current period/quarter."""
        event_id = str(fs.get("flashscore_event_id") or "").strip()
        if not event_id:
            return {}
        now = time.monotonic()
        ttl = max(4.0, _float_env("GOOL_MULTISPORT_FS_STATS_CACHE_SECONDS", 8.0))
        cache_key = f"{event_id}:{current_period}"
        cached_at, cached = self._fs_live_stats_cache.get(cache_key, (0.0, {}))
        if cached and now - cached_at <= ttl:
            return dict(cached)
        try:
            detailed = dict(self._flashscore.fetch_stats_detailed(event_id) or {})
        except Exception as exc:
            print(f"GOOL_{cfg.key.upper()}_FS_STATS_ERROR event={event_id} {type(exc).__name__}:{exc}", flush=True)
            detailed = {}

        sections = dict(detailed.get("sections") or {})
        wanted = live_scopes_from_period(cfg.key, current_period)
        requested_scope = next(iter(wanted), "")
        if not requested_scope:
            requested_scope = _infer_flashscore_scope(fs, cfg, list(sections))
        scope = requested_scope
        selected = dict(sections.get(scope) or {})
        selected_stats = dict(selected.get("stats") or {})
        # Flashscore can expose a current-period/quarter section header before
        # it exposes any stats inside that section. Do not treat an empty
        # section as usable: fall back to cumulative FULL_MATCH stats and
        # reconstruct the current segment from the baseline delta below.
        if not selected_stats:
            full_match = dict(sections.get("FULL_MATCH") or {})
            full_stats = dict(full_match.get("stats") or {})
            if full_stats:
                selected = full_match
                selected_stats = full_stats
                scope = "FULL_MATCH"
            elif not selected:
                selected = full_match
                scope = "FULL_MATCH" if full_match else requested_scope
        stats = selected_stats or dict(selected.get("stats") or {})
        segment_stats: dict[str, list[float]] = {}
        segment_attempts: dict[str, list[float]] = {}
        for key, item in stats.items():
            if not isinstance(item, dict):
                continue
            hv, av = item.get("home"), item.get("away")
            if hv is not None and av is not None:
                try:
                    segment_stats[str(key)] = [float(hv), float(av)]
                except (TypeError, ValueError):
                    pass
            ha, aa = item.get("home_attempts"), item.get("away_attempts")
            if ha is not None and aa is not None:
                try:
                    segment_attempts[str(key)] = [float(ha), float(aa)]
                except (TypeError, ValueError):
                    pass

        stats_mode = "direct_segment"
        current_segment_available = bool(segment_stats) and scope == requested_scope
        if scope == "FULL_MATCH" and requested_scope and requested_scope != "FULL_MATCH":
            # Product rule: the LIVE Brain may use cumulative Flashscore stats
            # through the current segment. Q2 = Q1+Q2, P3 = P1+P2+P3, etc.
            # The bet itself is still priced ONLY on the current quarter/period.
            # This avoids throwing away useful LIVE pressure just because
            # Flashscore has not published a separate segment-stat section.
            scope = requested_scope
            current_segment_available = bool(segment_stats)
            stats_mode = "cumulative_through_current_segment"

        out: dict[str, Any] = {
            "source": "flashscore",
            "scope": scope or None,
            "segment_stats": segment_stats,
            "segment_attempts": segment_attempts,
            "available": bool(segment_stats),
            "requested_scope": requested_scope or None,
            "current_segment_available": bool(current_segment_available),
            "stats_mode": stats_mode,
            "section_keys": list(sections),
        }
        if cfg.key == "hockey":
            for key in ("shots_on_goal","shots","blocked_shots","saves","penalties_2m","penalties","penalty_minutes","powerplay_goals","powerplay_opportunities","faceoffs_won"):
                if key in segment_stats:
                    out[key] = [int(x) if float(x).is_integer() else float(x) for x in segment_stats[key]]
        else:
            out["basketball_stats"] = segment_stats
            out["basketball_attempts"] = segment_attempts

        self._fs_live_stats_cache[cache_key] = (now, dict(out))
        return out

    def _flashscore_live_brain(self, fs: dict[str, Any], cfg: SportConfig) -> dict[str, Any]:
        """Analyse the LIVE game from Flashscore before any bookmaker lookup."""
        event_id = str(fs.get("flashscore_event_id") or "")
        scoped_scores = _flashscore_scoped_scores(fs, cfg)
        inferred_scope = _infer_flashscore_scope(fs, cfg)
        stats_payload = self._flashscore_live_stats(fs, cfg, current_period=inferred_scope)
        scope = str(stats_payload.get("scope") or inferred_scope)
        if scope == SCOPE_FULL:
            scope = inferred_scope
        segment_score = scoped_scores.get(scope)
        if segment_score is None:
            full = list(fs.get("score") or [0, 0])
            segment_score = (int(full[0] or 0), int(full[1] or 0))

        now = time.time()
        duration = _segment_duration_seconds({"league": str(fs.get("league") or "")}, cfg)
        period_start = float(fs.get("period_start_ts") or 0.0)
        elapsed = now - period_start if period_start > 0 else 0.0
        if elapsed <= 0 or elapsed > duration + 15 * 60:
            elapsed = 0.0
        elapsed = min(duration, elapsed) if elapsed > 0 else 0.0
        remaining = max(0.0, duration - elapsed) if elapsed > 0 else 0.0

        snapshot = {
            "ts": now,
            "scope": scope,
            "score": [int(segment_score[0]), int(segment_score[1])],
            "live_game_stats": stats_payload,
        }
        key = f"{cfg.key}:{event_id}:{scope}"
        history = self._fs_brain_history[key]
        history.append(snapshot)
        recent = [row for row in history if now - float(row.get("ts") or 0.0) <= 180.0]
        first = recent[0] if recent else snapshot
        age = max(1.0, now - float(first.get("ts") or now))

        stats = dict(stats_payload.get("segment_stats") or {})
        current_total = int(segment_score[0]) + int(segment_score[1])
        previous_score = list(first.get("score") or [0, 0])
        first_total = int(previous_score[0] or 0) + int(previous_score[1] or 0)
        score_delta = max(0, current_total - first_total)
        recent_score_rate = score_delta * 60.0 / age if len(recent) >= 2 else 0.0
        recent_shot_rate = 0.0
        projection: float | None = None

        if cfg.key == "hockey":
            shots = list(stats.get("shots_on_goal") or stats.get("shots") or [])
            shot_total = sum(max(0.0, float(v)) for v in shots[:2]) if len(shots) >= 2 else 0.0
            first_stats = dict((first.get("live_game_stats") or {}).get("segment_stats") or {})
            first_shots = list(first_stats.get("shots_on_goal") or first_stats.get("shots") or [])
            first_shot_total = sum(max(0.0, float(v)) for v in first_shots[:2]) if len(first_shots) >= 2 else shot_total
            recent_shot_rate = max(0.0, shot_total - first_shot_total) * 60.0 / age if len(recent) >= 2 else 0.0
            pp = list(stats.get("powerplay_goals") or stats.get("power_play_goals") or [])
            pp_total = sum(max(0.0, float(v)) for v in pp[:2]) if len(pp) >= 2 else 0.0
            penalties = list(stats.get("penalties_2m") or stats.get("penalties") or [])
            penalty_total = sum(max(0.0, float(v)) for v in penalties[:2]) if len(penalties) >= 2 else 0.0
            blocked = list(stats.get("blocked_shots") or [])
            blocked_total = sum(max(0.0, float(v)) for v in blocked[:2]) if len(blocked) >= 2 else 0.0

            if elapsed >= 45.0:
                overall_shot_rate = shot_total * 60.0 / max(1.0, elapsed)
                blended_shot_rate = overall_shot_rate * 0.55 + recent_shot_rate * 0.45
                projected_remaining_shots = max(0.0, blended_shot_rate * remaining / 60.0)
                shot_goal_remaining = projected_remaining_shots * _float_env("GOOL_HOCKEY_LIVE_GOAL_PER_SHOT", 0.08)
                goal_rate = current_total * 60.0 / max(1.0, elapsed)
                goal_remaining = min(2.5, goal_rate * remaining / 60.0)
                projection = current_total + shot_goal_remaining * 0.65 + goal_remaining * 0.35
                projection += min(0.18, penalty_total * 0.02 + pp_total * 0.03)

            rating = (
                30.0
                + min(32.0, shot_total * 1.8)
                + min(18.0, recent_shot_rate * 6.0)
                + min(10.0, current_total * 5.0)
                + min(6.0, pp_total * 3.0)
                + min(4.0, penalty_total * 0.7)
                + min(4.0, blocked_total * 0.35)
            )
            reason = (
                f"броски {shot_total:g}, темп бросков {recent_shot_rate:.1f}/мин, "
                f"шайбы периода {current_total}"
            )
        else:
            rebounds = list(stats.get("rebounds") or [])
            rebound_total = sum(max(0.0, float(v)) for v in rebounds[:2]) if len(rebounds) >= 2 else 0.0
            turnovers = list(stats.get("turnovers") or [])
            turnover_total = sum(max(0.0, float(v)) for v in turnovers[:2]) if len(turnovers) >= 2 else 0.0
            attempts = dict(stats_payload.get("segment_attempts") or {})
            fg_attempts = 0.0
            for key_name in ("field_goals", "two_point_field_goals", "three_point_field_goals"):
                pair = list(attempts.get(key_name) or [])
                if len(pair) >= 2:
                    fg_attempts = max(fg_attempts, sum(max(0.0, float(v)) for v in pair[:2]))

            if elapsed >= 30.0:
                overall_rate = current_total * 60.0 / max(1.0, elapsed)
                pace_rate = overall_rate if recent_score_rate <= 0 else overall_rate * 0.55 + recent_score_rate * 0.45
                projection = current_total + pace_rate * remaining / 60.0

            rating = (
                25.0
                + min(32.0, current_total * 0.80)
                + min(23.0, recent_score_rate * 2.4)
                + min(8.0, fg_attempts * 0.18)
                + min(7.0, rebound_total * 0.18)
                + min(5.0, turnover_total * 0.20)
            )
            reason = (
                f"очки четверти {current_total}, свежий темп {recent_score_rate:.1f}/мин, "
                f"подборы {rebound_total:g}"
            )

        if not stats_payload.get("current_segment_available"):
            rating = min(rating, 48.0)
            reason = "Flashscore LIVE есть, статистика именно текущего периода/четверти ещё недоступна"
        direction_hint = ""
        # Candidate discovery stays Flashscore-only. Do not use AO as a game
        # clock: real basketball feeds proved that its age can differ sharply
        # from the actual quarter clock. Short-window stat changes can still
        # mark slow/fast games as interesting before bookmaker pricing.
        if len(recent) >= 2 and age >= 25.0:
            if cfg.key == "basketball":
                if recent_score_rate <= _float_env("GOOL_BASKETBALL_LIVE_SLOW_PACE_PER_MIN", 2.2):
                    rating = max(rating, 58.0)
                    direction_hint = "under"
                    reason += f" · свежий темп {recent_score_rate:.1f}/мин выглядит низким"
                elif recent_score_rate >= _float_env("GOOL_BASKETBALL_LIVE_FAST_PACE_PER_MIN", 5.8):
                    rating = max(rating, 68.0)
                    direction_hint = "over"
                    reason += f" · свежий темп {recent_score_rate:.1f}/мин выглядит высоким"
            else:
                first_stats = dict((first.get("live_game_stats") or {}).get("segment_stats") or {})
                first_shots = list(first_stats.get("shots_on_goal") or first_stats.get("shots") or [])
                now_shots = list(stats.get("shots_on_goal") or stats.get("shots") or [])
                if len(first_shots) >= 2 and len(now_shots) >= 2:
                    shot_delta = max(0.0, float(now_shots[0]) + float(now_shots[1]) - float(first_shots[0]) - float(first_shots[1]))
                    recent_shot_rate = shot_delta * 60.0 / max(1.0, age)
                    if recent_shot_rate <= _float_env("GOOL_HOCKEY_LIVE_SLOW_SHOTS_PER_MIN", 0.9):
                        rating = max(rating, 52.0)
                        direction_hint = "under"
                        reason += f" · свежий темп бросков {recent_shot_rate:.1f}/мин низкий"
                    elif recent_shot_rate >= _float_env("GOOL_HOCKEY_LIVE_FAST_SHOTS_PER_MIN", 2.2):
                        rating = max(rating, 64.0)
                        direction_hint = "over"
                        reason += f" · свежий темп бросков {recent_shot_rate:.1f}/мин высокий"

        pass_default = 62.0 if cfg.key == "hockey" else 68.0
        borderline_default = 50.0 if cfg.key == "hockey" else 56.0
        pass_floor = _float_env(f"GOOL_{cfg.key.upper()}_LIVE_FS_BRAIN_PASS", pass_default)
        borderline_floor = _float_env(f"GOOL_{cfg.key.upper()}_LIVE_FS_BRAIN_BORDERLINE", borderline_default)
        state = "PASS" if rating >= pass_floor else ("BORDERLINE" if rating >= borderline_floor else "WAIT")
        if not stats_payload.get("current_segment_available"):
            state = "WAIT"
        return {
            "flashscore_event_id": event_id,
            "home": str(fs.get("home") or ""),
            "away": str(fs.get("away") or ""),
            "league": str(fs.get("league") or ""),
            "score": list(fs.get("score") or [0, 0]),
            "score_parts": list(fs.get("score_parts") or []),
            "scope": scope,
            "period": _flashscore_period_label(scope, str(fs.get("status_code") or "")),
            "status_code": str(fs.get("status_code") or ""),
            "brain_state": state,
            "brain_score": round(max(0.0, min(100.0, rating)), 1),
            "brain_reason": reason,
            "current_segment_score": [int(segment_score[0]), int(segment_score[1])],
            "current_segment_total": current_total,
            "elapsed_seconds": round(elapsed, 1),
            "remaining_seconds": round(remaining, 1),
            "projected_total": None,
            "live_game_stats": stats_payload,
            "history_points": len(recent),
            "recent_score_rate": round(recent_score_rate, 3),
            "recent_shot_rate": round(recent_shot_rate, 3),
            "direction_hint": direction_hint,
        }

    def _flashscore_live_analysis(
        self,
        fs_live: list[dict[str, Any]],
        cfg: SportConfig,
    ) -> list[dict[str, Any]]:
        maximum = max(1, min(120, _int_env("GOOL_MULTISPORT_LIVE_FS_BRAIN_MAX", 80)))
        rows = list(fs_live[:maximum])
        if not rows:
            return []
        workers = max(2, min(12, _int_env("GOOL_MULTISPORT_LIVE_FS_BRAIN_WORKERS", 6)))
        out: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._flashscore_live_brain, row, cfg) for row in rows]
            for future in as_completed(futures):
                try:
                    item = future.result(timeout=18)
                except Exception:
                    continue
                if item:
                    out.append(item)
        out.sort(key=lambda row: float(row.get("brain_score") or 0.0), reverse=True)
        return out

    def _flashscore_prematch_candidate(self, fs: dict[str, Any], cfg: SportConfig) -> dict[str, Any]:
        context = self._flashscore_prematch_context(fs, cfg)
        features = self._sport_context_features(context, fs, cfg)
        home_n = int(features.get("home_recent_n") or 0)
        away_n = int(features.get("away_recent_n") or 0)
        h2h_n = int(features.get("h2h_n") or 0)
        history_quality = min(1.0, min(home_n, away_n) / 5.0)
        h2h_quality = min(1.0, h2h_n / 5.0)

        home_win = features.get("home_win_pct")
        away_win = features.get("away_win_pct")
        venue_home = features.get("home_venue_win_pct")
        venue_away = features.get("away_venue_win_pct")
        form_gap = abs(float(home_win) - float(away_win)) if home_win is not None and away_win is not None else 0.0
        venue_gap = abs(float(venue_home) - float(venue_away)) if venue_home is not None and venue_away is not None else 0.0
        rest = abs(float(features.get("rest_advantage_days") or 0.0))
        recent_total = features.get("recent_total_avg")
        h2h_total = features.get("h2h_total_avg")
        total_agreement = 0.0
        if recent_total is not None and h2h_total is not None:
            scale = max(1.0, abs(float(recent_total)))
            total_agreement = max(0.0, 1.0 - abs(float(recent_total) - float(h2h_total)) / scale)

        rating = (
            32.0
            + 34.0 * history_quality
            + 8.0 * h2h_quality
            + 16.0 * min(1.0, form_gap)
            + 6.0 * min(1.0, venue_gap)
            + 2.0 * min(2.0, rest)
            + 6.0 * total_agreement
        )
        pass_floor = _float_env(f"GOOL_{cfg.key.upper()}_PREMATCH_FS_BRAIN_PASS", 70.0)
        borderline_floor = _float_env(f"GOOL_{cfg.key.upper()}_PREMATCH_FS_BRAIN_BORDERLINE", 58.0)
        state = "PASS" if rating >= pass_floor else ("BORDERLINE" if rating >= borderline_floor else "WAIT")
        return {
            **dict(fs),
            "prematch_brain": {
                "state": state,
                "score": round(max(0.0, min(100.0, rating)), 1),
                "history_quality": round(history_quality, 3),
                "h2h_quality": round(h2h_quality, 3),
                "form_gap": round(form_gap, 3),
                "venue_gap": round(venue_gap, 3),
                "features": features,
            },
        }

    def _flashscore_prematch_shortlist(
        self,
        fs_upcoming: list[dict[str, Any]],
        cfg: SportConfig,
    ) -> list[dict[str, Any]]:
        scan_max = max(1, min(160, _int_env("GOOL_MULTISPORT_PREMATCH_FS_BRAIN_SCAN_MAX", 64)))
        price_max = max(1, min(scan_max, _int_env("GOOL_MULTISPORT_PREMATCH_PRICE_MAX_PER_SPORT", 32)))
        rows = sorted(fs_upcoming, key=lambda row: float(row.get("start_ts") or 0.0))[:scan_max]
        if not rows:
            return []
        workers = max(2, min(12, _int_env("GOOL_MULTISPORT_PREMATCH_FS_BRAIN_WORKERS", 6)))
        analysed: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._flashscore_prematch_candidate, row, cfg) for row in rows]
            for future in as_completed(futures):
                try:
                    item = future.result(timeout=24)
                except Exception:
                    continue
                if item:
                    analysed.append(item)
        interesting = [
            row for row in analysed
            if str((row.get("prematch_brain") or {}).get("state") or "") in {"PASS", "BORDERLINE"}
        ]
        interesting.sort(
            key=lambda row: (
                float((row.get("prematch_brain") or {}).get("score") or 0.0),
                -float(row.get("start_ts") or 0.0),
            ),
            reverse=True,
        )
        return interesting[:price_max]

    def _flashscore_prematch_context(self, fs: dict[str, Any], cfg: SportConfig) -> dict[str, Any]:
        """Recent form + H2H from Flashscore, analogous to the football collector."""
        event_id = str(fs.get("flashscore_event_id") or "").strip()
        if not event_id:
            return {}
        now = time.monotonic()
        ttl = max(60.0, _float_env("GOOL_MULTISPORT_FS_HISTORY_CACHE_SECONDS", 30 * 60.0))
        cached_at, cached = self._fs_history_cache.get(event_id, (0.0, {}))
        if cached and now - cached_at <= ttl:
            return dict(cached)
        try:
            ctx = dict(self._flashscore.fetch_match_history(
                event_id,
                str(fs.get("home") or ""),
                str(fs.get("away") or ""),
                limit=max(5, _int_env("GOOL_MULTISPORT_HISTORY_MATCHES", 10)),
            ) or {})
        except Exception as exc:
            print(f"GOOL_{cfg.key.upper()}_FS_HISTORY_ERROR event={event_id} {type(exc).__name__}:{exc}", flush=True)
            ctx = {}
        self._fs_history_cache[event_id] = (now, dict(ctx))
        return ctx

    @staticmethod
    def _sport_context_features(context: dict[str, Any], fs: dict[str, Any], cfg: SportConfig) -> dict[str, Any]:
        """Leakage-safe PREMATCH features from Flashscore history only."""
        def rows(key: str) -> list[dict[str, Any]]:
            return [dict(r) for r in (context.get(key) or []) if isinstance(r, dict)]

        home_recent = rows("home_recent")
        away_recent = rows("away_recent")
        home_at_home = rows("home_at_home")
        away_away = rows("away_away")
        h2h = rows("h2h")

        def scored_allowed(items: list[dict[str, Any]], team: str) -> tuple[list[float], list[float], list[float], list[int]]:
            gf: list[float] = []
            ga: list[float] = []
            totals: list[float] = []
            wins: list[int] = []
            target = str(team or "").strip().casefold()
            for row in items:
                try:
                    hs = float(row.get("home_score"))
                    aws = float(row.get("away_score"))
                except (TypeError, ValueError):
                    continue
                home = str(row.get("home") or "").strip().casefold()
                away = str(row.get("away") or "").strip().casefold()
                if target and target in home:
                    gf.append(hs); ga.append(aws); wins.append(int(hs > aws))
                elif target and target in away:
                    gf.append(aws); ga.append(hs); wins.append(int(aws > hs))
                else:
                    # Name matching can fail for abbreviations. Totals are still
                    # useful and do not depend on which side the team occupied.
                    totals.append(hs + aws)
                    continue
                totals.append(hs + aws)
            return gf, ga, totals, wins

        home = str(fs.get("home") or "")
        away = str(fs.get("away") or "")
        hgf, hga, ht, hw = scored_allowed(home_recent, home)
        agf, aga, at, aw = scored_allowed(away_recent, away)
        vhgf, vhga, vht, vhw = scored_allowed(home_at_home, home)
        vagf, vaga, vat, vaw = scored_allowed(away_away, away)

        def avg(values: list[float]) -> float | None:
            return None if not values else sum(values) / len(values)

        def win_pct(values: list[int]) -> float | None:
            return None if not values else sum(values) / len(values)

        h2h_totals: list[float] = []
        for row in h2h:
            try:
                h2h_totals.append(float(row.get("home_score")) + float(row.get("away_score")))
            except (TypeError, ValueError):
                continue

        def latest_ts(items: list[dict[str, Any]]) -> int:
            values = []
            for row in items:
                try:
                    values.append(int(float(row.get("timestamp") or 0)))
                except (TypeError, ValueError):
                    pass
            return max(values) if values else 0

        start_ts = int(float(fs.get("start_ts") or 0))
        home_last = latest_ts(home_recent)
        away_last = latest_ts(away_recent)
        home_rest_days = ((start_ts - home_last) / 86400.0) if start_ts > home_last > 0 else None
        away_rest_days = ((start_ts - away_last) / 86400.0) if start_ts > away_last > 0 else None

        recent_total_values = ht + at
        recent_total = avg(recent_total_values[-20:])
        h2h_total = avg(h2h_totals[-10:])
        venue_total = avg((vht + vat)[-20:])

        return {
            "source": "flashscore_history",
            "sport": cfg.key,
            "home_recent_n": len(home_recent),
            "away_recent_n": len(away_recent),
            "h2h_n": len(h2h),
            "home_gf_avg": avg(hgf),
            "home_ga_avg": avg(hga),
            "away_gf_avg": avg(agf),
            "away_ga_avg": avg(aga),
            "home_win_pct": win_pct(hw),
            "away_win_pct": win_pct(aw),
            "home_venue_win_pct": win_pct(vhw),
            "away_venue_win_pct": win_pct(vaw),
            "recent_total_avg": recent_total,
            "venue_total_avg": venue_total,
            "h2h_total_avg": h2h_total,
            "home_rest_days": None if home_rest_days is None else round(home_rest_days, 2),
            "away_rest_days": None if away_rest_days is None else round(away_rest_days, 2),
            "home_back_to_back": bool(home_rest_days is not None and home_rest_days < 1.5),
            "away_back_to_back": bool(away_rest_days is not None and away_rest_days < 1.5),
            "rest_advantage_days": (
                None
                if home_rest_days is None or away_rest_days is None
                else round(home_rest_days - away_rest_days, 2)
            ),
        }

    @staticmethod
    def _prematch_history_support(
        context: dict[str, Any],
        lane_row: dict[str, Any],
        signal: dict[str, Any],
    ) -> float:
        """Small bounded context score; market evidence remains dominant."""
        rows: list[dict[str, Any]] = []
        for key in ("home_recent", "away_recent", "h2h"):
            rows.extend([r for r in (context.get(key) or []) if isinstance(r, dict)])
        if not rows:
            return 0.0
        # Deduplicate the same fixture appearing in multiple Flashscore sections.
        unique: dict[str, dict[str, Any]] = {}
        for idx, row in enumerate(rows):
            key = str(row.get("event_id") or f"row:{idx}")
            unique.setdefault(key, row)
        rows = list(unique.values())[:24]
        family = str(lane_row.get("market_family") or "")
        try:
            line = float(signal.get("line") if signal.get("line") is not None else lane_row.get("line") or 0.0)
        except (TypeError, ValueError):
            line = 0.0
        support = 0.0
        if family in {"match_total", "home_total", "away_total"} and line > 0:
            values: list[float] = []
            for row in rows:
                try:
                    hs, aws = float(row.get("home_score")), float(row.get("away_score"))
                except (TypeError, ValueError):
                    continue
                if family == "match_total":
                    values.append(hs + aws)
                else:
                    # Venue-neutral approximation: team-total history is weaker
                    # context than match-total history, hence capped below.
                    values.append(max(hs, aws))
            if values:
                avg = sum(values) / len(values)
                direction = str(signal.get("direction") or "over")
                delta = avg - line
                support = delta if direction == "over" else -delta
                scale = 0.8 if family == "match_total" else 0.45
                support *= scale
        elif family in {"moneyline", "handicap"}:
            side = str(signal.get("selection_side") or lane_row.get("selection_side") or "")
            wins = losses = 0
            target = str(lane_row.get("home") if side == "home" else lane_row.get("away") or "").casefold()
            for row in rows:
                home = str(row.get("home") or "").casefold()
                away = str(row.get("away") or "").casefold()
                try:
                    hs, aws = float(row.get("home_score")), float(row.get("away_score"))
                except (TypeError, ValueError):
                    continue
                if target and target in home:
                    wins += int(hs > aws); losses += int(hs < aws)
                elif target and target in away:
                    wins += int(aws > hs); losses += int(aws < hs)
            total = wins + losses
            if total:
                support = ((wins / total) - 0.5) * 4.0
        return max(-3.0, min(3.0, float(support)))

    def _state_index_fallback(self, cfg: SportConfig, *, prematch: bool) -> list[dict[str, Any]]:
        """Recover candidate ids from the last persisted state during index blackouts.

        Only identity is reused. Every selected event is still re-opened through
        GetGameZip, so stale odds are never reused as a betting signal.
        """
        try:
            state = json.loads(self.state_path.read_text("utf-8"))
        except Exception:
            return []
        try:
            captured = datetime.fromisoformat(str(state.get("captured_at") or "").replace("Z", "+00:00"))
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            age = max(0.0, (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return []
        max_age = _float_env(
            "GOOL_MULTISPORT_PREMATCH_STATE_INDEX_CACHE_SECONDS" if prematch else "GOOL_MULTISPORT_STATE_INDEX_CACHE_SECONDS",
            6 * 60 * 60.0 if prematch else 15 * 60.0,
        )
        if age > max_age:
            return []
        sport_state = ((state.get("sports") or {}).get(cfg.key) or {})
        source = sport_state.get("prematch_matches" if prematch else "matches") or []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in source:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "").strip()
            home = str(item.get("home") or "").strip()
            away = str(item.get("away") or "").strip()
            if not event_id or not home or not away or event_id in seen:
                continue
            seen.add(event_id)
            rows.append({"I": event_id, "O1": home, "O2": away, "_state_cache": True})
        return rows

    def _xbet_queries(self, cfg: SportConfig) -> list[str]:
        count = max(50, _int_env("XBET_MULTISPORT_INDEX_COUNT", 1000))
        base = {"sports": cfg.sport_id, "count": count, "lng": "en", "mode": 4}

        # GitHub-verified 1xBet team-sport profile used for Basketball (SI=3)
        # and Ice Hockey (SI=2). Keep this FIRST; football uses its own worker
        # and is intentionally untouched here.
        profiles = [
            {**base, "country": 71, "gr": 70, "getEmpty": "true"},
            {**base, "country": 71, "gr": 70, "partner": 1, "getEmpty": "true"},
            # Existing fallbacks retained for mirror/feed drift.
            {**base, "country": 1, "getEmpty": "true"},
            {**base, "country": 137, "gr": 285, "virtualSports": "true", "noFilterBlockEvent": "true", "getEmpty": "true"},
            {**base, "getEmpty": "true"},
        ]
        if cfg.key == "basketball":
            profiles.extend([
                {**base, "country": 1, "antisports": 188, "partner": 51, "getEmpty": "true"},
                {**base, "country": 153, "mobi": "true", "getEmpty": "true"},
                {**base, "country": 19, "getEmpty": "true"},
            ])
        return list(dict.fromkeys(urllib.parse.urlencode(profile) for profile in profiles))


    def _xbet_index(self, cfg: SportConfig) -> list[dict[str, Any]]:
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        attempts: list[dict[str, Any]] = []
        now = time.monotonic()

        # Exact profile verified in an open-source 1xBet Basketball/Hockey scraper.
        if cfg.key in {"hockey", "basketball"}:
            exact_query = urllib.parse.urlencode({
                "sports": cfg.sport_id,
                "count": 50,
                "lng": "en",
                "gr": 70,
                "mode": 4,
                "country": 71,
                "getEmpty": "true",
            })
            exact_root = "https://1xbet.com/LiveFeed"
            payload = _team_sport_exact_json(
                f"{exact_root}/Get1x2_VZip?{exact_query}",
                timeout=8.0,
            )
            values = payload.get("Value") if isinstance(payload, dict) else None
            usable = [
                row for row in (values or [])
                if isinstance(row, dict) and row.get("I") and row.get("O1") and row.get("O2")
            ] if isinstance(values, list) else []
            attempts.append({
                "root": exact_root,
                "query": "github-team-sport-exact",
                "raw": len(values) if isinstance(values, list) else 0,
                "usable": len(usable),
                "payload": bool(payload),
            })
            if usable:
                rows = [dict(row) for row in usable]
                self._roots[cfg.key] = exact_root
                self._last_index[cfg.key] = (now, rows)
                self._index_diag[cfg.key] = {
                    "ok": True,
                    "root": exact_root,
                    "query": "github-team-sport-exact",
                    "raw": len(values),
                    "usable": len(rows),
                    "cache": False,
                    "source": "github_team_sport_exact",
                    "attempts": attempts[-10:],
                }
                return rows

        # Do not merge every mirror: that multiplied requests and caused the
        # basketball index to be queried only after 1xBet started throttling us.
        for root in dict.fromkeys(roots):
            for query_no, query in enumerate(self._xbet_queries(cfg), 1):
                payload = _sport_http_json(f"{root}/Get1x2_VZip?{query}", timeout=7.0)
                values = payload.get("Value") if isinstance(payload, dict) else None
                raw_count = len(values) if isinstance(values, list) else 0
                usable = [
                    row for row in (values or [])
                    if isinstance(row, dict) and row.get("I") and row.get("O1") and row.get("O2")
                ] if isinstance(values, list) else []
                attempts.append({
                    "root": root, "query": query_no, "raw": raw_count,
                    "usable": len(usable), "payload": bool(payload),
                })
                if not usable:
                    continue
                rows = [dict(row) for row in usable]
                self._roots[cfg.key] = root
                self._last_index[cfg.key] = (now, rows)
                self._index_diag[cfg.key] = {
                    "ok": True,
                    "root": root,
                    "query": query_no,
                    "raw": raw_count,
                    "usable": len(rows),
                    "cache": False,
                    "attempts": attempts[-10:],
                }
                return rows


        # Current frontend fallback: the legacy LiveFeed index may be blocked
        # while main-live-feed/v3 remains healthy.
        if _truthy("GOOL_MULTISPORT_V3_INDEX_FALLBACK", True):
            hosts = _v3_hosts_from_roots(list(dict.fromkeys(roots)))
            count = max(50, _int_env("XBET_MULTISPORT_INDEX_COUNT", 1000))
            fcountry = os.getenv("GOOL_MULTISPORT_V3_FCOUNTRY", "66")
            for host in hosts:
                for gr in (1557, 412):
                    query = [
                        ("cfView", "3"),
                        ("count", str(count)),
                        ("fcountry", str(fcountry)),
                        ("gr", str(gr)),
                        ("grMode", "4"),
                        ("lng", "en"),
                        ("ref", "1"),
                    ]
                    payload = _sport_v3_json(host, "/service-api/main-live-feed/v3/games1x2", query, timeout=8.0)
                    games = payload if isinstance(payload, list) else []
                    rows: list[dict[str, Any]] = []
                    for game in games:
                        if not isinstance(game, dict):
                            continue
                        sport = game.get("sport") or {}
                        try:
                            sid = int(sport.get("id") or 0)
                        except (TypeError, ValueError, AttributeError):
                            sid = 0
                        if sid != cfg.sport_id:
                            continue
                        event_id = str(game.get("id") or "").strip()
                        home = str((game.get("opponent1") or {}).get("fullName") or "").strip()
                        away = str((game.get("opponent2") or {}).get("fullName") or "").strip()
                        if not event_id or not home or not away:
                            continue
                        rows.append({
                            "I": event_id,
                            "O1": home,
                            "O2": away,
                            "LE": str((game.get("liga") or {}).get("name") or ""),
                            "SI": sid,
                            "scores": dict(game.get("scores") or {}),
                            "_v3_index": True,
                        })
                    attempts.append({
                        "root": host + "/service-api/main-live-feed/v3",
                        "query": f"v3-gr-{gr}",
                        "raw": len(games),
                        "usable": len(rows),
                        "payload": bool(payload),
                    })
                    if rows:
                        self._last_index[cfg.key] = (now, [dict(row) for row in rows])
                        self._index_diag[cfg.key] = {
                            "ok": True,
                            "root": host + "/service-api/main-live-feed/v3",
                            "query": f"v3-gr-{gr}",
                            "raw": len(games),
                            "usable": len(rows),
                            "cache": False,
                            "source": "v3_games1x2",
                            "attempts": attempts[-10:],
                        }
                        return rows

        cached_at, cached_rows = self._last_index.get(cfg.key, (0.0, []))
        age = now - cached_at if cached_at else 10**9
        max_age = max(0.0, _float_env("GOOL_MULTISPORT_INDEX_CACHE_SECONDS", 900.0))
        if cached_rows and age <= max_age:
            self._index_diag[cfg.key] = {
                "ok": True,
                "root": self._roots[cfg.key],
                "raw": len(cached_rows),
                "usable": len(cached_rows),
                "cache": True,
                "cache_age_seconds": round(age, 1),
                "attempts": attempts[-10:],
            }
            return [dict(row) for row in cached_rows]

        state_rows = self._state_index_fallback(cfg, prematch=False)
        if state_rows:
            self._index_diag[cfg.key] = {
                "ok": True,
                "root": self._roots[cfg.key],
                "raw": len(state_rows),
                "usable": len(state_rows),
                "cache": True,
                "cache_source": "persisted_state_identity_only",
                "attempts": attempts[-10:],
            }
            return state_rows

        self._index_diag[cfg.key] = {
            "ok": False,
            "root": None,
            "raw": 0,
            "usable": 0,
            "attempts": attempts[-12:],
        }
        return []

    def _xbet_prematch_queries(self, cfg: SportConfig) -> list[str]:
        count = max(100, _int_env("GOOL_MULTISPORT_PREMATCH_INDEX_COUNT", 1000))
        base = {"sports": cfg.sport_id, "count": count, "lng": "en", "cfview": 2, "mode": 4}
        return [
            # GitHub-verified 1xBet Basketball/Hockey prematch discovery.
            urllib.parse.urlencode({**base, "country": 71, "gr": 70, "tf": 2200000, "tz": 5, "getEmpty": "true"}),
            urllib.parse.urlencode({**base, "country": 71, "gr": 70, "getEmpty": "true"}),
            # Existing fallbacks.
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
                payload = _sport_http_json(f"{root}/Get1x2_VZip?{query}", timeout=8.0)
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


        # Current frontend LineFeed fallback. New gateways use Get1x2_Zip with
        # partner/gr/mode rather than the older Get1x2_VZip profile.
        if _truthy("GOOL_MULTISPORT_CURRENT_LINEFEED_FALLBACK", True):
            hosts = _v3_hosts_from_roots(list(dict.fromkeys(roots)))
            count = max(100, _int_env("GOOL_MULTISPORT_PREMATCH_INDEX_COUNT", 1000))
            for host in hosts:
                for gr in (412, 1557):
                    query = [
                        ("sports", str(cfg.sport_id)),
                        ("lng", "en"),
                        ("partner", os.getenv("GOOL_MULTISPORT_LINEFEED_PARTNER", "159")),
                        ("getEmpty", "true"),
                        ("gr", str(gr)),
                        ("mode", "3"),
                        ("count", str(count)),
                    ]
                    payload = _sport_v3_json(
                        host,
                        "/service-api/LineFeed/Get1x2_Zip",
                        query,
                        timeout=8.0,
                    )
                    values = payload.get("Value") if isinstance(payload, dict) else payload
                    usable = [
                        row for row in (values or [])
                        if isinstance(row, dict) and row.get("I")
                        and (row.get("O1") or row.get("O1E"))
                        and (row.get("O2") or row.get("O2E"))
                    ] if isinstance(values, list) else []
                    attempts.append({
                        "root": host + "/service-api/LineFeed",
                        "query": f"current-gr-{gr}",
                        "raw": len(values) if isinstance(values, list) else 0,
                        "usable": len(usable),
                        "payload": bool(payload),
                    })
                    if not usable:
                        continue
                    rows = []
                    for row in usable:
                        item = dict(row)
                        item["O1"] = str(item.get("O1E") or item.get("O1") or "")
                        item["O2"] = str(item.get("O2E") or item.get("O2") or "")
                        item["_current_linefeed"] = True
                        rows.append(item)
                    self._last_prematch_index[cfg.key] = (now, [dict(row) for row in rows])
                    self._prematch_index_diag[cfg.key] = {
                        "ok": True,
                        "root": host + "/service-api/LineFeed",
                        "raw": len(values),
                        "usable": len(rows),
                        "cache": False,
                        "source": "current_linefeed_get1x2_zip",
                        "attempts": attempts[-10:],
                    }
                    return rows

        cached_at, cached_rows = self._last_prematch_index.get(cfg.key, (0.0, []))
        age = now - cached_at if cached_at else 10**9
        max_age = max(0.0, _float_env("GOOL_MULTISPORT_PREMATCH_INDEX_CACHE_SECONDS", 21600.0))
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

        state_rows = self._state_index_fallback(cfg, prematch=True)
        if state_rows:
            self._prematch_index_diag[cfg.key] = {
                "ok": True,
                "root": self._prematch_roots[cfg.key],
                "raw": len(state_rows),
                "usable": len(state_rows),
                "cache": True,
                "cache_source": "persisted_state_identity_only",
                "attempts": attempts[-10:],
            }
            return state_rows

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
            "countevents": 500,
            "country": 71,
            "fcountry": 71,
            "gr": 70,
            "grMode": 4,
            "marketType": 1,
            "isNewBuilder": "true",
        }
        roots = [self._prematch_roots[cfg.key], *[root for root in PREMATCH_ROOTS if root != self._prematch_roots[cfg.key]]]
        unique_roots = list(dict.fromkeys(roots))
        for root in unique_roots:
            payload = _sport_http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=8.0)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._prematch_roots[cfg.key] = root
                return value

        if _truthy("GOOL_MULTISPORT_CURRENT_LINEFEED_FALLBACK", True):
            query = [
                ("id", str(event_id)),
                ("lng", "en"),
                ("isSubGames", "true"),
                ("GroupEvents", "true"),
                ("countevents", "2000"),
                ("grMode", "4"),
                ("topGroups", ""),
                ("country", os.getenv("GOOL_MULTISPORT_V3_FCOUNTRY", "66")),
                ("marketType", "1"),
                ("isNewBuilder", "true"),
            ]
            for host in _v3_hosts_from_roots(unique_roots):
                payload = _sport_v3_json(
                    host,
                    "/service-api/LineFeed/GetGameZip",
                    query,
                    timeout=max(1.0, _float_env("GOOL_MULTISPORT_FALLBACK_GAME_TIMEOUT", 8.0)),
                )
                value = payload.get("Value") if isinstance(payload, dict) else None
                if isinstance(value, dict):
                    return value
        return None

    def _game(self, event_id: str, cfg: SportConfig) -> dict[str, Any] | None:
        params = {
            "id": event_id,
            "lng": "en",
            "cfview": 0,
            "isSubGames": "true",
            "GroupEvents": "true",
            "allEventsGroupSubGames": "true",
            # GitHub-verified Basketball/Hockey GetGameZip profile.
            "countevents": 500,
            "country": 71,
            "fcountry": 71,
            "marketType": 1,
            "gr": 70,
            "isNewBuilder": "true",
            "grMode": 2,
        }
        roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]
        unique_roots = list(dict.fromkeys(roots))

        # github-team-sport-game-exact
        if cfg.key in {"hockey", "basketball"}:
            exact_params = {
                "id": event_id,
                "lng": "en",
                "isSubGames": "true",
                "GroupEvents": "true",
                "allEventsGroupSubGames": "true",
                "countevents": 500,
                "country": 71,
                "fcountry": 71,
                "marketType": 1,
                "gr": 70,
                "isNewBuilder": "true",
            }
            payload = _team_sport_exact_json(
                "https://1xbet.com/LiveFeed/GetGameZip?" + urllib.parse.urlencode(exact_params),
                timeout=max(1.0, _float_env("GOOL_MULTISPORT_EXACT_GAME_TIMEOUT", 10.0)),
            )
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._roots[cfg.key] = "https://1xbet.com/LiveFeed"
                return value
        game_root_attempts = max(1, min(len(unique_roots), _int_env("GOOL_MULTISPORT_GAME_ROOT_ATTEMPTS", len(unique_roots))))
        game_timeout = max(1.0, _float_env("GOOL_MULTISPORT_GAME_HTTP_TIMEOUT", 7.0))
        for root in unique_roots[:game_root_attempts]:
            payload = _sport_http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=game_timeout)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._roots[cfg.key] = root
                return value

        if _truthy("GOOL_MULTISPORT_V3_GAME_FALLBACK", True):
            fcountry = os.getenv("GOOL_MULTISPORT_V3_FCOUNTRY", "66")
            for host in _v3_hosts_from_roots(unique_roots):
                for gr in (1557, 412):
                    query = [
                        ("cfView", "3"),
                        ("countEvents", "250"),
                        ("fcountry", str(fcountry)),
                        ("gameId", str(event_id)),
                        ("gr", str(gr)),
                        ("grMode", "4"),
                        ("lng", "en"),
                        ("marketType", "1"),
                        ("ref", "1"),
                    ]
                    payload = _sport_v3_json(
                        host,
                        "/service-api/main-live-feed/v3/gameEvents",
                        query,
                        timeout=max(1.0, _float_env("GOOL_MULTISPORT_V3_GAME_TIMEOUT", 8.0)),
                    )
                    if not isinstance(payload, dict):
                        continue
                    converted = _v3_to_legacy_market_game(payload, event_id)
                    if converted.get("AE") or converted.get("SG"):
                        converted["_v3_gr"] = gr
                        return converted
        return None

    def _subgame_game(self, event_id: str, cfg: SportConfig, *, prematch: bool) -> dict[str, Any]:
        if prematch:
            params = {
                "id": event_id, "lng": "en", "cfview": 0, "isSubGames": "true",
                "GroupEvents": "true", "allEventsGroupSubGames": "true",
                "countevents": 500, "country": 71, "fcountry": 71,
                "gr": 70, "grMode": 4, "marketType": 1, "isNewBuilder": "true",
            }
            roots = [self._prematch_roots[cfg.key], *[root for root in PREMATCH_ROOTS if root != self._prematch_roots[cfg.key]]]
        else:
            params = {
                "id": event_id, "lng": "en", "cfview": 0, "isSubGames": "true",
                "GroupEvents": "true", "allEventsGroupSubGames": "true",
                "countevents": 500, "country": 71, "fcountry": 71,
                "marketType": 1, "gr": 70, "isNewBuilder": "true", "grMode": 2,
            }
            roots = [self._roots[cfg.key], *[root for root in market.ROOTS if root != self._roots[cfg.key]]]

        # The parent event/index has already selected a healthy mirror. Sub-game
        # hydration must not retry every 1xBet mirror for every quarter/period.
        attempts = max(1, min(2, _int_env("GOOL_MULTISPORT_SUBGAME_ROOT_ATTEMPTS", 1)))
        timeout = max(1.0, _float_env("GOOL_MULTISPORT_SUBGAME_HTTP_TIMEOUT", 3.5))
        live_roots = list(dict.fromkeys(roots))[:attempts]
        for root in live_roots:
            payload = _sport_http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=timeout)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                if prematch:
                    self._prematch_roots[cfg.key] = root
                else:
                    self._roots[cfg.key] = root
                return value

        # Basketball LIVE only: if legacy GetGameZip failed for this exact
        # quarter subgame, ask the current frontend v3 endpoint for the same id.
        # We deliberately do not use this for parent/full-match scope.
        if not prematch and cfg.key == "basketball" and _truthy("GOOL_BASKETBALL_V3_SUBGAME_FALLBACK", True):
            hosts = []
            for root in live_roots:
                parsed = urllib.parse.urlsplit(root)
                if parsed.scheme and parsed.netloc:
                    hosts.append(f"{parsed.scheme}://{parsed.netloc}")
            for host in dict.fromkeys(hosts):
                for gr in (1557, 412):
                    query = urllib.parse.urlencode([
                        ("cfView", "3"),
                        ("countEvents", "250"),
                        ("fcountry", os.getenv("GOOL_BASKETBALL_V3_FCOUNTRY", "66")),
                        ("gameId", str(event_id)),
                        ("gr", str(gr)),
                        ("grMode", "4"),
                        ("lng", "en"),
                        ("marketType", "1"),
                        ("ref", "1"),
                    ])
                    ordered = [
                        ("cfView", "3"),
                        ("countEvents", "250"),
                        ("fcountry", os.getenv("GOOL_BASKETBALL_V3_FCOUNTRY", "66")),
                        ("gameId", str(event_id)),
                        ("gr", str(gr)),
                        ("grMode", "4"),
                        ("lng", "en"),
                        ("marketType", "1"),
                        ("ref", "1"),
                    ]
                    payload = _sport_v3_json(
                        host,
                        "/service-api/main-live-feed/v3/gameEvents",
                        ordered,
                        timeout=max(timeout, 5.0),
                    )
                    if not isinstance(payload, dict):
                        continue
                    converted = _v3_to_legacy_market_game(payload, event_id)
                    if converted.get("AE"):
                        converted["_v3_gr"] = gr
                        return converted
        return {}

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
        game = self._subgame_game(sub_id, cfg, prematch=prematch)
        if isinstance(game, dict) and game:
            self._subgame_cache[key] = (now, dict(game))
            return game
        if cached and now - cached_at <= stale_ttl:
            return dict(cached)
        return {}

    def _hockey_segment_stats(
        self,
        game: dict[str, Any],
        cfg: SportConfig,
        *,
        current_period: str,
    ) -> dict[str, Any]:
        """Fetch actual current-period hockey stats from 1xBet stat subgames.

        These are scoreboard values from SC/FS (shots, penalties, PP goals), not
        prices from those betting markets. Missing stat subgames simply produce
        an empty dict and the LIVE brain falls back to score/time pace.
        """
        if cfg.key != "hockey":
            return {}
        wanted_scopes = live_scopes_from_period(cfg.key, current_period)
        current_scope = next((scope for scope in wanted_scopes if scope.startswith("PERIOD_")), "")
        if not current_scope:
            return {}

        aliases = {
            "shots_on_goal": ("shots on goal", "shots on target"),
            "penalties_2m": ("2-minute penalties", "2 minute penalties"),
            "powerplay_goals": ("powerplay goals", "power play goals"),
        }
        wanted: list[tuple[str, str]] = []
        seen_metrics: set[str] = set()
        for sg in game.get("SG") or []:
            if not isinstance(sg, dict):
                continue
            if scope_from_subgame(sg, cfg.key) != current_scope:
                continue
            tg = str(sg.get("TG") or "").strip().casefold()
            if not tg:
                continue
            metric = next(
                (name for name, names in aliases.items() if any(alias in tg for alias in names)),
                "",
            )
            sub_id = str(sg.get("I") or "").strip()
            if metric and sub_id and metric not in seen_metrics:
                seen_metrics.add(metric)
                wanted.append((metric, sub_id))

        maximum = max(0, min(3, _int_env("GOOL_HOCKEY_LIVE_STAT_SUBGAMES_MAX", 3)))
        wanted = wanted[:maximum]
        if not wanted:
            return {}

        values: dict[str, Any] = {"scope": current_scope, "source": "1xbet_stat_subgame"}
        workers = max(1, min(3, len(wanted)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._cached_subgame, sub_id, cfg, prematch=False): metric
                for metric, sub_id in wanted
            }
            for future in as_completed(futures):
                metric = futures[future]
                try:
                    sub = future.result(timeout=10)
                except Exception:
                    sub = {}
                score = _score(sub) if sub else None
                if score is not None:
                    values[metric] = [int(score[0]), int(score[1])]
        return values

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
        wanted_scopes: set[str] | None = None,
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
            if wanted_scopes is not None and scope not in wanted_scopes:
                continue
            seen.add(scope)
            if sg.get("AE"):
                scope_decoded = decode_core_markets(sg, cfg.key, scope=scope)
                decoded[scope] = scope_decoded
                unknown_catalog[scope] = raw_catalog(scope_decoded)
                fetch_status[scope] = "embedded_v3"
                continue
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
        prematch_context = self._flashscore_prematch_context(fs, cfg)
        sport_context = self._sport_context_features(prematch_context, fs, cfg)
        lanes = prematch_market_lanes(decoded, cfg.key)
        for lane in lanes:
            lane["lane_key"] = lane_key(lane)
            if lane.get("choice_key"):
                lane["metric"] = float(lane.get("probability") or 0.0) * 100.0
            else:
                lane["metric"] = _metric(lane, (0, 0), cfg)
                # Do not prebuild a fake OVER label. The final label must be
                # created only after Brain has chosen over/under.
                lane.pop("selection", None)
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
            "home_team_id": str(fs.get("home_team_id") or ""),
            "away_team_id": str(fs.get("away_team_id") or ""),
            "home_team_slug": str(fs.get("home_team_slug") or ""),
            "away_team_slug": str(fs.get("away_team_slug") or ""),
            "home_logo_file": str(fs.get("home_logo_file") or ""),
            "away_logo_file": str(fs.get("away_logo_file") or ""),
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
            "prematch_context": prematch_context,
            "sport_context": sport_context,
            "flashscore_match_score": round(float(match_score), 4),
        }, None

    def _snapshot(self, event: dict[str, Any], fs: dict[str, Any], reversed_order: bool, match_score: float, cfg: SportConfig) -> tuple[dict[str, Any] | None, str | None]:
        event_id = str(event.get("I") or "").strip()
        game = self._game(event_id, cfg) or event
        if not _event_allowed(game):
            return None, "market_decode"

        fs_score_raw = list(fs.get("score") or [0, 0])
        fs_score = (int(fs_score_raw[0]), int(fs_score_raw[1]))
        candidates = _score_candidates(game, cfg) or _score_candidates(event, cfg)
        if not candidates:
            return None, "market_decode"
        canonical_candidates = [
            (score[1], score[0]) if reversed_order else score
            for score in candidates
        ]
        canonical = min(
            canonical_candidates,
            key=lambda score: abs(score[0] - fs_score[0]) + abs(score[1] - fs_score[1]),
        )
        exact_score_sync = canonical == fs_score
        if not _score_sync_allowed(cfg, fs_score, canonical, match_score):
            return None, "score_mismatch"

        current_period = _period(game)
        fs_scope = _infer_flashscore_scope(fs, cfg)
        fs_has_scope_evidence = bool(
            str(fs.get("status_code") or "").strip()
            or [p for p in (fs.get("score_parts") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        )
        if not fs_has_scope_evidence:
            fs_scope = next(iter(live_scopes_from_period(cfg.key, current_period)), fs_scope)
        wanted_live_scopes = set(live_scopes_from_period(cfg.key, current_period))
        if fs_scope:
            wanted_live_scopes.add(fs_scope)
        decoded, market_meta = self._market_tree(
            game,
            cfg,
            prematch=False,
            wanted_scopes=wanted_live_scopes,
        )
        raw_count = sum(len(item.get("raw") or []) for item in decoded.values())
        if raw_count <= 0:
            return None, "market_decode"
        xbet_scoped_scores = period_scores(game, cfg.key)
        fs_scoped_scores = _flashscore_scoped_scores(fs, cfg)
        scoped_scores = {**xbet_scoped_scores, **fs_scoped_scores}
        live_game_stats = self._flashscore_live_stats(fs, cfg, current_period=fs_scope or current_period)
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
            "home_team_id": str(fs.get("home_team_id") or ""),
            "away_team_id": str(fs.get("away_team_id") or ""),
            "home_team_slug": str(fs.get("home_team_slug") or ""),
            "away_team_slug": str(fs.get("away_team_slug") or ""),
            "home_logo_file": str(fs.get("home_logo_file") or ""),
            "away_logo_file": str(fs.get("away_logo_file") or ""),
            "score": [*fs_score],
            "score_parts": [list(part) for part in (fs.get("score_parts") or []) if isinstance(part, (list, tuple)) and len(part) >= 2],
            "scoped_scores": {scope: [score[0], score[1]] for scope, score in scoped_scores.items()},
            "scoped_score_source": "flashscore" if fs_scoped_scores else "1xbet_fallback",
            "period": _flashscore_period_label(fs_scope, str(fs.get("status_code") or "")) if fs_scope else current_period,
            "clock_seconds": _segment_clock_seconds(
                game,
                cfg,
                period=current_period,
                league=str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            ),
            "match_clock_seconds": _clock_seconds(game),
            "xbet_score": [int(canonical[0]), int(canonical[1])],
            "score_sync_mode": "exact" if exact_score_sync else "bounded_provider_lag",
            "score_sync_delta": [
                int(fs_score[0] - canonical[0]),
                int(fs_score[1] - canonical[1]),
            ],
            "live_game_stats": live_game_stats,
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
            if row.get("sport") != cfg.key:
                continue
            stored_result = str(row.get("result") or "pending")
            displayed_direction = _displayed_total_direction(row)
            technical_direction = str(row.get("direction") or "").casefold()
            repair_direction_mismatch = bool(
                stored_result in _FINAL_RESULTS
                and displayed_direction in {"over", "under"}
                and technical_direction in {"over", "under"}
                and displayed_direction != technical_direction
            )
            if stored_result in _FINAL_RESULTS and not repair_direction_mismatch:
                continue
            state = states.get(str(row.get("flashscore_event_id") or ""))
            if not state:
                continue
            scope = str(row.get("scope") or SCOPE_FULL)
            if scope == SCOPE_FULL:
                if str(state.get("coarse_status") or "") != "3":
                    continue
            elif not multisport_scope_is_complete(state, cfg.key, scope):
                continue
            # Legacy pending rows created before emblem metadata was journaled
            # can still render proper result cards: refresh identity assets from
            # the authoritative finished Flashscore row before settlement.
            for key in (
                "home_team_id",
                "away_team_id",
                "home_team_slug",
                "away_team_slug",
                "home_logo_file",
                "away_logo_file",
            ):
                if not str(row.get(key) or "").strip() and str(state.get(key) or "").strip():
                    row[key] = str(state.get(key) or "").strip()
            full_score = list(state.get("score") or [0, 0])
            if scope == SCOPE_FULL:
                # FULL_MATCH uses the authoritative final Flashscore score.
                # In basketball/hockey this includes overtime when the selected
                # 1xBet market itself is the full-match/2-way market.
                score = (int(full_score[0]), int(full_score[1]))
            else:
                # Segment bets are settled on the ENTIRE final period/quarter,
                # not only on scoring after the signal was sent. This matters
                # for e.g. P3 U1.5 when one goal already existed at entry time.
                final_scoped = _flashscore_scoped_scores(state, cfg)
                score = final_scoped.get(scope)
                if score is None:
                    score = self._scope_scores.get(f"{cfg.key}:{row.get('event_id')}", {}).get(scope)
                if score is None:
                    continue
            if repair_direction_mismatch and displayed_direction:
                # The user-facing signal is the contract we must settle.
                # Repair legacy rows where the displayed ТБ/ТМ disagreed with
                # the technical direction saved before this invariant existed.
                row["direction"] = displayed_direction
                row["settlement_corrected_at"] = now
                row["settlement_correction"] = "displayed_selection_direction_mismatch"
            result = settle_multisport_pick(row, int(score[0]), int(score[1]))
            profit = 0.0 if result == "void" else (float(row.get("odd") or 0.0) - 1.0 if result == "won" else -1.0)
            row.update({
                "result": result,
                "profit_units": round(profit, 4),
                "settled_at": now,
                "settled_score": [int(score[0]), int(score[1])],
                "settled_match_score": [int(full_score[0]), int(full_score[1])],
            })
            if (
                _mode() == "active"
                and _truthy("XBET_MULTISPORT_CARDS_ENABLED", True)
                and not repair_direction_mismatch
                and not row.get("result_card_sent_at")
            ):
                try:
                    png = (
                        render_hockey_result_card(row, cfg)
                        if cfg.key == "hockey"
                        else render_basketball_result_card(row, cfg)
                    )
                    sent = telegram.broadcast_photo(png, caption="")
                    if sent:
                        row["result_card_sent_at"] = now
                        row["result_card_sent"] = True
                        print(
                            f"GOOL_{cfg.key.upper()}_RESULT_CARD_SENT "
                            f"match={row.get('home')}--{row.get('away')} result={result}",
                            flush=True,
                        )
                    else:
                        print(
                            f"GOOL_{cfg.key.upper()}_RESULT_CARD_SEND_FAILED "
                            f"match={row.get('home')}--{row.get('away')} result={result}",
                            flush=True,
                        )
                except Exception as exc:
                    print(
                        f"GOOL_{cfg.key.upper()}_RESULT_CARD_ERROR "
                        f"{type(exc).__name__}:{exc}",
                        flush=True,
                    )
            if repair_direction_mismatch:
                print(
                    f"GOOL_{cfg.key.upper()}_SETTLEMENT_CORRECTED "
                    f"match={row.get('home')}--{row.get('away')} selection={row.get('selection')} "
                    f"old_direction={technical_direction} direction={row.get('direction')} result={result}",
                    flush=True,
                )
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
        flashscore_event_id: str = "",
    ) -> bool:
        wanted_phase = str(phase or "LIVE").upper()
        wanted_scope = str(scope or SCOPE_FULL)
        wanted_family = str(market_family or "match_total")
        for row in load_journal(self.journal_path):
            row_phase = str(row.get("phase") or ("PREMATCH" if row.get("origin") == "multisport_prematch" else "LIVE")).upper()
            row_scope = str(row.get("scope") or SCOPE_FULL)
            row_family = str(row.get("market_family") or "match_total")
            wanted_identity = str(flashscore_event_id or event_id or "")
            row_identity = str(row.get("flashscore_event_id") or row.get("event_id") or "")
            if (
                str(row.get("sport") or "") == sport
                and row_identity == wanted_identity
                and row_phase == wanted_phase
            ):
                # PREMATCH contract: one match = one pick. Once any PREMATCH
                # market is journaled for a Flashscore match, later scans must
                # never emit another family/scope for that same match.
                if wanted_phase == "PREMATCH":
                    return True
                if row_scope == wanted_scope and row_family == wanted_family:
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
        if str(signal.get("brain_mode") or "") == "segment_stats":
            return (
                f"{cfg.icon} <b>GOOL MULTI · LIVE SEGMENT BRAIN · {cfg.title}</b>\n"
                f"<b>{row.get('home','?')} — {row.get('away','?')}</b> · {int(score[0])}:{int(score[1])}\n"
                f"🏆 {row.get('league') or 'LIVE'}\n"
                f"⏱ {self._format_clock(row)} · ✅ Flashscore score sync\n"
                f"{arrow} <b>{market_label} @ {float(signal.get('odd') or 0):.2f}</b>\n"
                f"🧠 прогноз сегмента <b>{float(signal.get('projected_total') or 0):.2f}</b> · "
                f"stat edge {float(signal.get('stat_edge') or 0):+.2f}\n"
                f"⚡ темп {float(signal.get('recent_rate_per_min') or 0):.2f}/мин · "
                f"рынок {'✅ подтверждает' if signal.get('market_confirmed') else '➖ нейтрален'}"
            )
        return (
            f"{cfg.icon} <b>GOOL MULTI · LIVE · {cfg.title}</b>\n"
            f"<b>{row.get('home','?')} — {row.get('away','?')}</b> · {int(score[0])}:{int(score[1])}\n"
            f"🏆 {row.get('league') or 'LIVE'}\n"
            f"⏱ {self._format_clock(row)} · ✅ Flashscore\n"
            f"{arrow} <b>{market_label} @ {float(signal.get('odd') or 0):.2f}</b>"
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
                # Card already contains match, market, odd and diagnostics.
                # Do not duplicate the same signal as a Telegram caption.
                sent = telegram.broadcast_photo(png, caption="")
                if sent:
                    print(
                        f"GOOL_{cfg.key.upper()}_CARD_SENT phase={'PREMATCH' if prematch else 'LIVE'} "
                        f"match={row.get('home')}--{row.get('away')}",
                        flush=True,
                    )
                    return int(sent)
                print(
                    f"GOOL_{cfg.key.upper()}_CARD_SEND_FAILED phase={'PREMATCH' if prematch else 'LIVE'} "
                    f"match={row.get('home')}--{row.get('away')}",
                    flush=True,
                )
            except Exception as exc:
                print(f"GOOL_{cfg.key.upper()}_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        if _truthy("GOOL_MULTISPORT_TEXT_FALLBACK_ENABLED", False):
            return int(telegram.broadcast(message) or 0)
        return 0

    def _record_signal(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> tuple[bool, int]:
        event_id = str(row.get("event_id") or "")
        phase = str(row.get("phase") or "LIVE").upper()
        scope = str(row.get("scope") or SCOPE_FULL)
        family = str(row.get("market_family") or "match_total")
        if self._already_seen(
            cfg.key,
            event_id,
            phase,
            scope,
            family,
            str(row.get("flashscore_event_id") or ""),
        ):
            return False, 0
        mode = _mode()
        direction = str(signal.get("direction") or "over")
        if family in {"match_total", "home_total", "away_total"}:
            # Canonical invariant: the visible ТБ/ТМ or ИТБ/ИТМ label and the
            # technical direction used by settlement can never disagree.
            pick_label = selection_label(
                row,
                direction,
                float(signal.get("line") or row.get("line") or 0.0),
            )
        else:
            pick_label = str(
                signal.get("selection")
                or row.get("selection")
                or selection_label(row, direction, float(signal.get("line") or row.get("line") or 0.0))
            )
        row_for_delivery = {**row, "selection": pick_label, "scope": scope, "market_family": family}
        sent = self._deliver(row_for_delivery, signal, cfg) if mode == "active" else 0
        entry = {
            "entry_id": (
                f"{cfg.key}:prematch:{row.get('flashscore_event_id') or event_id}"
                if phase == "PREMATCH"
                else f"{cfg.key}:{phase.lower()}:{event_id}:{scope}:{family}"
            ),
            "journal_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "origin": str(row.get("origin") or ("multisport_prematch" if phase == "PREMATCH" else "multisport_live")),
            "signal_type": (
                "live_segment_stats" if phase == "LIVE" and str(signal.get("brain_mode") or "") == "segment_stats"
                else f"{phase.lower()}_{family}_movement"
            ),
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
            "home_team_id": str(row.get("home_team_id") or ""),
            "away_team_id": str(row.get("away_team_id") or ""),
            "home_team_slug": str(row.get("home_team_slug") or ""),
            "away_team_slug": str(row.get("away_team_slug") or ""),
            "home_logo_file": str(row.get("home_logo_file") or ""),
            "away_logo_file": str(row.get("away_logo_file") or ""),
            "score": list(row.get("score") or [0, 0]) if phase == "LIVE" else None,
            "match_score": list(row.get("match_score") or row.get("score") or [0, 0]) if phase == "LIVE" else None,
            "score_parts": list(row.get("score_parts") or []) if phase == "LIVE" else None,
            "period": row.get("period") if phase == "LIVE" else None,
            "start_ts": float(row.get("start_ts") or 0.0),
            "scheduled_start_ts": float(row.get("start_ts") or 0.0),
            "scheduled_start": row.get("scheduled_start"),
            "clock_seconds": row.get("clock_seconds"),
            "direction": direction,
            "selection_side": str(signal.get("selection_side") or row.get("selection_side") or ""),
            "moneyline_kind": str(row.get("moneyline_kind") or ""),
            "line": float(signal.get("line") or 0.0),
            "odd": float(signal.get("odd") or 0.0),
            "opening_line": float(((signal.get("start") or {}).get("line") or signal.get("line") or 0.0)) if phase == "PREMATCH" else None,
            "opening_odd": (
                float(
                    ((signal.get("start") or {}).get("odd") if row.get("choice_key") else (signal.get("start") or {}).get(direction))
                    or signal.get("odd")
                    or 0.0
                )
                if phase == "PREMATCH" else None
            ),
            "fair_probability": float(signal.get("fair_probability") or 0.0),
            "metric_delta": float(signal.get("metric_delta") or 0.0),
            "probability_delta_pp": float(signal.get("probability_delta_pp") or 0.0),
            "line_delta": float(signal.get("line_delta") or 0.0),
            "moves": int(signal.get("moves") or 0),
            "strength": float(signal.get("strength") or 0.0),
            "extreme": bool(signal.get("extreme")),
            "brain_mode": str(signal.get("brain_mode") or ""),
            "projected_total": signal.get("projected_total"),
            "stat_edge": signal.get("stat_edge"),
            "current_segment_total": signal.get("current_segment_total"),
            "elapsed_seconds": signal.get("elapsed_seconds"),
            "remaining_seconds": signal.get("remaining_seconds"),
            "overall_rate_per_min": signal.get("overall_rate_per_min"),
            "recent_rate_per_min": signal.get("recent_rate_per_min"),
            "market_confirmed": signal.get("market_confirmed"),
            "live_game_stats": row.get("live_game_stats") or {},
            "hockey_pressure": signal.get("hockey_pressure") or {},
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

    def _prematch_batch(
        self,
        cfg: SportConfig,
        mapped: list[tuple[dict[str, Any], dict[str, Any], bool, float]],
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any], bool, float]], int]:
        total_cap = max(1, _int_env("GOOL_MULTISPORT_PREMATCH_MAX_MAPPED_PER_SPORT", 160))
        rows = list(mapped[:total_cap])
        if not rows:
            return [], 0
        rows.sort(key=lambda item: float((item[1] or {}).get("start_ts") or 0.0))
        batch_size = max(1, min(total_cap, _int_env("GOOL_MULTISPORT_PREMATCH_BATCH_SIZE", 48)))
        if len(rows) <= batch_size:
            self._prematch_cursor[cfg.key] = 0
            return rows, len(rows)

        start = self._prematch_cursor[cfg.key] % len(rows)
        end = start + batch_size
        if end <= len(rows):
            batch = rows[start:end]
        else:
            batch = [*rows[start:], *rows[: end - len(rows)]]
        self._prematch_cursor[cfg.key] = end % len(rows)
        return batch, len(rows)

    def _scan_prematch(
        self,
        cfg: SportConfig,
        fs_today: list[dict[str, Any]],
        xbet_prematch_prefetched: list[dict[str, Any]] | None = None,
        fs_price_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        price_targets = fs_upcoming if fs_price_candidates is None else [dict(row) for row in fs_price_candidates]
        xbet_prematch = (
            [dict(row) for row in xbet_prematch_prefetched]
            if xbet_prematch_prefetched is not None
            else (self._xbet_prematch_index(cfg) if price_targets else [])
        )
        # Flashscore history/Brain selects the fixtures first. 1xBet is only
        # asked to price that shortlist, matching the football V4 architecture.
        mapped_all = map_xbet_to_flashscore(xbet_prematch, price_targets)
        mapped, mapped_total = self._prematch_batch(cfg, mapped_all)
        decoded = failed = detected = delivered = policy_blocked = 0
        latest: list[dict[str, Any]] = []
        recent_families = [
            str(item.get("market_family") or "")
            for item in load_journal(self.journal_path)
            if str(item.get("sport") or "") == cfg.key
            and str(item.get("phase") or "").upper() == "PREMATCH"
            and str(item.get("market_family") or "")
        ][-8:]
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
                candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
                for lane in row.get("market_lanes") or []:
                    allowed, policy_reason = lane_phase_policy(cfg.key, "PREMATCH", lane, row.get("period"))
                    if not allowed:
                        policy_blocked += 1
                        continue
                    lane_row = self._lane_row(row, {**lane, "phase_policy": policy_reason})
                    history = self._append_prematch_history(lane_row, cfg)
                    signal = (
                        detect_prematch_choice(history, cfg, now=float(lane_row["ts"]))
                        if lane_row.get("choice_key")
                        else detect_prematch_steam(history, cfg, now=float(lane_row["ts"]))
                    )
                    if signal is None:
                        continue
                    history_support = self._prematch_history_support(
                        dict(row.get("prematch_context") or {}),
                        lane_row,
                        signal,
                    )
                    sport_context = dict(row.get("sport_context") or {})
                    family = str(lane_row.get("market_family") or "")
                    direction = str(signal.get("direction") or "")
                    if family == "match_total":
                        try:
                            line = float(signal.get("line") or lane_row.get("line") or 0.0)
                            recent_avg = sport_context.get("recent_total_avg")
                            h2h_avg = sport_context.get("h2h_total_avg")
                            context_values = [float(v) for v in (recent_avg, h2h_avg) if v is not None]
                            if line > 0 and context_values:
                                context_avg = sum(context_values) / len(context_values)
                                context_edge = context_avg - line
                                if direction == "under":
                                    context_edge = -context_edge
                                history_support += max(-1.5, min(1.5, context_edge * (0.10 if cfg.key == "basketball" else 0.35)))
                        except (TypeError, ValueError):
                            pass
                    elif family in {"moneyline", "handicap"}:
                        side = str(lane_row.get("selection_side") or "")
                        form = sport_context.get("home_win_pct" if side == "home" else "away_win_pct")
                        if form is not None:
                            history_support += max(-1.0, min(1.0, (float(form) - 0.5) * 2.0))
                    history_support = max(-3.0, min(3.0, history_support))
                    signal = {
                        **signal,
                        "scope": lane_row.get("scope"),
                        "market_family": lane_row.get("market_family"),
                        "history_support": round(history_support, 2),
                        "strength": round(max(0.0, min(100.0, float(signal.get("strength") or 0.0) + history_support * 2.0)), 1),
                        "selection": str(
                            signal.get("selection")
                            or lane_row.get("selection")
                            or selection_label(lane_row, str(signal.get("direction") or "over"), float(signal.get("line") or 0.0))
                        ),
                    }
                    candidates.append((lane_row, signal))

                signals: list[dict[str, Any]] = []
                primary = select_prematch_primary(candidates, recent_families, cfg.key)
                if primary is not None:
                    best_row, best_signal = primary
                    recorded, sent = self._record_signal(best_row, best_signal, cfg)
                    detected += int(recorded)
                    delivered += int(bool(sent))
                    if recorded:
                        signals.append(best_signal)
                        recent_families.append(str(best_row.get("market_family") or ""))
                if signals:
                    row["signals"] = signals
                    row["signal"] = signals[0]
                latest.append(row)
                self._prematch_latest[cfg.key][str(row.get("event_id") or "")] = dict(row)

        # Keep the menu/state complete across rotating batches, but evict matches
        # that have started or moved outside the current PREMATCH horizon.
        cache = self._prematch_latest[cfg.key]
        for event_id, cached in list(cache.items()):
            start_ts = float(cached.get("start_ts") or 0.0)
            if start_ts <= now or start_ts - now > horizon:
                cache.pop(event_id, None)
        visible = sorted(cache.values(), key=lambda row: float(row.get("start_ts") or 0.0))[:80]
        return {
            "enabled": True,
            "flashscore_prematch": len(fs_upcoming),
            "prematch_brain_candidates": len(price_targets),
            "xbet_prematch": len(xbet_prematch),
            "prematch_mapped": mapped_total,
            "prematch_scanned": len(mapped),
            "prematch_decoded": decoded,
            "prematch_market_decode_failed": failed,
            "prematch_detected": detected,
            "prematch_delivered": delivered,
            "prematch_policy_blocked": policy_blocked,
            "xbet_prematch_diag": self._prematch_index_diag.get(cfg.key) or {},
            "matches": visible,
        }

    def _prepare_flashscore_sport(self, cfg: SportConfig) -> dict[str, Any]:
        """Stage 1: analyse Flashscore first, before any 1xBet request."""
        fs_today = self._flashscore_today(cfg)
        fs_live = [row for row in fs_today if str(row.get("coarse_status") or "") == "2"]
        live_analysis = self._flashscore_live_analysis(fs_live, cfg)
        live_price_max = max(1, min(80, _int_env("GOOL_MULTISPORT_LIVE_PRICE_MAX_PER_SPORT", 24)))
        live_candidates = [
            row for row in live_analysis
            if str(row.get("brain_state") or "") in {"PASS", "BORDERLINE"}
        ][:live_price_max]

        now = time.time()
        horizon = max(15 * 60.0, _float_env("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", 30 * 60 * 60.0))
        fs_upcoming = [
            row for row in fs_today
            if str(row.get("coarse_status") or "") == "1"
            and float(row.get("start_ts") or 0.0) > now
            and float(row.get("start_ts") or 0.0) - now <= horizon
        ]
        prematch_candidates = (
            self._flashscore_prematch_shortlist(fs_upcoming, cfg)
            if _truthy("GOOL_MULTISPORT_PREMATCH_ENABLED", True)
            else []
        )
        return {
            "fs_today": fs_today,
            "fs_live": fs_live,
            "live_analysis": live_analysis,
            "live_candidates": live_candidates,
            "prematch_candidates": prematch_candidates,
        }

    @staticmethod
    def _parlay_signature(parlay: dict[str, Any], sport: str) -> str:
        legs = []
        for leg in parlay.get("legs") or []:
            legs.append(
                str(
                    leg.get("entry_id")
                    or f"{leg.get('event_id')}:{leg.get('scope')}:{leg.get('market_family')}:{leg.get('selection')}"
                )
            )
        return f"{sport}|" + "|".join(sorted(legs))

    def _sent_parlay_signatures(self) -> set[str]:
        try:
            payload = json.loads(self.parlay_delivery_path.read_text("utf-8"))
        except Exception:
            return set()
        if isinstance(payload, dict):
            payload = payload.get("signatures") or []
        return {str(value) for value in payload if str(value)} if isinstance(payload, list) else set()

    def _save_sent_parlay_signatures(self, signatures: set[str]) -> None:
        self.parlay_delivery_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.parlay_delivery_path.with_suffix(".tmp")
        values = sorted(signatures)[-500:]
        tmp.write_text(json.dumps({"signatures": values}, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(self.parlay_delivery_path)

    def _deliver_new_parlays(
        self,
        cfg: SportConfig,
        parlays: list[dict[str, Any]],
    ) -> int:
        if _mode() != "active":
            return 0
        if not _truthy("XBET_MULTISPORT_TELEGRAM_ENABLED", True):
            return 0
        if not _truthy("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", True):
            return 0

        sent_signatures = self._sent_parlay_signatures()
        delivered = 0
        changed = False
        for parlay in parlays:
            signature = self._parlay_signature(parlay, cfg.key)
            if not signature or signature in sent_signatures:
                continue
            try:
                png = render_multisport_parlay_card(parlay, cfg.key)
                sent = int(telegram.broadcast_photo(png, caption="") or 0)
            except Exception as exc:
                print(
                    f"GOOL_{cfg.key.upper()}_PARLAY_CARD_ERROR {type(exc).__name__}:{exc}",
                    flush=True,
                )
                continue
            if sent <= 0:
                print(
                    f"GOOL_{cfg.key.upper()}_PARLAY_CARD_SEND_FAILED signature={signature}",
                    flush=True,
                )
                continue
            sent_signatures.add(signature)
            changed = True
            delivered += sent
            print(
                f"GOOL_{cfg.key.upper()}_PARLAY_CARD_SENT legs={len(parlay.get('legs') or [])} "
                f"odd={float(parlay.get('combined_odd') or 0):.2f}",
                flush=True,
            )
        if changed:
            self._save_sent_parlay_signatures(sent_signatures)
        return delivered

    def _enrich_parlay_source_rows(
        self,
        rows: list[dict[str, Any]],
        fs_today: list[dict[str, Any]],
        cfg: SportConfig,
    ) -> list[dict[str, Any]]:
        """Attach fresh Flashscore identity/logo metadata before building parlays."""
        by_id = {
            str(item.get("flashscore_event_id") or ""): item
            for item in fs_today
            if str(item.get("flashscore_event_id") or "")
        }
        out: list[dict[str, Any]] = []
        keys = (
            "home_team_id", "away_team_id",
            "home_team_slug", "away_team_slug",
            "home_logo_file", "away_logo_file",
        )
        for raw in rows:
            row = dict(raw)
            if str(row.get("sport") or "") != cfg.key:
                out.append(row)
                continue
            fs = by_id.get(str(row.get("flashscore_event_id") or ""))
            if fs is None:
                best = None
                best_score = 0.0
                for candidate in fs_today:
                    quality, reversed_order, weakest = _match_quality(
                        {"O1": row.get("home"), "O2": row.get("away")},
                        candidate,
                    )
                    if reversed_order:
                        continue
                    if quality > best_score and weakest >= 0.72:
                        best, best_score = candidate, quality
                if best_score >= 0.82:
                    fs = best
            if fs:
                for key in keys:
                    if not str(row.get(key) or "").strip() and str(fs.get(key) or "").strip():
                        row[key] = str(fs.get(key) or "").strip()
            out.append(row)
        return out

    def _scan_sport(
        self,
        cfg: SportConfig,
        *,
        prepared: dict[str, Any] | None = None,
        xbet_live_prefetched: list[dict[str, Any]] | None = None,
        xbet_prematch_prefetched: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prepared = dict(prepared or self._prepare_flashscore_sport(cfg))
        fs_today = [dict(row) for row in (prepared.get("fs_today") or [])]
        states = {str(row["flashscore_event_id"]): row for row in fs_today}
        settled = self._settle(cfg, states)
        prematch_candidates = [dict(row) for row in (prepared.get("prematch_candidates") or [])]
        prematch = self._scan_prematch(
            cfg,
            fs_today,
            xbet_prematch_prefetched=xbet_prematch_prefetched,
            fs_price_candidates=prematch_candidates,
        )
        parlay_source = self._enrich_parlay_source_rows(load_journal(self.journal_path), fs_today, cfg)
        prematch_parlays = build_sport_parlays(parlay_source, cfg.key)
        parlay_delivered = self._deliver_new_parlays(cfg, prematch_parlays)
        fs_live = [dict(row) for row in (prepared.get("fs_live") or [])]
        live_analysis = [dict(row) for row in (prepared.get("live_analysis") or [])]
        live_candidates = [dict(row) for row in (prepared.get("live_candidates") or [])]
        candidate_ids = {
            str(row.get("flashscore_event_id") or "") for row in live_candidates
            if str(row.get("flashscore_event_id") or "")
        }
        fs_to_price = [
            row for row in fs_live
            if str(row.get("flashscore_event_id") or "") in candidate_ids
        ]
        xbet_live = (
            [dict(row) for row in xbet_live_prefetched]
            if xbet_live_prefetched is not None
            else (self._xbet_index(cfg) if fs_to_price else [])
        )
        # Price only Flashscore Brain candidates; non-candidates still remain
        # visible in analysis and continue building stat history.
        mapped = map_xbet_to_flashscore(xbet_live, fs_to_price)[:max(1, _int_env("XBET_MULTISPORT_MAX_MAPPED_PER_SPORT", 120))]
        analysis_by_fs = {
            str(row.get("flashscore_event_id") or ""): row
            for row in live_analysis
            if str(row.get("flashscore_event_id") or "")
        }

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
                fs_brain = analysis_by_fs.get(str(row.get("flashscore_event_id") or "")) or {}
                target_scope = str(fs_brain.get("scope") or "")
                for lane in row.get("market_lanes") or []:
                    # Product contract: LIVE pricing is only the current
                    # Flashscore period/quarter total. The Brain has already
                    # selected the game before this 1xBet market is considered.
                    if (
                        str(lane.get("market_family") or "") != "match_total"
                        or not target_scope
                        or str(lane.get("scope") or "") != target_scope
                    ):
                        policy_blocked += 1
                        continue
                    lane_row = self._lane_row(row, {**lane, "phase_policy": "flashscore_brain_current_segment"})
                    self._append_history(lane_row, cfg)
                    signal = price_flashscore_live_candidate(fs_brain, lane_row, cfg)
                    if signal is None:
                        continue
                    signal = {
                        **signal,
                        "scope": lane_row.get("scope"),
                        "market_family": lane_row.get("market_family"),
                        "selection": selection_label(
                            lane_row,
                            str(signal.get("direction") or "over"),
                            float(signal.get("line") or 0.0),
                        ),
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
                if fs_brain:
                    row["flashscore_brain"] = fs_brain
                latest.append(row)

        return {
            "enabled": True,
            "settled": settled,
            "prematch": prematch,
            "flashscore_prematch": int(prematch.get("flashscore_prematch") or 0),
            "prematch_brain_candidates": int(prematch.get("prematch_brain_candidates") or 0),
            "xbet_prematch": int(prematch.get("xbet_prematch") or 0),
            "prematch_mapped": int(prematch.get("prematch_mapped") or 0),
            "prematch_decoded": int(prematch.get("prematch_decoded") or 0),
            "prematch_scanned": int(prematch.get("prematch_scanned") or 0),
            "prematch_detected": int(prematch.get("prematch_detected") or 0),
            "prematch_delivered": int(prematch.get("prematch_delivered") or 0),
            "prematch_policy_blocked": int(prematch.get("prematch_policy_blocked") or 0),
            "prematch_matches": list(prematch.get("matches") or []),
            "prematch_parlays": prematch_parlays,
            "prematch_parlay_delivered": parlay_delivered,
            "flashscore_live": len(fs_live),
            "live_brain_candidates": len(live_candidates),
            "flashscore_analysis_matches": live_analysis[:120],
            # Authoritative LIVE identity/status from Flashscore. Keep this
            # independently from 1xBet mapping so PREMATCH picks move to
            # "In Game" immediately even when bookmaker matching is delayed.
            "flashscore_live_matches": [
                {
                    "flashscore_event_id": str(row.get("flashscore_event_id") or ""),
                    "home": str(row.get("home") or ""),
                    "away": str(row.get("away") or ""),
                    "league": str(row.get("league") or ""),
                    "score": list(row.get("score") or [0, 0]),
                    "score_parts": list(row.get("score_parts") or []),
                    "status_code": str(row.get("status_code") or ""),
                    "coarse_status": str(row.get("coarse_status") or ""),
                    "match_start_ts": int(row.get("match_start_ts") or 0),
                    "period_start_ts": int(row.get("period_start_ts") or 0),
                    "start_ts": int(row.get("start_ts") or 0),
                }
                for row in fs_live[:120]
            ],
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
        enabled = [(key, cfg) for key, cfg in SPORTS.items() if _sport_enabled(key)]

        # Stage 1 mirrors football V4: Flashscore data/Brain decides which
        # matches deserve bookmaker pricing. This happens for BOTH sports before
        # any 1xBet request, so a bookmaker mismatch can never suppress analysis.
        prepared: dict[str, dict[str, Any]] = {}
        prep_workers = max(1, min(4, len(enabled) or 1))
        with ThreadPoolExecutor(max_workers=prep_workers) as pool:
            jobs = {pool.submit(self._prepare_flashscore_sport, cfg): key for key, cfg in enabled}
            for future in as_completed(jobs):
                key = jobs[future]
                try:
                    prepared[key] = dict(future.result(timeout=90) or {})
                except Exception as exc:
                    prepared[key] = {"fs_today": [], "fs_live": [], "live_analysis": [], "live_candidates": [], "prematch_candidates": []}
                    print(f"GOOL_{key.upper()}_FS_BRAIN_ERROR {type(exc).__name__}:{exc}", flush=True)

        print(
            "GOOL_MULTISPORT_FS_BRAIN "
            + " ".join(
                f"{key}:live={len((prepared.get(key) or {}).get('fs_live') or [])}"
                f"/cand={len((prepared.get(key) or {}).get('live_candidates') or [])},"
                f"pre_cand={len((prepared.get(key) or {}).get('prematch_candidates') or [])}"
                for key, _cfg in enabled
            ),
            flush=True,
        )

        # Stage 2: fetch bookmaker indexes concurrently, but only when the
        # Flashscore Brain produced something worth pricing.
        prefetched_live: dict[str, list[dict[str, Any]]] = {}
        prefetched_prematch: dict[str, list[dict[str, Any]]] = {}
        index_workers = max(1, min(4, _int_env("GOOL_MULTISPORT_INDEX_PREFETCH_WORKERS", len(enabled) * 2 or 1)))
        with ThreadPoolExecutor(max_workers=index_workers) as pool:
            jobs = {}
            for key, cfg in enabled:
                prep = prepared.get(key) or {}
                if prep.get("live_candidates"):
                    jobs[pool.submit(self._xbet_index, cfg)] = ("live", key)
                else:
                    prefetched_live[key] = []
                if _truthy("GOOL_MULTISPORT_PREMATCH_ENABLED", True) and prep.get("prematch_candidates"):
                    jobs[pool.submit(self._xbet_prematch_index, cfg)] = ("prematch", key)
                else:
                    prefetched_prematch[key] = []
            for future in as_completed(jobs):
                phase, key = jobs[future]
                try:
                    rows = future.result(timeout=45)
                except Exception:
                    rows = []
                if phase == "live":
                    prefetched_live[key] = list(rows or [])
                else:
                    prefetched_prematch[key] = list(rows or [])

        print(
            "GOOL_MULTISPORT_PREFETCH "
            + " ".join(
                f"{key}:live={len(prefetched_live.get(key) or [])},pre={len(prefetched_prematch.get(key) or [])}"
                for key, _cfg in enabled
            ),
            flush=True,
        )

        for key, cfg in SPORTS.items():
            if not _sport_enabled(key):
                sports[key] = {"enabled": False}
                continue
            stats = self._scan_sport(
                cfg,
                prepared=prepared.get(key),
                xbet_live_prefetched=prefetched_live.get(key),
                xbet_prematch_prefetched=prefetched_prematch.get(key),
            )
            sports[key] = stats
            print(
                f"GOOL_{key.upper()} fs={stats['flashscore_live']} brain_cand={stats.get('live_brain_candidates',0)} "
                f"xbet={stats['xbet_live']} mapped={stats['mapped']} "
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
