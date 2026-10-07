from __future__ import annotations

import math
import os
from typing import Any

from .segment_memory import remaining_match_projection, segment_prior


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def league_goal_baseline(league: str) -> float:
    """Expected regulation goals per match; conservative league priors."""
    text = str(league or "").casefold()
    if "nhl" in text:
        return 6.10
    if "ahl" in text:
        return 5.85
    if "khl" in text:
        return 5.05
    if "vhl" in text:
        return 5.35
    if "mhl" in text or "junior" in text or "u20" in text or "u18" in text:
        return 6.20
    if "denmark" in text or "metal ligaen" in text:
        return 5.80
    return 5.60


def _poisson_probs(lam: float, cap: int = 18) -> list[float]:
    lam = _clamp(lam, 0.01, 15.0)
    probs = [math.exp(-lam)]
    for k in range(1, cap + 1):
        probs.append(probs[-1] * lam / k)
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def _side_probabilities(lam: float, line: float, direction: str) -> tuple[float, float, float]:
    probs = _poisson_probs(lam)
    win = push = loss = 0.0
    for goals, prob in enumerate(probs):
        margin = goals - line
        if direction == "under":
            margin = -margin
        if margin > 1e-9:
            win += prob
        elif abs(margin) <= 1e-9:
            push += prob
        else:
            loss += prob
    total = win + push + loss
    return win / total, push / total, loss / total


def _handicap_probabilities(lam_home: float, lam_away: float, side: str, line: float) -> tuple[float, float, float]:
    hp = _poisson_probs(lam_home, 14)
    ap = _poisson_probs(lam_away, 14)
    win = push = loss = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            p = ph * pa
            margin = (h - a + line) if side == "home" else (a - h + line)
            if margin > 1e-9:
                win += p
            elif abs(margin) <= 1e-9:
                push += p
            else:
                loss += p
    total = win + push + loss
    return win / total, push / total, loss / total


def _two_way_win_probability(lam_home: float, lam_away: float, side: str) -> float:
    hp = _poisson_probs(lam_home, 14)
    ap = _poisson_probs(lam_away, 14)
    home_reg = away_reg = tie = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            p = ph * pa
            if h > a:
                home_reg += p
            elif a > h:
                away_reg += p
            else:
                tie += p
    share = lam_home / max(0.01, lam_home + lam_away)
    home = home_reg + tie * share
    return home if side == "home" else 1.0 - home


def _decisive(win: float, push: float, loss: float) -> float:
    decisive = win + loss
    return 0.5 if decisive <= 1e-9 else win / decisive


def _scope_goal_share(scope: str) -> float:
    """Regulation scoring share for a hockey market scope."""
    scope = str(scope or "FULL_MATCH").upper()
    if scope == "PERIOD_1":
        return 0.34
    if scope in {"PERIOD_2", "PERIOD_3"}:
        return 0.33
    return 1.0


