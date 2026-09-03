from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .market_override_policy import can_override_another_goal, decorate_market_info
from .value_bet_policy import attach_value
from .xbet_market_pressure import evaluate_system


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
    strategy: str = ""
    source: str = "gool"
    expert_passed: bool = True
    expert_blocks: list[str] = field(default_factory=list)
    market_pressure_pp: float = 0.0
    market_level: str = "NEUTRAL"
    market_override: bool = False
    value_override: bool = False
    override_reason: str | None = None
    market_age_seconds: float | None = None
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


def _expert(experts: dict[str, Any], key: str) -> tuple[float | None, str, bool, list[str]]:
    raw = experts.get(key)
    if raw is None:
        return None, "missing", False, []
    if isinstance(raw, (int, float)):
        return _clamp(float(raw)), key, True, []
    if isinstance(raw, dict):
        value = raw.get("probability")
        if value is None:
            value = raw.get("confidence")
        if value is None:
            return None, str(raw.get("source") or key), bool(raw.get("passed", True)), []
        blocks = [str(x) for x in (raw.get("blocks") or []) if str(x)]
        return (
            _clamp(float(value)),
            str(raw.get("source") or key),
            bool(raw.get("passed", True)),
            blocks,
        )
    return None, "missing", False, []


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
    """Return signed 1xBet implied-probability movement for the selection.

    Positive values are steam toward the GOOL selection. Negative values mean
    the bookmaker market is moving against it and must remain visible to the
    router instead of being silently converted to NEUTRAL.
    """
    key = f"{market}:{line}"
    try:
        return float(((market_row.get("pressure") or {}).get(key) or {}).get("prob_delta_pp") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _age_seconds(market_row: dict[str, Any] | None) -> float | None:
    if not market_row or not market_row.get("captured_at"):
        return None
    try:
        dt = datetime.fromisoformat(str(market_row["captured_at"]).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _first_half_market_info(
    market_row: dict[str, Any],
    *,
    probability: float,
    hs: int,
    aws: int,
    source: str,
) -> dict[str, Any]:
    line = hs + aws + 0.5
    item = _row(list(((market_row.get("markets") or {}).get("first_half_total") or [])), line)
    pressure = (market_row.get("pressure") or {}).get(f"first_half_total:{line}") or {}
    delta = float(pressure.get("prob_delta_pp") or 0.0)
    moves = int(pressure.get("one_way_moves") or 0)
    available = bool(item and item.get("over"))
    if not available:
        level = "NO_DATA"
        confirmed = False
    elif delta >= 6.0 and moves >= 2:
        level = "STRONG_STEAM"
        confirmed = True
    elif delta >= 3.0:
        level = "PRESSURE"
        confirmed = True
    else:
        level = "NEUTRAL"
        confirmed = False
    info: dict[str, Any] = {
        "available": available,
        "confirmed": confirmed,
        "level": level,
        "score_pp": round(delta, 2),
        "line_move": bool(market_row.get("line_move")),
        "targets": [{
            "market": "first_half_total",
            "line": line,
            "weight": 1.0,
            "label": f"1Т ТБ {line:g}",
            "selection": None if not available else {
                "market": "first_half_total",
                "line": line,
                "odd": float(item["over"]),
                "opposite": item.get("under"),
            },
            "prob_delta_pp": delta,
            "one_way_moves": moves,
            "old_odd": pressure.get("old_odd"),
            "new_odd": None if not available else float(item["over"]),
        }],
        "head": "goal_before_ht",
        "reason": f"1xBet {level} · 1Т Δp={delta:.1f} п.п.",
    }
    info = decorate_market_info(info, market_row)
    return attach_value(info, probability, probability_source=source)


def _market_info(
    market_row: dict[str, Any],
    *,
    head: str,
    probability: float,
    hs: int,
    aws: int,
    source: str,
    selected_side: str | None = None,
) -> dict[str, Any]:
    if head == "goal_before_ht":
        return _first_half_market_info(
            market_row, probability=probability, hs=hs, aws=aws, source=source
        )
    info = evaluate_system(market_row, head, hs, aws, selected_side)
    info = decorate_market_info(info, market_row)
    info = attach_value(info, probability, probability_source=source)
    if head == "another_goal" and info.get("override"):
        info["override"] = bool(can_override_another_goal(info, probability))
        if not info["override"]:
            info["reason"] = "MARKET OVERRIDE suppressed: GOOL probability below another-goal floor"
    return info


def _candidate(
    *,
    key: str,
    family: str,
    strategy: str,
    label: str,
    odd: float,
    probability: float,
    expert_passed: bool,
    expert_blocks: list[str],
    push_probability: float = 0.0,
    opposite_odd: float | None = None,
    goals_to_win: int = 1,
    correlation_key: str,
    source: str,
    market_pressure_pp: float = 0.0,
    market_info: dict[str, Any] | None = None,
    market_age_seconds: float | None = None,
    data_quality: float = 1.0,
) -> MarketCandidate:
    info = market_info or {}
    return MarketCandidate(
        key=key,
        family=family,
        strategy=strategy,
        label=label,
        odd=float(odd),
        model_probability=_clamp(probability),
        push_probability=_clamp(push_probability, 0.0, max(0.0, 1.0 - probability)),
        market_probability=_norm_probability(float(odd), opposite_odd),
        goals_to_win=goals_to_win,
        correlation_key=correlation_key,
        source=source,
        expert_passed=bool(expert_passed),
        expert_blocks=list(expert_blocks),
        market_pressure_pp=market_pressure_pp,
        market_level=str(info.get("level") or "NEUTRAL"),
        market_override=bool(info.get("override")),
        value_override=bool(info.get("value_override")),
        override_reason=str(info.get("reason") or info.get("value_reason") or "") or None,
        market_age_seconds=market_age_seconds,
        data_quality=_clamp(data_quality),
    )


def build_goal_market_candidates(
    match: dict[str, Any],
    market_row: dict[str, Any] | None,
    experts: dict[str, Any],
    *,
    data_quality: float = 1.0,
) -> list[MarketCandidate]:
    """Build GOOL candidates using classic x.5 totals only.

    Integer/quarter Asian totals are intentionally not built. During the first
    half the existing GOOL goal_before_ht model can compete for the real 1xBet
    1st-half total. GOOL WAIT/NO remains a soft state that only verified market
    or value override may revive; score/time/staleness guards stay hard.
    """
    if not market_row or bool(match.get("is_finished")):
        return []

    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    minute = int(match.get("minute") or 0)
    if int(market_row.get("score_home") or 0) != hs or int(market_row.get("score_away") or 0) != aws:
        return []

    markets = market_row.get("markets") or {}
    total = hs + aws
    age = _age_seconds(market_row)

    p1, src1, pass1, blocks1 = _expert(experts, "another_goal")
    p2, src2, pass2, blocks2 = _expert(experts, "two_more_goals")
    pht, srcht, passht, blocksht = _expert(experts, "goal_before_ht")
    ph, srch, passh, blocksh = _expert(experts, "home_goal")
    pa, srca, passa, blocksa = _expert(experts, "away_goal")
    pb, srcb, passb, blocksb = _expert(experts, "btts")
    if pb is None and hs == 0 < aws and ph is not None:
        pb, srcb, passb, blocksb = ph, srch, passh, list(blocksh)
    elif pb is None and aws == 0 < hs and pa is not None:
        pb, srcb, passb, blocksb = pa, srca, passa, list(blocksa)

    out: list[MarketCandidate] = []
    totals = list(markets.get("match_total") or [])

    if p1 is not None:
        line = total + 0.5
        item = _row(totals, line)
        if item and item.get("over"):
            info = _market_info(
                market_row, head="another_goal", probability=p1, hs=hs, aws=aws, source=src1
            )
            out.append(_candidate(
                key=f"match_total:{line:g}", family="match_total", strategy="another_goal",
                label=f"ТБ {line:g}", odd=float(item["over"]), probability=p1,
                expert_passed=pass1, expert_blocks=blocks1, opposite_odd=item.get("under"),
                goals_to_win=1, correlation_key="any_next_goal", source=src1,
                market_pressure_pp=_pressure(market_row, "match_total", line),
                market_info=info, market_age_seconds=age, data_quality=data_quality,
            ))

    if p2 is not None:
        line = total + 1.5
        item = _row(totals, line)
        if item and item.get("over"):
            info = _market_info(
                market_row, head="two_more_goals", probability=p2, hs=hs, aws=aws, source=src2
            )
            out.append(_candidate(
                key=f"match_total:{line:g}", family="match_total", strategy="two_more_goals",
                label=f"ТБ {line:g}", odd=float(item["over"]), probability=p2,
                expert_passed=pass2, expert_blocks=blocks2, opposite_odd=item.get("under"),
                goals_to_win=2, correlation_key="two_goal_path", source=src2,
                market_pressure_pp=_pressure(market_row, "match_total", line),
                market_info=info, market_age_seconds=age, data_quality=data_quality,
            ))

    if pht is not None and 0 < minute <= 45:
        line = total + 0.5
        item = _row(list(markets.get("first_half_total") or []), line)
        if item and item.get("over"):
            info = _market_info(
                market_row, head="goal_before_ht", probability=pht, hs=hs, aws=aws, source=srcht
            )
            out.append(_candidate(
                key=f"first_half_total:{line:g}", family="first_half_total", strategy="goal_before_ht",
                label=f"1Т ТБ {line:g}", odd=float(item["over"]), probability=pht,
                expert_passed=passht, expert_blocks=blocksht, opposite_odd=item.get("under"),
                goals_to_win=1, correlation_key="any_next_goal", source=srcht,
                market_pressure_pp=_pressure(market_row, "first_half_total", line),
                market_info=info, market_age_seconds=age, data_quality=data_quality,
            ))

    for side, score, probability, source, passed, expert_blocks, market_name, prefix in (
        ("home", hs, ph, srch, passh, blocksh, "home_total", "ИТБ1"),
        ("away", aws, pa, srca, passa, blocksa, "away_total", "ИТБ2"),
    ):
        if probability is None:
            continue
        line = score + 0.5
        item = _row(list(markets.get(market_name) or []), line)
        if item and item.get("over"):
            info = _market_info(
                market_row, head="team_to_score", probability=probability,
                hs=hs, aws=aws, source=source, selected_side=side,
            )
            out.append(_candidate(
                key=f"{market_name}:{line:g}", family="team_total", strategy=f"{side}_goal",
                label=f"{prefix} {line:g}", odd=float(item["over"]), probability=probability,
                expert_passed=passed, expert_blocks=expert_blocks, opposite_odd=item.get("under"),
                goals_to_win=1, correlation_key=f"{side}_next_goal", source=source,
                market_pressure_pp=_pressure(market_row, market_name, line),
                market_info=info, market_age_seconds=age, data_quality=data_quality,
            ))

    btts = markets.get("btts") or {}
    yes = btts.get("yes")
    no = btts.get("no")
    if yes and pb is not None and not (hs > 0 and aws > 0):
        target_side = None
        correlation = "both_teams_score"
        if hs == 0 < aws:
            target_side = "home"
            correlation = "home_next_goal"
        elif aws == 0 < hs:
            target_side = "away"
            correlation = "away_next_goal"
        info = _market_info(
            market_row, head="both_teams_to_score", probability=pb,
            hs=hs, aws=aws, source=srcb, selected_side=target_side,
        )
        out.append(_candidate(
            key="btts_yes", family="btts", strategy="both_teams_to_score",
            label="ОЗ — Да", odd=float(yes), probability=pb,
            expert_passed=passb, expert_blocks=blocksb, opposite_odd=no,
            goals_to_win=1, correlation_key=correlation, source=srcb,
            market_pressure_pp=_pressure(market_row, "btts_yes", None),
            market_info=info, market_age_seconds=age, data_quality=data_quality,
        ))

    return out


def _time_hard_block(candidate: MarketCandidate, minute: int) -> str | None:
    if minute < 10:
        return f"warmup_until_10:{minute}"
    if candidate.strategy == "goal_before_ht":
        if minute > 45:
            return f"first_half_window_closed:{minute}>45"
        return None
    if candidate.strategy == "two_more_goals":
        if minute > 65:
            return f"two_goal_window_closed:{minute}>65"
        return None
    if candidate.strategy == "another_goal":
        if minute > 85:
            return f"another_goal_window_closed:{minute}>85"
        return None
    if minute > 75:
        return f"entry_window_closed:{minute}>75"
    return None


def _override_allowed(candidate: MarketCandidate, minute: int) -> bool:
    if candidate.expert_passed:
        return True
    if candidate.strategy == "two_more_goals" and minute > 60:
        return False
    return bool(candidate.market_override or candidate.value_override)


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
    market_score = max(0.0, min(100.0, 50.0 + candidate.market_pressure_pp * 5.0))
    data_score = candidate.data_quality * 100.0
    override_bonus = 6.0 if candidate.market_override else (4.0 if candidate.value_override else 0.0)
    opposition_pp = max(0.0, -float(candidate.market_pressure_pp))
    opposition_penalty = min(12.0, max(0.0, opposition_pp - 3.0) * 2.0)
    candidate.rating = round(
        0.43 * probability_score
        + 0.31 * value_score
        + 0.16 * market_score
        + 0.10 * data_score
        + override_bonus
        - opposition_penalty,
        1,
    )

    hard_time = _time_hard_block(candidate, minute)
    if hard_time:
        candidate.blocks.append(hard_time)

    max_age = float(os.getenv("XBET_VALUE_MAX_AGE_SECONDS", "35"))
    if candidate.market_age_seconds is None:
        candidate.blocks.append("market_timestamp_missing")
    elif candidate.market_age_seconds > max_age:
        candidate.blocks.append(f"market_stale:{candidate.market_age_seconds:.1f}>{max_age:.1f}")

    if candidate.odd < 1.25:
        candidate.blocks.append("price_too_low")
    if candidate.data_quality < 0.35:
        candidate.blocks.append("data_quality_too_low")
    if candidate.expected_roi < 0.01:
        candidate.blocks.append("no_positive_value")
    if not _override_allowed(candidate, minute):
        candidate.blocks.append("gool_wait_without_verified_override")
    if candidate.rating < 62.0:
        candidate.blocks.append("router_rating_below_62")

    if not candidate.expert_passed and candidate.market_override:
        candidate.reason_tags.append("market_override")
    if not candidate.expert_passed and candidate.value_override:
        candidate.reason_tags.append("value_override")
    if p_push >= 0.08:
        candidate.reason_tags.append("push_protection")
    if candidate.value_edge_pp >= 8.0:
        candidate.reason_tags.append("strong_value")
    elif candidate.value_edge_pp >= 3.0:
        candidate.reason_tags.append("value")
    if candidate.market_pressure_pp >= 6.0:
        candidate.reason_tags.append("market_steam")
    elif candidate.market_pressure_pp <= -6.0:
        candidate.reason_tags.append("strong_market_opposition")
    elif candidate.market_pressure_pp <= -3.0:
        candidate.reason_tags.append("market_opposition")
    if candidate.family == "team_total":
        candidate.reason_tags.append("team_specific")
    if candidate.family == "first_half_total":
        candidate.reason_tags.append("first_half")

    candidate.eligible = not candidate.blocks
    return candidate


def _winner_reason(candidate: MarketCandidate, minute: int) -> str:
    if not candidate.expert_passed and candidate.market_override:
        return "1xBet MARKET OVERRIDE вернул мягко отклонённый GOOL-рынок; среди всех кандидатов он получил лучший рейтинг."
    if not candidate.expert_passed and candidate.value_override:
        return "Сильный VALUE вернул мягко отклонённый GOOL-рынок; после сравнения он оказался лучшим."
    if candidate.family == "first_half_total":
        return "Модель гола до перерыва и реальный тотал 1-го тайма дают лучший текущий баланс вероятности и цены."
    if candidate.family == "team_total":
        return "Выбран командный гол: цена лучше общего рынка при том же голевом сценарии."
    if candidate.family == "btts":
        return "ОЗ даёт лучший баланс цены и вероятности среди связанных голевых рынков."
    if candidate.strategy == "another_goal" and minute > 75:
        return "Позднее окно ещё одного гола активно до 85-й минуты; рынок проходит VALUE и LIVE-проверки."
    if candidate.goals_to_win == 1 and minute >= 61:
        return "На этой минуте один гол надёжнее агрессивного сценария +2."
    if candidate.goals_to_win >= 2:
        return "Есть запас времени и положительный VALUE для сценария двух голов."
    return "Лучший баланс вероятности, VALUE, движения 1xBet и качества LIVE-данных."


def route_market(
    candidates: list[MarketCandidate],
    *,
    minute: int,
    score: tuple[int, int],
    max_alternatives: int = 3,
) -> RouterDecision:
    scored = [score_candidate(row, minute) for row in candidates]
    eligible = sorted(
        (row for row in scored if row.eligible),
        key=lambda row: (row.rating, row.expected_roi, row.market_pressure_pp),
        reverse=True,
    )
    rejected = sorted((row for row in scored if not row.eligible), key=lambda row: row.rating, reverse=True)
    if not eligible:
        return RouterDecision(
            status="WAIT", minute=minute, score=score, winner=None, alternatives=[], rejected=rejected,
            reason="Нет рынка, который проходит GOOL/override, VALUE, время, свежесть и качество данных.",
        )

    shortlist: list[MarketCandidate] = []
    seen: set[str] = set()
    for row in eligible:
        correlation = row.correlation_key or row.key
        if correlation in seen:
            row.blocks.append("correlated_better_option")
            row.eligible = False
            rejected.append(row)
            continue
        seen.add(correlation)
        shortlist.append(row)

    winner = shortlist[0]
    alternatives = shortlist[1:1 + max(0, int(max_alternatives))]
    rejected.sort(key=lambda row: row.rating, reverse=True)
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
