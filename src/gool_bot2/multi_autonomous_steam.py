from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .multi_router import MarketCandidate, RouterDecision
from .value_bet_policy import ABSOLUTE_MIN_BET_ODD


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _enabled() -> bool:
    return str(os.getenv("GOOL_MULTI_AUTONOMOUS_STEAM", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _age(row: dict[str, Any] | None) -> float | None:
    raw = None if not row else row.get("captured_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _line(rows: list[dict[str, Any]], line: float) -> dict[str, Any] | None:
    for row in rows or []:
        try:
            if abs(float(row.get("line")) - line) < 1e-9:
                return dict(row)
        except (TypeError, ValueError):
            continue
    return None


def _fair(odd: Any, opposite: Any) -> float | None:
    try:
        a_odd = float(odd)
    except (TypeError, ValueError):
        return None
    if a_odd <= 1.0:
        return None
    a = 1.0 / a_odd
    try:
        b_odd = float(opposite)
    except (TypeError, ValueError):
        b_odd = 0.0
    if b_odd <= 1.0:
        return a
    b = 1.0 / b_odd
    return a / (a + b) if a + b else None


def _pressure_row(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = (row.get("pressure") or {}).get(key) or {}
    return dict(value) if isinstance(value, dict) else {}


def _pressure_values(row: dict[str, Any], key: str) -> tuple[float, int]:
    pressure = _pressure_row(row, key)
    try:
        delta = float(pressure.get("prob_delta_pp") or 0.0)
    except (TypeError, ValueError):
        delta = 0.0
    try:
        moves = int(pressure.get("one_way_moves") or 0)
    except (TypeError, ValueError):
        moves = 0
    return delta, moves


def _related_pressure_keys(
    *,
    strategy: str,
    hs: int,
    aws: int,
    target_key: str,
) -> list[str]:
    """Return markets that should reprice in the same football direction.

    We deliberately use only nearby x.5 goal markets. A second market is
    confirmation, not another independent prediction.
    """
    total = hs + aws
    another = f"match_total:{float(total + 0.5)}"
    two_more = f"match_total:{float(total + 1.5)}"
    home = f"home_total:{float(hs + 0.5)}"
    away = f"away_total:{float(aws + 0.5)}"
    btts = "btts_yes:None"

    if strategy == "steam_another_goal":
        keys = [home, away, two_more]
        if not (hs > 0 and aws > 0):
            keys.append(btts)
    elif strategy == "steam_two_more_goals":
        keys = [another, home, away]
        if not (hs > 0 and aws > 0):
            keys.append(btts)
    elif strategy == "steam_home_goal":
        keys = [another]
        if hs == 0:
            keys.append(btts)
        keys.append(two_more)
    elif strategy == "steam_away_goal":
        keys = [another]
        if aws == 0:
            keys.append(btts)
        keys.append(two_more)
    elif strategy == "steam_btts":
        keys = [another]
        if hs == 0:
            keys.append(home)
        if aws == 0:
            keys.append(away)
        keys.append(two_more)
    elif strategy == "steam_goal_before_ht":
        keys = [another, home, away]
        if not (hs > 0 and aws > 0):
            keys.append(btts)
    else:
        keys = []

    return list(dict.fromkeys(key for key in keys if key != target_key))


def _breadth_confirmation(
    *,
    row: dict[str, Any],
    strategy: str,
    hs: int,
    aws: int,
    target_key: str,
) -> dict[str, Any]:
    min_delta = max(0.0, _f("XBET_STEAM_BREADTH_MIN_DELTA_PP", 3.0))
    min_moves = max(1, _i("XBET_STEAM_BREADTH_MIN_ONE_WAY_MOVES", 1))
    related = _related_pressure_keys(strategy=strategy, hs=hs, aws=aws, target_key=target_key)

    supportive: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for key in related:
        delta, moves = _pressure_values(row, key)
        if delta == 0.0 and moves == 0 and not _pressure_row(row, key):
            continue
        item = {"key": key, "delta_pp": round(delta, 3), "moves": moves}
        observed.append(item)
        if delta >= min_delta and moves >= min_moves:
            supportive.append(item)

    strongest = max((float(item["delta_pp"]) for item in supportive), default=0.0)
    return {
        "count": len(supportive),
        "supportive": supportive,
        "observed": observed,
        "strongest_delta_pp": round(strongest, 3),
        "min_delta_pp": min_delta,
        "min_moves": min_moves,
    }


def _strength(delta: float, moves: int, quality: float, breadth_count: int) -> float:
    md = _f("XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP", 8.0)
    mm = _i("XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES", 3)
    mq = _f("XBET_AUTONOMOUS_STEAM_MIN_DATA_QUALITY", 0.45)
    score = (
        70
        + min(16, max(0.0, delta - md) * 2)
        + min(8, max(0, moves - mm) * 4)
        + min(5, max(0.0, quality - mq) * 10)
        + min(6, max(0, int(breadth_count)) * 3)
    )
    return round(max(70.0, min(99.0, score)), 1)


def _candidate(
    *,
    row: dict[str, Any],
    key: str,
    pkey: str,
    family: str,
    label: str,
    strategy: str,
    odd: Any,
    opposite: Any,
    goals: int,
    correlation: str,
    age: float,
    quality: float,
    hs: int,
    aws: int,
) -> MarketCandidate | None:
    try:
        current = float(odd)
    except (TypeError, ValueError):
        return None

    min_odd = max(ABSOLUTE_MIN_BET_ODD, _f("XBET_AUTONOMOUS_STEAM_MIN_ODD", 1.40))
    max_odd = max(min_odd, _f("XBET_AUTONOMOUS_STEAM_MAX_ODD", 2.50))
    if not min_odd <= current <= max_odd:
        return None

    pressure = _pressure_row(row, pkey)
    try:
        delta = float(pressure.get("prob_delta_pp") or 0.0)
        moves = int(pressure.get("one_way_moves") or 0)
        old = float(pressure.get("old_odd"))
    except (TypeError, ValueError):
        return None

    min_delta = _f("XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP", 8.0)
    min_moves = _i("XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES", 3)
    if delta < min_delta or moves < min_moves:
        return None
    if old <= current + max(0.0, _f("XBET_AUTONOMOUS_STEAM_MIN_ODD_DROP", 0.03)):
        return None

    breadth = _breadth_confirmation(
        row=row,
        strategy=strategy,
        hs=hs,
        aws=aws,
        target_key=pkey,
    )
    breadth_count = int(breadth["count"])

    extreme_delta = max(min_delta, _f("XBET_AUTONOMOUS_STEAM_EXTREME_DELTA_PP", 12.0))
    extreme_moves = max(min_moves, _i("XBET_AUTONOMOUS_STEAM_EXTREME_ONE_WAY_MOVES", 4))
    extreme = delta >= extreme_delta and moves >= extreme_moves
    required_breadth = max(0, _i("XBET_AUTONOMOUS_STEAM_MIN_RELATED_MARKETS", 1))
    if breadth_count < required_breadth and not extreme:
        return None

    strength = _strength(delta, moves, quality, breadth_count)
    related_text = ""
    if breadth_count:
        top = sorted(
            breadth["supportive"],
            key=lambda item: (float(item["delta_pp"]), int(item["moves"])),
            reverse=True,
        )[:3]
        related_text = " · связи=" + ",".join(
            f"{item['key']} {float(item['delta_pp']):+.1f}пп/{int(item['moves'])}x" for item in top
        )
    elif extreme:
        related_text = " · EXTREME без второго рынка"

    tags = [
        "autonomous_steam",
        "market_steam",
        "confidence_metric",
        f"market_breadth:{breadth_count}",
    ]
    if breadth_count:
        tags.append("multi_market_confirmation")
    if extreme and not breadth_count:
        tags.append("extreme_single_market")

    return MarketCandidate(
        key=key,
        family=family,
        label=label,
        odd=current,
        model_probability=strength / 100.0,
        push_probability=0.0,
        market_probability=_fair(current, opposite),
        goals_to_win=goals,
        correlation_key=correlation,
        strategy=strategy,
        source="1xbet:autonomous_steam",
        expert_passed=False,
        expert_blocks=["autonomous_steam_bypass"],
        market_pressure_pp=delta,
        market_level="AUTONOMOUS_STEAM",
        market_override=True,
        value_override=False,
        override_reason=(
            f"AUTONOMOUS STEAM · Δp={delta:.1f} п.п. · импульсов={moves} · "
            f"{old:.2f}→{current:.2f} · связанных={breadth_count}{related_text}"
        ),
        market_age_seconds=age,
        data_quality=max(0.0, min(1.0, float(quality))),
        rating=strength,
        expected_roi=0.0,
        value_edge_pp=0.0,
        eligible=True,
        reason_tags=tags,
    )


def build_autonomous_steam_candidates(
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    data_quality: float,
) -> list[MarketCandidate]:
    match = record.get("match") or {}
    if not _enabled() or not market_row or bool(match.get("is_finished")):
        return []
    if market_row.get("repricing_guard") or market_row.get("score_desync") or market_row.get("timeline_score_desync"):
        return []
    if market_row.get("score_verified") is False:
        return []

    hs, aws = int(match.get("home_score") or 0), int(match.get("away_score") or 0)
    if (int(market_row.get("score_home") or 0), int(market_row.get("score_away") or 0)) != (hs, aws):
        return []

    minute = int(match.get("minute") or 0)
    if minute <= 0:
        return []

    age = _age(market_row)
    if age is None or age > max(5.0, _f("XBET_AUTONOMOUS_STEAM_MAX_AGE_SECONDS", 30.0)):
        return []

    markets, total, out = market_row.get("markets") or {}, hs + aws, []

    def add(candidate: MarketCandidate | None) -> None:
        if candidate is not None:
            out.append(candidate)

    common = {"row": market_row, "age": age, "quality": data_quality, "hs": hs, "aws": aws}

    if minute > 0:
        line = total + 0.5
        target = _line(list(markets.get("match_total") or []), line)
        if target and target.get("over") is not None:
            add(_candidate(
                **common,
                key=f"match_total:{line:g}",
                pkey=f"match_total:{line}",
                family="match_total",
                label=f"ТБ {line:g}",
                strategy="steam_another_goal",
                odd=target.get("over"),
                opposite=target.get("under"),
                goals=1,
                correlation="any_next_goal",
            ))

    if minute > 0:
        line = total + 1.5
        target = _line(list(markets.get("match_total") or []), line)
        if target and target.get("over") is not None:
            add(_candidate(
                **common,
                key=f"match_total:{line:g}",
                pkey=f"match_total:{line}",
                family="match_total",
                label=f"ТБ {line:g}",
                strategy="steam_two_more_goals",
                odd=target.get("over"),
                opposite=target.get("under"),
                goals=2,
                correlation="two_goal_path",
            ))

    if minute > 0:
        for side, score, name, prefix in (
            ("home", hs, "home_total", "ИТБ1"),
            ("away", aws, "away_total", "ИТБ2"),
        ):
            line = score + 0.5
            target = _line(list(markets.get(name) or []), line)
            if target and target.get("over") is not None:
                add(_candidate(
                    **common,
                    key=f"{name}:{line:g}",
                    pkey=f"{name}:{line}",
                    family="team_total",
                    label=f"{prefix} {line:g}",
                    strategy=f"steam_{side}_goal",
                    odd=target.get("over"),
                    opposite=target.get("under"),
                    goals=1,
                    correlation=f"{side}_next_goal",
                ))

    if minute > 0 and not (hs > 0 and aws > 0):
        btts = markets.get("btts") or {}
        if btts.get("yes") is not None:
            correlation = "home_next_goal" if hs == 0 < aws else (
                "away_next_goal" if aws == 0 < hs else "both_teams_score"
            )
            add(_candidate(
                **common,
                key="btts_yes",
                pkey="btts_yes:None",
                family="btts",
                label="ОЗ — Да",
                strategy="steam_btts",
                odd=btts.get("yes"),
                opposite=btts.get("no"),
                goals=1,
                correlation=correlation,
            ))

    if 0 < minute <= 45 and not bool(match.get("is_halftime")):
        line = total + 0.5
        target = _line(list(markets.get("first_half_total") or []), line)
        if target and target.get("over") is not None:
            add(_candidate(
                **common,
                key=f"first_half_total:{line:g}",
                pkey=f"first_half_total:{line}",
                family="first_half_total",
                label=f"1Т ТБ {line:g}",
                strategy="steam_goal_before_ht",
                odd=target.get("over"),
                opposite=target.get("under"),
                goals=1,
                correlation="any_next_goal",
            ))
    return out


def apply_autonomous_steam(
    decision: RouterDecision,
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    data_quality: float,
) -> RouterDecision:
    rows = build_autonomous_steam_candidates(record, market_row, data_quality=data_quality)
    if not rows:
        return decision

    winner = max(
        rows,
        key=lambda item: (
            "multi_market_confirmation" in item.reason_tags,
            item.rating,
            item.market_pressure_pp,
            -item.goals_to_win,
            -item.odd,
        ),
    )
    old = decision.winner
    alternatives = ([old] if old is not None and old.key != winner.key else []) + [
        item for item in decision.alternatives
        if item.key != winner.key and (old is None or item.key != old.key)
    ]
    decision.status, decision.winner, decision.alternatives = "BET", winner, alternatives[:3]

    breadth_count = 0
    for tag in winner.reason_tags:
        if str(tag).startswith("market_breadth:"):
            try:
                breadth_count = int(str(tag).split(":", 1)[1])
            except ValueError:
                breadth_count = 0
            break

    if breadth_count:
        decision.reason = (
            f"Сверхсильный прогруз 1xBet подтверждён ещё {breadth_count} связанн. рынк.: "
            "движение устойчивое, счёт синхронен и цена свежая."
        )
    else:
        decision.reason = (
            "Экстремальный прогруз 1xBet прошёл автономный фильтр без второго рынка: "
            "движение превысило усиленный порог, счёт синхронен и цена свежая."
        )
    return decision
