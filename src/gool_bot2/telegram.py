from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if token and ":" not in token:
        bot_id = os.getenv("TELEGRAM_BOT_ID", "").strip()
        if bot_id.isdigit():
            token = f"{bot_id}:{token}"
    return token


def _owner_chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def _extra_chat_ids() -> set[str]:
    raw = os.getenv("TELEGRAM_EXTRA_CHAT_IDS", "")
    return {x for x in re.split(r"[\s,;]+", raw.strip()) if x}


def subscribers_file() -> Path:
    explicit = os.getenv("TELEGRAM_SUBSCRIBERS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    runtime = os.getenv("RUNTIME_DATA_DIR", "").strip()
    if runtime:
        return Path(runtime) / "telegram_subscribers.json"
    return Path("telegram_subscribers.json")


def _read_saved() -> set[str]:
    path = subscribers_file()
    if not path.exists():
        return set()
    try:
        rows = json.loads(path.read_text("utf-8"))
        return {str(x).strip() for x in rows if str(x).strip()} if isinstance(rows, list) else set()
    except Exception:
        return set()


def _write_saved(chat_ids: Iterable[str]) -> None:
    path = subscribers_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(map(str, chat_ids))), ensure_ascii=False, indent=2), "utf-8")


def get_subscribers() -> list[str]:
    rows = _read_saved() | _extra_chat_ids()
    owner = _owner_chat_id()
    if owner:
        rows.add(owner)
    return sorted(rows)


def subscribe(chat_id: str | int) -> bool:
    chat_id = str(chat_id).strip()
    rows = _read_saved()
    before = len(rows)
    rows.add(chat_id)
    _write_saved(rows)
    return len(rows) > before


def unsubscribe(chat_id: str | int) -> bool:
    chat_id = str(chat_id).strip()
    rows = _read_saved()
    existed = chat_id in rows
    rows.discard(chat_id)
    _write_saved(rows)
    return existed


def send_message(chat_id: str | int, text: str, parse_mode: str = "HTML") -> bool:
    token = _token()
    if not token:
        return False
    payload = json.dumps({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def broadcast(text: str, parse_mode: str = "HTML") -> int:
    return sum(int(send_message(chat_id, text, parse_mode=parse_mode)) for chat_id in get_subscribers())