def prematch_lambdas(features: dict[str, Any], league: str) -> tuple[float, float, float]:
    """Independent team scoring means from form/venue/rest with league shrinkage."""
    baseline = league_goal_baseline(league)
    team_base = baseline / 2.0

    hgf = _num(features.get("home_gf_avg"), team_base) or team_base
    hga = _num(features.get("home_ga_avg"), team_base) or team_base
    agf = _num(features.get("away_gf_avg"), team_base) or team_base
    aga = _num(features.get("away_ga_avg"), team_base) or team_base
    vhgf = _num(features.get("home_venue_gf_avg"), hgf) or hgf
    vhga = _num(features.get("home_venue_ga_avg"), hga) or hga
    vagf = _num(features.get("away_venue_gf_avg"), agf) or agf
    vaga = _num(features.get("away_venue_ga_avg"), aga) or aga

    lam_home = 0.36 * hgf + 0.31 * aga + 0.13 * vhgf + 0.10 * vaga + 0.10 * team_base
    lam_away = 0.36 * agf + 0.31 * hga + 0.13 * vagf + 0.10 * vhga + 0.10 * team_base

    recent_total = _num(features.get("recent_total_avg"))
    venue_total = _num(features.get("venue_total_avg"))
    h2h_total = _num(features.get("h2h_total_avg"))
    total_hint = baseline
    weights = 1.0
    if recent_total is not None:
        total_hint += recent_total * 0.18
        weights += 0.18
    if venue_total is not None:
        total_hint += venue_total * 0.10
        weights += 0.10
    if h2h_total is not None:
        total_hint += h2h_total * 0.04
        weights += 0.04
    total_hint /= weights
    raw_total = max(0.2, lam_home + lam_away)
    shrink = _clamp(total_hint / raw_total, 0.82, 1.18)
    lam_home *= shrink
    lam_away *= shrink

    home_b2b = bool(features.get("home_back_to_back"))
    away_b2b = bool(features.get("away_back_to_back"))
    if home_b2b and not away_b2b:
        lam_home *= 0.96
        lam_away *= 1.02
    elif away_b2b and not home_b2b:
        lam_away *= 0.96
        lam_home *= 1.02

    rest_adv = _num(features.get("rest_advantage_days"), 0.0) or 0.0
    rest_adj = _clamp(rest_adv, -2.0, 2.0) * 0.012
    lam_home *= 1.0 + rest_adj
    lam_away *= 1.0 - rest_adj

    away_goalie_gsax = _num(features.get("away_goalie_gsax_per60"), 0.0) or 0.0
    home_goalie_gsax = _num(features.get("home_goalie_gsax_per60"), 0.0) or 0.0
    lam_home *= _clamp(1.0 - away_goalie_gsax * 0.025, 0.90, 1.10)
    lam_away *= _clamp(1.0 - home_goalie_gsax * 0.025, 0.90, 1.10)

    return _clamp(lam_home, 0.7, 5.0), _clamp(lam_away, 0.7, 5.0), baseline


def _data_quality(features: dict[str, Any]) -> float:
    hn = max(0.0, _num(features.get("home_recent_n"), 0.0) or 0.0)
    an = max(0.0, _num(features.get("away_recent_n"), 0.0) or 0.0)
    history = _clamp(min(hn, an) / 10.0, 0.0, 1.0)
    core = sum(features.get(key) is not None for key in ("home_gf_avg", "home_ga_avg", "away_gf_avg", "away_ga_avg")) / 4.0
    venue = sum(features.get(key) is not None for key in ("home_venue_gf_avg", "away_venue_gf_avg")) / 2.0
    rest = 1.0 if features.get("home_rest_days") is not None and features.get("away_rest_days") is not None else 0.0
    return _clamp(0.45 * history + 0.35 * core + 0.12 * venue + 0.08 * rest, 0.0, 1.0)


def prematch_candidate(features: dict[str, Any], league: str) -> dict[str, Any]:
    lam_home, lam_away, baseline = prematch_lambdas(features, league)
    quality = _data_quality(features)
    side_gap = abs(lam_home - lam_away)
    total_gap = abs((lam_home + lam_away) - baseline)
    rest = abs(_num(features.get("rest_advantage_days"), 0.0) or 0.0)
    evidence = min(1.0, side_gap / 1.15) * 0.50 + min(1.0, total_gap / 1.00) * 0.35 + min(1.0, rest / 2.0) * 0.15
    # Data completeness is a gate, not a reason to bet. A perfectly populated
    # history with no real separation must remain WAIT instead of becoming the
    # old 21/22-style automatic shortlist.
    score = _clamp(42.0 + quality * 8.0 + evidence * 42.0, 0.0, 87.0)
    # Completeness only permits evaluation. Shortlisting itself requires real
    # separation from the league prior / opponent / rest context.
    if quality < 0.55 or evidence < 0.50:
        state = "WAIT"
    elif evidence >= 0.62:
        state = "PASS"
    else:
        state = "BORDERLINE"
    return {
        "state": state,
        "score": round(score, 1),
        "data_quality": round(quality, 3),
        "lambda_home": round(lam_home, 3),
        "lambda_away": round(lam_away, 3),
        "league_baseline": round(baseline, 3),
        "model_separation": round(side_gap, 3),
        "total_deviation": round(total_gap, 3),
    }


