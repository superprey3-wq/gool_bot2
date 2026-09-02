from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_signal_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def save_signal_journal(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _trim_analysis_if_needed(path: Path) -> None:
    """Keep diagnostic JSONL bounded; signal/result journals remain untouched."""
    max_bytes = max(256 * 1024, int(os.getenv("ANALYSIS_MAX_BYTES", str(6 * 1024 * 1024))))
    keep_bytes = max(128 * 1024, int(os.getenv("ANALYSIS_KEEP_BYTES", str(4 * 1024 * 1024))))
    keep_bytes = min(keep_bytes, max_bytes)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size <= max_bytes:
        return
    try:
        start = max(0, size - keep_bytes)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
        if start and b"\n" in data:
            data = data.split(b"\n", 1)[1]
        with path.open("wb") as handle:
            handle.write(data)
        print(f"ANALYSIS_ROTATE file={path.name} before={size} after={len(data)}", flush=True)
    except Exception as exc:
        print(f"ANALYSIS_ROTATE_ERROR file={path.name} err={type(exc).__name__}:{exc}", flush=True)


def append_analysis(path: Path, row: dict[str, Any]) -> None:
    """Append current decisions for audit while bounding disposable diagnostics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _trim_analysis_if_needed(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def mark_in_game(path: Path, match_id: str, head: str, chat_id: str | int | None = None) -> bool:
    """Mark the newest matching signal as an actual user entry."""
    rows = load_signal_journal(path)
    target: dict[str, Any] | None = None
    for row in reversed(rows):
        if str(row.get("match_id")) != str(match_id):
            continue
        if str(row.get("head")) != str(head):
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        target = row
        break
    if target is None:
        return False
    if bool(target.get("in_game")):
        return True
    target["in_game"] = True
    target["entered_at"] = datetime.now(timezone.utc).isoformat()
    if chat_id is not None:
        target["entered_by_chat_id"] = str(chat_id)
    save_signal_journal(path, rows)
    return True


def entry_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return actual entries; legacy rows without in_game remain compatible."""
    return [row for row in rows if bool(row.get("in_game"))]
