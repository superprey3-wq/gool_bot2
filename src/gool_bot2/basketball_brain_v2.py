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


def prematch_signal(
    lane: dict[str, Any],
    features: dict[str, Any],
    league: str,
    segment_memory: dict[str, Any] | None = None,
    *,
    odds_range: tuple[float, float] | None = None,
) -> dict[str, Any] | None:
    family = str(lane.get("market_family") or "")
    scope = str(lane.get("scope") or "FULL_MATCH")
    factor = _scope_factor(scope)
    mu_home_full, mu_away_full, profile = prematch_means(features, league)
    mu_home, mu_away = mu_home_full * factor, mu_away_full * factor

    memory = dict(segment_memory or {})
    memory_quality = _clamp(_num(memory.get("quality"), 0.0) or 0.0, 0.0, 1.0)
    memory_expected_home = memory_expected_away = None
    if scope.startswith("QUARTER_"):
        item = segment_prior(memory, scope)
        memory_expected_home = _num(item.get("expected_home"))
        memory_expected_away = _num(item.get("expected_away"))
    elif scope in {"FIRST_HALF", "SECOND_HALF"}:
        parts = ("QUARTER_1", "QUARTER_2") if scope == "FIRST_HALF" else ("QUARTER_3", "QUARTER_4")
        home_parts = [_num(segment_prior(memory, part).get("expected_home")) for part in parts]
        away_parts = [_num(segment_prior(memory, part).get("expected_away")) for part in parts]
        if all(value is not None for value in home_parts):
            memory_expected_home = sum(float(value) for value in home_parts if value is not None)
        if all(value is not None for value in away_parts):
            memory_expected_away = sum(float(value) for value in away_parts if value is not None)
    if (
        memory_quality >= 0.25
        and memory_expected_home is not None
        and memory_expected_away is not None
    ):
        # Exact Q/H team history is a specific prior, but remains sample-capped.
        memory_weight = _clamp(0.18 + memory_quality * 0.44, 0.18, 0.60)
        mu_home = mu_home * (1.0 - memory_weight) + float(memory_expected_home) * memory_weight
        mu_away = mu_away * (1.0 - memory_weight) + float(memory_expected_away) * memory_weight
    else:
        memory_weight = 0.0

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
        if odds_range is not None:
            candidates = [choice for choice in candidates if odds_range[0] <= choice[-1] <= odds_range[1]]
            if not candidates:
                return None
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
        if odds_range is not None:
            candidates = [choice for choice in candidates if odds_range[0] <= choice[-1] <= odds_range[1]]
            if not candidates:
                return None
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

    min_odd, max_odd = odds_range if odds_range is not None else (
        _env_float("GOOL_MULTISPORT_MIN_ODD", 1.50),
        _env_float("GOOL_MULTISPORT_MAX_ODD", 3.25),
    )
    if not (min_odd <= odd <= max_odd):
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
        48.0
        + quality * 12.0
        + min(0.20, max(0.0, model_p - 0.5)) * 45.0
        + min(0.20, max(0.0, edge)) * 60.0
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
        "scope_factor": factor,
        "mu_home": round(mu_home, 2),
        "mu_away": round(mu_away, 2),
        "mu_total": round(mu_home + mu_away, 2),
        "data_quality": round(quality, 3),
        "segment_memory_quality": round(memory_quality, 3),
        "segment_memory_weight": round(memory_weight, 3),
        "segment_prior_total": (
            None
            if memory_expected_home is None or memory_expected_away is None
            else round(float(memory_expected_home) + float(memory_expected_away), 2)
        ),
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


