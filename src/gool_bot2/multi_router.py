from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class MarketCandidate:
    key: str
    family: str
    label: str
    odd: float
    model_probability: float
    push_probability: float = 0.0
    market_probability: float | None = None
    goals_to_win: int = 1
    correlation_key: str = ""
    source: str = "gool"
    market_pressure_pp: float = 0.0
    data_quality: float = 1.0
    rating: float = 0.0
    expected_roi: float = 0.0
    value_edge_pp: float = 0.0
    eligible: bool = True
    blocks: list[str] = field(default_factory=list)
    reason_tags: list[str] = field(default_factory=list)

    @property
    def lose_probability(self) -> float:
        return max(0.0, 1.0 - float(self.model_probability) - float(self.push_probability))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouterDecision:
    status: str
    minute: int
    score: tuple[int, int]
    winner: MarketCandidate | None
    alternatives: list[MarketCandidate]
    rejected: list[MarketCandidate]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "minute": self.minute,
            "score": list(self.score),
            "winner": None if self.winner is None else self.winner.to_dict(),
            "alternatives": [row.to_dict() for row in self.alternatives],
            "rejected": [row.to_dict() for row in self.rejected],
            "reason": self.reason,
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _expert_probability(experts: dict[str, Any], key: str) -> tuple[float | None, str]:
    raw = experts.get(key)
    if raw is None:
        return None, "missing"
    if isinstance(raw, (int, float)):
        return _clamp(float(raw)), key
    if isinstance(raw, dict):
        value = raw.get("probability")
        if value is None:
            value = raw.get("confidence")
        if value is None:
            return None, str(raw.get("source") or key)
        return _clamp(float(value)), str(raw.get("source") or key)
    return None, "missing"


def _norm_probability(primary: float | None, opposite: float | None) -> float | None:
    if primary is None or float(primary) <= 1.0:
        return None
    a = 1.0 / float(primary)
    if opposite is None or float(opposite) <= 1.0:
        return a
    b = 1.0 / float(opposite)
    return a / (a + b) if a + b > 0 else None


def _row(rows: list[dict[str, Any]], line: float) -> dict[str, Any] | None:
    for item in rows:
        try:
            if abs(float(item.get("line")) - float(line)) < 1e-9:
                return dict(item)
        except (TypeError, ValueError):
            continue
    return None


