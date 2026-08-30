from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

from .journal import mark_in_game

HEAD_TO_CODE = {
    "another_goal": "AG",
    "goal_before_ht": "FH",
    "two_plus_goals_second_half": "2H",
}
CODE_TO_HEAD = {value: key for key, value in HEAD_TO_CODE.items()}


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


def _api_call(method: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any] | None:
    token = _token()
    if not token:
        return None
    req = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body if isinstance(body, dict) else None
    except Exception:
        return None


def _multipart_call(method: str, fields: dict[str, str], filename: str, data: bytes, timeout: int = 20) -> dict[str, Any] | None:
    token = _token()
    if not token:
        return None
    boundary = f"----gool{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: image/png\r\n\r\n",
        data,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    req = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body if isinstance(body, dict) else None
    except Exception:
        return None


def signal_keyboard(match_id: str, head: str, entered: bool = False) -> dict[str, Any]:
    code = HEAD_TO_CODE.get(head, "AG")
    text = "✅ В игре" if entered else "🎯 В игре"
    return {"inline_keyboard": [[{"text": text, "callback_data": f"ig:{code}:{match_id}"}]]}


def send_message(chat_id: str | int, text: str, parse_mode: str = "HTML", reply_markup: dict[str, Any] | None = None) -> bool:
    payload: dict[str, Any] = {"chat_id": str(chat_id), "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = _api_call("sendMessage", payload)
    return bool(result and result.get("ok"))


def send_photo(chat_id: str | int, png: bytes, caption: str, reply_markup: dict[str, Any] | None = None) -> bool:
    fields = {"chat_id": str(chat_id), "caption": caption}
    if reply_markup:
        fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    result = _multipart_call("sendPhoto", fields, "gool-live-signal.png", png)
    return bool(result and result.get("ok"))


def broadcast(text: str, parse_mode: str = "HTML", reply_markup: dict[str, Any] | None = None) -> int:
    return sum(int(send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)) for chat_id in get_subscribers())


def broadcast_photo(png: bytes, caption: str, reply_markup: dict[str, Any] | None = None, fallback_text: str | None = None) -> int:
    delivered = 0
    for chat_id in get_subscribers():
        ok = send_photo(chat_id, png, caption, reply_markup=reply_markup)
        if not ok and fallback_text:
            ok = send_message(chat_id, fallback_text, reply_markup=reply_markup)
        delivered += int(ok)
    return delivered


def poll_in_game_callbacks(journal_path: Path, offset: int = 0) -> tuple[int, int]:
    payload: dict[str, Any] = {"offset": int(offset), "timeout": 0, "allowed_updates": ["callback_query"]}
    result = _api_call("getUpdates", payload, timeout=5)
    if not result or not result.get("ok"):
        return offset, 0
    next_offset = offset
    changed = 0
    allowed = set(get_subscribers())
    for update in result.get("result") or []:
        try:
            update_id = int(update.get("update_id"))
            next_offset = max(next_offset, update_id + 1)
            callback = update.get("callback_query") or {}
            data = str(callback.get("data") or "")
            parts = data.split(":", 2)
            if len(parts) != 3 or parts[0] != "ig":
                continue
            head = CODE_TO_HEAD.get(parts[1])
            match_id = parts[2]
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            if not head or (allowed and chat_id not in allowed):
                continue
            ok = mark_in_game(journal_path, match_id, head, chat_id=chat_id)
            _api_call("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": "Записал заход в журнал" if ok else "Сигнал уже закрыт или не найден", "show_alert": False}, timeout=5)
            if ok:
                changed += 1
                if message.get("message_id") and chat_id:
                    _api_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message.get("message_id"), "reply_markup": signal_keyboard(match_id, head, entered=True)}, timeout=5)
        except Exception:
            continue
    return next_offset, changed
