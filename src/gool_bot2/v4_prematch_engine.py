from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Iterable


@dataclass(frozen=True)
class PrematchPick:
    event_id: str
    home: str
    away: str
    market: str
    selection: str
    odds: float
    model_probability: float
    market_probability: float
    data_quality: float = 1.0
    league: str = ""
    kickoff_ts: float = 0.0

    @property
    def edge(self) -> float:
        return self.model_probability - self.market_probability

    @property
    def expected_value(self) -> float:
        return self.model_probability * self.odds - 1.0


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    a, b = 1.0 / over_odds, 1.0 / under_odds
    margin = a + b
    if margin <= 0:
        return 0.5, 0.5
    return a / margin, b / margin


def qualified_pick(
    pick: PrematchPick,
    *,
    min_probability: float = 0.64,
    min_edge: float = 0.04,
    min_ev: float = 0.02,
    min_quality: float = 0.60,
    min_odds: float = 1.50,
    max_odds: float = 2.40,
) -> bool:
    return (
        min_odds <= pick.odds <= max_odds
        and pick.model_probability >= min_probability
        and pick.edge >= min_edge
        and pick.expected_value >= min_ev
        and pick.data_quality >= min_quality
    )


def build_accumulators(
    picks: Iterable[PrematchPick],
    *,
    legs: int = 2,
    min_combined_probability: float = 0.42,
    min_combined_odds: float = 1.65,
    max_combined_odds: float = 4.00,
) -> list[dict]:
    # Accumulators must use the same market-shrunk probabilities as singles.
    # Otherwise a leg can look qualified in an ACCA while being rejected by delivery.
    pool = [blend_with_market(p) for p in picks]
    pool = [p for p in pool if qualified_pick(p)]
    pool.sort(key=lambda p: (p.edge, p.expected_value, p.model_probability, p.data_quality), reverse=True)
    out: list[dict] = []

    def walk(start: int, chosen: list[PrematchPick]) -> None:
        if len(chosen) == legs:
            if len({p.event_id for p in chosen}) != legs:
                return
            combined_p = prod(p.model_probability for p in chosen)
            combined_odds = prod(p.odds for p in chosen)
            if combined_p < min_combined_probability:
                return
            if not (min_combined_odds <= combined_odds <= max_combined_odds):
                return
            out.append({
                "legs": chosen.copy(),
                "combined_probability": combined_p,
                "combined_odds": combined_odds,
                "expected_value": combined_p * combined_odds - 1.0,
                "score": combined_p * (1.0 + sum(p.edge for p in chosen)),
            })
            return
        for i in range(start, len(pool)):
            candidate = pool[i]
            if any(candidate.event_id == p.event_id for p in chosen):
                continue
            walk(i + 1, [*chosen, candidate])

    walk(0, [])
    out.sort(key=lambda row: (row["score"], row["expected_value"]), reverse=True)
    return out



def build_super_accumulator(
    picks: Iterable[PrematchPick],
    *,
    target_legs: int = 10,
    min_leg_odds: float = 1.15,
    max_leg_odds: float = 1.55,
    min_leg_probability: float = 0.72,
    min_quality: float = 0.75,
    min_edge: float = 0.025,
    min_ev: float = 0.015,
    model_weight: float = 0.65,
    max_same_market: int = 10,
) -> dict | None:
    """Build a calibrated, diversified SUPER ticket; never pad weak legs."""
    pool = [blend_with_market(p, model_weight=model_weight) for p in picks]
    pool = [
        p for p in pool
        if min_leg_odds <= p.odds <= max_leg_odds
        and p.model_probability >= min_leg_probability
        and p.data_quality >= min_quality
        and p.edge >= min_edge
        and p.expected_value >= min_ev
    ]
    pool.sort(
        key=lambda p: (p.model_probability * p.data_quality, p.edge, p.expected_value),
        reverse=True,
    )
    chosen: list[PrematchPick] = []
    seen: set[str] = set()
    market_counts: dict[str, int] = {}
    for pick in pool:
        if pick.event_id in seen:
            continue
        market_key = str(pick.market)
        if market_counts.get(market_key, 0) >= max(1, int(max_same_market)):
            continue
        seen.add(pick.event_id)
        market_counts[market_key] = market_counts.get(market_key, 0) + 1
        chosen.append(pick)
        if len(chosen) >= max(2, int(target_legs)):
            break
    if len(chosen) < max(2, int(target_legs)):
        return None
    combined_probability = prod(p.model_probability for p in chosen)
    combined_odds = prod(p.odds for p in chosen)
    return {
        "kind": "SUPER",
        "legs": chosen,
        "combined_probability": combined_probability,
        "combined_odds": combined_odds,
        "expected_value": combined_probability * combined_odds - 1.0,
    }


