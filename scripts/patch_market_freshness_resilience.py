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


# Startup defaults for freshness and fail-fast network behavior.
path = "monkey_start.py"
text = read(path)
anchor = '    os.environ.setdefault("MATCHBOOK_MIN_MARKET_VOLUME", "50")\n'
replacement = '''    os.environ.setdefault("MATCHBOOK_MIN_MARKET_VOLUME", "50")
    os.environ.setdefault("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")
    os.environ.setdefault("MATCHBOOK_PAGE_TIMEOUT_SECONDS", "8")
    os.environ.setdefault("TELEGRAM_API_TIMEOUT_SECONDS", "8")
    os.environ.setdefault("TELEGRAM_PHOTO_TIMEOUT_SECONDS", "10")
'''
text = replace_once(text, anchor, replacement, "startup resilience defaults")
write(path, text)


# Matchbook: a frozen state file must become unavailable instead of remaining actionable.
path = "src/gool_bot2/matchbook_exchange.py"
text = read(path)
anchor = '''def _now_iso() -> str:\n    return datetime.now(timezone.utc).isoformat()\n\n\ndef _number(value: Any) -> float | None:\n'''
insert = '''def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_age_seconds(payload: dict[str, Any]) -> float | None:
    raw = str(payload.get("captured_at") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _number(value: Any) -> float | None:
'''
text = replace_once(text, anchor, insert, "matchbook age helper")
old = '''def matchbook_context(record: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:\n    payload = state if isinstance(state, dict) else load_matchbook_state()\n    event, score = _best_event(record, payload)\n'''
new = '''def matchbook_context(record: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = state if isinstance(state, dict) else load_matchbook_state()
    age = _state_age_seconds(payload)
    max_age = max(15.0, float(os.getenv("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")))
    # Production state files always carry captured_at. Explicit in-memory states
    # without a timestamp remain useful for deterministic tests, but any state
    # that does expose an age is rejected once it exceeds the freshness window.
    missing_timestamp_in_production = state is None and age is None
    if missing_timestamp_in_production or (age is not None and age > max_age):
        return {
            "available": False,
            "source": "matchbook",
            "captured_at": payload.get("captured_at"),
            "age_seconds": None if age is None else round(age, 3),
            "stale": True,
            "reason": "matchbook_state_stale" if age is not None else "matchbook_timestamp_missing",
        }
    event, score = _best_event(record, payload)
'''
text = replace_once(text, old, new, "matchbook freshness gate")
text = replace_once(
    text,
    '        "captured_at": payload.get("captured_at"),\n        "match_score": round(score, 4),',
    '        "captured_at": payload.get("captured_at"),\n        "age_seconds": None if age is None else round(age, 3),\n        "stale": False,\n        "match_score": round(score, 4),',
    "matchbook age diagnostics",
)
write(path, text)


# Matchbook pagination: fail fast on a broken page, preserve already collected coverage.
path = "src/gool_bot2/matchbook_pagination.py"
text = read(path)
text = replace_once(
    text,
    '    with urllib.request.urlopen(req, timeout=20) as response:\n',
    '    timeout = max(3.0, min(20.0, float(os.getenv("MATCHBOOK_PAGE_TIMEOUT_SECONDS", "8"))))\n    with urllib.request.urlopen(req, timeout=timeout) as response:\n',
    "matchbook page timeout",
)
text = replace_once(
    text,
    '    for page in range(1, max_pages + 1):\n        payload = _page_payload(page, per_page)\n        events = [row for row in (payload.get("events") or []) if isinstance(row, dict)]\n',
    '''    for page in range(1, max_pages + 1):
        try:
            payload = _page_payload(page, per_page)
        except Exception as exc:
            print(
                f"MATCHBOOK_PAGE_ERROR page={page} collected={len(rows)} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )
            if page == 1:
                raise
            break
        events = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
''',
    "partial pagination resilience",
)
write(path, text)


