from __future__ import annotations

import copy
import json
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_HALF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gool-half-history")
_HALF_FUTURES: dict[str, Future[Any]] = {}
_FLOW_SCORE_EPOCHS: dict[str, dict[str, Any]] = {}
_INSTALLED = False


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _timestamp_age_seconds(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _match_id(record: dict[str, Any]) -> str:
    return str(((record.get("match") or {}).get("flashscore_event_id") or "")).strip()


def _same_score(record: dict[str, Any], market_row: dict[str, Any]) -> bool:
    match = record.get("match") or {}
    raw_score = (int(match.get("home_score") or 0), int(match.get("away_score") or 0))
    market_score = (
        int(market_row.get("flashscore_score_home") if market_row.get("flashscore_score_home") is not None else market_row.get("score_home") or 0),
        int(market_row.get("flashscore_score_away") if market_row.get("flashscore_score_away") is not None else market_row.get("score_away") or 0),
    )
    return raw_score == market_score


def hardened_run_once(self: Any, raw_dir: Path) -> int:
    """Consume new raw rows and safely re-evaluate the latest row on fresh markets.

    Football snapshots remain minute-scale and expensive. 1xBet/Matchbook update
    much faster. Re-running the latest football row every few seconds closes that
    latency gap without fabricating a new score: a market recheck is allowed only
    while the fresh 1xBet snapshot reports the same Flashscore score epoch.
    """
    from . import xbet_market_pressure as xbet

    emitted = 0
    latest: dict[str, dict[str, Any]] = getattr(self, "_runtime_latest_records", {})
    new_ids: set[str] = set()

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
                    mid = _match_id(record)
                    if mid:
                        latest[mid] = copy.deepcopy(record)
                        new_ids.add(mid)
                    try:
                        emitted += int(self._process(record) or 0)
                    except Exception as exc:
                        print(
                            f"SIGNAL_RECORD_ERROR match={mid or '-'} "
                            f"error={type(exc).__name__}:{exc}",
                            flush=True,
                        )
                    if bool((record.get("match") or {}).get("is_finished")) and mid:
                        latest.pop(mid, None)
                self._offsets[key] = handle.tell()
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"SIGNAL_FILE_ERROR file={path.name} error={type(exc).__name__}:{exc}", flush=True)

    self._runtime_latest_records = latest

    interval = max(5.0, _number(os.getenv("MARKET_REEVAL_INTERVAL_SECONDS", "10"), 10.0))
    now_mono = time.monotonic()
    previous_recheck = _number(getattr(self, "_runtime_last_market_recheck", 0.0), 0.0)
    if now_mono - previous_recheck < interval:
        return emitted
    self._runtime_last_market_recheck = now_mono

    state = xbet.load_market_state()
    market_matches = state.get("matches") or {}
    max_market_age = max(5.0, _number(os.getenv("MARKET_REEVAL_MAX_XBET_AGE_SECONDS", "30"), 30.0))
    max_raw_age = max(15.0, _number(os.getenv("MARKET_REEVAL_MAX_RAW_AGE_SECONDS", "75"), 75.0))

    for mid, cached in list(latest.items()):
        if mid in new_ids:
            continue
        match = cached.get("match") or {}
        if bool(match.get("is_finished")) or int(match.get("minute") or 0) <= 0:
            continue
        raw_age = _timestamp_age_seconds(cached.get("captured_at"))
        if raw_age is None or raw_age > max_raw_age:
            continue
        market_row = market_matches.get(mid)
        if not isinstance(market_row, dict):
            continue
        market_age = _timestamp_age_seconds(market_row.get("captured_at"))
        if market_age is None or market_age > max_market_age:
            continue
        if not _same_score(cached, market_row):
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

    return emitted


