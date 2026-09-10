from __future__ import annotations

from typing import Any


_INSTALLED = False
_ORIGINAL_GUARD: Any = None


def _guard(decision: Any, record: dict[str, Any], experts: dict[str, Any], market_row: dict[str, Any] | None) -> Any:
    """Use the Brain post-goal guard only for real RouterDecision objects.

    The production wrapper owns ordinary Brain decisions, but the underlying
    LIVE-only helper is also a public compatibility seam used by diagnostics and
    tests. Non-router/legacy calls must keep delegating to the original helper so
    its prematch-profile isolation behavior remains intact.
    """
    from . import brain_primary_mode as brain

    if not hasattr(decision, "winner"):
        return _ORIGINAL_GUARD(decision, record, experts, market_row) if _ORIGINAL_GUARD is not None else decision
    return brain._brain_post_goal_only(decision, record, experts, market_row)


def install_production_guard_compat() -> None:
    global _INSTALLED, _ORIGINAL_GUARD
    if _INSTALLED:
        return

    from . import brain_primary_mode as brain
    from . import multi_runtime as runtime

    original = brain._ORIGINALS.get("another_goal_guard")
    if original is None:
        original = runtime._enforce_another_goal_context_for_mode
    _ORIGINAL_GUARD = original
    runtime._enforce_another_goal_context_for_mode = _guard
    _INSTALLED = True
    print("GOOL_GUARD_COMPAT installed legacy_delegate=on brain_post_goal=on", flush=True)


__all__ = ["install_production_guard_compat"]
