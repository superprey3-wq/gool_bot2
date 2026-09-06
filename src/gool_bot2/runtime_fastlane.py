from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import runtime_hardening as hardening


_INSTALLED = False
_FAST_PATH_LOGGED = False
_ORIGINAL_SAFE_PROCESS = hardening._safe_process


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _fast_multi_live_only() -> bool:
    mode = str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower()
    return mode == "active" and _truthy("GOOL_LIVE_ONLY", True)


def _health_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = os.getenv("SIGNAL_WORKER_HEALTH_PATH", "").strip()
    return Path(raw) if raw else runtime / "live" / "signal_worker_health.json"


def _write_health(payload: dict[str, Any]) -> None:
    try:
        path = _health_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"SIGNAL_WORKER_HEALTH_ERROR {type(exc).__name__}:{exc}", flush=True)


def _record_ingest_age(record: dict[str, Any]) -> float | None:
    """Age of when this row actually reached storage, with source-time fallback.

    An active collector starts a cycle before expensive per-match detail calls. On a
    very large LIVE wave, `captured_at` can therefore be several minutes older than
    the moment the row is finally appended. Cold-start filtering must not throw
    away such freshly written rows merely because their source-cycle timestamp is
    old; `ingested_at` is the correct freshness clock for queue admission.
    """
    return hardening._timestamp_age_seconds(record.get("ingested_at") or record.get("captured_at"))


def _fast_process(self: Any, record: dict[str, Any]) -> int:
    """Run only the active Multi LIVE brain when legacy model output is discarded anyway.

    GOOL_LIVE_ONLY removes trained_probability/direct before Goal State routing.
    The current local legacy model does not produce the only remaining optional
    broad-live key (another_goal_live), so running the entire legacy analyzer for
    every match changes no Multi decision. It only burns cold-start/loop time.

    We keep the two pieces Multi actually needs from the legacy worker: persistent
    prematch hydration for diagnostics and the 5m/10m momentum accumulator.
    """
    global _FAST_PATH_LOGGED

    if not _fast_multi_live_only():
        return int(_ORIGINAL_SAFE_PROCESS(self, record) or 0)

    from . import storage_market_signal_worker_var as app

    mid = hardening._match_id(record)
    market_recheck = bool(record.get("runtime_market_recheck"))
    model_cache: dict[str, dict[str, Any]] = getattr(self, "_runtime_model_by_match", {})

    if not _FAST_PATH_LOGGED:
        _FAST_PATH_LOGGED = True
        print(
            "GOOL_RUNTIME_FASTLANE active=1 brain=LIVE_ONLY legacy_model=skipped ",
            "reason=legacy_prediction_not_used_by_multi",
            sep="",
            flush=True,
        )

    if not market_recheck:
        store = getattr(self, "_prematch_store_disk", None)
        if store is not None:
            try:
                from .storage_runtime import hydrate_prematch

                hydrate_prematch(record, store)
            except Exception as exc:
                print(
                    f"PREMATCH_FASTLANE_RESTORE_ERROR match={mid or '-'} "
                    f"error={type(exc).__name__}:{exc}",
                    flush=True,
                )

        attach_momentum = getattr(self, "_attach_momentum", None)
        if callable(attach_momentum) and mid:
            try:
                attach_momentum(record, mid)
            except Exception as exc:
                print(
                    f"GOOL_FASTLANE_MOMENTUM_ERROR match={mid} "
                    f"error={type(exc).__name__}:{exc}",
                    flush=True,
                )

    # In LIVE_ONLY the model buckets that can influence Goal State are stripped.
    # An empty snapshot is therefore decision-equivalent and much cheaper.
    self._diag_model_result = {}
    if mid:
        model_cache[mid] = {}
        self._runtime_model_by_match = model_cache

    try:
        app.observe_multi_shadow(self, record)
    except Exception as exc:
        print(
            f"GOOL_MULTI_RUNTIME_ERROR match={mid or '-'} "
            f"error={type(exc).__name__}:{exc}",
            flush=True,
        )

    try:
        app.maybe_emit_money_flow(record)
    except Exception as exc:
        print(
            f"GOOL_MONEY_FLOW_RUNTIME_ERROR match={mid or '-'} "
            f"error={type(exc).__name__}:{exc}",
            flush=True,
        )

    if bool((record.get("match") or {}).get("is_finished")) and mid:
        model_cache.pop(mid, None)
    return 0


