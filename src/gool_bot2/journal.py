from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - production is Linux.
    fcntl = None


_JOURNAL_LOCK = threading.RLock()


def _legacy_journal_silenced() -> bool:
    return str(os.getenv("GOOL_LEGACY_JOURNAL_SILENT", "")).strip().lower() in {"1", "true", "yes", "on"}


def _decode_rows(path: Path) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, list) else None


def load_signal_journal(path: Path) -> list[dict[str, Any]]:
    """Read a journal without turning a transient/partial read into data loss.

    Writers use atomic replace, but older deployments used a shared ``.tmp`` file
    and could leave a malformed journal after concurrent writes. Retry briefly and
    then fall back to the last valid backup. Returning an empty list is reserved
    for a genuinely missing/empty journal, not a non-empty corrupted one.
    """
    if _legacy_journal_silenced():
        return []
    path = Path(path)
    if not path.exists():
        return []
    try:
        if path.stat().st_size == 0:
            return []
    except OSError:
        return []

    for delay in (0.0, 0.01, 0.03):
        if delay:
            time.sleep(delay)
        rows = _decode_rows(path)
        if rows is not None:
            return rows

    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        rows = _decode_rows(backup)
        if rows is not None:
            print(f"GOOL_JOURNAL_RECOVERED path={path} backup={backup}", flush=True)
            return rows

    print(f"GOOL_JOURNAL_READ_ERROR path={path} reason=invalid_json", flush=True)
    return []


def _current_is_safe_to_replace(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        if path.stat().st_size == 0:
            return True
    except OSError:
        return False
    return _decode_rows(path) is not None


def save_signal_journal(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomically persist rows using a unique temporary file.

    Refuse to overwrite a non-empty malformed current journal. That turns a
    storage race/corruption into a visible error instead of silently replacing
    real history with ``[]``. A valid previous copy is retained as ``.bak``.
    """
    if _legacy_journal_silenced():
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _current_is_safe_to_replace(path):
        raise RuntimeError(f"journal_refuse_overwrite_corrupt path={path}")

    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    backup = path.with_name(path.name + ".bak")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and _decode_rows(path) is not None:
            try:
                shutil.copy2(path, backup)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


@contextmanager
def journal_lock(path: Path) -> Iterator[None]:
    """Serialize read-modify-write journal transactions across worker threads/processes."""
    path = Path(path)
    with _JOURNAL_LOCK:
        lock_path = path.with_name(path.name + ".journal.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            handle.close()


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
    path = Path(path)
    with journal_lock(path):
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


__all__ = [
    "append_analysis",
    "entry_rows",
    "journal_lock",
    "load_signal_journal",
    "mark_in_game",
    "save_signal_journal",
]
