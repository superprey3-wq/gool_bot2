from __future__ import annotations

import math
import re
from typing import Any


SPORT_SEGMENTS = {
    "basketball": ("QUARTER_1", "QUARTER_2", "QUARTER_3", "QUARTER_4"),
    "hockey": ("PERIOD_1", "PERIOD_2", "PERIOD_3"),
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "", str(value or "").casefold())


def _avg(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _same_team(left: Any, right: Any) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and (a == b or (len(a) >= 5 and len(b) >= 5 and (a in b or b in a))))


def segment_order(sport: str) -> tuple[str, ...]:
    return SPORT_SEGMENTS["hockey" if str(sport).casefold() == "hockey" else "basketball"]


def _team_segment(row: dict[str, Any], team: str, scope: str) -> tuple[float, float] | None:
    segments = dict(row.get("segments") or {})
    pair = segments.get(scope)
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        return None
    try:
        home_points, away_points = float(pair[0]), float(pair[1])
    except (TypeError, ValueError):
        return None
    if _same_team(row.get("home"), team):
        return home_points, away_points
    if _same_team(row.get("away"), team):
        return away_points, home_points
    return None


def _series(rows: list[dict[str, Any]], team: str, scope: str) -> tuple[list[float], list[float]]:
    scored: list[float] = []
    allowed: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pair = _team_segment(row, team, scope)
        if pair is None:
            continue
        scored.append(pair[0])
        allowed.append(pair[1])
    return scored, allowed


def _blend(values: list[tuple[float | None, float]]) -> float | None:
    valid = [(float(value), float(weight)) for value, weight in values if value is not None and weight > 0]
    if not valid:
        return None
    total_weight = sum(weight for _value, weight in valid)
    return sum(value * weight for value, weight in valid) / max(1e-9, total_weight)


def build_segment_memory(
    context: dict[str, Any],
    home: str,
    away: str,
    sport: str,
) -> dict[str, Any]:
    """Build Q1-Q4/P1-P3 priors from team form and direct H2H line scores.

    Recent team form is the base. Direct meetings receive extra weight, but that
    weight is sample-capped so one old H2H cannot overpower ten recent games.
    """
    home_recent = [dict(r) for r in (context.get("home_recent") or []) if isinstance(r, dict)]
    away_recent = [dict(r) for r in (context.get("away_recent") or []) if isinstance(r, dict)]
    h2h = [dict(r) for r in (context.get("h2h") or []) if isinstance(r, dict)]

    profiles: dict[str, dict[str, Any]] = {}
    usable_scopes = 0
    for scope in segment_order(sport):
        h_for, h_against = _series(home_recent, home, scope)
        a_for, a_against = _series(away_recent, away, scope)
        hh_home_for, _hh_home_against = _series(h2h, home, scope)
        hh_away_for, _hh_away_against = _series(h2h, away, scope)

        home_form = _blend([(_avg(h_for), 0.58), (_avg(a_against), 0.42)])
        away_form = _blend([(_avg(a_for), 0.58), (_avg(h_against), 0.42)])

        h2h_home = _avg(hh_home_for)
        h2h_away = _avg(hh_away_for)
        h2h_n = min(len(hh_home_for), len(hh_away_for))
        # 1 H2H ~= 18%; 3 ~= 30%; 5+ capped at 42%.
        h2h_weight = min(0.42, 0.12 + h2h_n * 0.06) if h2h_n else 0.0

        expected_home = _blend([(home_form, 1.0 - h2h_weight), (h2h_home, h2h_weight)])
        expected_away = _blend([(away_form, 1.0 - h2h_weight), (h2h_away, h2h_weight)])
        expected_total = None if expected_home is None or expected_away is None else expected_home + expected_away
        if expected_total is not None:
            usable_scopes += 1

        profiles[scope] = {
            "home_recent_for": _avg(h_for),
            "home_recent_against": _avg(h_against),
            "away_recent_for": _avg(a_for),
            "away_recent_against": _avg(a_against),
            "h2h_home_for": h2h_home,
            "h2h_away_for": h2h_away,
            "home_recent_n": len(h_for),
            "away_recent_n": len(a_for),
            "h2h_n": h2h_n,
            "h2h_weight": round(h2h_weight, 3),
            "expected_home": None if expected_home is None else round(expected_home, 3),
            "expected_away": None if expected_away is None else round(expected_away, 3),
            "expected_total": None if expected_total is None else round(expected_total, 3),
        }

    expected_home_match = sum(float(v["expected_home"]) for v in profiles.values() if v.get("expected_home") is not None)
    expected_away_match = sum(float(v["expected_away"]) for v in profiles.values() if v.get("expected_away") is not None)
    complete = usable_scopes == len(profiles)
    quality_n = min(
        len([r for r in home_recent if r.get("segments")]),
        len([r for r in away_recent if r.get("segments")]),
    )
    quality = min(1.0, quality_n / 8.0) * 0.70 + min(1.0, len([r for r in h2h if r.get("segments")]) / 5.0) * 0.30
    return {
        "sport": str(sport),
        "segments": profiles,
        "usable_scopes": usable_scopes,
        "complete": complete,
        "quality": round(quality, 3),
        "expected_home_match": round(expected_home_match, 3) if complete else None,
        "expected_away_match": round(expected_away_match, 3) if complete else None,
        "expected_match_total": round(expected_home_match + expected_away_match, 3) if complete else None,
    }


