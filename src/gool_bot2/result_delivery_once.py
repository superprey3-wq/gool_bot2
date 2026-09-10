from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ledger_path(journal_path: Path) -> Path:
    raw = os.getenv("GOOL_RESULT_DELIVERY_LEDGER_PATH", "").strip()
    if raw:
        return Path(raw)
    path = Path(journal_path)
    return path.with_name(path.name + ".result-delivery.json")


def _row_identity(row: dict[str, Any]) -> str:
    entry_key = str(row.get("entry_key") or row.get("brain_signal_key") or "").strip()
    if not entry_key:
        score = row.get("score") or [0, 0]
        try:
            score_text = f"{int(score[0] or 0)}-{int(score[1] or 0)}"
        except Exception:
            score_text = "0-0"
        entry_key = ":".join(
            [
                str(row.get("match_id") or ""),
                str(row.get("strategy") or row.get("head") or ""),
                str(row.get("minute") or 0),
                score_text,
                str(row.get("market_key") or row.get("market") or ""),
            ]
        )
    result = str(row.get("result") or "").strip().lower()
    return f"{entry_key}|{result}"


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    payload["entries"] = entries
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    with _LOCK:
        lock_path = path.with_name(path.name + ".lock")
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


def reserve_result_delivery(journal_path: Path, row: dict[str, Any]) -> str | None:
    """Reserve one result before Telegram using storage independent of the journal.

    Settlement can be performed concurrently by the LIVE worker and menu
    reconciliation. Those writers can overwrite journal claim fields because
    they loaded an older journal snapshot. This sidecar ledger is never written
    by settlement, so the same entry_key+result cannot pass this final gate twice.
    """
    path = _ledger_path(Path(journal_path))
    key = _row_identity(row)
    if not key or key.endswith("|"):
        return None
    with _locked(path):
        payload = _read(path)
        entries = payload["entries"]
        if key in entries:
            return None
        token = uuid.uuid4().hex
        entries[key] = {
            "token": token,
            "reserved_at": _now(),
            "entry_key": str(row.get("entry_key") or row.get("brain_signal_key") or ""),
            "match_id": str(row.get("match_id") or ""),
            "result": str(row.get("result") or "").lower(),
            "sent": False,
        }
        payload["updated_at"] = _now()
        _write(path, payload)
        return token


def finalize_result_reservation(journal_path: Path, row: dict[str, Any], token: str, sent: int) -> bool:
    path = _ledger_path(Path(journal_path))
    key = _row_identity(row)
    with _locked(path):
        payload = _read(path)
        current = (payload.get("entries") or {}).get(key)
        if not isinstance(current, dict) or str(current.get("token") or "") != str(token or ""):
            return False
        if int(sent or 0) > 0:
            current["sent"] = True
            current["sent_at"] = _now()
            current["delivery_count"] = int(sent)
        else:
            # An explicit Telegram failure may retry later. A process crash after
            # reservation intentionally stays reserved: at-most-once beats spam.
            (payload.get("entries") or {}).pop(key, None)
        payload["updated_at"] = _now()
        _write(path, payload)
        return True


__all__ = ["reserve_result_delivery", "finalize_result_reservation"]
