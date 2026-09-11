from __future__ import annotations

import html
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_INSTALLED = False
_WATCHDOG_STARTED = False
_PROCESS_LOCK = threading.RLock()
_STARTED_MONOTONIC = time.monotonic()
_ORIGINAL_PROCESS: Callable[..., Any] | None = None


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def _analysis_path() -> Path:
    raw = os.getenv("GOOL_MULTI_ANALYSIS_PATH", "").strip() or os.getenv("GOOL_MULTI_SHADOW_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multi_analysis.jsonl"


def _collector_health_path() -> Path:
    raw = os.getenv("LIVE_COVERAGE_HEALTH_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "collector_health.json"


def _worker_health_path() -> Path:
    raw = os.getenv("GOOL_ANALYSIS_WORKER_HEALTH_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "analysis_worker_health.json"


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _file_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _collector_snapshot() -> dict[str, Any]:
    payload = _read_json(_collector_health_path())
    captured = _parse_dt(payload.get("captured_at"))
    age = None
    if captured is not None:
        age = max(0.0, (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds())
    collector = payload.get("collector") if isinstance(payload.get("collector"), dict) else {}
    try:
        live = max(0, int(collector.get("live") or 0))
    except (TypeError, ValueError):
        live = 0
    return {"live": live, "age_seconds": age, "payload": payload}


def _write_worker_heartbeat(record: dict[str, Any], *, status: str, error: str = "") -> None:
    match = record.get("match") or {}
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error": error,
        "match_id": str(match.get("flashscore_event_id") or ""),
        "minute": int(match.get("minute") or 0),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "analysis_path": str(_analysis_path()),
    }
    try:
        _write_json(_worker_health_path(), payload)
    except Exception as exc:
        print(f"GOOL_ANALYSIS_HEARTBEAT_ERROR {type(exc).__name__}:{exc}", flush=True)


def _health_wrapped_process(self: Any, record: dict[str, Any]):
    """Observe the already-hardened production process without changing its logic."""
    if _ORIGINAL_PROCESS is None:
        return 0
    before = _file_mtime_ns(_analysis_path())
    try:
        result = _ORIGINAL_PROCESS(self, record)
    except Exception as exc:
        _write_worker_heartbeat(record, status="process_error", error=f"{type(exc).__name__}:{exc}")
        raise

    after = _file_mtime_ns(_analysis_path())
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    # A normal LIVE record should reach observe_multi_shadow and append one row.
    # Final/invalid-minute records are allowed to leave analysis unchanged.
    if minute > 0 and not finished and after == before:
        _write_worker_heartbeat(record, status="analysis_not_updated", error="process_returned_without_analysis_snapshot")
        print(
            f"GOOL_ANALYSIS_NOT_UPDATED match={match.get('flashscore_event_id')} minute={minute} score="
            f"{int(match.get('home_score') or 0)}:{int(match.get('away_score') or 0)}",
            flush=True,
        )
    else:
        _write_worker_heartbeat(record, status="ok")
    return result


def _watchdog_limits() -> tuple[float, float, float]:
    try:
        collector_fresh = max(60.0, float(os.getenv("GOOL_COLLECTOR_HEALTH_MAX_AGE_SECONDS", "180")))
    except (TypeError, ValueError):
        collector_fresh = 180.0
    try:
        analysis_stale = max(90.0, float(os.getenv("GOOL_ANALYSIS_WATCHDOG_STALE_SECONDS", "180")))
    except (TypeError, ValueError):
        analysis_stale = 180.0
    try:
        startup_grace = max(60.0, float(os.getenv("GOOL_ANALYSIS_WATCHDOG_STARTUP_GRACE_SECONDS", "150")))
    except (TypeError, ValueError):
        startup_grace = 150.0
    return collector_fresh, analysis_stale, startup_grace


def watchdog_should_restart() -> tuple[bool, dict[str, Any]]:
    collector_fresh, analysis_stale, startup_grace = _watchdog_limits()
    collector = _collector_snapshot()
    analysis_age = _file_age_seconds(_analysis_path())
    process_age = max(0.0, time.monotonic() - _STARTED_MONOTONIC)
    collector_age = collector.get("age_seconds")
    collector_ok = collector_age is not None and float(collector_age) <= collector_fresh
    stale = analysis_age is None or analysis_age > analysis_stale
    restart = bool(
        process_age >= startup_grace
        and collector_ok
        and int(collector.get("live") or 0) > 0
        and stale
    )
    return restart, {
        "collector_live": int(collector.get("live") or 0),
        "collector_age_seconds": collector_age,
        "analysis_age_seconds": analysis_age,
        "process_age_seconds": process_age,
        "collector_fresh": collector_ok,
        "analysis_stale": stale,
    }


def pipeline_diagnostic_text() -> str | None:
    """Explain an empty Analysis screen using collector and worker health."""
    collector = _collector_snapshot()
    collector_age = collector.get("age_seconds")
    analysis_age = _file_age_seconds(_analysis_path())
    worker = _read_json(_worker_health_path())
    try:
        live = int(collector.get("live") or 0)
    except (TypeError, ValueError):
        live = 0

    collector_fresh, analysis_stale, _ = _watchdog_limits()
    if collector_age is None or float(collector_age) > collector_fresh:
        return (
            "🧠 <b>GOOL MULTI · АНАЛИЗ</b>\n\n"
            "⚠️ Нет свежего heartbeat от Flashscore collector. "
            "Supervisor продолжает работу и должен восстановить поток автоматически."
        )
    if live <= 0:
        return None
    if analysis_age is None or analysis_age > analysis_stale:
        detail = html.escape(str(worker.get("error") or "").strip()[:180], quote=False)
        suffix = f"\nПоследняя ошибка: <code>{detail}</code>" if detail else ""
        return (
            "🧠 <b>GOOL MULTI · АНАЛИЗ</b>\n\n"
            f"⚠️ Flashscore collector видит <b>{live}</b> LIVE, но analysis-worker не обновляет оценки.\n"
            "Автовосстановление включено: зависший worker будет перезапущен supervisor'ом."
            f"{suffix}"
        )
    return None


def _watchdog_loop() -> None:
    try:
        interval = max(10.0, float(os.getenv("GOOL_ANALYSIS_WATCHDOG_INTERVAL_SECONDS", "30")))
    except (TypeError, ValueError):
        interval = 30.0
    while True:
        time.sleep(interval)
        restart, info = watchdog_should_restart()
        if not restart:
            continue
        print(
            "GOOL_ANALYSIS_WATCHDOG_RESTART "
            f"collector_live={info['collector_live']} "
            f"collector_age={info['collector_age_seconds']} "
            f"analysis_age={info['analysis_age_seconds']} "
            f"process_age={info['process_age_seconds']}",
            flush=True,
        )
        # monkey_start.py supervises the child and restarts it on exit. A full
        # process exit is the only reliable recovery if its main thread is stuck
        # in a blocking provider/helper call while Telegram's responder still runs.
        os._exit(86)


def install_analysis_pipeline_guard() -> bool:
    global _INSTALLED, _WATCHDOG_STARTED, _ORIGINAL_PROCESS
    if _INSTALLED:
        return True
    with _PROCESS_LOCK:
        if _INSTALLED:
            return True
        from . import storage_signal_worker as storage

        current = storage.StorageCardAllMatchSignalWorker._process
        if current is _health_wrapped_process:
            _INSTALLED = True
            return True
        _ORIGINAL_PROCESS = current
        storage.StorageCardAllMatchSignalWorker._process = _health_wrapped_process
        _INSTALLED = True
        print(
            "GOOL_ANALYSIS_PIPELINE_GUARD installed mode=health_wrapper watchdog=enabled",
            flush=True,
        )

        enabled = str(os.getenv("GOOL_ANALYSIS_WATCHDOG_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}
        if enabled and not _WATCHDOG_STARTED:
            thread = threading.Thread(target=_watchdog_loop, name="gool-analysis-watchdog", daemon=True)
            thread.start()
            _WATCHDOG_STARTED = True
        return True


__all__ = [
    "install_analysis_pipeline_guard",
    "pipeline_diagnostic_text",
    "watchdog_should_restart",
]
