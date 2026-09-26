from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .providers.common import pair_score
from .reference_feeds import PinnacleReference


def _participants(matchup: dict[str, Any]) -> tuple[str, str]:
    home = away = ""
    rows = matchup.get("participants") or []
    for p in rows:
        name = str(p.get("name") or "")
        side = str(p.get("alignment") or p.get("designation") or "").lower()
        if side == "home":
            home = name
        elif side == "away":
            away = name
    if not home and len(rows) >= 1:
        home = str(rows[0].get("name") or "")
    if not away and len(rows) >= 2:
        away = str(rows[1].get("name") or "")
    return home, away


def _start_ts(matchup: dict[str, Any]) -> float:
    raw = matchup.get("startTime") or matchup.get("start_time")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _soccer_id(feed: PinnacleReference) -> int | None:
    for row in feed.sports():
        if str(row.get("name") or "").strip().lower() in {"soccer", "football"}:
            try:
                return int(row.get("id"))
            except (TypeError, ValueError):
                pass
    return None


def _parse_markets(markets: list[dict[str, Any]]) -> dict[str, Any]:
    one_x_two: dict[str, float] = {}
    totals: dict[float, dict[str, float]] = {}
    for market in PinnacleReference.decimal_prices(markets):
        key = str(market.get("key") or "").lower()
        for p in market.get("prices") or []:
            try:
                odd = float(p.get("decimal"))
            except (TypeError, ValueError):
                continue
            if odd <= 1.0:
                continue
            side = str(p.get("designation") or p.get("name") or "").lower()
            if side in {"home", "draw", "away"} and (";m" in key or "money" in key):
                one_x_two[side] = odd
            if side in {"over", "under"}:
                try:
                    point = float(p.get("points") if p.get("points") is not None else market.get("points"))
                except (TypeError, ValueError):
                    continue
                totals.setdefault(point, {})[side] = odd
    total_rows = [
        {"line": line, "over": sides["over"], "under": sides["under"]}
        for line, sides in sorted(totals.items())
        if "over" in sides and "under" in sides
    ]
    return {
        "match_1x2": one_x_two if {"home", "draw", "away"} <= set(one_x_two) else {},
        "match_totals": total_rows,
    }


def pinnacle_market_for_match(match, feed: PinnacleReference | None = None) -> tuple[dict[str, Any] | None, float]:
    feed = feed or PinnacleReference()
    sport_id = _soccer_id(feed)
    if sport_id is None:
        return None, 0.0
    fs_ts = float((match.meta or {}).get("scheduled_start_ts") or 0)
    best = None
    best_score = 0.0
    for row in feed.matchups(sport_id, live=False):
        home, away = _participants(row)
        if not home or not away:
            continue
        names = pair_score(match.home, match.away, home, away)
        pin_ts = _start_ts(row)
        if fs_ts and pin_ts:
            delta = abs(fs_ts - pin_ts)
            if delta > 3 * 3600:
                continue
            score = 0.82 * names + 0.18 * max(0.0, 1.0 - delta / (3 * 3600))
        else:
            score = names
        if score > best_score:
            best, best_score = row, score
    if best is None or best_score < 0.72:
        return None, best_score
    try:
        matchup_id = int(best.get("id"))
    except (TypeError, ValueError):
        return None, best_score
    parsed = _parse_markets(feed.markets(matchup_id))
    if not parsed["match_1x2"] and not parsed["match_totals"]:
        return None, best_score
    home, away = _participants(best)
    parsed.update({"event_id": f"pinnacle:{matchup_id}", "home": home, "away": away, "scheduled_start_ts": _start_ts(best), "odds_source": "pinnacle"})
    return parsed, best_score
