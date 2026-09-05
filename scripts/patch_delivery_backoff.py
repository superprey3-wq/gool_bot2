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


# Shared Telegram circuit breaker: after a network failure, subsequent sends fail
# immediately for a short cooldown instead of each blocking on its own timeout.
path = "src/gool_bot2/telegram.py"
text = read(path)
text = replace_once(text, 'import re\nimport uuid\n', 'import re\nimport time\nimport uuid\n', 'telegram time import')
text = replace_once(
    text,
    'HEAD_TO_CODE={"another_goal":"AG","goal_before_ht":"FH","over_2_5":"O25","both_teams_to_score":"BTTS","two_more_goals":"PLUS2"}\n',
    '''HEAD_TO_CODE={"another_goal":"AG","goal_before_ht":"FH","over_2_5":"O25","both_teams_to_score":"BTTS","two_more_goals":"PLUS2"}
_TELEGRAM_UNAVAILABLE_UNTIL=0.0

def _telegram_circuit_open()->bool:
 return time.monotonic() < _TELEGRAM_UNAVAILABLE_UNTIL

def _trip_telegram_circuit()->None:
 global _TELEGRAM_UNAVAILABLE_UNTIL
 try:cooldown=max(5.0,float(os.getenv("TELEGRAM_NETWORK_BACKOFF_SECONDS","30")))
 except Exception:cooldown=30.0
 _TELEGRAM_UNAVAILABLE_UNTIL=max(_TELEGRAM_UNAVAILABLE_UNTIL,time.monotonic()+cooldown)
''',
    'telegram circuit helpers',
)
text = replace_once(
    text,
    '''def _api_call(method:str,payload:dict[str,Any],timeout:int|None=None)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 if timeout is None:
''',
    '''def _api_call(method:str,payload:dict[str,Any],timeout:int|None=None)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 if _telegram_circuit_open():return None
 if timeout is None:
''',
    'api circuit check',
)
text = replace_once(
    text,
    ''' except Exception as exc:
  print(f"telegram_api_error method={method} error={type(exc).__name__}:{exc}",flush=True);return None
''',
    ''' except Exception as exc:
  _trip_telegram_circuit();print(f"telegram_api_error method={method} error={type(exc).__name__}:{exc}",flush=True);return None
''',
    'api trip circuit',
)
text = replace_once(
    text,
    '''def _multipart_call(method:str,fields:dict[str,str],file_field:str,filename:str,file_bytes:bytes,content_type:str="image/png",timeout:int|None=None)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 if timeout is None:
''',
    '''def _multipart_call(method:str,fields:dict[str,str],file_field:str,filename:str,file_bytes:bytes,content_type:str="image/png",timeout:int|None=None)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 if _telegram_circuit_open():return None
 if timeout is None:
''',
    'photo circuit check',
)
text = replace_once(
    text,
    ''' except Exception as exc:
  print(f"telegram_photo_error error={type(exc).__name__}:{exc}",flush=True);return None
''',
    ''' except Exception as exc:
  _trip_telegram_circuit();print(f"telegram_photo_error error={type(exc).__name__}:{exc}",flush=True);return None
''',
    'photo trip circuit',
)
write(path, text)


# Multi result retry backoff.
path = "src/gool_bot2/multi_delivery.py"
text = read(path)
text = replace_once(text, 'from datetime import datetime, timezone\n', 'from datetime import datetime, timezone\nimport os\n', 'delivery os import')
insert_anchor = '''def pending_result_notifications(
    journal_path: Path,
    *,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
'''
helper = '''def _retry_due(row: dict[str, Any]) -> bool:
    raw = str(row.get("result_notification_last_attempt_at") or "").strip()
    if not raw:
        return True
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return elapsed >= max(15.0, float(os.getenv("GOOL_RESULT_RETRY_SECONDS", "60")))
    except Exception:
        return True


def pending_result_notifications(
    journal_path: Path,
    *,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
'''
text = replace_once(text, insert_anchor, helper, 'result retry helper')
text = replace_once(
    text,
    '        if not was_publicly_sent(row):\n            continue\n        out.append(dict(row))\n',
    '        if not was_publicly_sent(row):\n            continue\n        if not _retry_due(row):\n            continue\n        out.append(dict(row))\n',
    'result retry filter',
)
old = '''    if int(sent or 0) <= 0:
        return False
    entry_key = str(row.get("entry_key") or "")
'''
new = '''    entry_key = str(row.get("entry_key") or "")
'''
text = replace_once(text, old, new, 'allow failed attempt record')
text = replace_once(
    text,
    '''    if index is None:
        return False

    rows[index]["result_notification_pending"] = False
    rows[index]["result_telegram_sent"] = True
    rows[index]["result_telegram_sent_at"] = _now()
    rows[index]["result_telegram_delivery_count"] = int(sent)
    save_signal_journal(journal_path, rows)
    return True
''',
    '''    if index is None:
        return False

    rows[index]["result_notification_last_attempt_at"] = _now()
    rows[index]["result_notification_attempts"] = int(rows[index].get("result_notification_attempts") or 0) + 1
    if int(sent or 0) > 0:
        rows[index]["result_notification_pending"] = False
        rows[index]["result_telegram_sent"] = True
        rows[index]["result_telegram_sent_at"] = _now()
        rows[index]["result_telegram_delivery_count"] = int(sent)
    save_signal_journal(journal_path, rows)
    return int(sent or 0) > 0
''',
    'record result attempts',
)
write(path, text)