def segment_prior(memory: dict[str, Any], scope: str) -> dict[str, Any]:
    return dict((memory.get("segments") or {}).get(str(scope)) or {})


def remaining_match_projection(
    memory: dict[str, Any],
    *,
    scope: str,
    match_score: list[int] | tuple[int, int],
    current_segment_score: list[int] | tuple[int, int],
    current_segment_projection: float | None = None,
) -> dict[str, Any] | None:
    """Project regulation final score from current score plus expected remainder.

    Completed segments are already contained in match_score. The current segment
    contributes only its projected *remaining* points/goals; later segments use
    their H2H/form priors.
    """
    order = segment_order(str(memory.get("sport") or "basketball"))
    if scope not in order:
        return None
    try:
        mh, ma = float(match_score[0]), float(match_score[1])
        ch, ca = float(current_segment_score[0]), float(current_segment_score[1])
    except (TypeError, ValueError, IndexError):
        return None

    current = segment_prior(memory, scope)
    exp_h = current.get("expected_home")
    exp_a = current.get("expected_away")
    exp_t = current.get("expected_total")
    if exp_h is None or exp_a is None or exp_t is None:
        return None

    projected_segment_total = max(ch + ca, float(current_segment_projection if current_segment_projection is not None else exp_t))
    remaining_segment = max(0.0, projected_segment_total - ch - ca)
    share_h = float(exp_h) / max(1e-9, float(exp_h) + float(exp_a))
    add_h = remaining_segment * share_h
    add_a = remaining_segment * (1.0 - share_h)

    idx = order.index(scope)
    future_h = future_a = 0.0
    for future_scope in order[idx + 1:]:
        item = segment_prior(memory, future_scope)
        if item.get("expected_home") is None or item.get("expected_away") is None:
            return None
        future_h += float(item["expected_home"])
        future_a += float(item["expected_away"])

    final_h = mh + add_h + future_h
    final_a = ma + add_a + future_a
    expected_match_total = memory.get("expected_match_total")
    return {
        "home": round(final_h, 3),
        "away": round(final_a, 3),
        "total": round(final_h + final_a, 3),
        "remaining_home": round(add_h + future_h, 3),
        "remaining_away": round(add_a + future_a, 3),
        "schedule_expected_total": expected_match_total,
        "schedule_delta": (
            None if expected_match_total is None
            else round((final_h + final_a) - float(expected_match_total), 3)
        ),
        "current_scope": scope,
        "current_segment_projection": round(projected_segment_total, 3),
    }


def normal_side_probability(line: float, mean: float, sd: float, direction: str) -> float:
    sd = max(0.05, float(sd))
    cdf = 0.5 * (1.0 + math.erf((float(line) - float(mean)) / (sd * math.sqrt(2.0))))
    over = max(0.001, min(0.999, 1.0 - cdf))
    return over if str(direction) == "over" else 1.0 - over