def _pressure(market_row: dict[str, Any], market: str, line: float | None) -> float:
    key = f"{market}:{line}"
    try:
        return max(0.0, float(((market_row.get("pressure") or {}).get(key) or {}).get("prob_delta_pp") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _candidate(
    *,
    key: str,
    family: str,
    label: str,
    odd: float,
    probability: float,
    push_probability: float = 0.0,
    opposite_odd: float | None = None,
    goals_to_win: int = 1,
    correlation_key: str,
    source: str,
    market_pressure_pp: float = 0.0,
    data_quality: float = 1.0,
) -> MarketCandidate:
    return MarketCandidate(
        key=key,
        family=family,
        label=label,
        odd=float(odd),
        model_probability=_clamp(probability),
        push_probability=_clamp(push_probability, 0.0, max(0.0, 1.0 - probability)),
        market_probability=_norm_probability(float(odd), opposite_odd),
        goals_to_win=goals_to_win,
        correlation_key=correlation_key,
        source=source,
        market_pressure_pp=market_pressure_pp,
        data_quality=_clamp(data_quality),
    )


def build_goal_market_candidates(
    match: dict[str, Any],
    market_row: dict[str, Any] | None,
    experts: dict[str, Any],
    *,
    data_quality: float = 1.0,
) -> list[MarketCandidate]:
    """Build a small, goal-only candidate set from real 1xBet lines.

    The router deliberately starts narrow. It uses existing GOOL expert outputs
    and only constructs lines whose event probability can be described without
    inventing a new model. In particular, the Asian middle total N+1.0 is
    derived from P(>=1 more goal) and P(>=2 more goals): two goals win, exactly
    one goal pushes, no goals loses.
    """
    if not market_row:
        return []
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    if int(market_row.get("score_home") or 0) != hs or int(market_row.get("score_away") or 0) != aws:
        return []

    markets = market_row.get("markets") or {}
    total = hs + aws
    p1, src1 = _expert_probability(experts, "another_goal")
    p2, src2 = _expert_probability(experts, "two_more_goals")
    ph, srch = _expert_probability(experts, "home_goal")
    pa, srca = _expert_probability(experts, "away_goal")
    out: list[MarketCandidate] = []

    totals = list(markets.get("match_total") or [])
    if p1 is not None:
        line = total + 0.5
        item = _row(totals, line)
        if item and item.get("over"):
            out.append(_candidate(
                key=f"match_total:{line:g}", family="match_total", label=f"ТБ {line:g}",
                odd=float(item["over"]), probability=p1, opposite_odd=item.get("under"), goals_to_win=1,
                correlation_key="any_next_goal", source=src1,
                market_pressure_pp=_pressure(market_row, "match_total", line), data_quality=data_quality,
            ))

    if p1 is not None and p2 is not None and p1 >= p2:
        line = total + 1.0
        item = _row(totals, line)
        if item and item.get("over"):
            out.append(_candidate(
                key=f"match_total:{line:g}", family="asian_match_total", label=f"ТБ {line:g}",
                odd=float(item["over"]), probability=p2, push_probability=max(0.0, p1 - p2),
                opposite_odd=item.get("under"), goals_to_win=2, correlation_key="two_goal_path",
                source=f"{src1}+{src2}", market_pressure_pp=_pressure(market_row, "match_total", line),
                data_quality=data_quality,
            ))

    if p2 is not None:
        line = total + 1.5
        item = _row(totals, line)
        if item and item.get("over"):
            out.append(_candidate(
                key=f"match_total:{line:g}", family="match_total", label=f"ТБ {line:g}",
                odd=float(item["over"]), probability=p2, opposite_odd=item.get("under"), goals_to_win=2,
                correlation_key="two_goal_path", source=src2,
                market_pressure_pp=_pressure(market_row, "match_total", line), data_quality=data_quality,
            ))

    for side, score, probability, source, market_name, prefix in (
        ("home", hs, ph, srch, "home_total", "ИТБ1"),
        ("away", aws, pa, srca, "away_total", "ИТБ2"),
    ):
        if probability is None:
            continue
        line = score + 0.5
        item = _row(list(markets.get(market_name) or []), line)
        if item and item.get("over"):
            out.append(_candidate(
                key=f"{market_name}:{line:g}", family="team_total", label=f"{prefix} {line:g}",
                odd=float(item["over"]), probability=probability, opposite_odd=item.get("under"), goals_to_win=1,
                correlation_key=f"{side}_next_goal", source=source,
                market_pressure_pp=_pressure(market_row, market_name, line), data_quality=data_quality,
            ))

    # BTTS and the scoreless team's next-goal total describe the same football
    # event once the other team has already scored. The correlation key lets the
    # router compare prices instead of sending both bets.
    btts = markets.get("btts") or {}
    yes = btts.get("yes")
    no = btts.get("no")
    if yes and ((hs == 0 < aws) or (aws == 0 < hs)):
        probability, source, side = (ph, srch, "home") if hs == 0 else (pa, srca, "away")
        if probability is not None:
            out.append(_candidate(
                key="btts_yes", family="btts", label="ОЗ — Да", odd=float(yes), probability=probability,
                opposite_odd=no, goals_to_win=1, correlation_key=f"{side}_next_goal", source=source,
                market_pressure_pp=_pressure(market_row, "btts_yes", None), data_quality=data_quality,
            ))
    return out


def score_candidate(candidate: MarketCandidate, minute: int) -> MarketCandidate:
    p_win = _clamp(candidate.model_probability)
    p_push = _clamp(candidate.push_probability, 0.0, 1.0 - p_win)
    p_lose = max(0.0, 1.0 - p_win - p_push)
    candidate.expected_roi = p_win * (candidate.odd - 1.0) - p_lose
    break_even_win = (1.0 - p_push) / candidate.odd
    candidate.value_edge_pp = (p_win - break_even_win) * 100.0

    protected_probability = _clamp(p_win + 0.55 * p_push)
    probability_score = protected_probability * 100.0
    value_score = max(0.0, min(100.0, 50.0 + candidate.value_edge_pp * 3.0))
    market_score = max(40.0, min(100.0, 50.0 + candidate.market_pressure_pp * 5.0))
    data_score = candidate.data_quality * 100.0
    candidate.rating = round(
        0.45 * probability_score + 0.32 * value_score + 0.13 * market_score + 0.10 * data_score,
        1,
    )

    if candidate.odd < 1.25:
        candidate.blocks.append("price_too_low")
    if candidate.data_quality < 0.35:
        candidate.blocks.append("data_quality_too_low")
    if candidate.expected_roi < 0.01:
        candidate.blocks.append("no_positive_value")
    if candidate.goals_to_win >= 2 and minute > 65:
        candidate.blocks.append(f"two_goal_window_closed:{minute}>65")
    if candidate.rating < 62.0:
        candidate.blocks.append("router_rating_below_62")

    if p_push >= 0.08:
        candidate.reason_tags.append("push_protection")
    if candidate.value_edge_pp >= 8.0:
        candidate.reason_tags.append("strong_value")
    elif candidate.value_edge_pp >= 3.0:
        candidate.reason_tags.append("value")
    if candidate.market_pressure_pp >= 6.0:
        candidate.reason_tags.append("market_steam")
    if candidate.family == "team_total":
        candidate.reason_tags.append("team_specific")
    candidate.eligible = not candidate.blocks
    return candidate


def _winner_reason(candidate: MarketCandidate, minute: int) -> str:
    if candidate.family == "asian_match_total" and candidate.push_probability >= 0.08:
        return "Лучший баланс VALUE и вероятности: средняя линия даёт защиту возвратом."
    if candidate.family == "team_total":
        return "Выбран командный гол: цена лучше общего рынка при том же голевом сценарии."
    if candidate.family == "btts":
        return "ОЗ даёт лучшую цену на гол ещё не забившей команды."
    if candidate.goals_to_win == 1 and minute >= 61:
        return "На этой минуте один гол надёжнее агрессивного сценария +2."
    if candidate.goals_to_win >= 2:
        return "Есть запас времени и положительный VALUE для сценария двух голов."
    return "Лучший баланс вероятности, VALUE, рынка и качества LIVE-данных."


def route_market(
    candidates: list[MarketCandidate],
    *,
    minute: int,
    score: tuple[int, int],
    max_alternatives: int = 3,
) -> RouterDecision:
    scored = [score_candidate(row, minute) for row in candidates]
    eligible = sorted((row for row in scored if row.eligible), key=lambda row: (row.rating, row.expected_roi), reverse=True)
    rejected = sorted((row for row in scored if not row.eligible), key=lambda row: row.rating, reverse=True)
    if not eligible:
        return RouterDecision(
            status="WAIT", minute=minute, score=score, winner=None, alternatives=[], rejected=rejected,
            reason="Нет рынка, который одновременно проходит вероятность, VALUE, время и качество данных.",
        )

    # Keep only the best price/line for each football event in the final visible
    # shortlist. Correlated candidates still remain in `rejected`/raw snapshots
    # upstream, but Telegram never receives duplicate bets on the same goal.
    shortlist: list[MarketCandidate] = []
    seen: set[str] = set()
    for row in eligible:
        correlation = row.correlation_key or row.key
        if correlation in seen:
            continue
        seen.add(correlation)
        shortlist.append(row)

    winner = shortlist[0]
    alternatives = shortlist[1:1 + max(0, int(max_alternatives))]
    return RouterDecision(
        status="BET", minute=minute, score=score, winner=winner, alternatives=alternatives, rejected=rejected,
        reason=_winner_reason(winner, minute),
    )


def analyze_multi_match(
    match: dict[str, Any],
    market_row: dict[str, Any] | None,
    experts: dict[str, Any],
    *,
    data_quality: float = 1.0,
) -> RouterDecision:
    minute = int(match.get("minute") or 0)
    score = (int(match.get("home_score") or 0), int(match.get("away_score") or 0))
    candidates = build_goal_market_candidates(match, market_row, experts, data_quality=data_quality)
    return route_market(candidates, minute=minute, score=score)
