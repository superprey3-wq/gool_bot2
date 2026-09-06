from __future__ import annotations

import os
from pathlib import Path
from typing import Any


PUBLIC_RESET_ID = "live_brain70_total_volume_v2_2026_09_06"


def _runtime_root() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw) if raw else default


def reset_public_tracking_once(runtime: Path | None = None) -> dict[str, Any]:
    """Reset the public GOOL epoch exactly once after deployment.

    The marker is stored in persistent runtime storage. The first worker start
    after this code is deployed removes the public Multi/STEAM journal, its bank
    state and analysis log, plus the separate Matchbook MONEY FLOW journal and
    bank state. Subsequent restarts preserve every newly collected bet.
    """
    root = runtime or _runtime_root()
    live = root / "live"
    live.mkdir(parents=True, exist_ok=True)
    marker = live / f".gool_public_reset_{PUBLIC_RESET_ID}"
    if marker.exists():
        return {"reset": False, "marker": str(marker), "removed": []}

    multi_journal = _path_from_env("GOOL_MULTI_JOURNAL_PATH", live / "gool_multi_journal.json")
    multi_bank = _path_from_env(
        "GOOL_MULTI_BANK_STATE_PATH",
        multi_journal.with_name("gool_multi_bank_state.json"),
    )
    analysis_raw = os.getenv("GOOL_MULTI_ANALYSIS_PATH", "").strip() or os.getenv("GOOL_MULTI_SHADOW_PATH", "").strip()
    multi_analysis = Path(analysis_raw) if analysis_raw else live / "gool_multi_analysis.jsonl"

    flow_journal = _path_from_env(
        "GOOL_MONEY_FLOW_JOURNAL_PATH",
        live / "gool_money_flow_journal.json",
    )
    flow_bank = _path_from_env(
        "GOOL_MONEY_FLOW_BANK_STATE_PATH",
        flow_journal.with_name("gool_money_flow_bank_state.json"),
    )

    targets = (multi_journal, multi_bank, multi_analysis, flow_journal, flow_bank)
    removed: list[str] = []
    for path in targets:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            raise RuntimeError(f"public_epoch_reset_failed path={path} err={exc}") from exc

    marker.write_text(
        f"reset_id={PUBLIC_RESET_ID}\n"
        "systems=goal_before_ht,another_goal,1xbet_steam,matchbook_money_flow\n",
        "utf-8",
    )
    print(
        f"GOOL_BOOT public_tracking_reset={PUBLIC_RESET_ID} removed={len(removed)} "
        f"multi_journal={multi_journal} flow_journal={flow_journal}",
        flush=True,
    )
    return {"reset": True, "marker": str(marker), "removed": removed}


__all__ = ["PUBLIC_RESET_ID", "reset_public_tracking_once"]
