"""PREMATCH sports-strategy scenario registry, purely observational.

The scenario thresholds are hypotheses, NOT validated betting systems.
Only strictly pregame context and existing published bookmaker quotes may be
used; no scenario may create bets, odds, or calibrated probabilities.
Designed to collect prospective per-ID evidence before activation.
"""
from __future__ import annotations

import math
from typing import Any


VERSION = "prematch_scenarios_v1"
_NO_DATA = "NO_DATA"
_WATCH = "WATCH"
_MATCH = "MATCH"


def _num(value: Any) -> float | None:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None


def _scene(
    code: str,
    name: str,
    condition: bool | None,
    *,
    reason: str,
    markets: list[tuple[str, str, str]],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": code,
        "name": name,
        "status": _NO_DATA if condition is None else _MATCH if condition else _WATCH,
        "reason": reason if condition is None else "hypothesis_matched" if condition else "hypothesis_not_matched",
        "market_targets": [
            {"family": family, "direction": direction, "scope": scope}
            for family, direction, scope in markets
        ],
        "evidence": dict(evidence or {}),
        "calibrated": False,
        "publication_enabled": False,
    }


def _target(family: str, direction: str, scope: str = "FULL_MATCH") -> tuple[str, str, str]:
    return family, direction, scope


def _match_target(target: dict[str, str], item: dict[str, Any]) -> bool:
    if item.get("family") != target["family"]:
        return False
    if item.get("direction") != target["direction"]:
        return False
    scope = item.get("scope")
    t_scope = target["scope"]
    return t_scope == "ANY_SEGMENT" and scope not in {"", "FULL_MATCH"} or t_scope == scope


def _quoted_options(sport: str, lanes: list[dict[str, Any]] | None, football_quote: dict[str, Any] | None) -> list[dict[str, Any]]:
    offered: list[dict[str, Any]] = []
    if sport == "football":
        quote = football_quote or {}
        for family, key in (
            ("match_total", "match_totals"), ("home_total", "home_totals"),
            ("away_total", "away_totals"), ("first_half_total", "first_half_totals"),
        ):
            for row in quote.get(key) or []:
                if not isinstance(row, dict):
                    continue
                for d in ("over", "under"):
                    odd = _num(row.get(d))
                    if odd is not None and odd > 1.001:
                        offered.append({"family": family, "direction": d, "scope": "FIRST_HALF" if family == "first_half_total" else "FULL_MATCH", "odd": round(odd, 4)})
        for family, key, directions in (
            ("match_1x2", "match_1x2", ("home", "draw", "away")),
            ("btts", "btts", ("yes", "no")),
        ):
            row = quote.get(key) or {}
            if not isinstance(row, dict):
                continue
            for d in directions:
                odd = _num(row.get(d))
                if odd is not None and odd > 1.001:
                    offered.append({"family": family, "direction": d, "scope": "FULL_MATCH", "odd": round(odd, 4)})
        return offered
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        family = str(lane.get("market_family") or "")
        scope = str(lane.get("scope") or "FULL_MATCH")
        if family in {"match_total", "home_total", "away_total"}:
            for d in ("over", "under"):
                odd = _num(lane.get(d))
                if odd is not None and odd > 1.001:
                    offered.append({"family": family, "direction": d, "scope": scope, "odd": round(odd, 4)})
        elif family in {"moneyline", "handicap"}:
            side = str(lane.get("selection_side") or "")
            odd = _num(lane.get("odd"))
            if side in {"home", "away"} and odd is not None and odd > 1.001:
                offered.append({
                    "family": family, "direction": side, "scope": scope,
                    "odd": round(odd, 4), "line": _num(lane.get("line")),
                })
    return offered


