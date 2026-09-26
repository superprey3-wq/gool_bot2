from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import telegram
from .journal import load_signal_journal, save_signal_journal
from .v4_prematch_card import render_v4_prematch_card, render_v4_prematch_result_card


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prematch_keyboard(entry_id: str, entered: bool = False) -> dict[str, Any]:
    text = "✅ В игре" if entered else "🎯 В игре"
    return {"inline_keyboard": [[{"text": text, "callback_data": f"v4ig:{entry_id}"}]]}


def append_prematch_entry(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    rows = load_signal_journal(path)
    entry = dict(row)
    entry.setdefault("created_at", _now())
    entry.setdefault("origin", "prematch")
    entry.setdefault("head", "prematch")
    entry.setdefault("result", "pending")
    entry.setdefault("lifecycle", "scheduled")
    entry.setdefault("entry_id", str(entry.get("event_id") or f"prematch-{len(rows)+1}"))
    if any(str(x.get("entry_id") or "") == str(entry["entry_id"]) for x in rows):
        return entry
    rows.append(entry)
    save_signal_journal(path, rows)
    return entry


def mark_prematch_in_game(path: Path, entry_id: str, chat_id: str | int | None = None) -> bool:
    rows = load_signal_journal(path)
    for row in reversed(rows):
        if str(row.get("entry_id") or "") != str(entry_id):
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            return False
        row["in_game"] = True
        row["entered_at"] = row.get("entered_at") or _now()
        if chat_id is not None:
            row["entered_by_chat_id"] = str(chat_id)
        save_signal_journal(path, rows)
        return True
    return False


def emit_prematch_signal(row: dict[str, Any], journal_path: Path) -> int:
    entry = append_prematch_entry(journal_path, row)
    png = render_v4_prematch_card(entry)
    markup = prematch_keyboard(str(entry["entry_id"]))
    caption = (
        f"⚽ <b>GOOL V4 · PREMATCH</b>\n"
        f"{entry.get('home','?')} — {entry.get('away','?')}\n"
        f"<b>{entry.get('market','?')} @ {float(entry.get('odd') or 0):.2f}</b>"
    )
    sent = telegram.broadcast_photo(png, caption=caption, reply_markup=markup)
    if sent:
        rows = load_signal_journal(journal_path)
        for stored in reversed(rows):
            if str(stored.get("entry_id") or "") == str(entry["entry_id"]):
                stored["telegram_sent"] = True
                stored["telegram_sent_at"] = _now()
                stored["telegram_delivery_count"] = int(sent)
                break
        save_signal_journal(journal_path, rows)
    return sent


def parlay_leg_states(row: dict[str, Any], live_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    live_rows = list(live_rows)
    states: list[dict[str, Any]] = []
    for leg in list(row.get("legs") or []):
        state = dict(leg)
        event_id = str(leg.get("event_id") or "")
        matching = next((x for x in live_rows if str(x.get("event_id") or "") == event_id), None)
        if matching:
            state["lifecycle"] = matching.get("lifecycle") or "in_game"
            state["current_minute"] = matching.get("current_minute")
            state["current_score"] = matching.get("current_score")
            state["result"] = matching.get("result") or "pending"
        states.append(state)
    return states


def emit_prematch_result(row: dict[str, Any], record: dict[str, Any] | None = None) -> int:
    png = render_v4_prematch_result_card(row, record=record)
    result = str(row.get("result") or "void").lower()
    icon = {"won": "✅", "lost": "❌", "push": "↩️", "void": "↩️"}.get(result, "ℹ️")
    caption = (
        f"{icon} <b>GOOL V4 · PREMATCH RESULT</b>\n"
        f"{row.get('home','?')} — {row.get('away','?')}\n"
        f"<b>{row.get('market','?')} @ {float(row.get('odd') or 0):.2f}</b>"
    )
    return telegram.broadcast_photo(png, caption=caption)