# MONEY FLOW: score-epoch reset independent of goal timeline, required for cheap market heartbeats.
path = "src/gool_bot2/multi_money_flow.py"
text = read(path)
text = replace_once(
    text,
    'FINAL_RESULTS = {"won", "lost", "push", "void"}\n\n\ndef _now() -> str:\n',
    '''FINAL_RESULTS = {"won", "lost", "push", "void"}
_FLOW_SCORE_STATE: dict[str, dict[str, Any]] = {}


def _flow_now(record: dict[str, Any]) -> float:
    parsed = _parse_dt(record.get("captured_at"))
    return (parsed or datetime.now(timezone.utc)).timestamp()


def _score_epoch_reset_age(record: dict[str, Any]) -> float | None:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    if not mid:
        return None
    if bool(match.get("is_finished")):
        _FLOW_SCORE_STATE.pop(mid, None)
        return None
    score = (int(match.get("home_score") or 0), int(match.get("away_score") or 0))
    now = _flow_now(record)
    state = _FLOW_SCORE_STATE.get(mid)
    if state is None:
        _FLOW_SCORE_STATE[mid] = {"score": score, "changed_at": None}
        return None
    previous = tuple(state.get("score") or score)
    if previous != score:
        state["score"] = score
        state["changed_at"] = now
        return 0.0
    changed_at = state.get("changed_at")
    if changed_at is None:
        return None
    return max(0.0, now - float(changed_at))


def _now() -> str:
''',
    "flow score state",
)
# _flow_now calls _parse_dt defined later; runtime name resolution is safe.
anchor = '''    match = record.get("match") or {}\n    minute = int(match.get("minute") or 0)\n    last_goal = _last_goal_minute(record)\n    reset_minutes = int(_threshold("MATCHBOOK_FLOW_POST_GOAL_RESET_MINUTES", 3))\n    if last_goal is not None and minute - last_goal < reset_minutes:\n        return {"eligible": False, "reason": "post_goal_exchange_reset", "last_goal_minute": last_goal}\n\n    flow = context.get("flow") or {}\n'''
replacement = '''    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    reset_minutes = int(_threshold("MATCHBOOK_FLOW_POST_GOAL_RESET_MINUTES", 3))
    score_reset_age = _score_epoch_reset_age(record)
    if score_reset_age is not None and score_reset_age < reset_minutes * 60.0:
        return {
            "eligible": False,
            "reason": "post_goal_exchange_reset",
            "score_epoch_reset": True,
            "seconds_since_score_change": round(score_reset_age, 1),
        }
    last_goal = _last_goal_minute(record)
    if last_goal is not None and minute - last_goal < reset_minutes:
        return {"eligible": False, "reason": "post_goal_exchange_reset", "last_goal_minute": last_goal}

    flow = context.get("flow") or {}
'''
text = replace_once(text, anchor, replacement, "flow score epoch guard")
write(path, text)


# Telegram: fail fast on network trouble so one send cannot block the single signal worker for ~1 minute.
path = "src/gool_bot2/telegram.py"
text = read(path)
text = replace_once(
    text,
    'def _api_call(method:str,payload:dict[str,Any],timeout:int=15)->dict[str,Any]|None:\n token=_token()\n if not token:return None\n',
    '''def _api_call(method:str,payload:dict[str,Any],timeout:int|None=None)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 if timeout is None:
  try:timeout=max(3,int(float(os.getenv("TELEGRAM_API_TIMEOUT_SECONDS","8"))))
  except Exception:timeout=8
''',
    "telegram api timeout",
)
text = replace_once(
    text,
    'def _multipart_call(method:str,fields:dict[str,str],file_field:str,filename:str,file_bytes:bytes,content_type:str="image/png",timeout:int=25)->dict[str,Any]|None:\n token=_token()\n if not token:return None\n',
    '''def _multipart_call(method:str,fields:dict[str,str],file_field:str,filename:str,file_bytes:bytes,content_type:str="image/png",timeout:int|None=None)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 if timeout is None:
  try:timeout=max(4,int(float(os.getenv("TELEGRAM_PHOTO_TIMEOUT_SECONDS","10"))))
  except Exception:timeout=10
''',
    "telegram photo timeout",
)
write(path, text)


