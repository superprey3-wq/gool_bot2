from __future__ import annotations

import threading
from typing import Any, Callable


_INSTALLED = False
_LOCK = threading.RLock()
_UNSTABLE_SCAN: dict[str, int] = {}


def _mid(record: dict[str, Any]) -> str:
    return str(((record.get("match") or {}).get("flashscore_event_id") or "")).strip()


def install_brain_v3_candidate_stability() -> None:
    """Keep a deteriorating candidate blocked until a later fresh football scan.

    The selection tournament correctly detects a drop on a new football snapshot.
    A later market-only recheck must not erase that warning: odds changing is not
    evidence that the football state recovered. Recovery therefore requires a new
    field scan whose football candidate no longer deteriorates.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from . import brain_v3_selection_hardening as selection

    original: Callable[..., dict[str, Any]] = selection._apply_tournament
    original_reset = selection._reset_pool_for_tests

    def stable_apply(
        record: dict[str, Any],
        experts: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        out = original(record, experts, decision)
        if not isinstance(out, dict):
            return out

        match_id = _mid(record)
        if not match_id:
            return out

        tournament = dict(out.get("selection_tournament") or {})
        scan_id = int(tournament.get("last_scan_id") or 0)
        deteriorating_now = bool(tournament.get("deteriorating"))
        market_recheck = bool(record.get("runtime_market_recheck"))

        with _LOCK:
            if deteriorating_now and scan_id > 0:
                _UNSTABLE_SCAN[match_id] = scan_id
            elif not market_recheck:
                unstable_scan = int(_UNSTABLE_SCAN.get(match_id) or 0)
                # Only a genuinely newer football scan can clear the warning.
                if unstable_scan > 0 and scan_id > unstable_scan:
                    _UNSTABLE_SCAN.pop(match_id, None)
            unstable_scan = int(_UNSTABLE_SCAN.get(match_id) or 0)

        if unstable_scan <= 0:
            return out

        blocks = [str(x) for x in list(out.get("blocks") or [])]
        if "brain_v3_selection_candidate_deteriorating" not in blocks:
            blocks.append("brain_v3_selection_candidate_deteriorating")
        out["blocks"] = blocks
        if str(out.get("status") or "") == "BET":
            out["status"] = "READY"

        tournament["deteriorating"] = True
        tournament["deterioration_sticky"] = True
        tournament["unstable_since_scan"] = unstable_scan
        tournament["verdict"] = "BLOCK_DETERIORATING"
        out["selection_tournament"] = tournament

        audit = dict(out.get("selection_audit") or {})
        audit["tournament"] = tournament
        audit["why_this_match"] = "block_deteriorating"
        out["selection_audit"] = audit
        selection._sync_expert(experts, out)

        print(
            f"GOOL_BRAIN_V3_STABILITY match={match_id} scan={scan_id} "
            f"unstable_since={unstable_scan} action=block_until_new_football_scan",
            flush=True,
        )
        return out

    def reset_all() -> None:
        original_reset()
        with _LOCK:
            _UNSTABLE_SCAN.clear()

    selection._apply_tournament = stable_apply
    selection._reset_pool_for_tests = reset_all
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_STABILITY installed rule=deterioration_survives_market_rechecks",
        flush=True,
    )


__all__ = ["install_brain_v3_candidate_stability"]