def prematch_signal(lane: dict[str, Any], features: dict[str, Any], league: str) -> dict[str, Any] | None:
    family = str(lane.get("market_family") or "")
    full_lam_home, full_lam_away, baseline = prematch_lambdas(features, league)
    scope = str(lane.get("scope") or "FULL_MATCH")
    scope_share = _scope_goal_share(scope)
    lam_home = full_lam_home * scope_share
    lam_away = full_lam_away * scope_share
    scoped_baseline = baseline * scope_share
    quality = _data_quality(features)
    if quality < 0.55:
        return None
    market_over = _clamp(_num(lane.get("probability"), 0.5) or 0.5, 0.01, 0.99)
    try:
        line = float(lane.get("line") or 0.0)
    except (TypeError, ValueError):
        return None

    direction = ""
    selection_side = str(lane.get("selection_side") or lane.get("choice_key") or "")
    push = 0.0
    market_probability = 0.5
    model_probability = 0.5
    odd = 0.0

    if family in {"match_total", "home_total", "away_total"}:
        lam = lam_home + lam_away if family == "match_total" else (lam_home if family == "home_total" else lam_away)
        ow, op, ol = _side_probabilities(lam, line, "over")
        uw, up, ul = _side_probabilities(lam, line, "under")
        over_model = _decisive(ow, op, ol)
        under_model = _decisive(uw, up, ul)
        choices = [
            (over_model - market_over, "over", over_model, op, market_over, _num(lane.get("over"), 0.0) or 0.0),
            (under_model - (1.0 - market_over), "under", under_model, up, 1.0 - market_over, _num(lane.get("under"), 0.0) or 0.0),
        ]
        _, direction, model_probability, push, market_probability, odd = max(choices, key=lambda x: x[0])
    elif family == "moneyline" and selection_side in {"home", "away"}:
        model_probability = _two_way_win_probability(lam_home, lam_away, selection_side)
        market_probability = market_over
        odd = _num(lane.get("odd"), 0.0) or 0.0
        direction = selection_side
    elif family == "handicap" and selection_side in {"home", "away"}:
        win, push, loss = _handicap_probabilities(lam_home, lam_away, selection_side, line)
        model_probability = _decisive(win, push, loss)
        market_probability = market_over
        odd = _num(lane.get("odd"), 0.0) or 0.0
        direction = selection_side
    else:
        return None

    min_odd = _env_float("GOOL_MULTISPORT_MIN_ODD", 1.45)
    max_odd = _env_float("GOOL_MULTISPORT_MAX_ODD", 3.25)
    if not (min_odd <= odd <= max_odd):
        return None
    edge = model_probability - market_probability
    min_edge = 0.055 if family in {"match_total", "home_total", "away_total"} else 0.060
    if edge < min_edge or model_probability < 0.545:
        return None

    agreement = 1
    side_threshold = 0.30 * max(0.33, scope_share)
    total_threshold = 0.35 * max(0.33, scope_share)
    if abs(lam_home - lam_away) >= side_threshold or abs((lam_home + lam_away) - scoped_baseline) >= total_threshold:
        agreement += 1
    rest_adv = _num(features.get("rest_advantage_days"), 0.0) or 0.0
    if family in {"moneyline", "handicap"}:
        if (selection_side == "home" and rest_adv > 0.4) or (selection_side == "away" and rest_adv < -0.4):
            agreement += 1
    else:
        recent_total = _num(features.get("recent_total_avg"))
        scoped_recent_total = recent_total * scope_share if recent_total is not None else None
        if scoped_recent_total is not None and ((direction == "over" and scoped_recent_total > line) or (direction == "under" and scoped_recent_total < line)):
            agreement += 1
    if agreement < 2:
        return None

    push_confidence_penalty = min(0.45, max(0.0, push)) * 10.0
    raw_strength = (
        45.0
        + quality * 10.0
        + (model_probability - 0.5) * 35.0
        + edge * 70.0
        + (agreement - 1) * 3.0
    )
    strength = _clamp(
        _clamp(raw_strength, 0.0, 87.0) - push_confidence_penalty,
        0.0,
        87.0,
    )
    return {
        "phase": "PREMATCH",
        "brain_mode": "hockey_prematch_v2",
        "direction": direction,
        "selection_side": selection_side,
        "selection": str(lane.get("selection") or ""),
        "line": line,
        "odd": odd,
        "fair_probability": round(model_probability, 6),
        "model_probability": round(model_probability, 6),
        "market_probability": round(market_probability, 6),
        "push_probability": round(push, 6),
        "push_confidence_penalty": round(push_confidence_penalty, 2),
        "edge": round(edge, 6),
        "strength": round(strength, 1),
        "lambda_home": round(lam_home, 3),
        "lambda_away": round(lam_away, 3),
        "full_match_lambda_home": round(full_lam_home, 3),
        "full_match_lambda_away": round(full_lam_away, 3),
        "scope_goal_share": round(scope_share, 3),
        "league_baseline": round(scoped_baseline, 3),
        "data_quality": round(quality, 3),
        "agreement_blocks": agreement,
        "market_confirmed": edge >= min_edge,
        "metric_delta": round(edge * 100.0, 3),
        "probability_delta_pp": 0.0,
        "line_delta": 0.0,
        "moves": 0,
        "age_seconds": 0.0,
        "start": {},
        "end": dict(lane),
    }