def choose_delivery(
    picks: Iterable[PrematchPick],
    *,
    max_singles: int | None = None,
    max_doubles: int | None = None,
) -> dict:
    """Build singles and parlays as independent products.

    A strong fixture may legitimately be published as a single and also be one
    leg of a parlay. Correlation protection is enforced inside each parlay:
    build_accumulators/build_super_accumulator allow only one selection per event.
    """
    rows = list(picks)
    single_limit = len(rows) if max_singles is None else max(0, int(max_singles))
    singles = rank_prematch_for_delivery(rows, limit=single_limit)

    # Parlays have their own confidence/value selection. Do not starve them just
    # because the same high-quality fixtures were already selected as singles.
    parlay_pool = [
        p for p in rows
        if 1.15 <= float(p.odds) <= 1.70
    ]

    super_ticket = build_super_accumulator(
        parlay_pool, target_legs=10, min_leg_probability=.74,
        min_quality=.80, min_edge=.035, min_ev=.02, max_same_market=6,
    )
    doubles = build_accumulators(
        parlay_pool, legs=2, min_combined_probability=.50,
        min_combined_odds=1.70, max_combined_odds=3.20,
    )
    strong = []
    used = set()
    for acc in doubles:
        legs = acc["legs"]
        if any(p.model_probability < .69 or p.data_quality < .75 or p.edge < .045 for p in legs):
            continue
        if any(p.event_id in used for p in legs):
            continue
        strong.append(acc)
        used.update(p.event_id for p in legs)
        if max_doubles is not None and len(strong) >= max(0, int(max_doubles)):
            break

    # Delivery may contain independent singles and independent parlays together.
    if super_ticket:
        mode = "SUPER"
    elif strong:
        mode = "DOUBLES"
    elif singles:
        mode = "SINGLES"
    else:
        mode = "NO_BET"
    return {"mode": mode, "super": super_ticket, "doubles": strong, "singles": singles}

def picks_from_goal_profile(
    *,
    event_id: str,
    home: str,
    away: str,
    profile: dict,
    market: dict,
    data_quality: float = 1.0,
) -> list[PrematchPick]:
    """Convert existing GOOL prematch history + 1xBet snapshot into priced V4 picks.

    V4 intentionally starts with full-match totals. 1X2 needs a separate
    home/draw/away probability head; it must not be inferred from total goals.
    """
    first = profile.get("first_half") or {}
    second = profile.get("second_half") or {}
    full = profile.get("full_match") or {}
    try:
        if first.get("available") and second.get("available"):
            lam = float(first["expected_total"]) + float(second["expected_total"])
        elif full.get("available"):
            lam = float(full["expected_total"])
        else:
            return []
    except (TypeError, ValueError, KeyError):
        return []
    if lam <= 0:
        return []

    rows = market.get("match_totals") or []
    out: list[PrematchPick] = []
    import math
    for row in rows:
        try:
            line = float(row.get("line"))
            over_odd = float(row.get("over"))
            under_odd = float(row.get("under"))
        except (TypeError, ValueError):
            continue
        if over_odd <= 1 or under_odd <= 1:
            continue
        # Binary Poisson pricing below is exact for half-goal lines only.
        # Whole/quarter Asian totals need explicit push/half-win settlement EV.
        if abs((line * 2) - round(line * 2)) > 1e-9 or int(round(line * 2)) % 2 == 0:
            continue
        threshold = int(math.floor(line)) + 1
        cdf = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(threshold))
        model_over = max(0.0, min(1.0, 1.0 - cdf))
        fair_over, fair_under = devig_two_way(over_odd, under_odd)
        out.extend([
            PrematchPick(event_id, home, away, "match_total", f"over {line:g}", over_odd, model_over, fair_over, data_quality),
            PrematchPick(event_id, home, away, "match_total", f"under {line:g}", under_odd, 1.0 - model_over, fair_under, data_quality),
        ])
    return out


def devig_three_way(home_odds: float, draw_odds: float, away_odds: float) -> tuple[float, float, float]:
    raw = (1.0 / home_odds, 1.0 / draw_odds, 1.0 / away_odds)
    margin = sum(raw)
    if margin <= 0:
        return 1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0
    return tuple(x / margin for x in raw)


def poisson_1x2(home_lambda: float, away_lambda: float, max_goals: int = 10) -> tuple[float, float, float]:
    import math
    home_lambda = max(0.01, float(home_lambda))
    away_lambda = max(0.01, float(away_lambda))
    hp = [math.exp(-home_lambda) * home_lambda ** k / math.factorial(k) for k in range(max_goals + 1)]
    ap = [math.exp(-away_lambda) * away_lambda ** k / math.factorial(k) for k in range(max_goals + 1)]
    home = draw = away = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            p = ph * pa
            if h > a:
                home += p
            elif h == a:
                draw += p
            else:
                away += p
    mass = home + draw + away
    if mass <= 0:
        return 1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0
    return home / mass, draw / mass, away / mass