# 365 half-history: schedule in background; never block the signal worker on 9s+9s HTTP.
path = "src/gool_bot2/prematch_goal_profile.py"
text = read(path)
text = replace_once(
    text,
    'import time\nfrom datetime import datetime, timezone\n',
    'import time\nfrom concurrent.futures import Future, ThreadPoolExecutor\nfrom datetime import datetime, timezone\n',
    "future imports",
)
text = replace_once(
    text,
    '_HALF_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}\n_HALF_PROVIDER: Scores365Provider | None = None\n',
    '_HALF_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}\n_HALF_CONTEXT_FUTURES: dict[str, Future[Any]] = {}\n_HALF_CONTEXT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gool-half-history")\n_HALF_PROVIDER: Scores365Provider | None = None\n',
    "half history executor",
)
old = '''    cached = _HALF_CONTEXT_CACHE.get(cache_key)\n    if cached and now - cached[0] < ttl:\n        extra = cached[1]\n    else:\n        try:\n            limit = max(3, min(10, int(os.getenv("GOOL_HALF_PREMATCH_HISTORY_MATCHES", "6"))))\n            method = getattr(_provider(), "half_prematch_context", None)\n            extra = method(home, away, limit=limit) if callable(method) else None\n        except Exception as exc:\n            print(\n                f"GOOL_HALF_PREMATCH_FETCH_ERROR match={cache_key} error={type(exc).__name__}:{exc}",\n                flush=True,\n            )\n            extra = None\n        _HALF_CONTEXT_CACHE[cache_key] = (now, extra if isinstance(extra, dict) else None)\n\n    if isinstance(extra, dict):\n'''
new = '''    cached = _HALF_CONTEXT_CACHE.get(cache_key)
    extra: dict[str, Any] | None = None
    if cached and now - cached[0] < ttl:
        extra = cached[1]
    else:
        future = _HALF_CONTEXT_FUTURES.get(cache_key)
        if future is not None and future.done():
            try:
                result = future.result()
                extra = result if isinstance(result, dict) else None
            except Exception as exc:
                print(
                    f"GOOL_HALF_PREMATCH_FETCH_ERROR match={cache_key} error={type(exc).__name__}:{exc}",
                    flush=True,
                )
                extra = None
            _HALF_CONTEXT_FUTURES.pop(cache_key, None)
            _HALF_CONTEXT_CACHE[cache_key] = (now, extra)
        elif future is None:
            limit = max(3, min(10, int(os.getenv("GOOL_HALF_PREMATCH_HISTORY_MATCHES", "6"))))

            def task() -> dict[str, Any] | None:
                method = getattr(_provider(), "half_prematch_context", None)
                return method(home, away, limit=limit) if callable(method) else None

            _HALF_CONTEXT_FUTURES[cache_key] = _HALF_CONTEXT_POOL.submit(task)
            profile["lazy_365_pending"] = True
            profile["lazy_365_loaded"] = False
            return profile
        else:
            profile["lazy_365_pending"] = True
            profile["lazy_365_loaded"] = False
            return profile

    if isinstance(extra, dict):
'''
text = replace_once(text, old, new, "nonblocking 365")
write(path, text)


