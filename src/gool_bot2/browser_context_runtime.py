from __future__ import annotations

from typing import Any, Callable

from .browser_context_store import attach_browser_context


_INSTALLED = False


def install_browser_context_runtime() -> None:
    """Attach browser evidence before the Brain V3 memory snapshot is built.

    Install this after install_brain_v3_memory(): the wrapper then runs outside the
    memory wrapper, so fresh browser365 fallback stats are visible to provider_pair
    while stale/missing browser state remains completely non-fatal.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    from . import runtime_hardening as hardening

    original: Callable[[Any, dict[str, Any]], int] = hardening._safe_process

    def process_with_browser_context(self: Any, record: dict[str, Any]) -> int:
        try:
            attach_browser_context(record)
        except Exception as exc:
            match = record.get("match") or {}
            print(
                f"GOOL_BROWSER_ATTACH_ERROR match={match.get('flashscore_event_id') or '-'} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )
        return int(original(self, record) or 0)

    hardening._safe_process = process_with_browser_context
    _INSTALLED = True
    print("GOOL_BROWSER_CONTEXT_RUNTIME installed stats=fallback_only trends=capped_support", flush=True)


__all__ = ["install_browser_context_runtime"]
