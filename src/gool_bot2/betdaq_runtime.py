from __future__ import annotations

import sys
from typing import Any

_INSTALLED = False


def install_betdaq_runtime() -> None:
    """Replace the old Matchbook context with BETDAQ while preserving interfaces.

    Existing MONEY FLOW code intentionally reads the legacy `matchbook_exchange`
    record key. We keep that key as an internal compatibility seam, but its data
    source and all new journal metadata are BETDAQ.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from . import matchbook_exchange
    from .betdaq_exchange import betdaq_context

    matchbook_exchange.matchbook_context = betdaq_context
    runtime = sys.modules.get("gool_bot2.multi_runtime")
    if runtime is not None:
        setattr(runtime, "matchbook_context", betdaq_context)

    # Keep the mature MONEY FLOW decision/settlement implementation but make new
    # rows explicit about the real exchange source.
    from . import multi_money_flow

    original_entry = multi_money_flow._entry

    def betdaq_entry(record: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
        row = dict(original_entry(record, info))
        row["signal_source"] = "BETDAQ_FLOW"
        row["source"] = "betdaq:money_flow"
        row["reason"] = (
            "Аномальный проторгованный объём BETDAQ подтверждён движением fair probability "
            "и устойчивым давлением биржевого стакана в сторону следующего гола."
        )
        tags = []
        for tag in row.get("reason_tags") or []:
            text = str(tag).replace("matchbook_", "betdaq_")
            tags.append(text)
        row["reason_tags"] = tags
        if isinstance(row.get("matchbook_flow"), dict):
            row["betdaq_flow"] = dict(row["matchbook_flow"])
        return row

    multi_money_flow._entry = betdaq_entry
    _INSTALLED = True
    print(
        "GOOL_BETDAQ_RUNTIME installed exchange=BETDAQ anonymous_aapi money_flow=independent legacy_key=compat_only",
        flush=True,
    )


__all__ = ["install_betdaq_runtime"]
