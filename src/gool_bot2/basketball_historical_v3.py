"""GOOL basketball quarter/team history engine.

Purpose: analyse 10 *completed, pre-kickoff* games from each team, optionally
adjusted by five prior head-to-head games. Opponent correction is estimated per
team and per quarter; 8/10 on BOTH sides is PASS, 7/10 is WATCH (not sent).
No in-play possessions, shot tempo or results from the fixture being predicted.
"""
from __future__ import annotations

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def _truthy(value: Any) -> bool:
    return str(value or "").casefold() in {"1", "true", "yes", "on"}


def _matches(fs: Any, left: Any, right: Any) -> bool:
    return bool(fs._same_team(str(left or ""), str(right or "")))


def _is_pair(fs: Any, item: dict[str, Any], home: str, away: str) -> bool:
    h, a = item.get("home"), item.get("away")
    return (
        (_matches(fs, h, home) and _matches(fs, a, away))
        or (_matches(fs, h, away) and _matches(fs, a, home))
    )


def _recent_team_games(
    fs: Any, history: list[dict[str, Any]], team: str, opponent: str,
    event_id: str, kickoff: int,
) -> list[dict[str, Any]]:
    result, seen = [], set()
    ordered = sorted(history, key=lambda x: int(x.get("timestamp") or 0), reverse=True)
    for game in ordered:
        eid = str(game.get("event_id") or "")
        stamp = int(game.get("timestamp") or 0)
        if not eid or eid == event_id or eid in seen or not (0 < stamp < kickoff):
            continue
        if not (_matches(fs, game.get("home"), team) or _matches(fs, game.get("away"), team)):
            continue
        if _is_pair(fs, game, team, opponent):
            continue
        seen.add(eid)
        result.append(game)
        if len(result) == 10:
            break
    return result


def _historical_quarters(fs: Any, eid: str) -> list[tuple[int, int]] | None:
    try:
        data = fs.fetch_segment_scores(eid, "basketball")
        output = []
        for q in range(1, 5):
            pair = data.get(f"QUARTER_{q}")
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                return None
            home, away = int(pair[0]), int(pair[1])
            if min(home, away) < 0:
                return None
            output.append((home, away))
        return output
    except (TypeError, ValueError, KeyError):
        return None


def _team_points(
    fs: Any, game: dict[str, Any], quarters: list[tuple[int, int]], team: str,
) -> dict[str, list[int]] | None:
    home = _matches(fs, game.get("home"), team)
    away = _matches(fs, game.get("away"), team)
    if home == away:
        return None
    scored = [pair[0 if home else 1] for pair in quarters]
    allowed = [pair[1 if home else 0] for pair in quarters]
    return {
        "scored": scored,
        "allowed": allowed,
        "total": [x+y for x, y in zip(scored, allowed)],
    }


def opponent_weight(coefficient: float) -> float:
    """50% shrinkage with max +/-12.5% adjustment, per team per quarter."""
    bounded = min(1.25, max(0.75, float(coefficient)))
    return 1.0 + 0.5 * (bounded - 1.0)