def _qualified_options(qualified: list[Any] | None, *, sport: str) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for entry in qualified or []:
        if sport == "football":
            family = str(getattr(entry, "market", "") or "")
            label = str(getattr(entry, "selection", "") or "")
            direction = (
                label.split(" ", 1)[0].casefold()
                if family in {"match_total", "home_total", "away_total", "first_half_total"}
                else label.casefold()
            )
            odd = _num(getattr(entry, "odds", None))
            scope = "FIRST_HALF" if family == "first_half_total" else "FULL_MATCH"
        else:
            if not isinstance(entry, (tuple, list)) or len(entry) < 2:
                continue
            lane, signal = entry[0], entry[1]
            if not isinstance(lane, dict) or not isinstance(signal, dict):
                continue
            family = str(lane.get("market_family") or "")
            direction = str(signal.get("direction") or "")
            label = str(signal.get("selection") or lane.get("selection") or "")
            odd = _num(signal.get("odd"))
            scope = str(lane.get("scope") or "FULL_MATCH")
        if odd is not None and odd > 1.001:
            options.append({
                "family": family, "direction": direction,
                "scope": scope, "odd": round(odd, 4),
                "selection": label,
            })
    return options


def _football(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    lh = _num(forecast.get("home_lambda"))
    la = _num(forecast.get("away_lambda"))
    quality = _num(forecast.get("quality"))
    ready = forecast.get("status") == "READY" and None not in (lh, la, quality) and min(lh, la) > 0 and quality >= 0.55 if None not in (lh, la, quality) else False
    if not ready:
        lh = la = None
    total = lh + la if lh is not None and la is not None else None
    gap = lh - la if lh is not None and la is not None else None
    first_half = _num(forecast.get("first_half_lambda"))
    if first_half is None:
        part = forecast.get("first_half") or {}
        if isinstance(part, dict):
            a, b = _num(part.get("home_lambda")), _num(part.get("away_lambda"))
            if a is not None and b is not None:
                first_half = a + b
    if not ready:
        first_half = None
    return [
        _scene("F01", "Ранний гол", None if first_half is None else first_half >= 1.20,
               reason="first_half_expected_goals_unavailable",
               markets=[_target("first_half_total", "over", "FIRST_HALF")],
               evidence={"first_half_lambda": first_half}),
        _scene("F02", "Две результативные атаки",
               None if total is None else min(lh, la) >= 0.95 and total >= 2.65,
               reason="football_v5_forecast_unavailable",
               markets=[_target("btts", "yes"), _target("match_total", "over")],
               evidence={"total_lambda": total, "min_team_lambda": None if total is None else min(lh, la)}),
        _scene("F03", "Оборонительная игра", None if total is None else total <= 2.20,
               reason="football_v5_forecast_unavailable",
               markets=[_target("match_total", "under"), _target("btts", "no")],
               evidence={"total_lambda": total}),
        _scene("F04", "Большой перевес фаворита", None if gap is None else abs(gap) >= 0.75 and max(lh, la) >= 1.60,
               reason="football_v5_forecast_unavailable",
               markets=[_target("match_1x2", "home" if gap is None or gap > 0 else "away"),
                        _target("home_total" if gap is None or gap > 0 else "away_total", "over")],
               evidence={"expected_goal_difference": gap}),
        _scene("F05", "Равный матч — проверка Ф+", None if gap is None else abs(gap) <= 0.35,
               reason="football_v5_forecast_unavailable",
               markets=[_target("match_1x2", "draw")],
               evidence={"expected_goal_difference": gap, "handicap_quote_not_in_current_v5_decoder": True}),
    ]


def _hockey(features: dict[str, Any], league: str, memory: dict[str, Any]) -> list[dict[str, Any]]:
    from .hockey_brain_v2 import league_goal_baseline, prematch_lambdas
    from .segment_memory import segment_prior

    n = min(_num(features.get("home_recent_n")) or 0, _num(features.get("away_recent_n")) or 0)
    valid = n >= 5 and all(_num(features.get(key)) is not None for key in (
        "home_gf_avg", "home_ga_avg", "away_gf_avg", "away_ga_avg"
    ))
    home, away, baseline = prematch_lambdas(features, league) if valid else (None, None, league_goal_baseline(league))
    total = home + away if valid else None
    recent = _num(features.get("recent_total_avg"))
    hdiff = home - away if valid else None
    high = None if total is None or recent is None else total >= baseline + .55 and recent >= baseline + .20
    low = None if total is None or recent is None else total <= baseline - .55 and recent <= baseline - .20
    imbalance = None if hdiff is None else abs(hdiff) >= .80
    goalie_a = _num(features.get("away_goalie_gsax_per60"))
    goalie_h = _num(features.get("home_goalie_gsax_per60"))
    goalie_bad = bool(goalie_a is not None and goalie_a < -.45 or goalie_h is not None and goalie_h < -.45) if valid and (goalie_a is not None or goalie_h is not None) else None
    rest = _num(features.get("rest_advantage_days"))
    fatigue = None if not valid or rest is None else abs(rest) >= 1.25 and (
        bool(features.get("home_back_to_back")) or bool(features.get("away_back_to_back"))
    )
    power = _num(features.get("home_pp_pct")), _num(features.get("away_pp_pct"))
    penalty = _num(features.get("home_pk_pct")), _num(features.get("away_pk_pct"))
    special = None if not valid or any(x is None for x in (*power, *penalty)) else abs(power[0] - penalty[1]) >= 0.12 or abs(power[1] - penalty[0]) >= 0.12

    period_obs: dict[str, Any] = {}
    best_period: tuple[str, float] | None = None
    memory_quality = _num(memory.get("quality"))
    if valid and memory_quality is not None and memory_quality >= .55:
        for part in ("PERIOD_1", "PERIOD_2", "PERIOD_3"):
            item = segment_prior(memory, part)
            ha, aa = _num(item.get("expected_home")), _num(item.get("expected_away"))
            if ha is None or aa is None:
                continue
            expected = ha + aa
            period_obs[part] = round(expected, 3)
            gap = abs(expected - baseline / 3.0)
            if gap >= 0.35 and (best_period is None or gap > best_period[1]):
                best_period = part, gap

    return [
        _scene("H01", "Повышенная результативность", high, reason="recent_scoring_or_form_unavailable",
               markets=[_target("match_total", "over"), _target("home_total", "over"),
                        _target("away_total", "over")],
               evidence={"lambda_total": total, "recent_total": recent, "league_baseline": baseline}),
        _scene("H02", "Оборона и низкий тотал", low, reason="recent_scoring_or_form_unavailable",
               markets=[_target("match_total", "under")],
               evidence={"lambda_total": total, "recent_total": recent, "league_baseline": baseline}),
        _scene("H03", "Разница в классе", imbalance, reason="matchup_scoring_unknown",
               markets=[_target("moneyline", "home" if hdiff is None or hdiff > 0 else "away"),
                        _target("handicap", "home" if hdiff is None or hdiff > 0 else "away")],
               evidence={"lambda_goal_gap_signed": hdiff, "handicap_publication_quarantined": True}),
        _scene("H04", "Подтверждённый слабый вратарь", goalie_bad,
               reason="verified_starting_goalie_performance_unavailable",
               markets=[_target("home_total" if goalie_a is not None and goalie_a < -.45 else "away_total", "over")],
               evidence={"home_goalie_gsax_per60": goalie_h, "away_goalie_gsax_per60": goalie_a, "starter_not_verified": True}),
        _scene("H05", "Неравный отдых", fatigue, reason="rest_or_form_unavailable",
               markets=[_target("moneyline", "home" if rest is None or rest > 0 else "away")],
               evidence={"rest_advantage_days": rest}),
        _scene("H06", "Большинство против меньшинства", special,
               reason="verified_pregame_power_play_penalty_kill_missing",
               markets=[_target("match_total", "over")],
               evidence={"special_teams_metrics_available": all(v is not None for v in (*power, *penalty))}),
        _scene("H07", "Сильный период", None if memory_quality is None or memory_quality < .55 or not valid or not period_obs else best_period is not None,
               reason="period_segment_prior_or_quality_missing",
               markets=[_target("match_total", "over", best_period[0] if best_period else "ANY_SEGMENT"),
                        _target("match_total", "under", best_period[0] if best_period else "ANY_SEGMENT")],
               evidence={"period_expected": period_obs, "segment_memory_quality": memory_quality}),
    ]


def _basketball(features: dict[str, Any], league: str, memory: dict[str, Any], historical: dict[str, Any]) -> list[dict[str, Any]]:
    from .basketball_brain_v2 import league_profile, prematch_means
    from .segment_memory import segment_prior

    n = min(_num(features.get("home_recent_n")) or 0, _num(features.get("away_recent_n")) or 0)
    valid = n >= 5 and all(_num(features.get(key)) is not None for key in (
        "home_gf_avg", "home_ga_avg", "away_gf_avg", "away_ga_avg"
    ))
    home, away, profile = prematch_means(features, league) if valid else (None, None, league_profile(league))
    baseline = profile["game_total"]
    total = home + away if valid else None
    margin = home - away if valid else None
    recent = _num(features.get("recent_total_avg"))
    high = None if total is None or recent is None else total >= baseline + 9.0 and recent >= baseline + 5.0
    low = None if total is None or recent is None else total <= baseline - 9.0 and recent <= baseline - 5.0
    mismatch = None if margin is None else abs(margin) >= 10.0
    close = None if margin is None else abs(margin) <= 4.0

    quarter_observations: dict[str, float] = {}
    best_quarter: tuple[str, float] | None = None
    memory_quality = _num(memory.get("quality"))
    if valid and memory_quality is not None and memory_quality >= 0.55:
        for part in ("QUARTER_1", "QUARTER_2", "QUARTER_3", "QUARTER_4"):
            segment = segment_prior(memory, part)
            h, a = _num(segment.get("expected_home")), _num(segment.get("expected_away"))
            if h is None or a is None:
                continue
            points = h + a
            quarter_observations[part] = round(points, 2)
            distance = abs(points - profile["quarter_total"])
            if distance >= 3.5 and (best_quarter is None or distance > best_quarter[1]):
                best_quarter = part, distance
    halves: dict[str, float] = {}
    quarters = historical.get("quarters") or []
    if valid and historical.get("status") == "READY" and isinstance(quarters, list) and len(quarters) == 4:
        values = []
        for row in quarters:
            if not isinstance(row, dict):
                values = []
                break
            h, a = _num(row.get("home_mu")), _num(row.get("away_mu"))
            if h is None or a is None:
                values = []
                break
            values.append(h + a)
        if len(values) == 4:
            halves = {"FIRST_HALF": round(values[0] + values[1], 3),
                      "SECOND_HALF": round(values[2] + values[3], 3)}
    rest = _num(features.get("rest_advantage_days"))
    fatigue = None if not valid or rest is None else abs(rest) >= 1.25 and (
        bool(features.get("home_back_to_back")) or bool(features.get("away_back_to_back"))
    )

    return [
        _scene("B01", "Темповый матч — предварительный фильтр", high,
               reason="historical_scoring_unavailable", markets=[_target("match_total", "over")],
               evidence={"projection": total, "baseline": baseline, "recent_total": recent, "pace_possessions_verified": False}),
        _scene("B02", "Медленный матч — предварительный фильтр", low,
               reason="historical_scoring_unavailable", markets=[_target("match_total", "under")],
               evidence={"projection": total, "baseline": baseline, "recent_total": recent, "pace_possessions_verified": False}),
        _scene("B03", "Преимущество сильной атаки", mismatch,
               reason="matchup_margin_unavailable",
               markets=[_target("home_total" if margin is None or margin > 0 else "away_total", "over"),
                        _target("handicap", "home" if margin is None or margin > 0 else "away")],
               evidence={"expected_margin": margin, "handicap_v5_publication_inactive": True}),
        _scene("B04", "Равный матч — проверка Ф+", close,
               reason="matchup_margin_unavailable",
               markets=[_target("handicap", "home"), _target("handicap", "away")],
               evidence={"expected_margin": margin, "requires_real_plus_line_check": True}),
        _scene("B05", "Аномалия одной четверти",
               None if memory_quality is None or memory_quality < .55 or not valid or not quarter_observations else best_quarter is not None,
               reason="qualified_period_history_unavailable",
               markets=[_target("match_total", "over", best_quarter[0] if best_quarter else "ANY_SEGMENT"),
                        _target("match_total", "under", best_quarter[0] if best_quarter else "ANY_SEGMENT")],
               evidence={"period_expected": quarter_observations, "segment_memory_quality": memory_quality}),
        _scene("B06", "Различие результативности половин",
               None if not halves else abs(halves["FIRST_HALF"] - halves["SECOND_HALF"]) >= 8.0,
               reason="historical_quarters_missing",
               markets=[_target("match_total", "over", "FIRST_HALF" if halves.get("FIRST_HALF", 0) >= halves.get("SECOND_HALF", 0) else "SECOND_HALF"),
                        _target("match_total", "under", "SECOND_HALF" if halves.get("FIRST_HALF", 0) >= halves.get("SECOND_HALF", 0) else "FIRST_HALF")],
               evidence={"expected_halves": halves}),
        _scene("B07", "Плотный календарь", fatigue,
               reason="rest_or_form_unavailable",
               markets=[_target("home_total" if rest is None or rest > 0 else "away_total", "over")],
               evidence={"rest_advantage_days": rest}),
    ]


def evaluate_prematch_scenarios(
    sport: str,
    *,
    features: dict[str, Any] | None = None,
    league: str = "",
    segment_memory: dict[str, Any] | None = None,
    historical_profile: dict[str, Any] | None = None,
    football_forecast: dict[str, Any] | None = None,
    market_lanes: list[dict[str, Any]] | None = None,
    football_quote: dict[str, Any] | None = None,
    qualified_picks: list[Any] | None = None,
) -> dict[str, Any]:
    """Compare predefined strategy hypotheses with real market availability.

    MATCH means only that the pattern is present; NOT that a bet is approved
    or its price contains value. Qualified picks are reported separately.
    """
    sport = str(sport or "").strip().lower()
    context = dict(features or {})
    memory = dict(segment_memory or {})
    if sport == "football":
        scenarios = _football(dict(football_forecast or {}))
    elif sport == "hockey":
        scenarios = _hockey(context, league, memory)
    elif sport == "basketball":
        scenarios = _basketball(context, league, memory, dict(historical_profile or {}))
    else:
        return {"version": VERSION, "sport": sport, "status": "UNSUPPORTED", "scenarios": []}
    offered = _quoted_options(sport, market_lanes, football_quote)
    approved = _qualified_options(qualified_picks, sport=sport)
    for s in scenarios:
        targets = s["market_targets"]
        aligned_offers = [row for row in offered if any(_match_target(t, row) for t in targets)]
        aligned_picks = [row for row in approved if any(_match_target(t, row) for t in targets)]
        s["quoted_market_sides"] = len(aligned_offers)
        s["brain_qualified_market_sides"] = len(aligned_picks)
        s["approved_examples"] = aligned_picks[:3] if s["status"] == _MATCH else []
        if s["status"] == _MATCH:
            s["verification"] = (
                "PATTERN_WITH_QUALIFIED_BRAIN_CANDIDATE" if aligned_picks
                else "PATTERN_AND_QUOTE_ONLY" if aligned_offers
                else "PATTERN_WITHOUT_QUOTED_MARKET"
            )
        else:
            s["verification"] = "NOT_ACTIONABLE"
    return {
        "version": VERSION, "sport": sport, "status": "SHADOW_ONLY",
        "calibrated": False, "publication_enabled": False,
        "sample_outcomes_not_yet_checked": True,
        "scenarios": scenarios,
        "matched_count": sum(s["status"] == _MATCH for s in scenarios),
        "qualified_pattern_count": sum(s["status"] == _MATCH and s["brain_qualified_market_sides"] > 0 for s in scenarios),
        "quoted_market_sides_total": len(offered),
    }