def _fresh_run_once(self: Any, raw_dir: Path) -> int:
    """Coalesce unread rows and never deep-analyze stale pre-restart snapshots.

    The raw directory persists across Monkey restarts. Starting every file at
    offset zero used to make the worker deep-process the newest state of many old
    matches before it could catch the current LIVE wave. We still advance every
    cursor to EOF, but only current snapshots enter the expensive pipeline.
    """
    from . import xbet_market_pressure as xbet

    started = time.monotonic()
    emitted = 0
    latest: dict[str, dict[str, Any]] = getattr(self, "_runtime_latest_records", {})
    new_ids: set[str] = set()
    pending_by_match: dict[str, dict[str, Any]] = {}
    pending_without_id: list[dict[str, Any]] = []
    parsed_rows = 0
    stale_rows = 0
    max_raw_age = max(
        90.0,
        hardening._number(os.getenv("SIGNAL_FRESH_RAW_MAX_AGE_SECONDS", "300"), 300.0),
    )

    for path in sorted(Path(raw_dir).glob("*.jsonl")):
        key = str(path)
        offset = self._offsets.get(key, 0)
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    parsed_rows += 1
                    age = _record_ingest_age(record)
                    if age is not None and age > max_raw_age:
                        stale_rows += 1
                        continue
                    mid = hardening._match_id(record)
                    if mid:
                        pending_by_match[mid] = record
                    else:
                        pending_without_id.append(record)
                self._offsets[key] = handle.tell()
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"SIGNAL_FILE_ERROR file={path.name} error={type(exc).__name__}:{exc}", flush=True)

    pending_records = pending_without_id + list(pending_by_match.values())
    dropped_rows = max(0, parsed_rows - stale_rows - len(pending_records))
    if dropped_rows:
        print(
            f"SIGNAL_BACKLOG_COALESCE read={parsed_rows - stale_rows} latest={len(pending_records)} "
            f"dropped_stale={dropped_rows}",
            flush=True,
        )
    if stale_rows:
        print(
            f"SIGNAL_COLD_START_SKIP stale_rows={stale_rows} max_age={max_raw_age:.0f}s",
            flush=True,
        )

    def freshness(record: dict[str, Any]) -> float:
        age = _record_ingest_age(record)
        return age if age is not None else float("inf")

    pending_records.sort(key=freshness)
    batch_total = len(pending_records)
    processed = 0
    last_health = 0.0
    _write_health({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "status": "processing" if batch_total else "idle",
        "batch_total": batch_total,
        "processed": 0,
        "parsed_rows": parsed_rows,
        "stale_rows_skipped": stale_rows,
        "cached_live": len(latest),
        "fast_lane": _fast_multi_live_only(),
    })

    for record in pending_records:
        mid = hardening._match_id(record)
        if mid:
            new_ids.add(mid)
        try:
            emitted += int(self._process(record) or 0)
        except Exception as exc:
            print(
                f"SIGNAL_RECORD_ERROR match={mid or '-'} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )
        finished = bool((record.get("match") or {}).get("is_finished"))
        if mid and finished:
            latest.pop(mid, None)
        elif mid:
            latest[mid] = copy.deepcopy(record)
        processed += 1
        now_mono = time.monotonic()
        if processed == batch_total or now_mono - last_health >= 2.0:
            last_health = now_mono
            _write_health({
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "status": "processing" if processed < batch_total else "batch_done",
                "batch_total": batch_total,
                "processed": processed,
                "parsed_rows": parsed_rows,
                "stale_rows_skipped": stale_rows,
                "cached_live": len(latest),
                "fast_lane": _fast_multi_live_only(),
                "batch_elapsed_seconds": round(now_mono - started, 2),
            })

    self._runtime_latest_records = latest

    interval = max(5.0, hardening._number(os.getenv("MARKET_REEVAL_INTERVAL_SECONDS", "10"), 10.0))
    now_mono = time.monotonic()
    previous_recheck = hardening._number(getattr(self, "_runtime_last_market_recheck", 0.0), 0.0)
    if now_mono - previous_recheck >= interval:
        self._runtime_last_market_recheck = now_mono
        state = xbet.load_market_state()
        market_matches = state.get("matches") or {}
        max_market_age = max(
            5.0,
            hardening._number(os.getenv("MARKET_REEVAL_MAX_XBET_AGE_SECONDS", "30"), 30.0),
        )
        max_cached_age = max(
            15.0,
            hardening._number(os.getenv("MARKET_REEVAL_MAX_RAW_AGE_SECONDS", "75"), 75.0),
        )

        for mid, cached in list(latest.items()):
            if mid in new_ids:
                continue
            match = cached.get("match") or {}
            if bool(match.get("is_finished")) or int(match.get("minute") or 0) <= 0:
                continue
            raw_age = hardening._timestamp_age_seconds(cached.get("captured_at"))
            if raw_age is None or raw_age > max_cached_age:
                continue
            market_row = market_matches.get(mid)
            if not isinstance(market_row, dict):
                continue
            market_age = hardening._timestamp_age_seconds(market_row.get("captured_at"))
            if market_age is None or market_age > max_market_age:
                continue
            if not hardening._same_score(cached, market_row):
                continue

            replay = copy.deepcopy(cached)
            replay["runtime_market_recheck"] = True
            replay["runtime_market_recheck_at"] = datetime.now(timezone.utc).isoformat()
            replay_match = replay.get("match") or {}
            try:
                market_minute = int(market_row.get("minute") or 0)
            except (TypeError, ValueError):
                market_minute = 0
            if market_minute > 0:
                replay_match["minute"] = market_minute
            replay["match"] = replay_match
            try:
                emitted += int(self._process(replay) or 0)
            except Exception as exc:
                print(
                    f"SIGNAL_MARKET_RECHECK_ERROR match={mid} "
                    f"error={type(exc).__name__}:{exc}",
                    flush=True,
                )

    elapsed = time.monotonic() - started
    _write_health({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "status": "idle",
        "batch_total": batch_total,
        "processed": processed,
        "parsed_rows": parsed_rows,
        "stale_rows_skipped": stale_rows,
        "cached_live": len(latest),
        "fast_lane": _fast_multi_live_only(),
        "cycle_seconds": round(elapsed, 2),
    })
    if batch_total:
        print(
            f"SIGNAL_COVERAGE batch={processed}/{batch_total} cached={len(latest)} "
            f"cycle={elapsed:.1f}s fast_lane={int(_fast_multi_live_only())}",
            flush=True,
        )
    return emitted


def install_runtime_fastlane() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Five minutes was too short for a large restart wave and made the Telegram
    # analysis page look as if only 1-2 matches existed. Explicit server config
    # still wins; this only changes the production default.
    os.environ.setdefault("ANALYSIS_ONLINE_MAX_AGE_MINUTES", "15")
    os.environ.setdefault("SIGNAL_FRESH_RAW_MAX_AGE_SECONDS", "300")

    # runtime_hardening.install_runtime_hardening() resolves these module globals
    # when it is called later by storage_market_signal_worker_var.
    hardening._safe_process = _fast_process
    hardening.hardened_run_once = _fresh_run_once
    _INSTALLED = True


__all__ = ["install_runtime_fastlane", "_fast_process", "_fresh_run_once", "_record_ingest_age"]
