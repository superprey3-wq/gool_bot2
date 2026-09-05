from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any

from .providers import Scores365Provider
from .providers.common import pair_score


TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
_HISTORY_BUCKETS = ("home_recent", "away_recent", "home_at_home", "away_away", "h2h")
_HALF_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_HALF_PROVIDER: Scores365Provider | None = None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _similar(name: str, target: str) -> bool:
    name = str(name or "").strip()
    target = str(target or "").strip()
    if not name or not target:
        return False
    if name.casefold() == target.casefold():
        return True
    return pair_score(target, target, name, name) >= 0.72


def _half_score(row: dict[str, Any], period: str) -> tuple[int, int] | None:
    ft_home = _number(row.get("home_score"))
    ft_away = _number(row.get("away_score"))
    ht_home = _number(row.get("halftime_home_score"))
    ht_away = _number(row.get("halftime_away_score"))
    if ht_home is None or ht_away is None:
        score = row.get("halftime_score") or row.get("ht_score")
        if isinstance(score, (list, tuple)) and len(score) >= 2:
            ht_home, ht_away = _number(score[0]), _number(score[1])
    if ht_home is None or ht_away is None:
        return None
    ht = (max(0, int(ht_home)), max(0, int(ht_away)))
    if period == "1H":
        return ht
    sh_home = _number(row.get("second_half_home_score"))
    sh_away = _number(row.get("second_half_away_score"))
    if sh_home is not None and sh_away is not None:
        return max(0, int(sh_home)), max(0, int(sh_away))
    if ft_home is None or ft_away is None:
        return None
    final = (max(0, int(ft_home)), max(0, int(ft_away)))
    if ht[0] > final[0] or ht[1] > final[1]:
        return None
    return final[0] - ht[0], final[1] - ht[1]


