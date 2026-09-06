from __future__ import annotations

import os
import time
from collections import Counter
from typing import Any


_INSTALLED = False
_AUDIT_LAST: dict[str, tuple[float, str]] = {}


# These are production defaults, not hard overrides. Explicit gool.env values
# continue to win. The previous defaults stacked several independent filters on
# top of each other and made valid LIVE states / real market moves exceptionally
# rare even before Telegram delivery was reached.
_PRODUCTION_DEFAULTS = {
    # Goal State: raw side pressure already requires pressure + recent threat +
    # quality threat + evidence. Do not silently demand a much stronger second
    # confidence threshold afterwards.
    "GOOL_STATE_SIDE_PASS": "0.62",
    "GOOL_STATE_ANY_PASS": "0.64",
    "GOOL_STATE_FIRST_HALF_PASS": "0.64",
    # One-provider lower-league snapshots with a valid attack proxy used to land
    # around 0.33 and were rejected by the old 0.35 hard floor.
    "GOOL_MULTI_MIN_DATA_QUALITY": "0.30",
    # 1xBet STEAM still needs a fast, one-way move and related-market breadth,
    # but +8pp / 3 moves inside a ~2 minute history window was too exceptional.
    "XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP": "5.0",
    "XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES": "2",
    "XBET_STEAM_BREADTH_MIN_DELTA_PP": "2.0",
    "XBET_STEAM_BREADTH_MIN_ONE_WAY_MOVES": "1",
    "XBET_AUTONOMOUS_STEAM_MIN_RELATED_MARKETS": "1",
    "XBET_AUTONOMOUS_STEAM_EXTREME_DELTA_PP": "8.0",
    "XBET_AUTONOMOUS_STEAM_EXTREME_ONE_WAY_MOVES": "3",
    # Matchbook FLOW remains directional. We only reduce the old extreme
    # absolute move requirement; price, spread, score epoch and orderbook guards
    # remain intact in the underlying evaluator.
    "MATCHBOOK_FLOW_BET_MIN_DELTA_30S": "200",
    "MATCHBOOK_FLOW_BET_MIN_DELTA_60S": "400",
    "MATCHBOOK_FLOW_BET_MIN_RELATIVE_PCT": "6",
    "MATCHBOOK_FLOW_BET_MIN_FAIR_PP": "1.5",
    # A non-top league must be judged by unusually large money relative to its
    # own small market, not by Premier-League-sized absolute turnover.
    "MATCHBOOK_FLOW_NON_TOP_BIG_MARKET_VOLUME": "500",
    "MATCHBOOK_FLOW_NON_TOP_BIG_DELTA_30S": "150",
    "MATCHBOOK_FLOW_NON_TOP_BIG_DELTA_60S": "300",
    "MATCHBOOK_FLOW_NON_TOP_MIN_RELATIVE_PCT": "12",
    "MATCHBOOK_FLOW_NON_TOP_MIN_FAIR_PP": "1.0",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _rate_limited_log(key: str, message: str, *, interval: float = 60.0) -> None:
    now = time.monotonic()
    previous = _AUDIT_LAST.get(key)
    if previous is not None:
        previous_at, previous_message = previous
        if previous_message == message and now - previous_at < interval:
            return
    _AUDIT_LAST[key] = (now, message)
    print(message, flush=True)


def _brain_pass_policy(decision: Any, experts: dict[str, Any]) -> Any:
    """Final ordinary-GOOL gate: Brain PASS + operationally tradable 1xBet quote.

    The previous isolation layer correctly removed VALUE/steam/Matchbook from the
    main decision, but then added a second rating>=70 veto after the Brain had
    already returned PASS. Rating remains useful for ranking/display, not as a
    second hidden decision engine.
    """
    from . import architecture_isolation as isolation
    from .value_bet_policy import ABSOLUTE_MIN_BET_ODD

    rows = isolation._all_candidates(decision)
    rejected: list[Any] = []
    eligible: list[Any] = []

    for row in rows:
        isolation._clear_main_market_influence(row)
        expert = isolation._expert_for(experts, row)
        state = str(expert.get("state") or ("PASS" if expert.get("passed") else "BORDERLINE")).upper()
        try:
            strength = float(expert.get("probability"))
        except (TypeError, ValueError):
            strength = _number(getattr(row, "model_probability", 0.0))
        strength = max(0.0, min(1.0, strength))
        quality = max(0.0, min(1.0, _number(getattr(row, "data_quality", 0.0))))

        # Confidence number for ordering/card display only. It cannot veto PASS.
        row.rating = round(max(0.0, min(99.0, 0.90 * strength * 100.0 + 0.10 * quality * 100.0)), 1)

        blocks = list(getattr(row, "blocks", []) or [])
        if _number(getattr(row, "odd", 0.0)) < ABSOLUTE_MIN_BET_ODD:
            blocks.append("price_too_low")
        if state == "HARD_NO":
            blocks.append("goal_state_hard_no")
        elif state == "BORDERLINE":
            blocks.append("goal_state_borderline")
        elif state == "NO_DATA":
            blocks.append("goal_state_no_data")
        elif state != "PASS":
            blocks.append("goal_state_not_pass")

        # Explicitly purge the obsolete second-Brain rating veto if it arrived
        # from an older candidate snapshot during a hot/recheck path.
        blocks = [block for block in blocks if str(block) != "goal_state_rating_below_70"]
        row.blocks = list(dict.fromkeys(str(block) for block in blocks if str(block)))
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
        blockers = Counter(
            str(block)
            for row in rejected
            for block in list(getattr(row, "blocks", []) or [])
            if str(block)
        )
        detail = ",".join(f"{name}:{count}" for name, count in blockers.most_common(4)) or "no_candidate"
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = f"WAIT: LIVE Brain не дал проходящий PASS/кэф; blockers={detail}."
        return decision

    decision.status = "BET"
    decision.winner = shortlist[0]
    decision.alternatives = shortlist[1:4]
    decision.reason = "GOOL LIVE Brain дал PASS; 1xBet используется только как источник актуального кэфа."
    return decision


def _steam_summary(record: dict[str, Any], market_row: dict[str, Any] | None, decision: Any) -> None:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "-")
    winner = getattr(decision, "winner", None)
    source = str(getattr(winner, "source", "") or "") if winner is not None else ""
    if source.startswith("1xbet:autonomous_steam"):
        message = (
            f"GOOL_AUDIT_STEAM match={mid} signal=1 market={getattr(winner, 'key', '-')} "
            f"delta_pp={_number(getattr(winner, 'market_pressure_pp', 0.0)):.2f}"
        )
        _rate_limited_log(f"steam:{mid}", message, interval=15.0)
        return

    if not isinstance(market_row, dict):
        message = f"GOOL_AUDIT_STEAM match={mid} signal=0 reason=no_1xbet_match"
        _rate_limited_log(f"steam:{mid}", message)
        return

    pressure = market_row.get("pressure") or {}
    max_delta = 0.0
    max_moves = 0
    for row in pressure.values() if isinstance(pressure, dict) else []:
        if not isinstance(row, dict):
            continue
        max_delta = max(max_delta, _number(row.get("prob_delta_pp")))
        try:
            max_moves = max(max_moves, int(row.get("one_way_moves") or 0))
        except (TypeError, ValueError):
            pass
    message = (
        f"GOOL_AUDIT_STEAM match={mid} signal=0 max_delta_pp={max_delta:.2f} "
        f"max_moves={max_moves} score_verified={market_row.get('score_verified')} "
        f"repricing_guard={bool(market_row.get('repricing_guard'))}"
    )
    _rate_limited_log(f"steam:{mid}", message)