def build_profile(
    fs: Any, event_id: str, home: str, away: str, kickoff: int,
    history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use only strictly older matches. Network results are cached by caller."""
    if kickoff <= 0:
        return {"status": "WAIT_NO_START_TIME"}
    try:
        ctx = history or fs.fetch_match_history(event_id, home, away, limit=40)
        available = []
        for name in ("home_recent", "away_recent", "home_at_home", "away_away", "h2h"):
            available.extend(x for x in (ctx.get(name) or []) if isinstance(x, dict))
        hg = _recent_team_games(fs, available, home, away, event_id, kickoff)
        ag = _recent_team_games(fs, available, away, home, event_id, kickoff)
        if len(hg) < 10 or len(ag) < 10:
            return {"status": "WAIT_HISTORY", "home_games": len(hg), "away_games": len(ag)}
        h2h = []
        seen = set()
        for game in sorted(ctx.get("h2h") or [], key=lambda x: int(x.get("timestamp") or 0), reverse=True):
            eid = str(game.get("event_id") or "")
            stamp = int(game.get("timestamp") or 0)
            if not eid or eid in seen or eid == event_id or not (0 < stamp < kickoff):
                continue
            if not _is_pair(fs, game, home, away):
                continue
            h2h.append(game)
            seen.add(eid)
            if len(h2h) == 5:
                break
        allgames = list({str(g["event_id"]): g for g in (hg+ag+h2h)}.values())
        # Bound parallelism to avoid saturating production Flashscore.
        workers = max(1, min(6, int(os.getenv("GOOL_BASKETBALL_HISTORY_FETCH_WORKERS", "4"))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            data = list(pool.map(lambda m: _historical_quarters(fs, str(m["event_id"])), allgames))
        byid = {str(g["event_id"]): q for g, q in zip(allgames, data)}
        def lines(games: list[dict[str, Any]], team: str) -> list[dict[str, list[int]]]:
            out = []
            for g in games:
                q = byid.get(str(g["event_id"]))
                item = _team_points(fs, g, q, team) if q else None
                if item:
                    out.append(item)
            return out
        hp = lines(hg, home)
        ap = lines(ag, away)
        if len(hp) != 10 or len(ap) != 10:
            return {"status": "WAIT_QUARTERS", "home_games": len(hp), "away_games": len(ap)}
        hhp = lines(h2h, home)
        ahp = lines(h2h, away)
        ready_coeff = len(hhp) == len(ahp) == 5
        scored_h = [statistics.mean(p["scored"][q] for p in hp) for q in range(4)]
        scored_a = [statistics.mean(p["scored"][q] for p in ap) for q in range(4)]
        conceded_h = [statistics.mean(p["allowed"][q] for p in hp) for q in range(4)]
        conceded_a = [statistics.mean(p["allowed"][q] for p in ap) for q in range(4)]
        quarters = []
        for q in range(4):
            # Same formula as isolated Oct 8/9 audit: attack/defence matchup.
            home_mu = (scored_h[q] + conceded_a[q]) / 2
            away_mu = (scored_a[q] + conceded_h[q]) / 2
            kh = (statistics.mean(p["scored"][q] for p in hhp) / max(1.0, scored_h[q])) if ready_coeff else 1.0
            ka = (statistics.mean(p["scored"][q] for p in ahp) / max(1.0, scored_a[q])) if ready_coeff else 1.0
            dh = home_mu * (opponent_weight(kh) - 1.0)
            da = away_mu * (opponent_weight(ka) - 1.0)
            quarters.append({
                "home_mu": round(home_mu + dh, 4),
                "away_mu": round(away_mu + da, 4),
                "home_delta": round(dh, 4), "away_delta": round(da, 4),
                "home_coeff": round(kh, 4), "away_coeff": round(ka, 4),
            })
        return {
            "status": "READY", "event_id": event_id, "home": home, "away": away,
            "home_games": 10, "away_games": 10, "h2h_games": len(hhp),
            "opponent_coefficient_available": ready_coeff,
            "home_history": hp, "away_history": ap,
            "quarters": quarters,
        }
    except Exception as exc:
        return {"status": "WAIT_ERROR", "reason": f"{type(exc).__name__}:{exc}"}


def screen_market(
    profile: dict[str, Any], lane: dict[str, Any], direction: str, *,
    threshold: int = 8,
    min_odd: float = 1.45,
    max_odd: float = 3.25,
    phase: str = "PREMATCH",
) -> dict[str, Any] | None:
    """Screen one actual bookmaker line; always require BOTH 10-game samples."""
    if profile.get("status") != "READY":
        return None
    direction = str(direction).lower()
    if direction not in {"over", "under"}:
        return None
    scope = str(lane.get("scope") or "")
    family = str(lane.get("market_family") or "")
    if family not in {"match_total", "home_total", "away_total"}:
        return None
    if str(phase).upper() == "LIVE" and (not scope.startswith("QUARTER_") or family != "match_total"):
        return None
    if scope.startswith("QUARTER_"):
        try:
            indices = [int(scope.split("_")[-1]) - 1]
        except (TypeError, ValueError):
            return None
        if not 0 <= indices[0] < 4:
            return None
    elif scope == "FIRST_HALF":
        indices = [0, 1]
    elif scope == "SECOND_HALF":
        indices = [2, 3]
    elif scope == "FULL_MATCH":
        # Prevent regulation-only historical forecasts from being compared to
        # bookmaker overtime-inclusive lines unless explicitly accepted.
        if not _truthy(os.getenv("GOOL_BASKETBALL_HIST_FULL_MATCH_SETTLEMENT_VERIFIED", "0")):
            return None
        indices = [0, 1, 2, 3]
    else:
        return None
    try:
        line = float(lane["line"])
        odd = float(lane[direction])
    except (ValueError, TypeError, KeyError):
        return None
    if line <= 0 or not min_odd <= odd <= max_odd:
        return None
    groups = [profile["home_history"], profile["away_history"]]
    delta_home = sum(profile["quarters"][i]["home_delta"] for i in indices)
    delta_away = sum(profile["quarters"][i]["away_delta"] for i in indices)
    home_mu = sum(profile["quarters"][i]["home_mu"] for i in indices)
    away_mu = sum(profile["quarters"][i]["away_mu"] for i in indices)
    if family == "match_total":
        field_left, field_right = "total", "total"
        delta = delta_home + delta_away
        expected = home_mu + away_mu
    elif family == "home_total":
        field_left, field_right = "scored", "allowed"
        delta = delta_home
        expected = home_mu
    else:
        field_left, field_right = "allowed", "scored"
        delta = delta_away
        expected = away_mu
    def count(row: dict[str, list[int]], field: str) -> float:
        return sum(row[field][i] for i in indices)
    def hits(rows: list[dict[str, list[int]]], field: str) -> int:
        return sum(
            count(row, field) + delta > line if direction == "over" else count(row, field) + delta < line
            for row in rows
        )
    ha, hb = hits(groups[0], field_left), hits(groups[1], field_right)
    margin = (expected - line) * (1 if direction == "over" else -1)
    # A sample rate of 8/10 is not a calibrated 80% predictive probability.
    minimum_gap = (1.5 if len(indices) == 1 else 2.5 if len(indices) == 2 else 4.0)
    if family != "match_total":
        minimum_gap *= 0.7
    tier = "PASS_8" if min(ha, hb) >= max(8, threshold) else "WATCH_7" if min(ha, hb) >= 7 else "WAIT"
    if margin < minimum_gap:
        tier = "WAIT"
    return {
        "tier": tier, "direction": direction, "line": line, "odd": odd,
        "scope": scope, "market_family": family,
        "home_hits": ha, "away_hits": hb,
        "expected": round(expected, 2), "margin": round(margin, 2),
        "opponent_coefficient_available": bool(profile.get("opponent_coefficient_available")),
        "coefficient_home": [profile["quarters"][i]["home_coeff"] for i in indices],
        "coefficient_away": [profile["quarters"][i]["away_coeff"] for i in indices],
        "correction_points": round(delta, 2),
    }


def best_market(
    profile: dict[str, Any], lane: dict[str, Any], *,
    phase: str = "PREMATCH", min_odd: float = 1.45, max_odd: float = 3.25,
) -> dict[str, Any] | None:
    results = [
        screen_market(profile, lane, direction, phase=phase, min_odd=min_odd, max_odd=max_odd)
        for direction in ("over", "under")
    ]
    qualified = [r for r in results if r and r["tier"] == "PASS_8"]
    if not qualified:
        return None
    return max(qualified, key=lambda r: (min(r["home_hits"], r["away_hits"]), r["margin"], r["odd"]))


class HistoricalProfileCache:
    """TTL cache so workers don't refetch 25 historical games every 20 seconds."""

    def __init__(self, fs: Any, ttl: float = 4 * 3600) -> None:
        self.fs = fs
        self.ttl = ttl
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, event_id: str, home: str, away: str, kickoff: int) -> dict[str, Any]:
        now = time.monotonic()
        old = self._values.get(event_id)
        if old and now - old[0] < (self.ttl if old[1].get("status") == "READY" else 15 * 60):
            return old[1]
        profile = build_profile(self.fs, event_id, home, away, kickoff)
        if len(self._values) > 300:
            oldest = min(self._values, key=lambda key: self._values[key][0])
            self._values.pop(oldest, None)
        self._values[event_id] = (now, profile)
        return profile
