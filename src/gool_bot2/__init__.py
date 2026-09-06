"""gool_bot2 live-football probability engine."""

from typing import Any

from . import goal_state_policy as _goal_state_policy
from .architecture_isolation import install_architecture_isolation
from .brain_v3_memory import install_brain_v3_memory
from .live_goal_hazard import install_live_goal_hazard
from .multi_money_flow_total_volume import install_money_flow_total_volume
from .production_calibration import install_production_calibration
from .runtime_fastlane import install_runtime_fastlane
from .steam_quality_hardening import install_steam_quality_hardening


_ORIGINAL_GOAL_STATE_POLICY = _goal_state_policy.enforce_goal_state_policy


def _final_live_brain_policy(decision: Any, experts: dict[str, Any]) -> Any:
    """Ordinary GOOL = 100% LIVE Brain; 1xBet contributes only the quote."""
    seen: set[int] = set()
    rows = [
        getattr(decision, "winner", None),
        *list(getattr(decision, "alternatives", []) or []),
        *list(getattr(decision, "rejected", []) or []),
    ]
    for row in rows:
        if row is None or id(row) in seen:
            continue
        seen.add(id(row))
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
    return _ORIGINAL_GOAL_STATE_POLICY(decision, experts)


# Install the older compatibility/calibration layers first. The final rules below
# intentionally run last so emergency calibration cannot reintroduce weak public
# signals or bypass total-volume money-flow intelligence.
install_architecture_isolation()
install_production_calibration()
_goal_state_policy.enforce_goal_state_policy = _final_live_brain_policy
install_money_flow_total_volume()
install_live_goal_hazard()
install_steam_quality_hardening()
install_runtime_fastlane()
install_brain_v3_memory()
