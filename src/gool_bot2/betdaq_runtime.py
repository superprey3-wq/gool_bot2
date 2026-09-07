from __future__ import annotations

from typing import Any, Callable

_INSTALLED = False
_ORIGINAL_MATCHBOOK_EMIT: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


def install_betdaq_runtime() -> None:
    """Add BETDAQ MONEY FLOW beside Matchbook, never in place of it.

    The storage worker already calls ``multi_money_flow.maybe_emit_money_flow`` on
    every fresh football record. Wrap that one dispatch point so the original
    Matchbook system runs first and the new BETDAQ system runs independently
    afterwards. Neither exchange can suppress the other, and Brain V3 is untouched.
    """
    global _INSTALLED, _ORIGINAL_MATCHBOOK_EMIT
    if _INSTALLED:
        return

    from . import multi_money_flow
    from .multi_betdaq_money_flow import maybe_emit_betdaq_money_flow

    _ORIGINAL_MATCHBOOK_EMIT = multi_money_flow.maybe_emit_money_flow

    def emit_both(record: dict[str, Any]) -> dict[str, Any] | None:
        matchbook_result: dict[str, Any] | None = None
        betdaq_result: dict[str, Any] | None = None
        try:
            if _ORIGINAL_MATCHBOOK_EMIT is not None:
                matchbook_result = _ORIGINAL_MATCHBOOK_EMIT(record)
        except Exception as exc:
            print(
                f"GOOL_MATCHBOOK_MONEY_FLOW_RUNTIME_ERROR error={type(exc).__name__}:{exc}",
                flush=True,
            )
        try:
            betdaq_result = maybe_emit_betdaq_money_flow(record)
        except Exception as exc:
            print(
                f"GOOL_BETDAQ_MONEY_FLOW_RUNTIME_ERROR error={type(exc).__name__}:{exc}",
                flush=True,
            )
        return matchbook_result or betdaq_result

    multi_money_flow.maybe_emit_money_flow = emit_both
    _INSTALLED = True
    print(
        "GOOL_BETDAQ_RUNTIME installed mode=third_independent_system "
        "matchbook=preserved betdaq=separate_money_flow brain_v3=untouched",
        flush=True,
    )


__all__ = ["install_betdaq_runtime"]