def live_candidate_gate(brain: dict[str, Any]) -> dict[str, Any]:
    """Flashscore-only freshness gate; direction is decided only after pricing.

    A new period starts with a clean history key. Do not let two very close
    snapshots turn every period into an automatic candidate.
    """
    stats_payload = dict(brain.get("live_game_stats") or {})
    available = bool(stats_payload.get("current_segment_available"))
    points = int(brain.get("history_points") or 0)
    window = max(0.0, _num(brain.get("recent_window_seconds"), 0.0) or 0.0)
    min_points = max(2, int(_env_float("GOOL_HOCKEY_LIVE_MIN_ANALYSIS_SNAPSHOTS", 3.0)))
    min_window = max(30.0, _env_float("GOOL_HOCKEY_LIVE_MIN_ANALYSIS_SECONDS", 60.0))

    score = 38.0
    if available:
        score += 12.0
    if points >= min_points:
        score += 14.0
    elif points >= 2:
        score += 6.0
    if window >= min_window:
        score += 8.0

    score = _clamp(score, 0.0, 78.0)
    state = "PASS" if score >= 68.0 else ("BORDERLINE" if score >= 60.0 else "WAIT")
    if not available or points < min_points or window < min_window:
        state = "WAIT"
    return {
        "state": state,
        "score": round(score, 1),
        "required_snapshots": min_points,
        "required_window_seconds": round(min_window, 1),
    }