def live_quarter_context_assist(
    brain: dict[str, Any],
    profile: dict[str, float],
) -> dict[str, Any]:
    """Soft quarter-history prior for the live total model.

    The layer only nudges the pre-live quarter prior. Live possession/PPP data
    still dominates as the quarter progresses.
    """
    scope = str(brain.get("scope") or "")
    base = float(profile.get("quarter_total") or 41.5)
    scale = max(0.75, min(1.50, base / 41.5))
    parts = [
        row for row in (brain.get("score_parts") or [])
        if isinstance(row, (list, tuple)) and len(row) >= 2
    ]

    out: dict[str, Any] = {
        "active": False,
        "scope": scope,
        "base_prior_total": round(base, 3),
        "adjusted_prior_total": round(base, 3),
        "prior_adjustment": 0.0,
        "q4_baseline_adjustment": 0.0,
        "game_state_adjustment": 0.0,
        "mean_reversion_adjustment": 0.0,
        "previous_quarter_total": None,
        "entering_q4_margin": None,
        "diagnostic_q3_split_winners": {
            "active": False,
            "q1_winner": None,
            "q2_winner": None,
            "watch_side": None,
            "audit_prior": "12/16",
        },
        "reasons": [],
    }

    quarter_map = {
        "QUARTER_1": 1,
        "QUARTER_2": 2,
        "QUARTER_3": 3,
        "QUARTER_4": 4,
    }
    quarter = quarter_map.get(scope)
    if quarter is None:
        return out

    reasons: list[str] = []
    adjustment = 0.0
    q4_adj = 0.0
    state_adj = 0.0
    reversion_adj = 0.0

    # Do not impose an unconditional Q4 UNDER prior. That old one-day rule
    # systematically pulled every fourth-quarter projection downward before any
    # live evidence was considered. Q4 context is now state-driven only: close
    # games may score more because of fouls; comfortable/blowout games may slow.
    if quarter == 4:
        q4_adj = 0.0

        if len(parts) >= 3:
            try:
                home3 = sum(float(parts[i][0]) for i in range(3))
                away3 = sum(float(parts[i][1]) for i in range(3))
                entering_margin = abs(home3 - away3)
                out["entering_q4_margin"] = round(entering_margin, 2)
                if entering_margin <= 5.0:
                    state_adj = 1.2 * scale
                    reasons.append("close game entering Q4")
                elif entering_margin <= 19.0:
                    state_adj = -0.8 * scale
                    reasons.append("comfortable Q4 margin")
                else:
                    state_adj = -1.3 * scale
                    reasons.append("Q4 blowout/garbage-time risk")
                adjustment += state_adj
            except (TypeError, ValueError, IndexError):
                pass

    # Mean reversion: a very hot quarter usually cools next; a very cold one
    # usually rebounds. Thresholds are league-relative through quarter baseline.
    prev_index = quarter - 2
    if prev_index >= 0 and len(parts) > prev_index:
        try:
            prev_total = float(parts[prev_index][0]) + float(parts[prev_index][1])
            out["previous_quarter_total"] = round(prev_total, 2)
            deviation = prev_total - base
            trigger = 5.0 * scale
            if abs(deviation) >= trigger:
                raw = -0.18 * deviation
                cap = 2.5 * scale
                if raw >= 0:
                    reversion_adj = min(cap, max(0.8 * scale, raw))
                    reasons.append("cold previous quarter -> mean-reversion up")
                else:
                    reversion_adj = max(-cap, min(-0.8 * scale, raw))
                    reasons.append("hot previous quarter -> mean-reversion down")
                adjustment += reversion_adj
        except (TypeError, ValueError, IndexError):
            pass

    # Interesting one-day pattern only: when Q1/Q2 winners split, Q3 winner
    # matched the Q1 winner in 12/16. Log it, never move the total projection.
    if quarter == 3 and len(parts) >= 2:
        try:
            q1m = float(parts[0][0]) - float(parts[0][1])
            q2m = float(parts[1][0]) - float(parts[1][1])
            if q1m != 0 and q2m != 0 and (q1m > 0) != (q2m > 0):
                q1_winner = "home" if q1m > 0 else "away"
                q2_winner = "home" if q2m > 0 else "away"
                out["diagnostic_q3_split_winners"] = {
                    "active": True,
                    "q1_winner": q1_winner,
                    "q2_winner": q2_winner,
                    "watch_side": q1_winner,
                    "audit_prior": "12/16",
                }
        except (TypeError, ValueError, IndexError):
            pass

    max_adjustment = 4.0 * scale
    adjustment = _clamp(adjustment, -max_adjustment, max_adjustment)
    adjusted = max(1.0, base + adjustment)
    out.update({
        "active": bool(reasons) or bool(out["diagnostic_q3_split_winners"]["active"]),
        "adjusted_prior_total": round(adjusted, 3),
        "prior_adjustment": round(adjustment, 3),
        "q4_baseline_adjustment": round(q4_adj, 3),
        "game_state_adjustment": round(state_adj, 3),
        "mean_reversion_adjustment": round(reversion_adj, 3),
        "reasons": reasons,
    })
    return out


