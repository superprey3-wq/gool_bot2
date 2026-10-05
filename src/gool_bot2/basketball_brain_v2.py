from __future__ import annotations

import math
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    sd = max(0.01, float(sd))
    return 0.5 * (1.0 + math.erf((float(x) - float(mean)) / (sd * math.sqrt(2.0))))


def _normal_over(line: float, mean: float, sd: float) -> float:
    return _clamp(1.0 - _normal_cdf(line, mean, sd), 0.001, 0.999)


def league_profile(league: str) -> dict[str, float]:
    """Conservative league priors used only when team data is unavailable.

    quarter_total is regulation scoring for both teams in one quarter.
    quarter_possessions is possessions per team per quarter.
    """
    text = str(league or "").casefold()
    if ("nba" in text or "g league" in text) and "wnba" not in text:
        return {
            "game_total": 226.0,
            "quarter_total": 56.5,
            "quarter_possessions": 24.0,
            "sigma_total": 21.5,
            "sigma_margin": 13.0,
            "quarter_sigma": 8.7,
        }
    if "wnba" in text:
        return {
            "game_total": 163.0,
            "quarter_total": 40.8,
            "quarter_possessions": 20.0,
            "sigma_total": 18.0,
            "sigma_margin": 11.5,
            "quarter_sigma": 7.2,
        }
    if "nbl" in text and ("australia" in text or "australian" in text):
        return {
            "game_total": 181.0,
            "quarter_total": 45.3,
            "quarter_possessions": 20.5,
            "sigma_total": 19.0,
            "sigma_margin": 12.0,
            "quarter_sigma": 7.6,
        }
    if any(token in text for token in ("euroleague", "eurocup", "champions league")):
        return {
            "game_total": 164.0,
            "quarter_total": 41.0,
            "quarter_possessions": 19.2,
            "sigma_total": 18.0,
            "sigma_margin": 11.5,
            "quarter_sigma": 7.1,
        }
    # Most Flashscore basketball outside NBA is played under FIBA-like timing.
    return {
        "game_total": 166.0,
        "quarter_total": 41.5,
        "quarter_possessions": 19.5,
        "sigma_total": 18.5,
        "sigma_margin": 12.0,
        "quarter_sigma": 7.3,
    }


def _data_quality(features: dict[str, Any]) -> float:
    hn = max(0.0, _num(features.get("home_recent_n"), 0.0) or 0.0)
    an = max(0.0, _num(features.get("away_recent_n"), 0.0) or 0.0)
    history = _clamp(min(hn, an) / 10.0, 0.0, 1.0)
    core = sum(
        features.get(key) is not None
        for key in ("home_gf_avg", "home_ga_avg", "away_gf_avg", "away_ga_avg")
    ) / 4.0
    venue = sum(
        features.get(key) is not None
        for key in ("home_venue_gf_avg", "away_venue_gf_avg")
    ) / 2.0
    rest = 1.0 if features.get("home_rest_days") is not None and features.get("away_rest_days") is not None else 0.0
    return _clamp(history * 0.48 + core * 0.34 + venue * 0.10 + rest * 0.08, 0.0, 1.0)


def prematch_means(features: dict[str, Any], league: str) -> tuple[float, float, dict[str, float]]:
    profile = league_profile(league)
    team_base = profile["game_total"] / 2.0
    hgf = _num(features.get("home_gf_avg"), team_base) or team_base
    hga = _num(features.get("home_ga_avg"), team_base) or team_base
    agf = _num(features.get("away_gf_avg"), team_base) or team_base
    aga = _num(features.get("away_ga_avg"), team_base) or team_base
    vhgf = _num(features.get("home_venue_gf_avg"), hgf) or hgf
    vagf = _num(features.get("away_venue_gf_avg"), agf) or agf

    # Offence/defence matchup + venue, shrunk toward the league scoring baseline.
    mu_home = 0.38 * hgf + 0.30 * aga + 0.12 * vhgf + 0.20 * team_base
    mu_away = 0.38 * agf + 0.30 * hga + 0.12 * vagf + 0.20 * team_base

    recent_total = _num(features.get("recent_total_avg"))
    venue_total = _num(features.get("venue_total_avg"))
    total_anchor = profile["game_total"]
    w = 1.0
    if recent_total is not None:
        total_anchor += recent_total * 0.24
        w += 0.24
    if venue_total is not None:
        total_anchor += venue_total * 0.12
        w += 0.12
    total_anchor /= w
    raw_total = max(1.0, mu_home + mu_away)
    scale = _clamp(total_anchor / raw_total, 0.90, 1.10)
    mu_home *= scale
    mu_away *= scale

    rest_adv = _num(features.get("rest_advantage_days"), 0.0) or 0.0
    rest_adj = _clamp(rest_adv, -2.0, 2.0) * 0.006
    mu_home *= 1.0 + rest_adj
    mu_away *= 1.0 - rest_adj
    return _clamp(mu_home, 45.0, 145.0), _clamp(mu_away, 45.0, 145.0), profile