def _audited_money_flow(record: dict[str, Any]) -> dict[str, Any]:
    from . import architecture_isolation as isolation

    info = dict(isolation.isolated_money_flow(record) or {})
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "-")
    if info.get("eligible"):
        message = (
            f"GOOL_AUDIT_FLOW match={mid} signal=1 level={info.get('level')} "
            f"delta={_number(info.get('volume_delta')):.0f} window={info.get('window')} "
            f"fair_pp={_number(info.get('fair_delta_pp')):+.2f} league={info.get('league_tier')}"
        )
        _rate_limited_log(f"flow:{mid}", message, interval=15.0)
    else:
        message = (
            f"GOOL_AUDIT_FLOW match={mid} signal=0 reason={info.get('reason') or 'unknown'} "
            f"league={info.get('league_tier') or '-'}"
        )
        _rate_limited_log(f"flow:{mid}", message)
    return info


def install_production_calibration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for name, value in _PRODUCTION_DEFAULTS.items():
        os.environ.setdefault(name, value)

    from . import goal_state_policy
    from . import multi_autonomous_steam as steam
    from . import multi_money_flow
    from . import multi_router

    # multi_router reads its data-quality floor at import time, so synchronize the
    # already-imported constant with the effective production environment.
    multi_router.MIN_DATA_QUALITY = _number(os.getenv("GOOL_MULTI_MIN_DATA_QUALITY"), 0.30)

    goal_state_policy.enforce_goal_state_policy = _brain_pass_policy

    original_apply_steam = steam.apply_autonomous_steam

    def audited_apply_steam(
        decision: Any,
        record: dict[str, Any],
        market_row: dict[str, Any] | None,
        *,
        data_quality: float,
    ) -> Any:
        result = original_apply_steam(
            decision,
            record,
            market_row,
            data_quality=data_quality,
        )
        _steam_summary(record, market_row, result)
        return result

    steam.apply_autonomous_steam = audited_apply_steam
    multi_money_flow.evaluate_money_flow = _audited_money_flow

    _INSTALLED = True
    print(
        "GOOL_AUDIT_CONFIG "
        f"telegram_mode={os.getenv('GOOL_MULTI_TELEGRAM_MODE', 'shadow')} "
        f"brain=PASS_ONLY min_data={multi_router.MIN_DATA_QUALITY:.2f} "
        f"side_pass={os.getenv('GOOL_STATE_SIDE_PASS')} any_pass={os.getenv('GOOL_STATE_ANY_PASS')} "
        f"steam_delta={os.getenv('XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP')} "
        f"steam_moves={os.getenv('XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES')} "
        f"flow30={os.getenv('MATCHBOOK_FLOW_BET_MIN_DELTA_30S')} "
        f"flow_fair_pp={os.getenv('MATCHBOOK_FLOW_BET_MIN_FAIR_PP')}",
        flush=True,
    )


__all__ = ["install_production_calibration"]