def q3_rebound_assist(
    brain: dict[str, Any],
    *,
    elapsed_seconds: float | None = None,
    current_segment_score: list[int] | tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Soft Q3 context layer; never creates a signal by itself.

    It detects the pattern seen in the daily audit: one team lost both Q1 and
    Q2, especially by a meaningful halftime deficit, then becomes much more
    competitive in Q3. The layer may add one agreement block to an already
    valid OVER model when live pace independently agrees.
    """
    out: dict[str, Any] = {
        "active": False,
        "applied": False,
        "stage": "none",
        "trailing_side": None,
        "q1_loss": 0.0,
        "q2_loss": 0.0,
        "halftime_deficit": 0.0,
        "q3_margin": None,
        "margin_improvement": 0.0,
        "audit_prior": {
            "same_winner_q1_q2_then_other_q3": "5/20",
            "halftime_deficit_10plus_then_q3_within3_or_better": "7/16",
        },
    }
    if str(brain.get("scope") or "") != "QUARTER_3":
        return out

    parts = [
        row for row in (brain.get("score_parts") or [])
        if isinstance(row, (list, tuple)) and len(row) >= 2
    ]
    if len(parts) < 2:
        return out

    try:
        q1h, q1a = float(parts[0][0]), float(parts[0][1])
        q2h, q2a = float(parts[1][0]), float(parts[1][1])
    except (TypeError, ValueError, IndexError):
        return out

    q1_home_margin = q1h - q1a
    q2_home_margin = q2h - q2a
    if q1_home_margin < 0 and q2_home_margin < 0:
        trailing_side = "home"
        q1_loss, q2_loss = -q1_home_margin, -q2_home_margin
    elif q1_home_margin > 0 and q2_home_margin > 0:
        trailing_side = "away"
        q1_loss, q2_loss = q1_home_margin, q2_home_margin
    else:
        return out

    halftime_deficit = q1_loss + q2_loss
    average_quarter_loss = (q1_loss + q2_loss) / 2.0
    severe = halftime_deficit >= 10.0 or (q1_loss >= 5.0 and q2_loss >= 5.0)
    if not severe:
        return out

    q3_score = current_segment_score
    if q3_score is None and len(parts) >= 3:
        q3_score = parts[2]
    try:
        q3h, q3a = float(q3_score[0]), float(q3_score[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        q3h = q3a = 0.0

    q3_margin = (q3h - q3a) if trailing_side == "home" else (q3a - q3h)
    improvement = q3_margin + average_quarter_loss
    elapsed = max(0.0, float(elapsed_seconds or brain.get("elapsed_seconds") or 0.0))

    if elapsed < 90.0:
        stage = "watch"
    elif q3_margin > 0.0 and improvement >= 5.0:
        stage = "reversal"
    elif q3_margin >= -3.0 and improvement >= 5.0:
        stage = "close"
    elif improvement >= 5.0:
        stage = "improving"
    else:
        stage = "watch"

    return {
        **out,
        "active": True,
        "stage": stage,
        "trailing_side": trailing_side,
        "q1_loss": round(q1_loss, 2),
        "q2_loss": round(q2_loss, 2),
        "halftime_deficit": round(halftime_deficit, 2),
        "q3_margin": round(q3_margin, 2),
        "margin_improvement": round(improvement, 2),
    }


def _live_window_readiness(brain: dict[str, Any]) -> dict[str, Any]:
    points = int(brain.get("history_points") or 0)
    window = max(0.0, _num(brain.get("recent_window_seconds"), 0.0) or 0.0)
    min_points = max(2, int(_env_float("GOOL_BASKETBALL_LIVE_MIN_ANALYSIS_SNAPSHOTS", 3.0)))
    min_window = max(30.0, _env_float("GOOL_BASKETBALL_LIVE_MIN_ANALYSIS_SECONDS", 60.0))
    early_points = max(2, int(_env_float("GOOL_BASKETBALL_LIVE_EARLY_ANALYSIS_SNAPSHOTS", 2.0)))
    early_window = max(20.0, _env_float("GOOL_BASKETBALL_LIVE_EARLY_ANALYSIS_SECONDS", 30.0))

    recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
    activity_raw = brain.get("recent_activity_available")
    recent_activity = (
        bool(activity_raw)
        if activity_raw is not None
        else recent_score_rate > 0.0
    )

    strict_ready = points >= min_points and window >= min_window
    early_ready = (
        recent_activity
        and points >= early_points
        and window >= early_window
    )
    return {
        "ready": bool(strict_ready or early_ready),
        "mode": "strict" if strict_ready else ("active_early" if early_ready else "warming"),
        "points": points,
        "window": window,
        "min_points": min_points,
        "min_window": min_window,
        "early_points": early_points,
        "early_window": early_window,
        "recent_activity": recent_activity,
    }


def live_candidate_gate(brain: dict[str, Any]) -> dict[str, Any]:
    stats_payload = dict(brain.get("live_game_stats") or {})
    available = bool(stats_payload.get("current_segment_available"))
    readiness = _live_window_readiness(brain)
    points = int(readiness["points"])
    window = float(readiness["window"])
    recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
    recent_poss_rate = max(0.0, _num(brain.get("recent_possessions_per_min"), 0.0) or 0.0)

    score = 40.0
    if available:
        score += 12.0
    if readiness["mode"] == "strict":
        score += 13.0
    elif readiness["mode"] == "active_early":
        score += 10.0
    elif points >= 2:
        score += 5.0
    if window >= float(readiness["min_window"]):
        score += 7.0
    elif readiness["mode"] == "active_early":
        score += 5.0
    if recent_poss_rate > 0:
        score += 7.0
    elif recent_score_rate > 0:
        score += 4.0

    state = "PASS" if score >= 72.0 else ("BORDERLINE" if score >= 64.0 else "WAIT")
    # Some competitions expose a trustworthy current-quarter scoreboard but no
    # detailed current-quarter stat section. The final LIVE model already has a
    # conservative points/clock fallback for exactly this case. Do not kill the
    # match before 1xBet pricing when we have a verified segment score, a full
    # analysis window and real scoring activity; surface it only as BORDERLINE.
    score_only_fallback = bool(
        not available
        and bool(brain.get("segment_score_verified"))
        and str(readiness.get("mode") or "") == "strict"
        and bool(readiness.get("recent_activity"))
    )
    if bool(brain.get("break_transition")) or not bool(readiness["ready"]):
        state = "WAIT"
    elif not available:
        state = "BORDERLINE" if score_only_fallback and score >= 64.0 else "WAIT"
    return {
        "state": state,
        "score": round(_clamp(score, 0.0, 82.0), 1),
        "readiness_mode": str(readiness["mode"]),
        "required_snapshots": int(readiness["min_points"]),
        "required_window_seconds": round(float(readiness["min_window"]), 1),
        "early_required_snapshots": int(readiness["early_points"]),
        "early_required_window_seconds": round(float(readiness["early_window"]), 1),
    }


def live_signal(brain: dict[str, Any], lane: dict[str, Any]) -> dict[str, Any] | None:
    if str(brain.get("brain_state") or "") not in {"PASS", "BORDERLINE"}:
        return None
    family = str(lane.get("market_family") or "")
    lane_scope = str(lane.get("scope") or "")
    current_scope = str(brain.get("scope") or "")
    segment_market = family == "match_total" and lane_scope == current_scope
    full_market = lane_scope == "FULL_MATCH" and family in {"match_total", "home_total", "away_total"}
    if not (segment_market or full_market):
        return None
    segment_memory = dict(brain.get("segment_memory") or {})
    memory_quality = _clamp(_num(segment_memory.get("quality"), 0.0) or 0.0, 0.0, 1.0)
    if full_market and memory_quality < _env_float("GOOL_BASKETBALL_LIVE_FULL_MIN_MEMORY_QUALITY", 0.45):
        return None
    readiness = _live_window_readiness(brain)
    if bool(brain.get("break_transition")):
        return None
    if not bool(readiness["ready"]):
        return None
    try:
        line = float(lane.get("line"))
    except (TypeError, ValueError):
        return None
    duration = 720.0 if ("nba" in str(lane.get("league") or brain.get("league") or "").casefold() or "g league" in str(lane.get("league") or brain.get("league") or "").casefold()) and "wnba" not in str(lane.get("league") or brain.get("league") or "").casefold() else 600.0

    # Price the 1xBet line against the 1xBet in-game clock. Flashscore AO /
    # period_start_ts is wall time since the segment marker and includes
    # stoppages/timeouts, so using it as game elapsed can make a normal game look
    # artificially slow and manufacture UNDERs. Keep it only as a fallback when
    # the bookmaker segment clock is unavailable.
    brain_elapsed = _num(brain.get("elapsed_seconds"))
    book_elapsed = _num(lane.get("clock_seconds"))
    if book_elapsed is not None and 0.0 < book_elapsed < duration:
        elapsed = float(book_elapsed)
        clock_source = "1xbet_segment_clock"
    elif brain_elapsed is not None and 0.0 < brain_elapsed < duration:
        elapsed = float(brain_elapsed)
        clock_source = "flashscore_elapsed_fallback"
    else:
        return None
    remaining = duration - elapsed
    if elapsed < 45.0:
        return None
    # Avoid betting a quarter in its final seconds, when one foul/possession
    # dominates the segment line. Do NOT apply this guard to Q1-Q3 FULL_MATCH
    # projections: those bets explicitly use the remaining-quarter history and
    # were being incorrectly suppressed near a quarter boundary.
    if segment_market and remaining < 35.0:
        return None
    # Keep the conservative final-seconds guard for full-game pricing only in
    # Q4, where the match itself is about to end and foul variance dominates.
    if full_market and current_scope == "QUARTER_4" and remaining < 35.0:
        return None

    # FULL_MATCH lanes carry the full scoreboard, but the pace model below
    # must always model the CURRENT quarter first.
    score = list(
        brain.get("current_segment_score")
        if full_market
        else (lane.get("score") or brain.get("current_segment_score") or [0, 0])
    )
    try:
        current = max(0.0, float(score[0])) + max(0.0, float(score[1]))
    except (TypeError, ValueError, IndexError):
        return None

    league = str(lane.get("league") or brain.get("league") or "")
    profile = league_profile(league)
    quarter_context = live_quarter_context_assist(brain, profile)
    prior_total = float(quarter_context.get("adjusted_prior_total") or profile["quarter_total"])
    historical_segment = segment_prior(segment_memory, current_scope)
    historical_total = _num(historical_segment.get("expected_total"))
    if historical_total is not None and memory_quality >= 0.25:
        # Team/H2H quarter history is the specific prior; league context remains
        # the shrinkage anchor so small samples cannot take over the model.
        history_weight = _clamp(0.22 + memory_quality * 0.43, 0.22, 0.65)
        prior_total = prior_total * (1.0 - history_weight) + float(historical_total) * history_weight
    else:
        history_weight = 0.0
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
        recent_possessions = max(0.0, _num(brain.get("recent_possessions"), 0.0) or 0.0)
        recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
        min_recent_possessions = max(
            1.0,
            _env_float("GOOL_BASKETBALL_LIVE_MIN_RECENT_POSSESSIONS", 2.0),
        )
        recent_activity_available = bool(brain.get("recent_activity_available"))
        if not recent_activity_available:
            # Backward-compatible inference for callers/tests that predate the
            # explicit activity flag: a positive score delta is itself real
            # evidence that the provider refreshed. A possession delta without
            # any score change still needs enough observed possessions.
            recent_activity_available = bool(
                recent_score_rate > 0 or recent_possessions >= min_recent_possessions
            )
        if recent_poss_rate > 0 and recent_possessions >= min_recent_possessions:
            # Flashscore possession stats refresh in bursts. Do not turn a
            # fractional 0.x/1.x possession delta into a slow-tempo signal just
            # because the scoreboard changed. Until enough actual possessions
            # are observed, fall through to the points/clock model below.
            poss_per_min = _clamp(
                recent_poss_rate,
                prior_poss_per_min * 0.60,
                prior_poss_per_min * 1.45,
            )
            recent_ppp = recent_score_rate / max(0.20, recent_poss_rate)
            observed_ppp_pair = recent_ppp if recent_score_rate > 0 else prior_ppp_pair
            recent_window = max(
                20.0,
                _num(brain.get("recent_window_seconds"), 60.0) or 60.0,
            )
            observed_poss = recent_possessions
            possession_source = "flashscore_recent_delta"
            data_quality = 0.65

    if poss_per_min is None:
        observed_rate = current / max(0.5, elapsed / 60.0)
        raw_pace_total = current * duration / max(1.0, elapsed)
        observed_weight = _clamp(elapsed / duration, 0.15, 0.68)

        # Score-only competitions need the fresh scoreboard delta to participate
        # in the projection itself. Previously recent pace was used only as a
        # final direction veto, so a stale league/history prior could keep the
        # model below a rapidly rising live line and repeatedly manufacture
        # UNDER candidates. Blend fresh pace symmetrically, stealing weight from
        # the prior rather than adding extra confidence.
        recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
        recent_window = max(0.0, _num(brain.get("recent_window_seconds"), 0.0) or 0.0)
        recent_activity_available = bool(brain.get("recent_activity_available"))
        recent_weight = 0.0
        recent_pace_total = raw_pace_total
        if recent_activity_available and recent_score_rate > 0.0 and recent_window >= 30.0:
            recent_pace_total = current + recent_score_rate * remaining / 60.0
            recent_weight = min(
                0.30,
                max(0.0, 1.0 - observed_weight) * _clamp(recent_window / 120.0, 0.20, 0.55),
            )
        prior_weight = max(0.0, 1.0 - observed_weight - recent_weight)
        projection = (
            prior_total * prior_weight
            + raw_pace_total * observed_weight
            + recent_pace_total * recent_weight
        )
        poss_per_min = prior_poss_per_min
        posterior_ppp = prior_ppp_pair
        remaining_poss = prior_poss_per_min * remaining / 60.0
    else:
        # Shrink possession tempo and scoring efficiency separately. A full
        # current-quarter sample earns more weight; a short recent-delta sample
        # is intentionally capped because provider updates arrive in bursts.
        if possession_source == "flashscore_recent_delta":
            recent_window = max(
                20.0,
                _num(brain.get("recent_window_seconds"), 60.0) or 60.0,
            )
            tempo_weight = _clamp(recent_window / 180.0 * 0.55, 0.10, 0.45)
        else:
            tempo_weight = _clamp(elapsed / duration, 0.18, 0.78)
        posterior_poss_rate = prior_poss_per_min * (1.0 - tempo_weight) + poss_per_min * tempo_weight
        eff_n = max(1.0, observed_poss or 1.0)
        eff_weight = eff_n / (eff_n + 12.0)
        posterior_ppp = prior_ppp_pair * (1.0 - eff_weight) + (observed_ppp_pair or prior_ppp_pair) * eff_weight
        posterior_ppp = _clamp(posterior_ppp, prior_ppp_pair * 0.70, prior_ppp_pair * 1.30)
        remaining_poss = max(0.0, posterior_poss_rate * remaining / 60.0)
        projection = current + remaining_poss * posterior_ppp

    projection = max(current, projection)

    remaining_projection: dict[str, Any] | None = None
    target_projection = projection
    if full_market:
        remaining_projection = remaining_match_projection(
            segment_memory,
            scope=current_scope,
            match_score=list(brain.get("score") or lane.get("match_score") or [0, 0]),
            current_segment_score=list(brain.get("current_segment_score") or [0, 0]),
            current_segment_projection=projection,
        )
        if remaining_projection is None:
            return None
        if family == "home_total":
            target_projection = float(remaining_projection["home"])
        elif family == "away_total":
            target_projection = float(remaining_projection["away"])
        else:
            target_projection = float(remaining_projection["total"])

        order = ("QUARTER_1", "QUARTER_2", "QUARTER_3", "QUARTER_4")
        try:
            idx = order.index(current_scope)
        except ValueError:
            return None
        fraction_current_left = _clamp(remaining / duration, 0.0, 1.0)
        remaining_fraction = (fraction_current_left + max(0, 3 - idx)) / 4.0
        base_sigma = profile["sigma_total"] if family == "match_total" else profile["sigma_total"] / math.sqrt(2.0)
        sigma = max(5.0 if family == "match_total" else 3.8, base_sigma * math.sqrt(max(0.18, remaining_fraction)))
    else:
        sigma = max(3.2, profile["quarter_sigma"] * math.sqrt(max(0.25, remaining / duration)))

    raw_over_model = _normal_over(line, target_projection, sigma)
    history_points = max(0, int(brain.get("history_points") or 0))
    probability_reliability = _clamp(
        0.35 + data_quality * 0.55 + min(0.10, history_points * 0.02),
        0.55,
        0.92,
    )
    # Reliability shrink prevents a sparse live snapshot from claiming 99%+
    # certainty simply because a noisy projection is far from the line.
    over_model = 0.5 + (raw_over_model - 0.5) * probability_reliability
    over_model = _clamp(over_model, 0.03, 0.97)
    under_model = 1.0 - over_model
    market_over = _clamp(_num(lane.get("probability"), 0.5) or 0.5, 0.01, 0.99)
    choices = [
        (over_model - market_over, "over", over_model, market_over, _num(lane.get("over"), 0.0) or 0.0),
        (under_model - (1.0 - market_over), "under", under_model, 1.0 - market_over, _num(lane.get("under"), 0.0) or 0.0),
    ]
    edge, direction, model_p, market_p, odd = max(choices, key=lambda x: x[0])

    # A short active window may surface a genuinely fast OVER, but a quiet
    # 20-30 second slice is not enough evidence for an UNDER. Require the full
    # analysis window before any basketball LIVE UNDER can be published.
    if direction == "under" and str(readiness.get("mode") or "") != "strict":
        return None

    if not (_env_float("GOOL_MULTISPORT_MIN_ODD", 1.50) <= odd <= _env_float("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None
    if edge < 0.055 or model_p < 0.56:
        return None
    stat_edge = abs(target_projection - line)
    if full_market:
        min_stat_edge = _env_float(
            "GOOL_BASKETBALL_LIVE_FULL_MIN_STAT_EDGE"
            if family == "match_total"
            else "GOOL_BASKETBALL_LIVE_TEAM_MIN_STAT_EDGE",
            5.5 if family == "match_total" else 3.5,
        )
        if stat_edge < min_stat_edge:
            return None

    scope = str(brain.get("scope") or "")
    q3_assist = q3_rebound_assist(
        brain,
        elapsed_seconds=elapsed,
        current_segment_score=score,
    )
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
    directional_confirmation = False
    if possession_source != "points_clock_fallback":
        # Provider deltas arrive in bursts, so demand a stronger pace move when
        # the evidence comes from a short delta rather than a full quarter box.
        pace_up = 1.12 if possession_source == "flashscore_recent_delta" else 1.06
        pace_down = 0.88 if possession_source == "flashscore_recent_delta" else 0.94
        if direction == "over" and poss_per_min >= prior_poss_per_min * pace_up:
            agreement += 1
            directional_confirmation = True
        elif direction == "under" and poss_per_min <= prior_poss_per_min * pace_down:
            agreement += 1
            directional_confirmation = True
    if factors:
        efg = factors.get("efg")
        ft_rate = factors.get("ft_rate")
        tov_rate = factors.get("tov_rate")
        if direction == "over" and ((efg is not None and efg >= 0.56) or (ft_rate is not None and ft_rate >= 0.30)):
            agreement += 1
            directional_confirmation = True
        if direction == "under" and ((efg is not None and efg <= 0.47) or (tov_rate is not None and tov_rate >= 0.16)):
            agreement += 1
            directional_confirmation = True

    # Some Flashscore basketball competitions expose the live score and current
    # quarter but not enough FGA/FTA/OREB/TOV detail to estimate possessions.
    # Previously that made current-quarter LIVE impossible even with a very
    # clear points pace. Hockey already has an equivalent fallback via SOG pace.
    # Allow points pace to confirm direction only when both the recent window
    # and the overall quarter pace agree materially with the league prior.
    if possession_source == "points_clock_fallback":
        expected_score_rate = prior_total / max(1.0, duration / 60.0)
        recent_score_rate = max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0)
        overall_score_rate = current / max(0.75, elapsed / 60.0)
        recent_activity_raw = brain.get("recent_activity_available")
        recent_possessions = max(0.0, _num(brain.get("recent_possessions"), 0.0) or 0.0)
        min_recent_possessions = max(
            1.0,
            _env_float("GOOL_BASKETBALL_LIVE_MIN_RECENT_POSSESSIONS", 2.0),
        )
        recent_activity_available = (
            bool(recent_activity_raw)
            if recent_activity_raw is not None
            else bool(recent_score_rate > 0 or recent_possessions >= min_recent_possessions)
        )
        if (
            recent_activity_available
            and direction == "over"
            and recent_score_rate >= expected_score_rate * 1.15
            and overall_score_rate >= expected_score_rate * 1.05
        ):
            agreement += 1
            directional_confirmation = True
        elif (
            recent_activity_available
            and direction == "under"
            and recent_score_rate <= expected_score_rate * 0.82
            and overall_score_rate <= expected_score_rate * 0.95
        ):
            agreement += 1
            directional_confirmation = True

    if abs(projection - line) >= 3.0:
        agreement += 1

    # Current-quarter bets still require an independent LIVE pace/efficiency
    # confirmation. FULL_MATCH/IT may also be confirmed by a sufficiently deep
    # team+H2H segment profile: that is exactly how we can catch a team that
    # scored 60 by halftime but historically slows sharply in Q3/Q4.
    historical_confirmation = bool(
        full_market
        and memory_quality >= 0.55
        and stat_edge >= (
            _env_float("GOOL_BASKETBALL_LIVE_FULL_HISTORY_EDGE", 7.0)
            if family == "match_total"
            else _env_float("GOOL_BASKETBALL_LIVE_TEAM_HISTORY_EDGE", 4.5)
        )
    )
    if historical_confirmation:
        agreement += 1

    # Segment history is a prior/strengthener, never a standalone LIVE trigger.
    # The current game must independently confirm the chosen direction. This is
    # deliberately symmetric: neither OVER nor UNDER may be created only from
    # team/H2H history.
    if not directional_confirmation:
        return None

    # Q3 comeback context is deliberately only an assistant. It cannot create
    # a pick; it contributes one extra agreement block only when the live
    # possession/pace model is already independently leaning OVER.
    if (
        direction == "over"
        and bool(q3_assist.get("active"))
        and str(q3_assist.get("stage") or "") in {"close", "reversal"}
        and projection > line
        and poss_per_min >= prior_poss_per_min * 0.92
    ):
        agreement += 1
        q3_assist["applied"] = True

    if agreement < 2:
        return None

    strength = _clamp(
        48.0
        + data_quality * 12.0
        + min(0.20, max(0.0, model_p - 0.5)) * 45.0
        + min(0.20, max(0.0, edge)) * 60.0
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
        "projected_total": round(target_projection, 2),
        "current_segment_projection": round(projection, 2),
        "raw_stat_projection": round(target_projection, 2),
        "stat_edge": round(stat_edge, 2),
        "segment_memory_quality": round(memory_quality, 3),
        "segment_memory": segment_memory,
        "segment_prior_total": None if historical_total is None else round(float(historical_total), 2),
        "segment_history_weight": round(history_weight, 3),
        "segment_h2h_total": (
            None
            if historical_segment.get("h2h_home_for") is None or historical_segment.get("h2h_away_for") is None
            else round(float(historical_segment["h2h_home_for"]) + float(historical_segment["h2h_away_for"]), 2)
        ),
        "segment_h2h_n": int(historical_segment.get("h2h_n") or 0),
        "remaining_match_projection": remaining_projection or {},
        "historical_confirmation": historical_confirmation,
        "current_segment_total": round(current, 1),
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(remaining, 1),
        "recent_rate_per_min": round(max(0.0, _num(brain.get("recent_score_rate"), 0.0) or 0.0), 3),
        "recent_possessions": round(max(0.0, _num(brain.get("recent_possessions"), 0.0) or 0.0), 3),
        "recent_activity_available": bool(brain.get("recent_activity_available")),
        "possessions_per_min": round(poss_per_min, 3),
        "projected_remaining_possessions": round(remaining_poss, 2),
        "posterior_ppp_pair": round(posterior_ppp, 3),
        "possession_source": possession_source,
        "projection_clock_source": clock_source,
        "flashscore_brain_score": round(float(brain.get("brain_score") or 0.0), 1),
        "flashscore_brain_state": str(brain.get("brain_state") or ""),
        "flashscore_brain_reason": str(brain.get("brain_reason") or ""),
        "data_quality": round(data_quality, 3),
        "probability_reliability": round(probability_reliability, 3),
        "agreement_blocks": agreement,
        "directional_confirmation": directional_confirmation,
        "four_factors": factors,
        "q3_rebound_assist": q3_assist,
        "quarter_context_assist": quarter_context,
        "score_only_recent_projection_weight": round(recent_weight, 3) if possession_source == "points_clock_fallback" else 0.0,
        "score_only_recent_pace_total": round(recent_pace_total, 2) if possession_source == "points_clock_fallback" else None,
        "market_confirmed": edge >= 0.055,
        "probability_delta_pp": 0.0,
        "line_delta": 0.0,
        "moves": max(0, int(brain.get("history_points") or 1) - 1),
        "age_seconds": float(brain.get("recent_window_seconds") or 0.0),
        "start": {},
        "end": dict(lane),
    }