# Call finalize_result_delivery for both successful and failed attempts.
path = "src/gool_bot2/multi_telegram.py"
text = read(path)
text = replace_once(
    text,
    '''        finalized = False
        if sent > 0 and journal_path is not None:
            finalized = finalize_result_delivery(journal_path, row, sent)
''',
    '''        finalized = False
        if journal_path is not None:
            finalized = finalize_result_delivery(journal_path, row, sent)
''',
    'persist result failed attempt',
)
write(path, text)


# MONEY FLOW result retry backoff uses the same default window.
path = "src/gool_bot2/multi_money_flow.py"
text = read(path)
anchor = '''def _settle_and_notify(record: dict[str, Any], path: Path) -> None:
'''
helper = '''def _result_retry_due(row: dict[str, Any]) -> bool:
    last = _parse_dt(row.get("result_notification_last_attempt_at"))
    if last is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
    return elapsed >= max(15.0, _number(os.getenv("GOOL_RESULT_RETRY_SECONDS", "60"), 60.0))


def _settle_and_notify(record: dict[str, Any], path: Path) -> None:
'''
text = replace_once(text, anchor, helper, 'flow retry helper')
text = replace_once(
    text,
    '''            and bool(row.get("telegram_sent"))
            and not bool(row.get("result_telegram_sent"))
        ):
            sent = 0
''',
    '''            and bool(row.get("telegram_sent"))
            and not bool(row.get("result_telegram_sent"))
            and _result_retry_due(row)
        ):
            sent = 0
''',
    'flow retry due filter',
)
text = replace_once(
    text,
    '''            if sent > 0:
                row["result_telegram_sent"] = True
                row["result_telegram_sent_at"] = _now()
                row["result_telegram_delivery_count"] = int(sent)
                changed = True
''',
    '''            row["result_notification_last_attempt_at"] = _now()
            row["result_notification_attempts"] = int(row.get("result_notification_attempts") or 0) + 1
            changed = True
            if sent > 0:
                row["result_telegram_sent"] = True
                row["result_telegram_sent_at"] = _now()
                row["result_telegram_delivery_count"] = int(sent)
''',
    'flow record attempt',
)
write(path, text)


# Startup default for shared retry/circuit policy.
path = "monkey_start.py"
text = read(path)
text = replace_once(
    text,
    '    os.environ.setdefault("TELEGRAM_PHOTO_TIMEOUT_SECONDS", "10")\n',
    '    os.environ.setdefault("TELEGRAM_PHOTO_TIMEOUT_SECONDS", "10")\n    os.environ.setdefault("TELEGRAM_NETWORK_BACKOFF_SECONDS", "30")\n    os.environ.setdefault("GOOL_RESULT_RETRY_SECONDS", "60")\n',
    'delivery startup defaults',
)
write(path, text)


Path("tests/test_delivery_backoff.py").write_text(r'''from __future__ import annotations

from datetime import datetime, timezone

import gool_bot2.multi_delivery as delivery
import gool_bot2.telegram as telegram
from gool_bot2.journal import save_signal_journal, load_signal_journal


def test_failed_result_attempt_gets_backoff(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    row = {
        "entry_key": "e1", "match_id": "m1", "mode": "active", "telegram_sent": True,
        "result": "won", "result_notification_pending": True,
    }
    save_signal_journal(path, [row])
    assert len(delivery.pending_result_notifications(path, match_id="m1")) == 1
    assert delivery.finalize_result_delivery(path, row, 0) is False
    assert delivery.pending_result_notifications(path, match_id="m1") == []
    stored = load_signal_journal(path)[0]
    assert stored["result_notification_attempts"] == 1
    assert stored["result_notification_pending"] is True


def test_telegram_circuit_prevents_repeated_blocking_calls(monkeypatch):
    telegram._TELEGRAM_UNAVAILABLE_UNTIL = 0.0
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test")
    monkeypatch.setenv("TELEGRAM_NETWORK_BACKOFF_SECONDS", "30")
    calls = {"n": 0}
    def broken(*args, **kwargs):
        calls["n"] += 1
        raise TimeoutError("network")
    monkeypatch.setattr(telegram, "urlopen", broken)
    assert telegram._api_call("sendMessage", {"chat_id": "1", "text": "x"}) is None
    assert calls["n"] == 1
    assert telegram._api_call("sendMessage", {"chat_id": "1", "text": "x"}) is None
    assert calls["n"] == 1
    telegram._TELEGRAM_UNAVAILABLE_UNTIL = 0.0
''', "utf-8")

print("delivery backoff patch applied")