def picks_from_1x2_profile(
    *,
    event_id: str,
    home: str,
    away: str,
    profile: dict,
    market: dict,
    data_quality: float = 1.0,
) -> list[PrematchPick]:
    first = profile.get("first_half") or {}
    second = profile.get("second_half") or {}
    full = profile.get("full_match") or {}
    try:
        if first.get("available") and second.get("available"):
            home_lambda = float(first["home_expected_goals"]) + float(second["home_expected_goals"])
            away_lambda = float(first["away_expected_goals"]) + float(second["away_expected_goals"])
        elif full.get("available"):
            home_lambda = float(full["home_expected_goals"])
            away_lambda = float(full["away_expected_goals"])
        else:
            return []
        prices = market.get("match_1x2") or {}
        home_odd = float(prices["home"])
        draw_odd = float(prices["draw"])
        away_odd = float(prices["away"])
    except (TypeError, ValueError, KeyError):
        return []
    if min(home_odd, draw_odd, away_odd) <= 1.0:
        return []

    model = poisson_1x2(home_lambda, away_lambda)
    fair = devig_three_way(home_odd, draw_odd, away_odd)
    labels = ("home", "draw", "away")
    odds = (home_odd, draw_odd, away_odd)
    return [
        PrematchPick(event_id, home, away, "match_1x2", label, odd, mp, fp, data_quality)
        for label, odd, mp, fp in zip(labels, odds, model, fair)
    ]


def picks_from_btts_profile(
    *, event_id: str, home: str, away: str, profile: dict, market: dict,
    data_quality: float = 1.0,
) -> list[PrematchPick]:
    """Price BTTS Yes/No from the same team goal rates used by the score model."""
    first = profile.get("first_half") or {}
    second = profile.get("second_half") or {}
    full = profile.get("full_match") or {}
    try:
        if first.get("available") and second.get("available"):
            home_lambda = float(first["home_expected_goals"]) + float(second["home_expected_goals"])
            away_lambda = float(first["away_expected_goals"]) + float(second["away_expected_goals"])
        elif full.get("available"):
            home_lambda = float(full["home_expected_goals"])
            away_lambda = float(full["away_expected_goals"])
        else:
            return []
        prices = market.get("btts") or {}
        yes_odd = float(prices["yes"]); no_odd = float(prices["no"])
    except (TypeError, ValueError, KeyError):
        return []
    if min(yes_odd, no_odd) <= 1.0 or min(home_lambda, away_lambda) < 0:
        return []
    import math
    model_yes = (1.0 - math.exp(-home_lambda)) * (1.0 - math.exp(-away_lambda))
    fair_yes, fair_no = devig_two_way(yes_odd, no_odd)
    return [
        PrematchPick(event_id, home, away, "btts", "yes", yes_odd, model_yes, fair_yes, data_quality),
        PrematchPick(event_id, home, away, "btts", "no", no_odd, 1.0 - model_yes, fair_no, data_quality),
    ]



def picks_from_team_totals_profile(
    *, event_id: str, home: str, away: str, profile: dict, market: dict,
    data_quality: float = 1.0,
) -> list[PrematchPick]:
    """Price home/away team totals from the same team lambdas as the score model."""
    first = profile.get("first_half") or {}
    second = profile.get("second_half") or {}
    full = profile.get("full_match") or {}
    try:
        if first.get("available") and second.get("available"):
            home_lambda = float(first["home_expected_goals"]) + float(second["home_expected_goals"])
            away_lambda = float(first["away_expected_goals"]) + float(second["away_expected_goals"])
        elif full.get("available"):
            home_lambda = float(full["home_expected_goals"])
            away_lambda = float(full["away_expected_goals"])
        else:
            return []
    except (TypeError, ValueError, KeyError):
        return []

    import math
    out: list[PrematchPick] = []
    for side, lam, rows, market_name in (
        ("home", home_lambda, market.get("home_totals") or [], "home_total"),
        ("away", away_lambda, market.get("away_totals") or [], "away_total"),
    ):
        for row in rows:
            try:
                line = float(row.get("line"))
                over_odd = float(row.get("over"))
                under_odd = float(row.get("under"))
            except (TypeError, ValueError):
                continue
            if min(over_odd, under_odd) <= 1.0:
                continue
            # Exact binary pricing only for half-goal lines.
            if abs((line * 2) - round(line * 2)) > 1e-9 or int(round(line * 2)) % 2 == 0:
                continue
            threshold = int(math.floor(line)) + 1
            cdf = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(threshold))
            model_over = max(0.0, min(1.0, 1.0 - cdf))
            fair_over, fair_under = devig_two_way(over_odd, under_odd)
            label = "ИТБ1" if side == "home" else "ИТБ2"
            out.extend([
                PrematchPick(event_id, home, away, market_name, f"{label} {line:g}", over_odd, model_over, fair_over, data_quality),
                PrematchPick(event_id, home, away, market_name, f"ИТМ{'1' if side == 'home' else '2'} {line:g}", under_odd, 1.0-model_over, fair_under, data_quality),
            ])
    return out

