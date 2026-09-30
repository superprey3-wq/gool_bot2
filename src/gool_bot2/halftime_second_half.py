from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .match_context import card_context, provider_count, provider_pair, xg_or_proxy_pair


@dataclass(frozen=True)
class HalftimeSecondHalfAnalysis:
    expected_goals_2h: float
    probability_goal_2h: float
    probability_over_1_5_2h: float
    probability_over_2_5_2h: float
    home_score_probability_2h: float
    away_score_probability_2h: float
    home_expected_goals_2h: float
    away_expected_goals_2h: float
    confidence: float
    first_half_xg_or_proxy: float
    first_half_xg_source: str
    first_half_shots: float
    first_half_sot: float
    first_half_big_chances: float
    first_half_touches_box: float
    prematch_second_half_sample: int
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HalftimeMarketPick:
    decision: str
    market: str | None
    selection: str | None
    line: float | None
    odds: float | None
    model_probability: float | None
    market_probability: float | None
    edge: float | None
    expected_value: float | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _pair(record: dict[str, Any], key: str) -> tuple[float, float]:
    try:
        home, away = provider_pair(record, key)
    except Exception:
        home = away = None
    return max(0.0, float(home or 0.0)), max(0.0, float(away or 0.0))


def _poisson_over(lam: float, line: float) -> float:
    threshold = int(math.floor(float(line))) + 1
    cdf = 0.0
    for goals in range(threshold):
        cdf += math.exp(-lam) * (lam ** goals) / math.factorial(goals)
    return _clamp(1.0 - cdf)


def _market_probability(primary: float | None, opposite: float | None) -> float | None:
    if primary is None or primary <= 1.0:
        return None
    a = 1.0 / primary
    if opposite is None or opposite <= 1.0:
        return a
    b = 1.0 / opposite
    return a / (a + b) if a + b > 0 else None


def _first_half_activity(record: dict[str, Any]) -> dict[str, float]:
    shots_h, shots_a = _pair(record, "shots")
    sot_h, sot_a = _pair(record, "shots_on_target")
    big_h, big_a = _pair(record, "big_chances")
    box_h, box_a = _pair(record, "touches_box")
    return {
        "shots_home": shots_h,
        "shots_away": shots_a,
        "shots": shots_h + shots_a,
        "sot_home": sot_h,
        "sot_away": sot_a,
        "sot": sot_h + sot_a,
        "big_home": big_h,
        "big_away": big_a,
        "big": big_h + big_a,
        "box_home": box_h,
        "box_away": box_a,
        "box": box_h + box_a,
    }


