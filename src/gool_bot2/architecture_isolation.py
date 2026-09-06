from __future__ import annotations

import os
from typing import Any


_INSTALLED = False
_ORIGINAL_MONEY_FLOW_EVALUATOR = None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _threshold(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


def _all_candidates(decision: Any) -> list[Any]:
    rows: list[Any] = []
    seen: set[int] = set()
    for row in [getattr(decision, "winner", None), *list(getattr(decision, "alternatives", []) or []), *list(getattr(decision, "rejected", []) or [])]:
        if row is None or id(row) in seen:
            continue
        seen.add(id(row))
        rows.append(row)
    return rows


def _expert_for(experts: dict[str, Any], candidate: Any) -> dict[str, Any]:
    strategy_map = {
        "another_goal": "another_goal",
        "two_more_goals": "two_more_goals",
        "goal_before_ht": "goal_before_ht",
        "home_goal": "home_goal",
        "away_goal": "away_goal",
        "both_teams_to_score": "btts",
    }
    key = strategy_map.get(str(getattr(candidate, "strategy", "") or ""))
    return dict(experts.get(key) or {}) if key else {}


def _clear_main_market_influence(row: Any) -> None:
    """Leave only the tradable 1xBet quote on an ordinary GOOL candidate."""
    removable_exact = {
        "no_positive_value",
        "gool_wait_without_verified_override",
        "router_rating_below_62",
        "correlated_better_option",
        "goal_state_hard_no",
        "goal_state_borderline_without_market",
        "goal_state_no_data_without_market",
        "goal_state_rating_below_70",
        "goal_state_market_opposition",
        "goal_state_borderline",
        "goal_state_no_data",
    }
    row.blocks = [str(block) for block in list(getattr(row, "blocks", []) or []) if str(block) not in removable_exact]
    row.market_pressure_pp = 0.0
    row.market_level = "ODDS_ONLY"
    row.market_override = False
    row.value_override = False
    row.override_reason = None
    row.expected_roi = 0.0
    row.value_edge_pp = 0.0
    row.reason_tags = [
        str(tag)
        for tag in list(getattr(row, "reason_tags", []) or [])
        if not (
            str(tag) in {
                "market_override",
                "value_override",
                "strong_value",
                "value",
                "market_steam",
                "market_opposition",
                "strong_market_opposition",
                "matchbook_xbet_confluence",
                "matchbook_strong_opposition",
                "matchbook_opposition",
            }
            or str(tag).startswith("matchbook_")
            or str(tag).startswith("market_breadth:")
        )
    ]
    if "main_brain_live_only" not in row.reason_tags:
        row.reason_tags.append("main_brain_live_only")
    if "1xbet_odds_only" not in row.reason_tags:
        row.reason_tags.append("1xbet_odds_only")


def isolated_goal_state_policy(decision: Any, experts: dict[str, Any]) -> Any:
    """Ordinary GOOL is decided only by the LIVE football Brain.

    1xBet is deliberately reduced to a quote source for the ordinary system:
    the current market must exist, match the score epoch, be fresh and meet the
    absolute price floor, but line movement/VALUE/steam can neither create nor
    veto an ordinary GOOL signal. Autonomous 1xBet STEAM runs later as its own
    system and is not touched here.
    """
    from .value_bet_policy import ABSOLUTE_MIN_BET_ODD

    rows = _all_candidates(decision)
    rejected: list[Any] = []
    eligible: list[Any] = []
    min_rating = _threshold("GOOL_MULTI_MIN_RATING", 70.0)

    for row in rows:
        _clear_main_market_influence(row)
        expert = _expert_for(experts, row)
        state = str(expert.get("state") or ("PASS" if expert.get("passed") else "BORDERLINE")).upper()
        try:
            strength = float(expert.get("probability"))
        except (TypeError, ValueError):
            strength = _number(getattr(row, "model_probability", 0.0))
        strength = max(0.0, min(1.0, strength))
        data_quality = max(0.0, min(1.0, _number(getattr(row, "data_quality", 0.0))))

        # The ranking is football confidence plus the quality of the LIVE feed.
        # Bookmaker/exchange movement has exactly zero decision weight.
        row.rating = round(max(0.0, min(99.0, 0.90 * strength * 100.0 + 0.10 * data_quality * 100.0)), 1)

        if _number(getattr(row, "odd", 0.0)) < ABSOLUTE_MIN_BET_ODD:
            row.blocks.append("price_too_low")
        if state == "HARD_NO":
            row.blocks.append("goal_state_hard_no")
        elif state == "BORDERLINE":
            row.blocks.append("goal_state_borderline")
        elif state == "NO_DATA":
            row.blocks.append("goal_state_no_data")
        elif state != "PASS":
            row.blocks.append("goal_state_not_pass")
        if row.rating < min_rating:
            row.blocks.append("goal_state_rating_below_70")

        row.blocks = list(dict.fromkeys(str(block) for block in row.blocks if str(block)))
        row.eligible = not row.blocks
        (eligible if row.eligible else rejected).append(row)

    eligible.sort(
        key=lambda row: (
            _number(getattr(row, "rating", 0.0)),
            -int(_number(getattr(row, "goals_to_win", 1), 1.0)),
            _number(getattr(row, "odd", 0.0)),
        ),
        reverse=True,
    )

    shortlist: list[Any] = []
    seen: set[str] = set()
    for row in eligible:
        correlation = str(getattr(row, "correlation_key", "") or getattr(row, "key", ""))
        if correlation in seen:
            row.blocks.append("correlated_better_option")
            row.eligible = False
            rejected.append(row)
            continue
        seen.add(correlation)
        shortlist.append(row)

    decision.rejected = sorted(rejected, key=lambda row: _number(getattr(row, "rating", 0.0)), reverse=True)
    if not shortlist:
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = "WAIT: LIVE-мозг GOOL не дал PASS; движение 1xBet и Matchbook не могут изменить решение."
        return decision

    decision.status = "BET"
    decision.winner = shortlist[0]
    decision.alternatives = shortlist[1:4]
    decision.reason = "GOOL LIVE Brain дал PASS; 1xBet используется только как источник актуального кэфа."
    return decision


def _identity_matchbook_confirmation(decision: Any, record: dict[str, Any]) -> Any:
    """Matchbook never confirms/vetoes the main Brain; Money Flow uses it separately."""
    return decision


def _identity_another_goal_guard(
    decision: Any,
    record: dict[str, Any],
    experts: dict[str, Any],
    market_row: dict[str, Any] | None,
) -> Any:
    """Do not let 1xBet 1X2 movement veto the ordinary LIVE Brain."""
    return decision


def _identity_match_suitability(decision: Any, record: dict[str, Any]) -> Any:
    """The candidate/router already enforce current quote, score epoch and feed safety."""
    return decision


def _norm_league(value: Any) -> str:
    text = str(value or "").casefold()
    for char in ":_-–—./()[]":
        text = text.replace(char, " ")
    return " ".join(text.split())


def _top_league(league: Any) -> bool:
    name = _norm_league(league)
    if not name:
        return False

    custom = [
        _norm_league(item)
        for item in str(os.getenv("MATCHBOOK_FLOW_TOP_LEAGUES", "")).split(";")
        if _norm_league(item)
    ]
    if any(item in name for item in custom):
        return True

    exact = {
        "premier league",
        "english premier league",
        "la liga",
        "laliga",
        "serie a",
        "bundesliga",
        "ligue 1",
        "uefa champions league",
        "champions league",
        "uefa europa league",
        "europa league",
        "uefa conference league",
        "conference league",
    }
    if name in exact:
        return True

    patterns = (
        ("england", "premier league"),
        ("spain", "la liga"),
        ("spain", "laliga"),
        ("italy", "serie a"),
        ("germany", "bundesliga"),
        ("france", "ligue 1"),
        ("uefa", "champions league"),
        ("uefa", "europa league"),
        ("uefa", "conference league"),
    )
    return any(all(piece in name for piece in pattern) for pattern in patterns)


def _money_flow_context(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    exchange = dict(record.get("matchbook_exchange") or {})
    systems = exchange.get("systems") or {}
    context = dict(systems.get("money_flow") or {})
    if not context:
        minute = int(((record.get("match") or {}).get("minute") or 0))
        legacy_key = "goal_before_ht" if minute <= 45 else "another_goal"
        context = dict(systems.get(legacy_key) or {})
    return exchange, context


def _league_fields(record: dict[str, Any]) -> dict[str, Any]:
    league = str(((record.get("match") or {}).get("league") or ""))
    top = _top_league(league)
    return {
        "league": league,
        "league_tier": "top" if top else "non_top",
        "unusual_for_league": False,
    }


def isolated_money_flow(record: dict[str, Any]) -> dict[str, Any]:
    """Keep Money Flow autonomous and add a non-top-league big-money trigger.

    The normal Matchbook FLOW engine remains authoritative. The additional path
    activates only after all its liquidity/orderbook/price/post-goal safeguards
    passed and the sole failure was the standard flow threshold. A lower league
    can then signal on unusually large *new* matched money, but still needs a
    directional fair-probability move so balanced turnover is not called a bet.
    """
    if _ORIGINAL_MONEY_FLOW_EVALUATOR is None:
        return {"eligible": False, "reason": "money_flow_not_installed"}

    base = dict(_ORIGINAL_MONEY_FLOW_EVALUATOR(record) or {})
    league_fields = _league_fields(record)
    base.update(league_fields)
    if bool(base.get("eligible")):
        base["volume_signal"] = "standard_flow"
        return base

    if base.get("reason") != "money_flow_threshold_not_reached" or league_fields["league_tier"] == "top":
        base["volume_signal"] = "none"
        return base

    exchange, context = _money_flow_context(record)
    if not context:
        base["volume_signal"] = "none"
        return base

    flow = dict(context.get("flow") or {})
    market_volume = _number(context.get("volume"))
    min_market_volume = _threshold("MATCHBOOK_FLOW_NON_TOP_BIG_MARKET_VOLUME", 2500.0)
    min_relative = _threshold("MATCHBOOK_FLOW_NON_TOP_MIN_RELATIVE_PCT", 3.0)
    min_fair_pp = _threshold("MATCHBOOK_FLOW_NON_TOP_MIN_FAIR_PP", 1.0)
    windows = (
        ("30s", _threshold("MATCHBOOK_FLOW_NON_TOP_BIG_DELTA_30S", 750.0)),
        ("60s", _threshold("MATCHBOOK_FLOW_NON_TOP_BIG_DELTA_60S", 1250.0)),
    )

    chosen: dict[str, Any] | None = None
    for label, min_delta in windows:
        if not bool(flow.get(f"window_ready_{label}")):
            continue
        delta = _number(flow.get(f"volume_delta_{label}"))
        fair_pp = _number(flow.get(f"fair_over_delta_pp_{label}"))
        prior_volume = max(1.0, market_volume - delta)
        relative_pct = delta / prior_volume * 100.0
        if (
            market_volume >= min_market_volume
            and delta >= min_delta
            and relative_pct >= min_relative
            and fair_pp >= min_fair_pp
        ):
            chosen = {
                "window": label,
                "volume_delta": delta,
                "relative_pct": relative_pct,
                "fair_delta_pp": fair_pp,
                "absolute_delta_threshold": min_delta,
            }
            break

    if chosen is None:
        base["volume_signal"] = "none"
        return base

    over = context.get("over") or {}
    best_back = _number((over.get("best_back") or {}).get("odds"))
    best_lay = _number((over.get("best_lay") or {}).get("odds"))
    fair_over = _number(context.get("fair_over"))
    period = str(context.get("period") or "FT")
    family = "first_half_total" if period == "1H" else "match_total"
    back_wom = _number(flow.get("back_wom"), 0.5)
    orderflow_imbalance = _number(flow.get("orderflow_imbalance"))
    orderbook_streak = int(_number(flow.get("orderbook_support_streak")))
    orderbook_confirmations = int(_number(flow.get("orderbook_confirmation_count")))

    score = min(
        99.0,
        88.0
        + min(4.0, chosen["fair_delta_pp"] * 1.5)
        + min(3.0, chosen["relative_pct"] * 0.15)
        + min(3.0, chosen["volume_delta"] / max(1.0, chosen["absolute_delta_threshold"]))
        + min(1.0, market_volume / max(1.0, min_market_volume) * 0.25),
    )
    previous_fair = max(0.0, fair_over - chosen["fair_delta_pp"] / 100.0)
    return {
        "eligible": True,
        "level": "NON_TOP_BIG_MONEY",
        "score": round(score, 1),
        "strategy": "money_flow",
        "reference_strategy": "money_flow",
        "period": period,
        "market_family": family,
        "line": _number(context.get("line")),
        "odd": best_back,
        "lay_odd": best_lay,
        "fair_over": fair_over,
        "previous_fair_over": previous_fair,
        "market_volume": market_volume,
        "market_id": context.get("market_id"),
        "market_name": context.get("market_name"),
        "matchbook_event_id": ((exchange.get("event") or {}).get("id")),
        "orderbook_ready": bool(flow.get("orderbook_ready")),
        "orderbook_confirmed": bool(flow.get("orderbook_confirmed")),
        "orderbook_confirmations": orderbook_confirmations,
        "back_wom": back_wom,
        "book_imbalance": _number(flow.get("book_imbalance")),
        "orderflow_imbalance": orderflow_imbalance,
        "orderbook_support_streak": orderbook_streak,
        "back_depth_weighted": _number(flow.get("back_depth_weighted")),
        "lay_depth_weighted": _number(flow.get("lay_depth_weighted")),
        **chosen,
        **league_fields,
        "unusual_for_league": True,
        "volume_signal": "non_top_big_money",
        "reason": "Unusually large new Matchbook money for a non-top league with directional fair-probability support.",
    }


def install_architecture_isolation() -> None:
    global _INSTALLED, _ORIGINAL_MONEY_FLOW_EVALUATOR
    if _INSTALLED:
        return

    from . import goal_state_policy
    from . import multi_another_goal_guard
    from . import multi_exchange_confirmation
    from . import multi_match_intelligence
    from . import multi_money_flow

    _ORIGINAL_MONEY_FLOW_EVALUATOR = multi_money_flow.evaluate_money_flow
    goal_state_policy.enforce_goal_state_policy = isolated_goal_state_policy
    multi_exchange_confirmation.apply_matchbook_confirmation = _identity_matchbook_confirmation
    multi_another_goal_guard.enforce_another_goal_context = _identity_another_goal_guard
    multi_match_intelligence.enforce_match_suitability = _identity_match_suitability
    multi_money_flow.evaluate_money_flow = isolated_money_flow

    _INSTALLED = True


__all__ = ["install_architecture_isolation", "isolated_goal_state_policy", "isolated_money_flow"]
