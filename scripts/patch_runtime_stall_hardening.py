from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text("utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, "utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# 1) Startup: 15s light heartbeat, 60s expensive detail, bounded cleanup cadence.
path = "monkey_start.py"
text = read(path)
text = replace_once(
    text,
    '    marker.write_text(\n        f"reset_id={MULTI_RESET_ID}\\nmin_rating={os.getenv(\'GOOL_MULTI_MIN_RATING\', \'70\')}\\n",\n        "utf-8",\n    )',
    '    marker.write_text(\n        f"reset_id={MULTI_RESET_ID}\\narchitecture=one_brain_all_live_market_hunters\\n",\n        "utf-8",\n    )',
    "remove dead min rating marker",
)
text = replace_once(
    text,
    '    # The collector writes a new LIVE snapshot once per minute. Polling the same\n    # raw files every 3 seconds adds needless process wakeups on a small VPS.\n    os.environ.setdefault("SIGNAL_WORKER_SLEEP", "5")\n    os.environ.setdefault("SHADOW_MARKET_SLEEP", "5")',
    '    # Market hunters need a fast master-score heartbeat, while expensive\n    # football detail remains throttled separately. This prevents a 60s ceiling\n    # on STEAM/FLOW reaction without multiplying FotMob/365/detail load.\n    os.environ.setdefault("LIVE_INTERVAL_SECONDS", "15")\n    os.environ.setdefault("LIVE_DETAIL_INTERVAL_SECONDS", "60")\n    os.environ.setdefault("STORAGE_CLEANUP_EVERY_CYCLES", "20")\n    os.environ.setdefault("SIGNAL_WORKER_SLEEP", "3")\n    os.environ.setdefault("SHADOW_MARKET_SLEEP", "5")',
    "heartbeat defaults",
)
text = text.replace('    os.environ.setdefault("GOOL_MULTI_MIN_RATING", "70")\n', '')
text = text.replace('    print(f"GOOL_BOOT multi_min_rating={os.environ[\'GOOL_MULTI_MIN_RATING\']}", flush=True)\n', '')
text = replace_once(
    text,
    '    print(f"GOOL_BOOT worker_sleep={os.environ[\'SIGNAL_WORKER_SLEEP\']}s", flush=True)',
    '    print(\n        f"GOOL_BOOT collector heartbeat={os.environ[\'LIVE_INTERVAL_SECONDS\']}s "\n        f"detail={os.environ[\'LIVE_DETAIL_INTERVAL_SECONDS\']}s worker_sleep={os.environ[\'SIGNAL_WORKER_SLEEP\']}s",\n        flush=True,\n    )',
    "startup interval log",
)
text = text.replace('        "--interval", os.getenv("LIVE_INTERVAL_SECONDS", "60"),', '        "--interval", os.getenv("LIVE_INTERVAL_SECONDS", "15"),')
write(path, text)


# 2) Collector: cheap market-only row for every LIVE match first, expensive detail only when due.
path = "src/gool_bot2/storage_live_collector.py"
text = read(path)
text = replace_once(
    text,
    '        self._history_futures: dict[str, Future[Any]] = {}\n        runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))',
    '        self._history_futures: dict[str, Future[Any]] = {}\n        self._last_detail_at: dict[str, float] = {}\n        runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))',
    "detail throttle state",
)
text = replace_once(
    text,
    '        record: dict[str, Any] = {\n            "schema_version": 1,\n            "captured_at": now.isoformat(),',
    '        record: dict[str, Any] = {\n            "schema_version": 1,\n            "runtime_scope": "market_only",\n            "captured_at": now.isoformat(),',
    "cheap scope",
)
# Mark the full detail record; target the occurrence after football_prefilter.
anchor = '        pref = football_prefilter(fs_stats, minute, threshold=self.prefilter_threshold)\n        record: dict[str, Any] = {\n            "schema_version": 1,\n'
text = replace_once(
    text,
    anchor,
    '        pref = football_prefilter(fs_stats, minute, threshold=self.prefilter_threshold)\n        record: dict[str, Any] = {\n            "schema_version": 1,\n            "runtime_scope": "full",\n',
    "full scope",
)
start = text.index('        matches = self.flashscore.live_matches()\n', text.index('    def collect_once'))
end = text.index('        missing = set(self._tracked_matches) - current_ids\n', start)
new_block = '''        matches = self.flashscore.live_matches()\n        current_ids = {str(m.provider_match_id) for m in matches}\n        active = [m for m in matches if self._entry_window(int(m.minute or 0)) and not m.is_halftime]\n        market_watch = [m for m in matches if int(m.minute or 0) > 0]\n        halftime = [m for m in market_watch if bool(m.is_halftime)]\n        dead_first_half = [m for m in market_watch if 36 <= int(m.minute or 0) <= 45 and not m.is_halftime]\n\n        detail_interval = max(15.0, float(os.getenv("LIVE_DETAIL_INTERVAL_SECONDS", "60")))\n        active_due = []\n        for match in active:\n            mid = str(match.provider_match_id)\n            previous = self._last_detail_at.get(mid)\n            if previous is None or started - previous >= detail_interval:\n                active_due.append(match)\n                # Throttle retries too: one bad provider must not be hammered every\n                # 15 seconds while market-only heartbeat remains healthy.\n                self._last_detail_at[mid] = started\n\n        counters: dict[str, Any] = {\n            "live": len(matches),\n            "entry_window": len(active),\n            "detail_due": len(active_due),\n            "detail": 0,\n            "appended": 0,\n            "candidate": 0,\n            "secondary": 0,\n            "settlement_only": 0,\n            "market_watch": len(market_watch),\n            "skipped_dead_window": len(dead_first_half),\n            "final": 0,\n            "errors": 0,\n            "detail_workers": self._detail_workers,\n            "history_workers": self._history_workers,\n        }\n        top_live = sum(1 for m in matches if self._top_league(m.league))\n        top_active = sum(1 for m in active if self._top_league(m.league))\n        top_due = sum(1 for m in active_due if self._top_league(m.league))\n        top_detail = 0\n\n        for match in matches:\n            mid = str(match.provider_match_id)\n            self._tracked_matches[mid] = {\n                "home": match.home,\n                "away": match.away,\n                "league": match.league,\n                "meta": dict(match.meta or {}),\n            }\n\n        # The market heartbeat is intentionally written before any expensive\n        # detail futures are awaited. STEAM/FLOW can therefore react even when a\n        # statistics provider is slow or unavailable.\n        for match in market_watch:\n            try:\n                self._append(self._cheap_record(match, now), now)\n                counters["appended"] += 1\n                counters["settlement_only"] += 1\n            except Exception as exc:\n                counters["errors"] += 1\n                print(\n                    f"LIVE_MARKET_WATCH_SNAPSHOT_ERROR match={match.provider_match_id} "\n                    f"error={type(exc).__name__}:{exc}",\n                    flush=True,\n                )\n\n        # Use 36-45/HT to warm PREMATCH asynchronously for the second half.\n        for match in dead_first_half + halftime:\n            self._schedule_history(match)\n\n        futures = {self._detail_pool.submit(self._active_record, match, now): match for match in active_due}\n        for future in as_completed(futures):\n            match = futures[future]\n            try:\n                record, refreshed = future.result()\n                self._append(record, now)\n                counters["detail"] += 1\n                counters["appended"] += 1\n                counters["secondary"] += refreshed\n                if bool((record.get("prefilter") or {}).get("candidate")):\n                    counters["candidate"] += 1\n                if self._top_league(match.league):\n                    top_detail += 1\n            except Exception as exc:\n                counters["errors"] += 1\n                print(\n                    f"LIVE_DETAIL_ERROR match={match.provider_match_id} {match.home}-{match.away} "\n                    f"error={type(exc).__name__}:{exc}",\n                    flush=True,\n                )\n\n'''
text = text[:start] + new_block + text[end:]
text = text.replace('                    self._history_futures.pop(mid, None)\n', '                    self._history_futures.pop(mid, None)\n                    self._last_detail_at.pop(mid, None)\n')
text = replace_once(
    text,
    '        coverage = (float(counters["detail"]) / len(active) * 100.0) if active else 100.0',
    '        coverage = (float(counters["detail"]) / len(active_due) * 100.0) if active_due else 100.0',
    "coverage due denominator",
)
text = replace_once(
    text,
    '            "top_league_coverage_ok": top_detail == top_active,',
    '            "top_league_coverage_ok": top_detail == top_due,',
    "top due health",
)
text = replace_once(
    text,
    '            f"LIVE_COVERAGE live={len(matches)} active={len(active)} detail={counters[\'detail\']} "\n            f"coverage={coverage:.1f}% top={top_detail}/{top_active} cycle={elapsed:.1f}s errors={counters[\'errors\']}",',
    '            f"LIVE_COVERAGE live={len(matches)} active={len(active)} due={len(active_due)} detail={counters[\'detail\']} "\n            f"market={len(market_watch)} coverage={coverage:.1f}% top={top_detail}/{top_due} "\n            f"cycle={elapsed:.1f}s errors={counters[\'errors\']}",',
    "coverage log",
)
text = text.replace('os.getenv("STORAGE_CLEANUP_EVERY_CYCLES", "5")', 'os.getenv("STORAGE_CLEANUP_EVERY_CYCLES", "20")')
text = text.replace('default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60"))', 'default=int(os.getenv("LIVE_INTERVAL_SECONDS", "15"))')
write(path, text)


# 3) One bad JSONL record must never terminate the signal worker.
path = "src/gool_bot2/signal_worker.py"
text = read(path)
old = '''                        if isinstance(record, dict):\n                            emitted += self._process(record)\n                    self._offsets[key] = handle.tell()\n'''
new = '''                        if isinstance(record, dict):\n                            try:\n                                emitted += self._process(record)\n                            except Exception as exc:\n                                match = record.get("match") or {}\n                                mid = str(match.get("flashscore_event_id") or "-")\n                                print(\n                                    f"SIGNAL_RECORD_ERROR file={path.name} match={mid} "\n                                    f"error={type(exc).__name__}:{exc}",\n                                    flush=True,\n                                )\n                    self._offsets[key] = handle.tell()\n'''
text = replace_once(text, old, new, "per-record isolation")
write(path, text)


# 4) Isolate legacy, refresh, Multi and FLOW failure domains; skip legacy on market-only heartbeat.
path = "src/gool_bot2/storage_market_signal_worker_var.py"
text = read(path)
start = text.index('def _process_with_multi(self, record: dict[str, Any]):\n')
end = text.index('\n\ndef _poll_with_multi_bank', start)
new_func = '''def _process_with_multi(self, record: dict[str, Any]):\n    market_only = str(record.get("runtime_scope") or "").strip().lower() == "market_only"\n    emitted = 0\n\n    # Market heartbeat must not pay for the hidden legacy analyzer. Full detail\n    # snapshots still feed it for model diagnostics used by ordinary GOOL.\n    if not market_only:\n        try:\n            with silence_legacy_telegram():\n                emitted = _ORIG_PROCESS(self, record)\n        except Exception as exc:\n            print(f"GOOL_LEGACY_ANALYZER_ERROR {type(exc).__name__}:{exc}", flush=True)\n\n        try:\n            refresh_late_another_goal_model(self, record)\n        except Exception as exc:\n            print(f"GOOL_LATE_REFRESH_ERROR {type(exc).__name__}:{exc}", flush=True)\n\n    # Each public system has its own failure domain. A malformed football record\n    # may not suppress exchange flow, and a Matchbook error may not suppress GOOL.\n    try:\n        observe_multi_shadow(self, record)\n    except Exception as exc:\n        print(f"GOOL_MULTI_RUNTIME_ERROR {type(exc).__name__}:{exc}", flush=True)\n\n    try:\n        maybe_emit_money_flow(record)\n    except Exception as exc:\n        print(f"GOOL_MONEY_FLOW_RUNTIME_ERROR {type(exc).__name__}:{exc}", flush=True)\n    return emitted\n'''
text = text[:start] + new_func + text[end:]
write(path, text)


# 5) Market-only fast path: settlement + current market state + STEAM, no football Brain/history work.
path = "src/gool_bot2/multi_runtime.py"
text = read(path)
text = replace_once(
    text,
    'from .multi_router import analyze_multi_match',
    'from .multi_router import RouterDecision, analyze_multi_match',
    "router decision import",
)
anchor = '''    if minute <= 0 or bool(match.get("is_finished")):\n        return\n\n    model_result = dict(getattr(worker, "_diag_model_result", {}) or {})\n'''
fast = '''    if minute <= 0 or bool(match.get("is_finished")):\n        return\n\n    market_only = str(record.get("runtime_scope") or "").strip().lower() == "market_only"\n    if market_only:\n        quality = _data_quality(record)\n        market = _market_row(record)\n        record["xbet_live_1x2"] = live_1x2_context(market)\n        record["matchbook_exchange"] = matchbook_context(record)\n        decision = RouterDecision(\n            status="WAIT",\n            minute=minute,\n            score=(int(match.get("home_score") or 0), int(match.get("away_score") or 0)),\n            winner=None,\n            alternatives=[],\n            rejected=[],\n            reason="MARKET_ONLY heartbeat: ordinary GOOL Brain intentionally skipped.",\n        )\n        decision = apply_autonomous_steam(decision, record, market, data_quality=quality)\n        decision = enforce_entry_cutoff(decision)\n        if decision.status != "BET" or decision.winner is None:\n            return\n        decision = enforce_reentry_cooldown(decision, record, journal_path)\n        if decision.status != "BET" or decision.winner is None:\n            return\n        # Persist diagnostics only when the fast heartbeat actually found a BET;\n        # otherwise hundreds of 15s heartbeats would bloat analysis JSONL.\n        append_shadow_snapshot(\n            analysis_path,\n            decision_snapshot(record, decision, {}, data_quality=quality, market_row=market),\n        )\n        _, created = sync_multi_journal(\n            record, decision, {}, journal_path, data_quality=quality\n        )\n        if created is not None:\n            created = enrich_multi_entry(\n                journal_path, created, record, decision, {}, data_quality=quality\n            )\n            sent = emit_multi_signal(record, decision, created, market_row=market)\n            finalized = finalize_multi_delivery(journal_path, created, sent)\n            print(\n                f"GOOL_MARKET_HEARTBEAT_BET match={mid} minute={minute} "\n                f"sent={sent} finalized={int(finalized)} source={created.get('source')}",\n                flush=True,\n            )\n        return\n\n    model_result = dict(getattr(worker, "_diag_model_result", {}) or {})\n'''
text = replace_once(text, anchor, fast, "market-only fast path")
write(path, text)


# 6) Focused regressions for isolation and fast heartbeat behavior.
Path("tests/test_runtime_stall_hardening.py").write_text(r'''from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import gool_bot2.storage_market_signal_worker_var as runtime_wrapper
from gool_bot2.signal_worker import SignalWorker


def test_signal_worker_continues_after_one_bad_record(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "live.jsonl"
    rows = [
        {"match": {"flashscore_event_id": "bad"}},
        {"match": {"flashscore_event_id": "good"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", "utf-8")
    worker = SignalWorker(tmp_path / "journal.json", analysis_path=tmp_path / "analysis.jsonl")
    seen = []

    def fake_process(record):
        mid = record["match"]["flashscore_event_id"]
        seen.append(mid)
        if mid == "bad":
            raise RuntimeError("broken provider row")
        return 1

    monkeypatch.setattr(worker, "_process", fake_process)
    assert worker.run_once(raw) == 1
    assert seen == ["bad", "good"]
    # Offset advanced beyond the bad row: next poll does not replay it forever.
    assert worker.run_once(raw) == 0


def test_market_only_wrapper_skips_legacy_and_keeps_flow_independent(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_wrapper, "_ORIG_PROCESS", lambda *_: (_ for _ in ()).throw(AssertionError("legacy must not run")))
    monkeypatch.setattr(runtime_wrapper, "refresh_late_another_goal_model", lambda *_: (_ for _ in ()).throw(AssertionError("refresh must not run")))
    monkeypatch.setattr(runtime_wrapper, "observe_multi_shadow", lambda *_: calls.append("multi"))
    monkeypatch.setattr(runtime_wrapper, "maybe_emit_money_flow", lambda *_: calls.append("flow"))

    result = runtime_wrapper._process_with_multi(
        SimpleNamespace(),
        {"runtime_scope": "market_only", "match": {"flashscore_event_id": "m1", "minute": 90}},
    )
    assert result == 0
    assert calls == ["multi", "flow"]


def test_full_wrapper_failure_domains_do_not_suppress_other_systems(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_wrapper, "_ORIG_PROCESS", lambda *_: (_ for _ in ()).throw(RuntimeError("legacy")))
    monkeypatch.setattr(runtime_wrapper, "refresh_late_another_goal_model", lambda *_: (_ for _ in ()).throw(RuntimeError("refresh")))
    monkeypatch.setattr(runtime_wrapper, "observe_multi_shadow", lambda *_: calls.append("multi"))
    monkeypatch.setattr(runtime_wrapper, "maybe_emit_money_flow", lambda *_: calls.append("flow"))

    assert runtime_wrapper._process_with_multi(SimpleNamespace(), {"runtime_scope": "full", "match": {}}) == 0
    assert calls == ["multi", "flow"]
''', "utf-8")

print("runtime stall hardening patch applied")
