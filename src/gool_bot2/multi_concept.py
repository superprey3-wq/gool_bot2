from __future__ import annotations

from typing import Any

from .brain_card_restore import install_brain_card_patch
from .brain_journal_results import install_brain_journal_results
from .brain_primary_mode import install_runtime_patches
from .journal_in_game import install_journal_in_game
from .multi_router import RouterDecision
from .result_delivery_guard import install_result_delivery_guard
from .stale_replay_guard import install_stale_replay_guard
from .value_bet_policy import ABSOLUTE_MIN_BET_ODD

FIRST_HALF_STRATEGY = "goal_before_ht"
SECOND_HALF_STRATEGY = "another_goal"
FIRST_HALF_MAX_MINUTE = 35
SECOND_HALF_MIN_MINUTE = 46
SECOND_HALF_MAX_MINUTE = 75
MIN_BET_ODD = ABSOLUTE_MIN_BET_ODD

# multi_runtime imports this module before importing multi_delivery, so install
# the stale/replay guard here. That ensures the runtime receives the guarded
# pending-result function from its very first event after process startup.
install_stale_replay_guard()


def ordinary_strategy(match: dict[str, Any]) -> str | None:
    """Return the only ordinary GOOL strategy allowed in the current phase.

    Production concept:
    - first half: goal before half-time only, entries through 35';
    - second half: one more goal only, from 46' through 75';
    - ordinary GOOL is decided by the main LIVE Brain; bookmaker odds are
      optional information and never confirmation/veto;
    - autonomous 1xBet STEAM is a separate market system and has no minute cap
      while the match and its market are genuinely LIVE.
    """
    if bool(match.get("is_finished")) or bool(match.get("is_halftime")):
        return None

    try:
        minute = int(match.get("minute") or 0)
    except (TypeError, ValueError):
        minute = 0

    if 0 < minute <= FIRST_HALF_MAX_MINUTE:
        return FIRST_HALF_STRATEGY
    if SECOND_HALF_MIN_MINUTE <= minute <= SECOND_HALF_MAX_MINUTE:
        return SECOND_HALF_STRATEGY
    return None


def routing_experts(match: dict[str, Any], experts: dict[str, Any]) -> dict[str, Any]:
    """Expose only the active ordinary system to the Brain-primary router.

    The runtime patch is installed lazily here, after ``multi_runtime`` has
    finished importing. Ordinary GOOL remains Brain-primary, autonomous 1xBet
    STEAM stays independent. Telegram result delivery is guarded at the final
    sender and "В игре" is sourced from delivered, unsettled journal entries.
    Flashscore is used to settle those entries, not to decide whether a bet
    existed.
    """
    install_runtime_patches()
    install_brain_card_patch()
    install_brain_journal_results()
    install_result_delivery_guard()
    install_journal_in_game()
    strategy = ordinary_strategy(match)
    if strategy is None or strategy not in experts:
        return {}
    return {strategy: experts[strategy]}


def enforce_entry_cutoff(decision: RouterDecision) -> RouterDecision:
    """Keep the 75' cutoff for ordinary GOOL, never for autonomous STEAM."""
    if decision.status != "BET" or decision.winner is None:
        return decision

    winner = decision.winner
    if str(getattr(winner, "source", "")).startswith("1xbet:autonomous_steam"):
        return decision
    if int(decision.minute) <= SECOND_HALF_MAX_MINUTE:
        return decision

    if "concept_entry_after_75" not in winner.blocks:
        winner.blocks.append("concept_entry_after_75")
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)
    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = "WAIT: обычный GOOL запрещает новые входы после 75-й минуты."
    return decision