def _safe_process(self: Any, record: dict[str, Any]) -> int:
    """Keep one bad provider/match from suppressing Multi, STEAM or FLOW."""
    from . import storage_market_signal_worker_var as app

    emitted = 0
    mid = _match_id(record)
    market_recheck = bool(record.get("runtime_market_recheck"))
    model_cache: dict[str, dict[str, Any]] = getattr(self, "_runtime_model_by_match", {})

    if market_recheck:
        try:
            self._diag_model_result = copy.deepcopy(model_cache.get(mid) or {})
        except Exception:
            self._diag_model_result = dict(model_cache.get(mid) or {})
    else:
        try:
            with app.silence_legacy_telegram():
                emitted = int(app._ORIG_PROCESS(self, record) or 0)
        except Exception as exc:
            self._diag_model_result = {}
            print(
                f"GOOL_LEGACY_ANALYZER_ERROR match={mid or '-'} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )
        try:
            snapshot = copy.deepcopy(dict(getattr(self, "_diag_model_result", {}) or {}))
        except Exception:
            snapshot = dict(getattr(self, "_diag_model_result", {}) or {})
        if mid:
            model_cache[mid] = snapshot
            self._runtime_model_by_match = model_cache
        try:
            app.refresh_late_another_goal_model(self, record)
        except Exception as exc:
            print(
                f"GOOL_LATE_REFRESH_ERROR match={mid or '-'} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )

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
    return emitted


def _async_half_history(record: dict[str, Any], experts: dict[str, dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    """Hydrate 365 half-history in background; never block the signal worker."""
    from . import prematch_goal_profile as prematch

    strategy, period = prematch._active_strategy(record)
    if strategy is None or period is None:
        return profile
    expert = experts.get(strategy) or {}
    if str(expert.get("state") or "") not in {"PASS", "BORDERLINE"}:
        return profile

    period_profile = profile.get("first_half") if period == "1H" else profile.get("second_half")
    if int((period_profile or {}).get("pair_sample") or 0) >= 3:
        return profile

    match = record.get("match") or {}
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    match_id = str(match.get("flashscore_event_id") or "").strip()
    if not home or not away:
        return profile

    cache_key = match_id or f"{home.casefold()}::{away.casefold()}"
    ttl = max(300.0, _number(os.getenv("GOOL_HALF_PREMATCH_CACHE_SECONDS", "3600"), 3600.0))
    now = time.time()
    cached = prematch._HALF_CONTEXT_CACHE.get(cache_key)
    extra: dict[str, Any] | None = None

    if cached and now - float(cached[0]) < ttl:
        extra = cached[1] if isinstance(cached[1], dict) else None
    else:
        future = _HALF_FUTURES.get(cache_key)
        if future is None:
            limit = max(3, min(10, int(_number(os.getenv("GOOL_HALF_PREMATCH_HISTORY_MATCHES", "6"), 6))))

            def load() -> dict[str, Any] | None:
                try:
                    method = getattr(prematch._provider(), "half_prematch_context", None)
                    value = method(home, away, limit=limit) if callable(method) else None
                    return value if isinstance(value, dict) else None
                except Exception as exc:
                    print(
                        f"GOOL_HALF_PREMATCH_FETCH_ERROR match={cache_key} "
                        f"error={type(exc).__name__}:{exc}",
                        flush=True,
                    )
                    return None

            _HALF_FUTURES[cache_key] = _HALF_EXECUTOR.submit(load)
            profile["lazy_365_loaded"] = False
            profile["lazy_365_pending"] = True
            return profile

        if not future.done():
            profile["lazy_365_loaded"] = False
            profile["lazy_365_pending"] = True
            return profile

        try:
            result = future.result()
            extra = result if isinstance(result, dict) else None
        except Exception:
            extra = None
        _HALF_FUTURES.pop(cache_key, None)
        prematch._HALF_CONTEXT_CACHE[cache_key] = (now, extra)

    if isinstance(extra, dict):
        limit = max(3, min(10, int(_number(os.getenv("GOOL_HALF_PREMATCH_HISTORY_MATCHES", "6"), 6))))
        prematch._merge_half_context(record, extra, limit)
        rebuilt = prematch.build_prematch_goal_profile(record)
        rebuilt["lazy_365_loaded"] = True
        rebuilt["lazy_365_pending"] = False
        return rebuilt

    profile["lazy_365_loaded"] = False
    profile["lazy_365_pending"] = False
    return profile


def _fresh_matchbook_context(record: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import matchbook_exchange as matchbook

    payload = state if isinstance(state, dict) else matchbook.load_matchbook_state()
    age = _timestamp_age_seconds(payload.get("captured_at")) if isinstance(payload, dict) else None
    max_age = max(10.0, _number(os.getenv("MATCHBOOK_STATE_MAX_AGE_SECONDS", "45"), 45.0))
    if age is None or age > max_age:
        return {
            "available": False,
            "source": "matchbook",
            "stale": True,
            "age_seconds": age,
            "max_age_seconds": max_age,
            "captured_at": None if not isinstance(payload, dict) else payload.get("captured_at"),
        }
    context = _ORIGINAL_MATCHBOOK_CONTEXT(record, payload)
    context["stale"] = False
    context["age_seconds"] = round(age, 2)
    context["max_age_seconds"] = max_age
    return context


def _flow_score_epoch_guard(record: dict[str, Any]) -> dict[str, Any] | None:
    match = record.get("match") or {}
    mid = _match_id(record)
    if not mid:
        return None
    if bool(match.get("is_finished")):
        _FLOW_SCORE_EPOCHS.pop(mid, None)
        return None

    score = (int(match.get("home_score") or 0), int(match.get("away_score") or 0))
    now = time.monotonic()
    reset_seconds = max(0.0, _number(os.getenv("MATCHBOOK_FLOW_SCORE_EPOCH_RESET_SECONDS", "180"), 180.0))
    state = _FLOW_SCORE_EPOCHS.get(mid)
    if state is None:
        _FLOW_SCORE_EPOCHS[mid] = {"score": score, "changed_at": None}
        return None

    if tuple(state.get("score") or ()) != score:
        state = {"score": score, "changed_at": now}
        _FLOW_SCORE_EPOCHS[mid] = state
        return {
            "eligible": False,
            "reason": "post_goal_exchange_score_epoch_reset",
            "score": [score[0], score[1]],
            "reset_seconds": reset_seconds,
            "seconds_since_score_change": 0.0,
        }

    changed_at = state.get("changed_at")
    if changed_at is not None:
        age = max(0.0, now - float(changed_at))
        if age < reset_seconds:
            return {
                "eligible": False,
                "reason": "post_goal_exchange_score_epoch_reset",
                "score": [score[0], score[1]],
                "reset_seconds": reset_seconds,
                "seconds_since_score_change": round(age, 1),
            }
    return None


def _safe_money_flow_eval(record: dict[str, Any]) -> dict[str, Any]:
    guard = _flow_score_epoch_guard(record)
    if guard is not None:
        return guard
    return _ORIGINAL_MONEY_FLOW_EVALUATE(record)


_ORIGINAL_MATCHBOOK_CONTEXT: Any = None
_ORIGINAL_MONEY_FLOW_EVALUATE: Any = None


def install_runtime_hardening() -> None:
    global _INSTALLED, _ORIGINAL_MATCHBOOK_CONTEXT, _ORIGINAL_MONEY_FLOW_EVALUATE
    if _INSTALLED:
        return

    from . import matchbook_exchange as matchbook
    from . import multi_money_flow as money_flow
    from . import multi_runtime
    from . import prematch_goal_profile as prematch
    from . import signal_worker as core
    from . import storage_signal_worker as storage

    _ORIGINAL_MATCHBOOK_CONTEXT = matchbook.matchbook_context
    _ORIGINAL_MONEY_FLOW_EVALUATE = money_flow.evaluate_money_flow

    core.SignalWorker.run_once = hardened_run_once
    storage.StorageCardAllMatchSignalWorker._process = _safe_process
    prematch._maybe_load_half_history = _async_half_history

    matchbook.matchbook_context = _fresh_matchbook_context
    multi_runtime.matchbook_context = _fresh_matchbook_context
    money_flow.evaluate_money_flow = _safe_money_flow_eval

    _INSTALLED = True
    print(
        "GOOL_RUNTIME_HARDENING installed "
        f"market_recheck={os.getenv('MARKET_REEVAL_INTERVAL_SECONDS', '10')}s "
        f"matchbook_max_age={os.getenv('MATCHBOOK_STATE_MAX_AGE_SECONDS', '45')}s "
        f"flow_score_reset={os.getenv('MATCHBOOK_FLOW_SCORE_EPOCH_RESET_SECONDS', '180')}s",
        flush=True,
    )


__all__ = [
    "hardened_run_once",
    "install_runtime_hardening",
    "_async_half_history",
    "_flow_score_epoch_guard",
    "_fresh_matchbook_context",
]