def prematch_candidate(features: dict[str, Any], league: str) -> dict[str, Any]:
    mu_home, mu_away, profile = prematch_means(features, league)
    quality = _data_quality(features)
    total_gap = abs((mu_home + mu_away) - profile["game_total"])
    margin_gap = abs(mu_home - mu_away)
    evidence = min(1.0, total_gap / 15.0) * 0.48 + min(1.0, margin_gap / 14.0) * 0.52

    # History completeness is a gate, never enough by itself to pass.
    score = _clamp(43.0 + quality * 10.0 + evidence * 34.0, 0.0, 88.0)
    if quality < 0.45:
        state = "WAIT"
    elif score >= 69.0:
        state = "PASS"
    elif score >= 61.0:
        state = "BORDERLINE"
    else:
        state = "WAIT"
    return {
        "state": state,
        "score": round(score, 1),
        "brain_mode": "basketball_prematch_v2",
        "data_quality": round(quality, 3),
        "scope_factor": factor,
        "mu_home": round(mu_home, 2),
        "mu_away": round(mu_away, 2),
        "mu_total": round(mu_home + mu_away, 2),
        "mu_margin": round(mu_home - mu_away, 2),
        "league_baseline": round(profile["game_total"], 2),
    }


def _scope_factor(scope: str) -> float:
    raw = str(scope or "FULL_MATCH")
    if raw.startswith("QUARTER_"):
        return 0.25
    if raw in {"FIRST_HALF", "SECOND_HALF"}:
        return 0.50
    return 1.0