def evaluate_halftime_second_half(
    record: dict[str, Any],
    *,
    prematch_profile: dict[str, Any] | None = None,
) -> HalftimeSecondHalfAnalysis:
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    margin = abs(hs - aws)

    activity = _first_half_activity(record)
    try:
        xh, xa, xg_source, _ = xg_or_proxy_pair(record)
    except Exception:
        xh = xa = None
        xg_source = "unavailable"
    xh = max(0.0, float(xh or 0.0))
    xa = max(0.0, float(xa or 0.0))
    xg_total = xh + xa

    profile = prematch_profile if isinstance(prematch_profile, dict) else (record.get("prematch_goal_profile") or {})
    second = dict((profile or {}).get("second_half") or {})
    first = dict((profile or {}).get("first_half") or {})

    prior_total = _number(second.get("expected_total"))
    prior_home = _number(second.get("home_expected_goals"))
    prior_away = _number(second.get("away_expected_goals"))
    pair_sample = max(0, int(_number(second.get("pair_sample")) or 0))

    # Historical 2H expectation is an anchor, not the whole decision.
    if prior_total is None:
        full = dict((profile or {}).get("full_match") or {})
        full_total = _number(full.get("expected_total"))
        prior_total = 1.28 if full_total is None else max(0.55, min(2.20, full_total * 0.54))

    # Use 1H chance creation to estimate how open the same match is. xG/proxy is
    # primary; SOT/big chances only make a small adjustment so they do not double
    # count the same attacks.
    live_pace_lambda = (
        xg_total * 1.05
        + 0.025 * max(0.0, activity["sot"] - 2.0)
        + 0.045 * activity["big"]
        + 0.0025 * min(30.0, activity["box"])
    )
    if xg_total <= 0.01:
        live_pace_lambda = 0.55 + 0.025 * activity["shots"] + 0.055 * activity["sot"] + 0.10 * activity["big"]

    prior_weight = 0.68 if pair_sample >= 6 else 0.58 if pair_sample >= 3 else 0.48
    if xg_source != "provider_xg":
        prior_weight = min(0.75, prior_weight + 0.08)
    live_weight = 1.0 - prior_weight
    lam = prior_weight * float(prior_total) + live_weight * max(0.20, live_pace_lambda)

    reasons: list[str] = [
        f"1H xg_or_proxy={xg_total:.2f}",
        f"1H shots={activity['shots']:.0f}, sot={activity['sot']:.0f}, big={activity['big']:.0f}",
        f"prematch_2H_lambda={float(prior_total):.2f}, sample={pair_sample}",
    ]
    cautions: list[str] = []

    # Score state changes 2H incentives. One-goal games usually preserve chase
    # pressure; large margins can reduce urgency.
    if hs == aws:
        lam *= 1.04
        reasons.append("HT draw: small 2H urgency boost")
    elif margin == 1:
        lam *= 1.07
        reasons.append("one-goal HT margin: trailing-side chase boost")
    elif margin >= 3:
        lam *= 0.90
        cautions.append("large HT margin can suppress 2H tempo")
    elif margin == 2:
        lam *= 0.97
        cautions.append("two-goal HT margin slightly reduces urgency")

    # Regression guard: many 1H goals without corresponding chance quality should
    # not automatically project another wild half.
    first_half_goals = hs + aws
    if first_half_goals >= 3 and xg_total < 1.20:
        lam *= 0.92
        cautions.append("1H score ran hotter than chance quality")

    cards = card_context(record)
    red_total = int(cards.get("home_red") or 0) + int(cards.get("away_red") or 0)
    if red_total:
        cautions.append(f"red_cards={red_total}: direction of effect is unstable")

    lam = max(0.20, min(3.60, lam))

    # Split the total lambda between teams using a blend of historical 2H rates
    # and actual 1H attacking share.
    if prior_home is not None and prior_away is not None and prior_home + prior_away > 0:
        prior_home_share = _clamp(prior_home / (prior_home + prior_away), 0.12, 0.88)
    else:
        prior_home_share = 0.50

    live_share_den = xh + xa
    if live_share_den >= 0.15:
        live_home_share = _clamp(xh / live_share_den, 0.08, 0.92)
    else:
        attack_h = activity["sot_home"] * 2.0 + activity["shots_home"] + activity["big_home"] * 3.0
        attack_a = activity["sot_away"] * 2.0 + activity["shots_away"] + activity["big_away"] * 3.0
        live_home_share = 0.50 if attack_h + attack_a <= 0 else _clamp(attack_h / (attack_h + attack_a), 0.08, 0.92)

    home_share = _clamp(0.62 * prior_home_share + 0.38 * live_home_share, 0.12, 0.88)
    home_lam = lam * home_share
    away_lam = lam - home_lam

    p05 = _poisson_over(lam, 0.5)
    p15 = _poisson_over(lam, 1.5)
    p25 = _poisson_over(lam, 2.5)
    ph = 1.0 - math.exp(-home_lam)
    pa = 1.0 - math.exp(-away_lam)

    sources = provider_count(record)
    stat_pairs = sum(
        1 for key in ("xg", "shots", "shots_on_target", "big_chances", "touches_box")
        if any(v > 0 for v in _pair(record, key))
    )
    confidence = (
        0.38
        + 0.07 * min(3, sources)
        + (0.13 if xg_source == "provider_xg" else 0.05)
        + 0.025 * min(5, stat_pairs)
        + 0.11 * min(1.0, pair_sample / 6.0)
        - (0.12 if red_total else 0.0)
    )
    confidence = _clamp(confidence, 0.35, 0.94)

    return HalftimeSecondHalfAnalysis(
        expected_goals_2h=round(lam, 4),
        probability_goal_2h=round(p05, 4),
        probability_over_1_5_2h=round(p15, 4),
        probability_over_2_5_2h=round(p25, 4),
        home_score_probability_2h=round(ph, 4),
        away_score_probability_2h=round(pa, 4),
        home_expected_goals_2h=round(home_lam, 4),
        away_expected_goals_2h=round(away_lam, 4),
        confidence=round(confidence, 4),
        first_half_xg_or_proxy=round(xg_total, 4),
        first_half_xg_source=xg_source,
        first_half_shots=round(activity["shots"], 2),
        first_half_sot=round(activity["sot"], 2),
        first_half_big_chances=round(activity["big"], 2),
        first_half_touches_box=round(activity["box"], 2),
        prematch_second_half_sample=pair_sample,
        reasons=tuple(reasons),
        cautions=tuple(cautions),
    )