def _timestamp_day(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        number = float(raw)
        if number > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return raw[:10]


def _history_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    home = str(row.get("home") or "").casefold().strip()
    away = str(row.get("away") or "").casefold().strip()
    day = _timestamp_day(row.get("timestamp"))
    home_score = int(_number(row.get("home_score")) or 0)
    away_score = int(_number(row.get("away_score")) or 0)
    if home and away and day:
        return ("match", home, away, day, home_score, away_score)
    event_id = str(row.get("event_id") or "").strip()
    if event_id:
        return ("event", str(row.get("source") or ""), event_id)
    return ("fallback", home, away, home_score, away_score)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _history_identity(row)
        old = selected.get(key)
        if old is None:
            selected[key] = row
            order.append(key)
            continue
        old_half = _half_score(old, "1H") is not None
        new_half = _half_score(row, "1H") is not None
        if new_half and not old_half:
            selected[key] = row
    return [selected[key] for key in order]


def _team_profile(rows: list[dict[str, Any]], team: str, period: str) -> dict[str, Any]:
    used: list[tuple[int, int]] = []
    totals: list[int] = []
    for row in _dedupe(rows):
        score = _half_score(row, period)
        if score is None:
            continue
        if _similar(str(row.get("home") or ""), team):
            gf, ga = score
        elif _similar(str(row.get("away") or ""), team):
            gf, ga = score[1], score[0]
        else:
            continue
        used.append((gf, ga))
        totals.append(gf + ga)
    count = len(used)
    if not count:
        return {
            "matches": 0,
            "avg_for": None,
            "avg_against": None,
            "avg_total": None,
            "scored_rate": None,
            "conceded_rate": None,
            "over": {f"{line:.1f}": None for line in TOTAL_LINES},
        }
    return {
        "matches": count,
        "avg_for": sum(gf for gf, _ in used) / count,
        "avg_against": sum(ga for _, ga in used) / count,
        "avg_total": sum(totals) / count,
        "scored_rate": sum(1 for gf, _ in used if gf > 0) / count,
        "conceded_rate": sum(1 for _, ga in used if ga > 0) / count,
        "over": {
            f"{line:.1f}": sum(1 for total in totals if total > line) / count
            for line in TOTAL_LINES
        },
    }


def _blend_profiles(overall: dict[str, Any], venue: dict[str, Any]) -> dict[str, Any]:
    overall_n = int(overall.get("matches") or 0)
    venue_n = int(venue.get("matches") or 0)
    if not overall_n:
        return dict(venue)
    if venue_n < 3:
        return dict(overall)
    venue_weight = min(0.35, 0.20 + 0.03 * venue_n)
    overall_weight = 1.0 - venue_weight

    def blend_key(key: str) -> float | None:
        a = _number(overall.get(key))
        b = _number(venue.get(key))
        if a is None:
            return b
        if b is None:
            return a
        return overall_weight * a + venue_weight * b

    over: dict[str, float | None] = {}
    for line in TOTAL_LINES:
        key = f"{line:.1f}"
        a = _number((overall.get("over") or {}).get(key))
        b = _number((venue.get("over") or {}).get(key))
        if a is None:
            over[key] = b
        elif b is None:
            over[key] = a
        else:
            over[key] = overall_weight * a + venue_weight * b
    return {
        "matches": overall_n,
        "venue_matches": venue_n,
        "venue_weight": venue_weight,
        "avg_for": blend_key("avg_for"),
        "avg_against": blend_key("avg_against"),
        "avg_total": blend_key("avg_total"),
        "scored_rate": blend_key("scored_rate"),
        "conceded_rate": blend_key("conceded_rate"),
        "over": over,
    }


def _poisson_over(lam: float, line: float) -> float:
    threshold = int(math.floor(line)) + 1
    cdf = 0.0
    for goals in range(threshold):
        cdf += math.exp(-lam) * (lam ** goals) / math.factorial(goals)
    return _clamp(1.0 - cdf)


def _mean(values: list[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return None if not usable else sum(usable) / len(usable)


def _period_profile(context: dict[str, Any], home: str, away: str, period: str) -> dict[str, Any]:
    home_overall = _team_profile(list(context.get("home_recent") or []), home, period)
    away_overall = _team_profile(list(context.get("away_recent") or []), away, period)
    home_venue = _team_profile(list(context.get("home_at_home") or []), home, period)
    away_venue = _team_profile(list(context.get("away_away") or []), away, period)
    home_effective = _blend_profiles(home_overall, home_venue)
    away_effective = _blend_profiles(away_overall, away_venue)

    home_lambda = _mean([
        _number(home_effective.get("avg_for")),
        _number(away_effective.get("avg_against")),
    ])
    away_lambda = _mean([
        _number(away_effective.get("avg_for")),
        _number(home_effective.get("avg_against")),
    ])
    team_lambda = None if home_lambda is None or away_lambda is None else max(0.05, home_lambda + away_lambda)

    h2h = _team_profile(list(context.get("h2h") or []), home, period)
    h2h_n = int(h2h.get("matches") or 0)
    h2h_weight = min(0.20, 0.20 * min(1.0, h2h_n / 5.0))
    h2h_total = _number(h2h.get("avg_total"))
    if team_lambda is None:
        expected_total = h2h_total
    elif h2h_total is None:
        expected_total = team_lambda
        h2h_weight = 0.0
    else:
        expected_total = (1.0 - h2h_weight) * team_lambda + h2h_weight * h2h_total

    pair_samples = min(int(home_overall.get("matches") or 0), int(away_overall.get("matches") or 0))
    over: dict[str, float | None] = {}
    for line in TOTAL_LINES:
        key = f"{line:.1f}"
        poisson = None if expected_total is None else _poisson_over(max(0.01, expected_total), line)
        team_empirical = _mean([
            _number((home_effective.get("over") or {}).get(key)),
            _number((away_effective.get("over") or {}).get(key)),
        ])
        if poisson is None:
            base = team_empirical
        elif team_empirical is None:
            base = poisson
        else:
            empirical_weight = 0.45 if pair_samples >= 6 else 0.25
            base = (1.0 - empirical_weight) * poisson + empirical_weight * team_empirical
        h2h_rate = _number((h2h.get("over") or {}).get(key))
        if base is not None and h2h_rate is not None and h2h_weight > 0:
            base = (1.0 - h2h_weight) * base + h2h_weight * h2h_rate
        over[key] = None if base is None else _clamp(base)

    return {
        "period": period,
        "home": home_effective,
        "away": away_effective,
        "h2h": h2h,
        "h2h_weight": round(h2h_weight, 4),
        "home_expected_goals": None if home_lambda is None else round(home_lambda, 4),
        "away_expected_goals": None if away_lambda is None else round(away_lambda, 4),
        "expected_total": None if expected_total is None else round(expected_total, 4),
        "over": {key: None if value is None else round(value, 4) for key, value in over.items()},
        "pair_sample": pair_samples,
        "available": expected_total is not None and pair_samples > 0,
    }


def _current_halftime_score(record: dict[str, Any]) -> tuple[int, int] | None:
    match = record.get("match") or {}
    home = _number(match.get("halftime_home_score"))
    away = _number(match.get("halftime_away_score"))
    if home is not None and away is not None:
        return int(home), int(away)
    meta = (((record.get("providers") or {}).get("365scores") or {}).get("meta") or {})
    score = meta.get("halftime_score")
    if isinstance(score, (list, tuple)) and len(score) >= 2:
        home, away = _number(score[0]), _number(score[1])
        if home is not None and away is not None:
            return int(home), int(away)
    goals = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or []
    home_goals = 0
    away_goals = 0
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        period = str(goal.get("period") or "")
        try:
            minute = int(float(goal.get("minute") or 0))
        except (TypeError, ValueError):
            minute = 0
        if period == "2H" or (not period and minute > 45):
            continue
        side = str(goal.get("side") or "")
        if side == "home":
            home_goals += 1
        elif side == "away":
            away_goals += 1
    if home_goals + away_goals > 0:
        return home_goals, away_goals
    return None


def _active_profile(record: dict[str, Any], first_half: dict[str, Any], second_half: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    if 1 <= minute <= 45 and not bool(match.get("is_halftime")):
        period = "1H"
        period_profile = first_half
        half_goals = home_score + away_score
        remaining = max(0.0, 45.0 - minute)
    elif 46 <= minute <= 90:
        period = "2H"
        period_profile = second_half
        halftime = _current_halftime_score(record)
        if halftime is None:
            return {"available": False, "period": "2H", "reason": "halftime_score_unavailable"}
        half_goals = max(0, home_score + away_score - halftime[0] - halftime[1])
        remaining = max(0.0, 90.0 - minute)
    else:
        return {"available": False, "period": None, "reason": "outside_live_half"}

    expected_total = _number(period_profile.get("expected_total"))
    if expected_total is None:
        return {"available": False, "period": period, "reason": "half_history_unavailable"}
    remaining_lambda = max(0.0, expected_total * remaining / 45.0)
    one_more = 1.0 - math.exp(-remaining_lambda)
    line = float(half_goals) + 0.5
    return {
        "available": True,
        "period": period,
        "minute": minute,
        "current_half_goals": half_goals,
        "next_total_line": line,
        "remaining_minutes": round(remaining, 2),
        "remaining_lambda": round(remaining_lambda, 4),
        "one_more_probability": round(_clamp(one_more), 4),
        "pair_sample": int(period_profile.get("pair_sample") or 0),
        "h2h_sample": int((period_profile.get("h2h") or {}).get("matches") or 0),
    }


def build_prematch_goal_profile(record: dict[str, Any]) -> dict[str, Any]:
    context = record.get("prematch_context") or {}
    match = record.get("match") or {}
    home = str(match.get("home") or "")
    away = str(match.get("away") or "")
    first_half = _period_profile(context, home, away, "1H")
    second_half = _period_profile(context, home, away, "2H")
    active = _active_profile(record, first_half, second_half)
    return {
        "home": home,
        "away": away,
        "sources": list(context.get("sources") or []),
        "first_half": first_half,
        "second_half": second_half,
        "active": active,
        "scores365_trend_flags": {
            "has_trends": bool(context.get("has_trends")),
            "has_top_trends": bool(context.get("has_top_trends")),
        },
    }


def _provider() -> Scores365Provider:
    global _HALF_PROVIDER
    if _HALF_PROVIDER is None:
        _HALF_PROVIDER = Scores365Provider()
    return _HALF_PROVIDER


def _merge_half_context(record: dict[str, Any], extra: dict[str, Any], limit: int) -> None:
    current = dict(record.get("prematch_context") or {})
    for bucket in _HISTORY_BUCKETS:
        rows = [
            *[dict(row) for row in (extra.get(bucket) or []) if isinstance(row, dict)],
            *[dict(row) for row in (current.get(bucket) or []) if isinstance(row, dict)],
        ]
        current[bucket] = _dedupe(rows)[:limit]

    sources = list(current.get("sources") or [])
    extra_sources = list(extra.get("sources") or [])
    source = str(extra.get("source") or "")
    if source and source != "none":
        extra_sources.append(source)
    current["sources"] = list(dict.fromkeys([*sources, *extra_sources]))
    current["source"] = "+".join(current["sources"]) if current["sources"] else str(current.get("source") or "none")
    current["half_score_matches"] = max(
        int(current.get("half_score_matches") or 0),
        int(extra.get("half_score_matches") or 0),
    )
    for key in ("has_trends", "has_top_trends", "has_previous_meetings", "has_recent_matches"):
        current[key] = bool(current.get(key) or extra.get(key))
    record["prematch_context"] = current


def _active_strategy(record: dict[str, Any]) -> tuple[str | None, str | None]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    if bool(match.get("is_halftime")):
        return None, None
    if 1 <= minute <= 45:
        return "goal_before_ht", "1H"
    if 46 <= minute <= 75:
        return "another_goal", "2H"
    return None, None


def _maybe_load_half_history(
    record: dict[str, Any],
    experts: dict[str, dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, Any]:
    strategy, period = _active_strategy(record)
    if strategy is None or period is None:
        return profile
    expert = experts.get(strategy) or {}
    if str(expert.get("state") or "") not in {"PASS", "BORDERLINE"}:
        return profile

    period_profile = profile.get("first_half") if period == "1H" else profile.get("second_half")
    if int((period_profile or {}).get("pair_sample") or 0) >= 3:
        return profile

    match = record.get("match") or {}
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    match_id = str(match.get("flashscore_event_id") or "").strip()
    if not home or not away:
        return profile
    cache_key = match_id or f"{home.casefold()}::{away.casefold()}"
    ttl = max(300.0, float(os.getenv("GOOL_HALF_PREMATCH_CACHE_SECONDS", "3600")))
    now = time.time()
    cached = _HALF_CONTEXT_CACHE.get(cache_key)
    if cached and now - cached[0] < ttl:
        extra = cached[1]
    else:
        try:
            limit = max(3, min(10, int(os.getenv("GOOL_HALF_PREMATCH_HISTORY_MATCHES", "6"))))
            method = getattr(_provider(), "half_prematch_context", None)
            extra = method(home, away, limit=limit) if callable(method) else None
        except Exception as exc:
            print(
                f"GOOL_HALF_PREMATCH_FETCH_ERROR match={cache_key} error={type(exc).__name__}:{exc}",
                flush=True,
            )
            extra = None
        _HALF_CONTEXT_CACHE[cache_key] = (now, extra if isinstance(extra, dict) else None)

    if isinstance(extra, dict):
        limit = max(3, min(10, int(os.getenv("GOOL_HALF_PREMATCH_HISTORY_MATCHES", "6"))))
        _merge_half_context(record, extra, limit)
        rebuilt = build_prematch_goal_profile(record)
        rebuilt["lazy_365_loaded"] = True
        return rebuilt
    profile["lazy_365_loaded"] = False
    return profile


def apply_half_goal_prior(record: dict[str, Any], experts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Blend half-specific history into GOOL while keeping LIVE dominant.

    Historical HT/FT requests are intentionally lazy: 365Scores fan-out is only
    executed when the active LIVE expert is already PASS/BORDERLINE and local
    context does not yet contain a useful half sample. History can therefore
    confirm or modestly weaken a football idea, but never manufacture PASS.
    """
    profile = build_prematch_goal_profile(record)
    profile = _maybe_load_half_history(record, experts, profile)
    record["prematch_goal_profile"] = profile
    active = profile.get("active") or {}
    if not active.get("available"):
        return profile
    strategy = "goal_before_ht" if active.get("period") == "1H" else "another_goal"
    expert = experts.get(strategy)
    if not isinstance(expert, dict):
        return profile
    live_probability = _number(expert.get("probability"))
    prior = _number(active.get("one_more_probability"))
    if live_probability is None or prior is None:
        return profile
    sample = int(active.get("pair_sample") or 0)
    reliability = min(1.0, sample / 5.0)
    weight = 0.20 * reliability
    blended = (1.0 - weight) * live_probability + weight * prior
    delta = max(-0.06, min(0.06, blended - live_probability))
    adjusted = _clamp(live_probability + delta, 0.01, 0.99)
    expert["probability"] = round(adjusted, 4)
    diagnostics = dict(expert.get("diagnostics") or {})
    diagnostics["half_prematch_prior"] = {
        "period": active.get("period"),
        "next_total_line": active.get("next_total_line"),
        "one_more_probability": round(prior, 4),
        "live_probability_before": round(live_probability, 4),
        "probability_after": round(adjusted, 4),
        "weight": round(weight, 4),
        "pair_sample": sample,
        "h2h_sample": int(active.get("h2h_sample") or 0),
        "lazy_365_loaded": bool(profile.get("lazy_365_loaded")),
        "scores365_has_trends": bool((profile.get("scores365_trend_flags") or {}).get("has_trends")),
    }
    expert["diagnostics"] = diagnostics
    return profile


__all__ = ["build_prematch_goal_profile", "apply_half_goal_prior"]
