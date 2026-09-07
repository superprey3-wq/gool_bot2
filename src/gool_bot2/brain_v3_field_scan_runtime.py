from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import runtime_hardening as hardening


_INSTALLED = False


def install_brain_v3_field_scan_runtime() -> None:
    """Mark complete football-field passes without double-processing the batch.

    runtime_fastlane already coalesces the newest row per LIVE match.  We wrap one
    run_once call as one field scan. Fresh football rows get the new scan id while
    market-only rechecks get the latest *completed* scan id. Selection can therefore
    require two completed football passes and cannot mistake repeated price rechecks
    of one old snapshot for fresh confirmation.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    original: Callable[[Any, Path], int] = hardening.hardened_run_once

    def field_run_once(self: Any, raw_dir: Path) -> int:
        completed = int(getattr(self, "_runtime_field_scan_completed_round", 0) or 0)
        scan_id = completed + 1
        fresh_seen = False
        original_process = self._process

        def field_process(record: dict[str, Any]) -> int:
            nonlocal fresh_seen
            if bool(record.get("runtime_market_recheck")):
                record["runtime_field_scan_confirmed_round"] = completed
            else:
                fresh_seen = True
                record["runtime_field_scan_id"] = scan_id
                # During this pass only the previous field is confirmed. The new
                # round becomes confirmed strictly after original() returns.
                record["runtime_field_scan_confirmed_round"] = completed
            return int(original_process(record) or 0)

        self._process = field_process
        try:
            emitted = int(original(self, raw_dir) or 0)
        finally:
            self._process = original_process

        if fresh_seen:
            self._runtime_field_scan_completed_round = scan_id
            print(
                f"GOOL_FIELD_SCAN completed round={scan_id} previous={completed} "
                f"next_bet_confirmation_round={scan_id}",
                flush=True,
            )
        return emitted

    hardening.hardened_run_once = field_run_once
    _INSTALLED = True
    print(
        "GOOL_FIELD_SCAN installed rule=two_completed_football_passes market_rechecks=freshness_only",
        flush=True,
    )


__all__ = ["install_brain_v3_field_scan_runtime"]