def prematch_signal(lane: dict[str, Any], features: dict[str, Any], league: str) -> dict[str, Any] | None:
    family = str(lane.get("market_family") or "")
    scope = str(lane.get("scope") or "FULL_MATCH")
    factor = _scope_factor(scope)
    mu_home_full, mu_away_full, profile = prematch_means(features, league)
    mu_home, mu_away = mu_home_full * factor, mu_away_full * factor
    variance_inflation = 1.12 if factor == 0.25 else (1.06 if factor == 0.50 else 1.0)
    sigma_total = profile["sigma_total"] * math.sqrt(factor) * variance_inflation
    sigma_margin = profile["sigma_margin"] * math.sqrt(factor) * variance_inflation
    quality = _data_quality(features)
    if quality < 0.45:
        return None
    try:
        line = float(lane.get("line") or 0.0)
        market_p = _clamp(float(lane.get("probability") or 0.5), 0.01, 0.99)
    except (TypeError, ValueError):
        return None

    direction = ""
    selection_side = str(lane.get("selection_side") or lane.get("choice_key") or "")
    model_p = 0.5
    odd = 0.0
    if family == "match_total":
        over_p = _normal_over(line, mu_home + mu_away, sigma_total)
        under_p = 1.0 - over_p
        candidates = [
            (over_p - market_p, "over", over_p, market_p, _num(lane.get("over"), 0.0) or 0.0),
            (under_p - (1.0 - market_p), "under", under_p, 1.0 - market_p, _num(lane.get("under"), 0.0) or 0.0),
        ]
        _, direction, model_p, market_p, odd = max(candidates, key=lambda x: x[0])
    elif family in {"home_total", "away_total"}:
        mu = mu_home if family == "home_total" else mu_away
        sd = sigma_total / math.sqrt(2.0)
        over_p = _normal_over(line, mu, sd)
        under_p = 1.0 - over_p
        candidates = [
            (over_p - market_p, "over", over_p, market_p, _num(lane.get("over"), 0.0) or 0.0),
            (under_p - (1.0 - market_p), "under", under_p, 1.0 - market_p, _num(lane.get("under"), 0.0) or 0.0),
        ]
        _, direction, model_p, market_p, odd = max(candidates, key=lambda x: x[0])
    elif family == "moneyline" and selection_side in {"home", "away"}:
        home_p = _normal_over(0.0, mu_home - mu_away, sigma_margin)
        model_p = home_p if selection_side == "home" else 1.0 - home_p
        direction = selection_side
        odd = _num(lane.get("odd"), 0.0) or 0.0
    elif family == "handicap" and selection_side in {"home", "away"}:
        margin = mu_home - mu_away
        if selection_side == "home":
            model_p = _normal_over(0.0, margin + line, sigma_margin)
        else:
            model_p = _normal_over(0.0, -margin + line, sigma_margin)
        direction = selection_side
        odd = _num(lane.get("odd"), 0.0) or 0.0
    else:
        return None

    if not (1.20 < odd < 6.0):
        return None
    edge = model_p - market_p
    if edge < 0.055 or model_p < 0.55:
        return None

    agreement = 1
    recent_total = _num(features.get("recent_total_avg"))
    if family in {"match_total", "home_total", "away_total"}:
        model_mean = mu_home + mu_away if family == "match_total" else (mu_home if family == "home_total" else mu_away)
        scoped_recent_total = recent_total * factor if recent_total is not None else None
        if scoped_recent_total is not None and family == "match_total":
            if (direction == "over" and scoped_recent_total > line) or (direction == "under" and scoped_recent_total < line):
                agreement += 1
        gap_floor = (7.0 if family == "match_total" else 4.0) * math.sqrt(factor)
        if abs(model_mean - line) >= gap_floor:
            agreement += 1
    else:
        margin = mu_home - mu_away
        if (selection_side == "home" and margin >= 3.0) or (selection_side == "away" and margin <= -3.0):
            agreement += 1
        rest = _num(features.get("rest_advantage_days"), 0.0) or 0.0
        if (selection_side == "home" and rest > 0.5) or (selection_side == "away" and rest < -0.5):
            agreement += 1
    if agreement < 2:
        return None

    strength = _clamp(
        49.0
        + quality * 13.0
        + min(0.20, max(0.0, model_p - 0.5)) * 42.0
        + min(0.18, max(0.0, edge)) * 65.0
        + (agreement - 1) * 3.0,
        0.0,
        88.0,
    )
    return {
        "phase": "PREMATCH",
        "brain_mode": "basketball_prematch_v2",
        "direction": direction,
        "selection_side": selection_side,
        "selection": str(lane.get("selection") or ""),
        "line": line,
        "odd": odd,
        "fair_probability": round(model_p, 6),
        "model_probability": round(model_p, 6),
        "market_probability": round(market_p, 6),
        "edge": round(edge, 6),
        "strength": round(strength, 1),
        "mu_home": round(mu_home, 2),
        "mu_away": round(mu_away, 2),
        "mu_total": round(mu_home + mu_away, 2),
        "data_quality": round(quality, 3),
        "agreement_blocks": agreement,
        "market_confirmed": edge >= 0.055,
        "metric_delta": round(edge * 100.0, 3),
        "probability_delta_pp": 0.0,
        "line_delta": 0.0,
        "moves": 0,
        "age_seconds": 0.0,
        "start": {},
        "end": dict(lane),
    }


def _pair(d: dict[str, Any], key: str) -> tuple[float, float] | None:
    row = list(d.get(key) or [])
    if len(row) < 2:
        return None
    try:
        return max(0.0, float(row[0])), max(0.0, float(row[1]))
    except (TypeError, ValueError):
        return None


def _attempt_pair(stats: dict[str, Any], attempts: dict[str, Any], base: str) -> tuple[float, float] | None:
    direct = _pair(attempts, base)
    if direct is not None:
        return direct
    for key in (f"{base}_attempts", f"{base}_attempt", f"{base}_attempted"):
        direct = _pair(stats, key)
        if direct is not None:
            return direct
    return None