def choose_second_half_market(
    analysis: HalftimeSecondHalfAnalysis,
    markets: dict[str, Any] | None,
) -> HalftimeMarketPick:
    """Choose a real 2H O/U candidate when price exists, otherwise return a lean."""

    probability_by_line = {
        0.5: analysis.probability_goal_2h,
        1.5: analysis.probability_over_1_5_2h,
        2.5: analysis.probability_over_2_5_2h,
    }
    rows = list((markets or {}).get("match_total") or [])
    candidates: list[dict[str, Any]] = []

    for row in rows:
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            continue
        if line not in probability_by_line:
            continue
        over_p = probability_by_line[line]
        for side, model_p, primary_key, opposite_key in (
            ("OVER", over_p, "over", "under"),
            ("UNDER", 1.0 - over_p, "under", "over"),
        ):
            try:
                odd = float(row.get(primary_key))
            except (TypeError, ValueError):
                continue
            if odd <= 1.0:
                continue
            try:
                opposite = float(row.get(opposite_key))
            except (TypeError, ValueError):
                opposite = None
            market_p = _market_probability(odd, opposite)
            if market_p is None:
                continue
            edge = model_p - market_p
            ev = model_p * odd - 1.0

            # We are not looking for the safest possible line. The objective is
            # an actionable 2H bet with real price/value. Tiny odds such as 1.20
            # on U2.5 are informationally weak even when the raw hit probability
            # is high, so they are excluded from BET and may only appear as context.
            floor = 0.64 if line == 0.5 else 0.45 if line == 1.5 else 0.24
            if side == "UNDER":
                floor = 0.60 if line == 1.5 else 0.52 if line == 2.5 else 0.34

            # Main value band: avoid trivial insurance prices and very speculative
            # long shots. Overs are slightly preferred for this halftime-goals task.
            min_odd = 1.38 if side == "OVER" and line == 0.5 else 1.45
            max_odd = 2.65
            qualifies = (
                analysis.confidence >= 0.58
                and model_p >= floor
                and edge >= 0.04
                and ev >= 0.025
                and min_odd <= odd <= max_odd
            )

            # Rank by value first, not by raw hit probability. A moderate-probability
            # +EV O1.5 should beat a near-certain U2.5 at 1.24.
            odds_quality = 1.0 - min(1.0, abs(odd - 1.85) / 1.15)
            market_usefulness = 1.0
            if side == "UNDER" and line >= 2.5:
                market_usefulness = 0.55
            elif side == "OVER" and line == 0.5:
                market_usefulness = 0.80
            elif side == "OVER" and line == 1.5:
                market_usefulness = 1.12

            rank = (
                0.34 * max(0.0, edge)
                + 0.34 * max(0.0, ev)
                + 0.14 * model_p
                + 0.10 * analysis.confidence
                + 0.08 * odds_quality
            ) * market_usefulness

            candidates.append({
                "side": side, "line": line, "odd": odd, "model_p": model_p,
                "market_p": market_p, "edge": edge, "ev": ev, "qualifies": qualifies,
                "rank": rank,
            })

    qualified = [row for row in candidates if row["qualifies"]]
    if qualified:
        best = max(qualified, key=lambda row: row["rank"])
        return HalftimeMarketPick(
            decision="BET",
            market="SECOND_HALF_TOTAL",
            selection=best["side"],
            line=best["line"],
            odds=round(best["odd"], 3),
            model_probability=round(best["model_p"], 4),
            market_probability=round(best["market_p"], 4),
            edge=round(best["edge"], 4),
            expected_value=round(best["ev"], 4),
            confidence=analysis.confidence,
            reason="Real 2H market passed probability, edge, EV and data-quality gates.",
        )

    if analysis.probability_goal_2h >= 0.72 and analysis.confidence >= 0.58:
        return HalftimeMarketPick(
            decision="LEAN",
            market="SECOND_HALF_TOTAL",
            selection="OVER",
            line=0.5,
            odds=None,
            model_probability=analysis.probability_goal_2h,
            market_probability=None,
            edge=None,
            expected_value=None,
            confidence=analysis.confidence,
            reason="Football evidence supports a 2H goal, but no qualifying real price was available.",
        )
    if analysis.probability_over_1_5_2h <= 0.34 and analysis.confidence >= 0.60:
        return HalftimeMarketPick(
            decision="LEAN",
            market="SECOND_HALF_TOTAL",
            selection="UNDER",
            line=1.5,
            odds=None,
            model_probability=round(1.0 - analysis.probability_over_1_5_2h, 4),
            market_probability=None,
            edge=None,
            expected_value=None,
            confidence=analysis.confidence,
            reason="Football evidence leans to a low-scoring 2H, but no qualifying real price was available.",
        )
    return HalftimeMarketPick(
        decision="SKIP",
        market=None,
        selection=None,
        line=None,
        odds=None,
        model_probability=None,
        market_probability=None,
        edge=None,
        expected_value=None,
        confidence=analysis.confidence,
        reason="No second-half market passed the evidence/value gates.",
    )


__all__ = [
    "HalftimeSecondHalfAnalysis",
    "HalftimeMarketPick",
    "evaluate_halftime_second_half",
    "choose_second_half_market",
]