def live_signal(brain: dict[str, Any], lane: dict[str, Any]) -> dict[str, Any] | None:
    family = str(lane.get("market_family") or "")
    lane_scope = str(lane.get("scope") or "")
    current_scope = str(brain.get("scope") or "")
    segment_market = family == "match_total" and lane_scope == current_scope
    full_market = lane_scope == "FULL_MATCH" and family in {"match_total", "home_total", "away_total"}
    if not (segment_market or full_market):
        return None
    segment_memory = dict(brain.get("segment_memory") or {})
    memory_quality = _clamp(_num(segment_memory.get("quality"), 0.0) or 0.0, 0.0, 1.0)
    if full_market and memory_quality < _env_float("GOOL_HOCKEY_LIVE_FULL_MIN_MEMORY_QUALITY", 0.45):
        return None
    min_points = max(2, int(_env_float("GOOL_HOCKEY_LIVE_MIN_ANALYSIS_SNAPSHOTS", 3.0)))
    min_window = max(30.0, _env_float("GOOL_HOCKEY_LIVE_MIN_ANALYSIS_SECONDS", 60.0))
    if int(brain.get("history_points") or 0) < min_points:
        return None
    if max(0.0, _num(brain.get("recent_window_seconds"), 0.0) or 0.0) < min_window:
        return None

    try:
        line = float(lane.get("line"))
    except (TypeError, ValueError):
        return None

    # For hockey the bookmaker clock can be cumulative (1200/2400+) or stale
    # at a period boundary. The Flashscore Brain already follows the current
    # segment and its period_start_ts, so prefer that local clock and keep the
    # bookmaker clock only as a fallback.
    brain_elapsed = _num(brain.get("elapsed_seconds"))
    book_elapsed = _num(lane.get("clock_seconds"))
    if brain_elapsed is not None and 0.0 < brain_elapsed < 1200.0:
        elapsed = float(brain_elapsed)
        clock_source = "flashscore_period"
    elif book_elapsed is not None and 0.0 < book_elapsed < 1200.0:
        elapsed = float(book_elapsed)
        clock_source = "1xbet_period_fallback"
    else:
        return None
    remaining = 1200.0 - elapsed
    if elapsed < 120.0 or remaining < 75.0:
        return None

    if bool(brain.get("segment_score_verified")):
        score = list(brain.get("current_segment_score") or [0, 0])
        score_source = "flashscore_verified"
    else:
        # A FULL_MATCH lane contains the cumulative scoreboard; never feed that
        # into the current-period projection.
        score = list(
            brain.get("current_segment_score")
            if full_market
            else (lane.get("score") or brain.get("current_segment_score") or [0, 0])
        )
        score_source = "1xbet_subgame_fallback"
    try:
        current = int(score[0] or 0) + int(score[1] or 0)
    except (TypeError, ValueError, IndexError):
        return None

    league = str(lane.get("league") or brain.get("league") or "")
    match_lambda = _num(brain.get("prematch_match_lambda"), league_goal_baseline(league)) or league_goal_baseline(league)
    period_lambda = _clamp(match_lambda / 3.0, 1.15, 2.35)
    historical_segment = segment_prior(segment_memory, current_scope)
    historical_total = _num(historical_segment.get("expected_total"))
    if historical_total is not None and memory_quality >= 0.25:
        history_weight = _clamp(0.22 + memory_quality * 0.43, 0.22, 0.65)
        period_lambda = _clamp(
            period_lambda * (1.0 - history_weight) + float(historical_total) * history_weight,
            0.80,
            3.20,
        )
    else:
        history_weight = 0.0
    base_remaining = period_lambda * remaining / 1200.0

    recent_shot_rate = max(0.0, _num(brain.get("recent_shot_rate"), 0.0) or 0.0)
    shot_rate_available = bool(brain.get("shot_rate_available"))
    recent_blocked_rate = max(0.0, _num(brain.get("recent_blocked_rate"), 0.0) or 0.0)
    recent_window = max(0.0, _num(brain.get("recent_window_seconds"), 0.0) or 0.0)
    pressure_window_ready = recent_window >= min_window and shot_rate_available

    # recent_shot_rate is COMBINED shots-on-goal per minute. The old fixed
    # 1.65 baseline was too high for normal hockey and therefore pushed the
    # posterior toward UNDER in ordinary games. Anchor the pace threshold to
    # the same goal/shot assumption used by the projection instead.
    goal_per_shot = _clamp(_env_float("GOOL_HOCKEY_LIVE_GOAL_PER_SHOT", 0.08), 0.05, 0.16)
    expected_shots_per_min = _clamp(period_lambda / max(0.2, goal_per_shot * 20.0), 0.72, 1.40)
    fast_shot_rate = expected_shots_per_min * 1.18
    slow_shot_rate = expected_shots_per_min * 0.82

    shot_factor = 1.0
    if pressure_window_ready:
        shot_factor = _clamp(
            1.0
            + (recent_shot_rate - expected_shots_per_min) * 0.28
            + recent_blocked_rate * 0.018,
            0.80,
            1.25,
        )

    stats_payload = dict(brain.get("live_game_stats") or {})
    recent_penalty_delta = max(0.0, _num(brain.get("recent_penalty_delta"), 0.0) or 0.0)
    recent_pp_goal_delta = max(0.0, _num(brain.get("recent_pp_goal_delta"), 0.0) or 0.0)
    special_factor = _clamp(1.0 + recent_penalty_delta * 0.055 + recent_pp_goal_delta * 0.08, 1.0, 1.18)

    match_score = list(lane.get("match_score") or brain.get("score") or [0, 0])
    try:
        margin = abs(int(match_score[0]) - int(match_score[1]))
    except (TypeError, ValueError, IndexError):
        margin = 0
    scope = str(brain.get("scope") or "")
    score_factor = 1.0
    if scope == "PERIOD_3":
        if margin <= 1:
            score_factor = 1.12 if remaining <= 420 else 1.05
        elif margin == 2:
            score_factor = 1.07 if remaining <= 360 else 1.02
        elif margin >= 4:
            score_factor = 0.94
    elif margin >= 3:
        score_factor = 0.96

    lam_remaining = _clamp(base_remaining * shot_factor * special_factor * score_factor, 0.03, 3.8)
    total_lambda = current + lam_remaining

    remaining_projection: dict[str, Any] | None = None
    target_projection = total_lambda
    if full_market:
        remaining_projection = remaining_match_projection(
            segment_memory,
            scope=current_scope,
            match_score=list(brain.get("score") or lane.get("match_score") or [0, 0]),
            current_segment_score=list(brain.get("current_segment_score") or [0, 0]),
            current_segment_projection=total_lambda,
        )
        if remaining_projection is None:
            return None
        match_now = list(brain.get("score") or lane.get("match_score") or [0, 0])
        try:
            if family == "home_total":
                actual_now = float(match_now[0])
                target_projection = float(remaining_projection["home"])
            elif family == "away_total":
                actual_now = float(match_now[1])
                target_projection = float(remaining_projection["away"])
            else:
                actual_now = float(match_now[0]) + float(match_now[1])
                target_projection = float(remaining_projection["total"])
        except (TypeError, ValueError, IndexError):
            return None
        future_lambda = max(0.02, target_projection - actual_now)
        ow, op, ol = _side_probabilities(future_lambda, line - actual_now, "over")
        uw, up, ul = _side_probabilities(future_lambda, line - actual_now, "under")
    else:
        ow, op, ol = _side_probabilities(lam_remaining, line - current, "over")
        uw, up, ul = _side_probabilities(lam_remaining, line - current, "under")
    over_model = _decisive(ow, op, ol)
    under_model = _decisive(uw, up, ul)

    market_over = _clamp(_num(lane.get("probability"), 0.5) or 0.5, 0.01, 0.99)
    choices = [
        (over_model - market_over, "over", over_model, op, market_over, _num(lane.get("over"), 0.0) or 0.0),
        (under_model - (1.0 - market_over), "under", under_model, up, 1.0 - market_over, _num(lane.get("under"), 0.0) or 0.0),
    ]
    edge, direction, model_probability, push, market_probability, odd = max(choices, key=lambda x: x[0])

    min_odd = _env_float("GOOL_MULTISPORT_MIN_ODD", 1.45)
    max_odd = _env_float("GOOL_MULTISPORT_MAX_ODD", 3.25)
    if not (min_odd <= odd <= max_odd):
        return None
    if edge < 0.055 or model_probability < 0.565:
        return None
    stat_edge = abs(target_projection - line)
    if full_market:
        min_stat_edge = _env_float(
            "GOOL_HOCKEY_LIVE_FULL_MIN_STAT_EDGE"
            if family == "match_total"
            else "GOOL_HOCKEY_LIVE_TEAM_MIN_STAT_EDGE",
            0.80 if family == "match_total" else 0.55,
        )
        if stat_edge < min_stat_edge:
            return None
    if scope == "PERIOD_3" and direction == "under" and remaining <= 330 and margin <= 2:
        return None

    agreements = 1
    if direction == "over" and pressure_window_ready and recent_shot_rate >= fast_shot_rate:
        agreements += 1
    elif direction == "under" and pressure_window_ready and recent_shot_rate <= slow_shot_rate:
        # Require genuinely slow pace relative to the league/match scoring
        # baseline, not merely a pace below the old fixed 1.65 SOG/min number.
        agreements += 1
    if direction == "over" and recent_penalty_delta > 0:
        agreements += 1
    if scope == "PERIOD_3" and direction == "over" and margin <= 2 and remaining <= 420:
        agreements += 1
    if (
        direction == "under"
        and scope != "PERIOD_3"
        and current == 0
        and elapsed >= 360
        and pressure_window_ready
        and recent_shot_rate <= slow_shot_rate * 0.95
    ):
        agreements += 1
    historical_confirmation = bool(
        full_market
        and memory_quality >= 0.55
        and stat_edge >= (
            _env_float("GOOL_HOCKEY_LIVE_FULL_HISTORY_EDGE", 1.05)
            if family == "match_total"
            else _env_float("GOOL_HOCKEY_LIVE_TEAM_HISTORY_EDGE", 0.70)
        )
    )
    if historical_confirmation:
        agreements += 1
    if agreements < 2:
        return None

    quality = _clamp(
        0.45
        + min(0.25, int(brain.get("history_points") or 0) * 0.06)
        + (0.15 if stats_payload.get("current_segment_available") else 0.0)
        + (0.08 * memory_quality if full_market else 0.0),
        0.0,
        0.92,
    )
    push_confidence_penalty = min(0.45, max(0.0, push)) * 12.0
    raw_strength = (
        45.0
        + quality * 10.0
        + (model_probability - 0.5) * 35.0
        + edge * 70.0
        + (agreements - 1) * 3.0
    )
    strength = _clamp(
        _clamp(raw_strength, 0.0, 87.0) - push_confidence_penalty,
        0.0,
        87.0,
    )
    return {
        "brain_mode": "hockey_live_v2",
        "direction": direction,
        "line": line,
        "odd": odd,
        "fair_probability": round(model_probability, 6),
        "model_probability": round(model_probability, 6),
        "market_probability": round(market_probability, 6),
        "push_probability": round(push, 6),
        "push_confidence_penalty": round(push_confidence_penalty, 2),
        "edge": round(edge, 6),
        "metric_delta": round(edge * 100.0, 3),
        "stat_edge": round(stat_edge, 3),
        "projected_total": round(target_projection, 3),
        "current_segment_projection": round(total_lambda, 3),
        "raw_stat_projection": round(target_projection, 3),
        "segment_memory_quality": round(memory_quality, 3),
        "segment_memory": segment_memory,
        "segment_prior_total": None if historical_total is None else round(float(historical_total), 3),
        "segment_history_weight": round(history_weight, 3),
        "segment_h2h_total": (
            None
            if historical_segment.get("h2h_home_for") is None or historical_segment.get("h2h_away_for") is None
            else round(float(historical_segment["h2h_home_for"]) + float(historical_segment["h2h_away_for"]), 3)
        ),
        "segment_h2h_n": int(historical_segment.get("h2h_n") or 0),
        "remaining_match_projection": remaining_projection or {},
        "historical_confirmation": historical_confirmation,
        "current_segment_total": current,
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(remaining, 1),
        "recent_rate_per_min": round(recent_shot_rate, 3),
        "expected_shots_per_min": round(expected_shots_per_min, 3),
        "shot_rate_available": shot_rate_available,
        "fast_shot_rate": round(fast_shot_rate, 3),
        "slow_shot_rate": round(slow_shot_rate, 3),
        "recent_blocked_rate_per_min": round(recent_blocked_rate, 3),
        "probability_delta_pp": 0.0,
        "line_delta": 0.0,
        "moves": max(0, int(brain.get("history_points") or 1) - 1),
        "age_seconds": 0.0,
        "strength": round(strength, 1),
        "market_confirmed": edge >= 0.055,
        "agreement_blocks": agreements,
        "lambda_remaining": round(lam_remaining, 4),
        "prematch_match_lambda": round(match_lambda, 4),
        "score_state_factor": round(score_factor, 3),
        "shot_factor": round(shot_factor, 3),
        "special_teams_factor": round(special_factor, 3),
        "recent_penalty_delta": round(recent_penalty_delta, 3),
        "recent_pp_goal_delta": round(recent_pp_goal_delta, 3),
        "flashscore_brain_score": float(brain.get("brain_score") or 0.0),
        "flashscore_brain_state": str(brain.get("brain_state") or ""),
        "flashscore_brain_reason": str(brain.get("brain_reason") or ""),
        "projection_clock_source": clock_source,
        "segment_score_source": score_source,
        "recent_window_seconds": round(recent_window, 1),
        "start": {},
        "end": dict(lane),
    }
