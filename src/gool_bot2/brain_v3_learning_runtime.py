from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .brain_v3_learning import (
    bootstrap_journal_learning,
    calibrate_brain_v3_decision,
    install_brain_v3_learning,
    learn_settled_entry,
)


_INSTALLED = False


def install_brain_v3_learning_runtime() -> None:
    """Attach outcome learning without changing STEAM or MONEY FLOW behavior."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import brain_v3_decision as decision_module
    from . import multi_runtime as runtime
    from .journal import load_signal_journal, save_signal_journal

    original_evaluate: Callable[..., dict[str, Any]] = decision_module.evaluate_brain_v3
    original_settle: Callable[..., list[dict[str, Any]]] = runtime.settle_multi_journal

    def evaluate_with_learning(
        record: dict[str, Any],
        *,
        data_quality: float,
        prematch_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = original_evaluate(
            record,
            data_quality=data_quality,
            prematch_profile=prematch_profile,
        )
        return calibrate_brain_v3_decision(base)

    def settle_with_learning(record: dict[str, Any], journal_path: Path) -> list[dict[str, Any]]:
        # Absorb already-settled V3 bets once per worker start. This lets the new
        # learner start from the current clean Brain V3 epoch instead of throwing
        # away the first real results collected before this feature existed.
        try:
            rows = load_signal_journal(journal_path)
            if bootstrap_journal_learning(rows, journal_path):
                save_signal_journal(journal_path, rows)
        except Exception as exc:
            print(f"GOOL_BRAIN_V3_LEARN_BOOTSTRAP_ERROR {type(exc).__name__}:{exc}", flush=True)

        changed = original_settle(record, journal_path)
        if not changed:
            return changed

        try:
            rows = load_signal_journal(journal_path)
            index = {str(row.get("entry_key") or ""): row for row in rows}
            dirty = False
            for settled_copy in changed:
                key = str(settled_copy.get("entry_key") or "")
                persisted = index.get(key)
                if persisted is None:
                    continue
                review = learn_settled_entry(persisted, record)
                if review is None:
                    continue
                persisted["brain_v3_learning_review"] = review
                settled_copy["brain_v3_learning_review"] = review
                dirty = True
            if dirty:
                save_signal_journal(journal_path, rows)
        except Exception as exc:
            print(f"GOOL_BRAIN_V3_LEARN_SETTLEMENT_ERROR {type(exc).__name__}:{exc}", flush=True)
        return changed

    decision_module.evaluate_brain_v3 = evaluate_with_learning
    runtime.settle_multi_journal = settle_with_learning
    install_brain_v3_learning()
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_LEARNING_RUNTIME installed scope=ordinary_gool settlement_review=on adaptive_calibration=guarded",
        flush=True,
    )


__all__ = ["install_brain_v3_learning_runtime"]