def build_prematch_candidates(
    *,
    event_id: str,
    home: str,
    away: str,
    profile: dict,
    market: dict,
    data_quality: float = 1.0,
) -> list[PrematchPick]:
    return [
        *picks_from_goal_profile(
            event_id=event_id, home=home, away=away, profile=profile,
            market=market, data_quality=data_quality,
        ),
        *picks_from_1x2_profile(
            event_id=event_id, home=home, away=away, profile=profile,
            market=market, data_quality=data_quality,
        ),
        *picks_from_btts_profile(
            event_id=event_id, home=home, away=away, profile=profile,
            market=market, data_quality=data_quality,
        ),
        *picks_from_team_totals_profile(
            event_id=event_id, home=home, away=away, profile=profile,
            market=market, data_quality=data_quality,
        ),
    ]


def blend_with_market(
    pick: PrematchPick,
    *,
    model_weight: float = 0.65,
) -> PrematchPick:
    """Shrink raw model probabilities toward the de-vig market prior.

    The weight is deliberately configurable and must ultimately be selected
    only from chronological out-of-sample calibration/CLV results.
    """
    w = max(0.0, min(1.0, float(model_weight)))
    p = w * pick.model_probability + (1.0 - w) * pick.market_probability
    return PrematchPick(
        event_id=pick.event_id,
        home=pick.home,
        away=pick.away,
        market=pick.market,
        selection=pick.selection,
        odds=pick.odds,
        model_probability=p,
        market_probability=pick.market_probability,
        data_quality=pick.data_quality,
        league=pick.league,
        kickoff_ts=pick.kickoff_ts,
    )


def rank_prematch_singles(
    picks: Iterable[PrematchPick],
    *,
    model_weight: float = 0.65,
    limit: int = 12,
) -> list[PrematchPick]:
    blended = [blend_with_market(p, model_weight=model_weight) for p in picks]
    qualified = [p for p in blended if qualified_pick(p)]
    qualified.sort(
        key=lambda p: (p.expected_value, p.edge, p.model_probability, p.data_quality),
        reverse=True,
    )
    seen: set[tuple[str, str]] = set()
    out: list[PrematchPick] = []
    for p in qualified:
        key = (p.event_id, p.market)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max(1, int(limit)):
            break
    return out


def signal_tier(
    pick: PrematchPick,
    *,
    normal_min_probability: float = 0.60,
    normal_min_edge: float = 0.025,
    normal_min_ev: float = 0.01,
    strong_min_probability: float = 0.68,
    strong_min_edge: float = 0.055,
    strong_min_ev: float = 0.04,
    min_quality: float = 0.55,
) -> str | None:
    """Two-tier throughput gate: reject junk without starving useful signals."""
    if pick.data_quality < min_quality or pick.odds < 1.50 or pick.odds > 3.25:
        return None
    if (
        pick.model_probability >= strong_min_probability
        and pick.edge >= strong_min_edge
        and pick.expected_value >= strong_min_ev
    ):
        return "STRONG"
    if (
        pick.model_probability >= normal_min_probability
        and pick.edge >= normal_min_edge
        and pick.expected_value >= normal_min_ev
    ):
        return "NORMAL"
    return None


def rank_prematch_for_delivery(
    picks: Iterable[PrematchPick],
    *,
    model_weight: float = 0.65,
    limit: int = 8,
    max_per_event: int = 1,
) -> list[tuple[PrematchPick, str]]:
    """Rank a selective daily shortlist without turning every fixture into a bet.

    One selection per event+market is kept, but NORMAL signals remain public;
    STRONG is a label, not a second hard filter.
    """
    blended = [blend_with_market(p, model_weight=model_weight) for p in picks]
    rows = [(p, signal_tier(p)) for p in blended]
    rows = [(p, tier) for p, tier in rows if tier is not None]
    rows.sort(
        key=lambda row: (
            row[1] == "STRONG",
            row[0].expected_value,
            row[0].edge,
            row[0].model_probability,
        ),
        reverse=True,
    )
    event_counts: dict[str, int] = {}
    out: list[tuple[PrematchPick, str]] = []
    for p, tier in rows:
        event_id = str(p.event_id)
        if event_counts.get(event_id, 0) >= max(1, int(max_per_event)):
            continue
        event_counts[event_id] = event_counts.get(event_id, 0) + 1
        out.append((p, tier))
        if len(out) >= max(1, int(limit)):
            break
    return out