def _possessions_from(stats: dict[str, Any], attempts: dict[str, Any]) -> tuple[float, dict[str, float]] | None:
    fga = _attempt_pair(stats, attempts, "field_goals")
    if fga is None:
        twos = _attempt_pair(stats, attempts, "two_point_field_goals")
        threes = _attempt_pair(stats, attempts, "three_point_field_goals")
        if twos is not None and threes is not None:
            fga = (twos[0] + threes[0], twos[1] + threes[1])
    fta = _attempt_pair(stats, attempts, "free_throws") or (0.0, 0.0)
    oreb = _pair(stats, "offensive_rebounds") or (0.0, 0.0)
    tov = _pair(stats, "turnovers") or (0.0, 0.0)
    if fga is None:
        return None
    home = max(0.0, fga[0] + 0.44 * fta[0] - oreb[0] + tov[0])
    away = max(0.0, fga[1] + 0.44 * fta[1] - oreb[1] + tov[1])
    poss = (home + away) / 2.0
    return poss, {
        "home_poss": home,
        "away_poss": away,
        "fga": fga[0] + fga[1],
        "fta": fta[0] + fta[1],
        "oreb": oreb[0] + oreb[1],
        "tov": tov[0] + tov[1],
    }


def _four_factors(stats: dict[str, Any], attempts: dict[str, Any]) -> dict[str, float | None]:
    fga = _attempt_pair(stats, attempts, "field_goals")
    fgm = _pair(stats, "field_goals")
    if fgm is None:
        twom = _pair(stats, "two_point_field_goals")
        threem = _pair(stats, "three_point_field_goals")
        if twom is not None and threem is not None:
            fgm = (twom[0] + threem[0], twom[1] + threem[1])
    three_a = _attempt_pair(stats, attempts, "three_point_field_goals")
    three_m = _pair(stats, "three_point_field_goals")
    fta = _attempt_pair(stats, attempts, "free_throws")
    oreb = _pair(stats, "offensive_rebounds")
    dreb = _pair(stats, "defensive_rebounds")
    tov = _pair(stats, "turnovers")

    out: dict[str, float | None] = {"efg": None, "tov_rate": None, "orb_rate": None, "ft_rate": None}
    if fga is not None and fgm is not None and sum(fga) > 0:
        threes_made = sum(three_m) if three_m is not None else 0.0
        out["efg"] = (sum(fgm) + 0.5 * threes_made) / sum(fga)
    poss = _possessions_from(stats, attempts)
    if poss is not None and poss[0] > 0 and tov is not None:
        out["tov_rate"] = sum(tov) / max(1.0, 2.0 * poss[0])
    if oreb is not None and dreb is not None:
        chances = sum(oreb) + sum(dreb)
        if chances > 0:
            out["orb_rate"] = sum(oreb) / chances
    if fga is not None and fta is not None and sum(fga) > 0:
        out["ft_rate"] = sum(fta) / sum(fga)
    return out


def recent_possession_metrics(
    first_payload: dict[str, Any],
    current_payload: dict[str, Any],
    age_seconds: float,
) -> dict[str, float]:
    """Estimate fresh possession tempo from two Flashscore stat snapshots."""
    age = max(1.0, float(age_seconds or 0.0))
    first_mode = str(first_payload.get("stats_mode") or "")
    current_mode = str(current_payload.get("stats_mode") or "")
    if first_mode and current_mode and first_mode != current_mode:
        return {}

    first_stats = dict(first_payload.get("segment_stats") or {})
    first_attempts = dict(first_payload.get("segment_attempts") or {})
    current_stats = dict(current_payload.get("segment_stats") or {})
    current_attempts = dict(current_payload.get("segment_attempts") or {})
    first_poss = _possessions_from(first_stats, first_attempts)
    current_poss = _possessions_from(current_stats, current_attempts)
    if first_poss is None or current_poss is None:
        return {}

    delta = max(0.0, current_poss[0] - first_poss[0])
    return {
        "recent_possessions": round(delta, 4),
        "recent_possessions_per_min": round(delta * 60.0 / age, 4),
    }


def live_candidate_gate(brain: dict[str, Any]) -> dict[str, Any]:
    stats_payload = dict(brain.get("live_game_stats") or {})
    available = bool(stats_payload.get("current_segment_available"))
    points = int(brain.get("history_points") or 0)
    window = max(0.0, _num(brain.get("recent_window_seconds"), 0.0) or 0.0)
    recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
    recent_poss_rate = max(0.0, _num(brain.get("recent_possessions_per_min"), 0.0) or 0.0)

    score = 40.0
    if available:
        score += 12.0
    if points >= 2:
        score += 9.0
    if points >= 3:
        score += 4.0
    if window >= 25.0:
        score += 5.0
    if recent_poss_rate > 0:
        score += 7.0
    elif recent_score_rate > 0:
        score += 4.0
    state = "PASS" if score >= 69.0 else ("BORDERLINE" if score >= 60.0 else "WAIT")
    if not available or points < 2 or bool(brain.get("break_transition")):
        state = "WAIT"
    return {"state": state, "score": round(_clamp(score, 0.0, 82.0), 1)}