# Regression tests for stale exchange, score epoch, partial pagination and nonblocking history.
Path("tests/test_market_freshness_resilience.py").write_text(r'''from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from gool_bot2 import matchbook_pagination, multi_money_flow, prematch_goal_profile
from gool_bot2.matchbook_exchange import matchbook_context


def _mb_record(score=(1, 1)):
    return {"match": {"flashscore_event_id": "fs1", "home": "Arsenal", "away": "Chelsea", "minute": 60, "home_score": score[0], "away_score": score[1], "is_finished": False}}


def _mb_state(captured_at: str):
    return {
        "captured_at": captured_at,
        "events": [{
            "event_id": "mb1", "name": "Arsenal vs Chelsea", "home": "Arsenal", "away": "Chelsea",
            "status": "open", "in_running": True, "volume": 5000.0, "match_odds": {},
            "totals": {"FT:2.5": {
                "id": "tot", "name": "Total Goals 2.5", "status": "open", "volume": 2000.0,
                "fair_over": 0.62,
                "over": {"best_back": {"odds": 1.70, "available": 300.0}, "best_lay": {"odds": 1.72, "available": 250.0}},
                "under": {"best_back": {"odds": 2.40, "available": 200.0}, "best_lay": {"odds": 2.44, "available": 200.0}},
                "flow": {"level": "STRONG_SUPPORT", "direction_pp": 3.2, "activity_volume": 400.0,
                         "window_ready_30s": True, "window_ready_60s": True,
                         "volume_delta_30s": 400.0, "volume_delta_60s": 600.0,
                         "fair_over_delta_pp_30s": 3.0, "fair_over_delta_pp_60s": 3.2},
            }},
        }],
    }


def test_matchbook_stale_state_is_not_actionable(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    ctx = matchbook_context(_mb_record(), _mb_state(stale))
    assert ctx["available"] is False
    assert ctx["stale"] is True
    assert ctx["reason"] == "matchbook_state_stale"


def test_matchbook_fresh_state_remains_actionable(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")
    ctx = matchbook_context(_mb_record(), _mb_state(datetime.now(timezone.utc).isoformat()))
    assert ctx["available"] is True
    assert ctx["stale"] is False
    assert ctx["systems"]["money_flow"]["available"] is True


def _flow_record(score=(1, 1)):
    return {
        "captured_at": "2026-09-05T20:00:00+00:00",
        "match": {"flashscore_event_id": "flow-score-epoch", "home": "A", "away": "B", "minute": 88, "home_score": score[0], "away_score": score[1], "is_finished": False},
        "providers": {"flashscore": {"meta": {}}},
        "matchbook_exchange": {"available": True, "systems": {"money_flow": {
            "available": True, "liquid": True, "period": "FT", "line": sum(score) + 0.5,
            "market_id": "m1", "market_name": "Total", "market_status": "open", "volume": 2000.0,
            "fair_over": 0.62,
            "over": {"best_back": {"odds": 1.70, "available": 300.0}, "best_lay": {"odds": 1.72, "available": 250.0}},
            "under": {"best_back": {"odds": 2.40, "available": 200.0}, "best_lay": {"odds": 2.44, "available": 200.0}},
            "flow": {"window_ready_30s": True, "window_ready_60s": True, "volume_delta_30s": 500.0, "volume_delta_60s": 700.0, "fair_over_delta_pp_30s": 3.0, "fair_over_delta_pp_60s": 3.2},
        }}},
    }


def test_money_flow_score_change_blocks_without_goal_timeline(monkeypatch):
    multi_money_flow._FLOW_SCORE_STATE.clear()
    clock = [1000.0]
    monkeypatch.setattr(multi_money_flow, "_flow_now", lambda record: clock[0])
    first = _flow_record((1, 1))
    assert multi_money_flow.evaluate_money_flow(first)["eligible"] is True
    clock[0] += 15
    changed = _flow_record((2, 1))
    blocked = multi_money_flow.evaluate_money_flow(changed)
    assert blocked["eligible"] is False
    assert blocked["reason"] == "post_goal_exchange_reset"
    assert blocked["score_epoch_reset"] is True
    clock[0] += 181
    assert multi_money_flow.evaluate_money_flow(changed)["eligible"] is True


def test_matchbook_pagination_keeps_first_page_if_later_page_fails(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_EVENTS_PER_PAGE", "20")
    monkeypatch.setenv("MATCHBOOK_MAX_PAGES", "3")
    def fake_page(page, per_page):
        if page == 1:
            return {"events": [{"id": f"e{i}"} for i in range(20)]}
        raise TimeoutError("slow second page")
    monkeypatch.setattr(matchbook_pagination, "_page_payload", fake_page)
    monkeypatch.setattr(matchbook_pagination, "decode_event", lambda row: {"event_id": row["id"], "home": "H", "away": "A", "start": None, "totals": {}})
    rows = matchbook_pagination.fetch_events_paginated()
    assert len(rows) == 20


def test_half_history_fetch_is_nonblocking(monkeypatch):
    gate = threading.Event()
    class SlowProvider:
        def half_prematch_context(self, home, away, limit=6):
            gate.wait(2.0)
            return {"home_recent": [], "away_recent": [], "h2h": []}
    prematch_goal_profile._HALF_CONTEXT_CACHE.clear()
    prematch_goal_profile._HALF_CONTEXT_FUTURES.clear()
    monkeypatch.setattr(prematch_goal_profile, "_HALF_PROVIDER", SlowProvider())
    record = {"match": {"flashscore_event_id": "async365", "home": "A", "away": "B", "minute": 20, "home_score": 0, "away_score": 0, "is_halftime": False}, "prematch_context": {}, "providers": {"flashscore": {"meta": {}}}}
    experts = {"goal_before_ht": {"state": "PASS", "probability": 0.75, "passed": True, "diagnostics": {}}}
    started = time.monotonic()
    profile = prematch_goal_profile.apply_half_goal_prior(record, experts)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert profile["lazy_365_pending"] is True
    gate.set()
''', "utf-8")

print("market freshness resilience patch applied")