def live_signal(brain: dict[str, Any], lane: dict[str, Any]) -> dict[str, Any] | None:
    if str(brain.get("brain_state") or "") not in {"PASS", "BORDERLINE"}:
        return None
    if str(lane.get("market_family") or "") != "match_total":
        return None
    if str(lane.get("scope") or "") != str(brain.get("scope") or ""):
        return None
    if bool(brain.get("break_transition")) or int(brain.get("history_points") or 0) < 2:
        return None
    try:
        elapsed = float(lane.get("clock_seconds"))
        line = float(lane.get("line"))
    except (TypeError, ValueError):
        return None
    duration = 720.0 if ("nba" in str(lane.get("league") or brain.get("league") or "").casefold() or "g league" in str(lane.get("league") or brain.get("league") or "").casefold()) and "wnba" not in str(lane.get("league") or brain.get("league") or "").casefold() else 600.0
    if elapsed <= 0 or elapsed >= duration:
        return None
    remaining = duration - elapsed
    if elapsed < 45.0 or remaining < 35.0:
        return None

    score = list(lane.get("score") or brain.get("current_segment_score") or [0, 0])
    try:
        current = max(0.0, float(score[0])) + max(0.0, float(score[1]))
    except (TypeError, ValueError, IndexError):
        return None

    league = str(lane.get("league") or brain.get("league") or "")
    profile = league_profile(league)
    prior_total = profile["quarter_total"]
    prior_possessions = profile["quarter_possessions"]
    prior_ppp_pair = prior_total / max(1.0, prior_possessions)
    prior_poss_per_min = prior_possessions / (duration / 60.0)

    payload = dict(brain.get("live_game_stats") or {})
    stats = dict(payload.get("segment_stats") or {})
    attempts = dict(payload.get("segment_attempts") or {})
    stats_mode = str(payload.get("stats_mode") or "")
    poss_info = _possessions_from(stats, attempts) if stats_mode == "direct_segment" else None

    data_quality = 0.45
    possession_source = "points_clock_fallback"
    observed_poss = None
    observed_ppp_pair = None
    poss_per_min = None
    factors = _four_factors(stats, attempts) if stats_mode == "direct_segment" else {}

    if poss_info is not None and poss_info[0] >= 2.0:
        observed_poss = poss_info[0]
        poss_per_min = observed_poss / max(0.5, elapsed / 60.0)
        observed_ppp_pair = current / max(1.0, observed_poss)
        possession_source = "flashscore_current_quarter"
        data_quality = 0.85
    else:
        recent_poss_rate = max(0.0, _num(brain.get("recent_possessions_per_min"), 0.0) or 0.0)
        recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
        if recent_poss_rate > 0:
            poss_per_min = recent_poss_rate
            recent_ppp = recent_score_rate / max(0.20, recent_poss_rate)
            observed_ppp_pair = recent_ppp if recent_score_rate > 0 else prior_ppp_pair
            observed_poss = max(1.0, recent_poss_rate * max(0.5, (_num(brain.get("recent_window_seconds"), 60.0) or 60.0) / 60.0))
            possession_source = "flashscore_recent_delta"
            data_quality = 0.70

    if poss_per_min is None:
        observed_rate = current / max(0.5, elapsed / 60.0)
        raw_pace_total = current * duration / max(1.0, elapsed)
        observed_weight = _clamp(elapsed / duration, 0.15, 0.68)
        projection = prior_total * (1.0 - observed_weight) + raw_pace_total * observed_weight
        poss_per_min = prior_poss_per_min
        posterior_ppp = prior_ppp_pair
        remaining_poss = prior_poss_per_min * remaining / 60.0
    else:
        # Shrink possession tempo and scoring efficiency separately.
        tempo_weight = _clamp(elapsed / duration, 0.18, 0.78)
        posterior_poss_rate = prior_poss_per_min * (1.0 - tempo_weight) + poss_per_min * tempo_weight
        eff_n = max(1.0, observed_poss or 1.0)
        eff_weight = eff_n / (eff_n + 12.0)
        posterior_ppp = prior_ppp_pair * (1.0 - eff_weight) + (observed_ppp_pair or prior_ppp_pair) * eff_weight
        posterior_ppp = _clamp(posterior_ppp, prior_ppp_pair * 0.70, prior_ppp_pair * 1.30)
        remaining_poss = max(0.0, posterior_poss_rate * remaining / 60.0)
        projection = current + remaining_poss * posterior_ppp

    projection = max(current, projection)
    sigma = max(3.2, profile["quarter_sigma"] * math.sqrt(max(0.25, remaining / duration)))
    over_model = _normal_over(line, projection, sigma)
    under_model = 1.0 - over_model
    market_over = _clamp(_num(lane.get("probability"), 0.5) or 0.5, 0.01, 0.99)
    choices = [
        (over_model - market_over, "over", over_model, market_over, _num(lane.get("over"), 0.0) or 0.0),
        (under_model - (1.0 - market_over), "under", under_model, 1.0 - market_over, _num(lane.get("under"), 0.0) or 0.0),
    ]
    edge, direction, model_p, market_p, odd = max(choices, key=lambda x: x[0])

    if not (1.20 < odd < 6.0):
        return None
    if edge < 0.055 or model_p < 0.56:
        return None

    scope = str(brain.get("scope") or "")
    match_score = list(lane.get("match_score") or brain.get("score") or [0, 0])
    try:
        match_margin = abs(int(match_score[0]) - int(match_score[1]))
    except (TypeError, ValueError, IndexError):
        match_margin = 99
    fouls = _pair(stats, "fouls")
    foul_total = sum(fouls) if fouls is not None else 0.0

    # Q4 endgame: a close score + foul pressure is toxic for UNDER.
    if scope == "QUARTER_4" and direction == "under":
        if remaining <= 150.0 and match_margin <= 8:
            return None
        if remaining <= 210.0 and match_margin <= 10 and foul_total >= 6:
            return None

    agreement = 1
    if possession_source != "points_clock_fallback":
        if direction == "over" and poss_per_min >= prior_poss_per_min * 1.06:
            agreement += 1
        elif direction == "under" and poss_per_min <= prior_poss_per_min * 0.94:
            agreement += 1
    if factors:
        efg = factors.get("efg")
        ft_rate = factors.get("ft_rate")
        tov_rate = factors.get("tov_rate")
        if direction == "over" and ((efg is not None and efg >= 0.56) or (ft_rate is not None and ft_rate >= 0.30)):
            agreement += 1
        if direction == "under" and ((efg is not None and efg <= 0.47) or (tov_rate is not None and tov_rate >= 0.16)):
            agreement += 1
    if abs(projection - line) >= 3.0:
        agreement += 1
    if agreement < 2:
        return None

    strength = _clamp(
        49.0
        + data_quality * 13.0
        + min(0.20, max(0.0, model_p - 0.5)) * 42.0
        + min(0.18, max(0.0, edge)) * 65.0
        + (agreement - 1) * 3.0,
        0.0,
        88.0,
    )
    return {
        "brain_mode": "basketball_live_v2",
        "direction": direction,
        "line": line,
        "odd": odd,
        "fair_probability": round(model_p, 6),
        "model_probability": round(model_p, 6),
        "market_probability": round(market_p, 6),
        "edge": round(edge, 6),
        "strength": round(strength, 1),
        "projected_total": round(projection, 2),
        "raw_stat_projection": round(projection, 2),
        "stat_edge": round(abs(projection - line), 2),
        "current_segment_total": round(current, 1),
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(remaining, 1),
        "recent_rate_per_min": round(max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0), 3),
        "possessions_per_min": round(poss_per_min, 3),
        "projected_remaining_possessions": round(remaining_poss, 2),
        "posterior_ppp_pair": round(posterior_ppp, 3),
        "possession_source": possession_source,
        "data_quality": round(data_quality, 3),
        "agreement_blocks": agreement,
        "four_factors": factors,
        "market_confirmed": edge >= 0.055,
        "probability_delta_pp": 0.0,
        "line_delta": 0.0,
        "moves": max(0, int(brain.get("history_points") or 1) - 1),
        "age_seconds": float(brain.get("recent_window_seconds") or 0.0),
        "start": {},
        "end": dict(lane),
    }
