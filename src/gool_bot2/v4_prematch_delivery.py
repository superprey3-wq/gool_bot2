from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import telegram
from .journal import load_signal_journal, save_signal_journal
from .v4_prematch_card import render_v4_parlay_card, render_v4_prematch_card, render_v4_prematch_result_card


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


def prematch_row_from_pick(pick: Any, *, tier: str = "NORMAL", bookmaker: str = "") -> dict[str, Any]:
    """Convert a calibrated PrematchPick into the shared production journal/card schema."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    kickoff_ts = float(getattr(pick, "kickoff_ts", 0.0) or 0.0)
    scheduled = ""
    if kickoff_ts:
        scheduled = datetime.fromtimestamp(kickoff_ts, ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M МСК")
    return {
        "entry_id": f"prematch:{pick.event_id}:{pick.market}",
        "event_id": str(pick.event_id),
        "match_id": str(pick.event_id),
        "home": str(pick.home),
        "away": str(pick.away),
        "league": str(getattr(pick, "league", "") or "FOOTBALL"),
        "scheduled_start": scheduled,
        "kickoff_ts": kickoff_ts,
        "market": str(pick.market),
        "market_family": str(pick.market),
        "selection": str(pick.selection),
        "odd": float(pick.odds),
        "bookmaker": str(bookmaker or ""),
        "model_probability": float(pick.model_probability),
        "probability": float(pick.model_probability),
        "market_probability": float(pick.market_probability),
        "edge": float(pick.edge),
        "data_quality": float(pick.data_quality),
        "tier": str(tier or "NORMAL"),
        "origin": "prematch",
        "head": "prematch",
        "result": "pending",
        "lifecycle": "scheduled",
    }


def emit_delivery_selection(delivery: dict[str, Any], meta: dict[str, Any], journal_path: Path) -> dict[str, int]:
    """Persist/send only the format chosen by GOOL; do not duplicate singles inside a parlay."""
    sent = {"cards": 0, "entries": 0, "parlays": 0}
    mode = str(delivery.get("mode") or "NO_BET").upper()
    selected: list[tuple[Any, str]] = []
    if mode == "SUPER" and delivery.get("super"):
        selected = [(p, "SUPER") for p in delivery["super"]["legs"]]
    elif mode == "DOUBLES":
        for acc in delivery.get("doubles") or []:
            selected.extend((p, "STRONG") for p in acc.get("legs") or [])
    elif mode == "SINGLES":
        for item in delivery.get("singles") or []:
            if isinstance(item, tuple):
                selected.append((item[0], str(item[1])))
            else:
                selected.append((item, "NORMAL"))

    seen: set[str] = set()
    child_rows: list[dict[str, Any]] = []
    for pick, tier in selected:
        key = f"{pick.event_id}:{pick.market}"
        if key in seen:
            continue
        seen.add(key)
        info = meta.get(str(pick.event_id)) or {}
        row = prematch_row_from_pick(pick, tier=tier, bookmaker=str(info.get("bookmaker") or ""))
        before = len(load_signal_journal(journal_path))
        emit_prematch_signal(row, journal_path)
        after = len(load_signal_journal(journal_path))
        if after > before:
            sent["entries"] += 1
            sent["cards"] += 1
        child_rows.append(row)

    # Parent parlay is journal-only for lifecycle/result accounting. Individual
    # leg cards carry tournament/time/market and remain independently settleable.
    if mode in {"SUPER", "DOUBLES"}:
        groups = [delivery["super"]] if mode == "SUPER" else list(delivery.get("doubles") or [])
        rows = load_signal_journal(journal_path)
        for idx, acc in enumerate(groups, 1):
            legs = []
            for p in acc.get("legs") or []:
                info = meta.get(str(p.event_id)) or {}
                legs.append(prematch_row_from_pick(p, tier="STRONG", bookmaker=str(info.get("bookmaker") or "")))
            if not legs:
                continue
            pid = f"parlay:{mode.lower()}:{idx}:" + ":".join(x["event_id"] for x in legs)
            if any(str(x.get("entry_id") or "") == pid for x in rows):
                continue
            parent = {
                "entry_id": pid, "origin": "prematch_parlay", "head": "prematch",
                "kind": mode, "result": "pending", "lifecycle": "scheduled",
                "odd": float(acc.get("combined_odds") or 0.0),
                "probability": float(acc.get("combined_probability") or 0.0),
                "legs": legs, "created_at": _now(),
            }
            rows.append(parent)
            leg_lines = []
            for n, leg in enumerate(legs, 1):
                when = str(leg.get("scheduled_start") or "").strip()
                league = str(leg.get("league") or "FOOTBALL")
                leg_lines.append(
                    f"{n}. <b>{leg.get('home','?')} — {leg.get('away','?')}</b>\n"
                    f"   {league} · {when}\n"
                    f"   {leg.get('market','?')} @ {float(leg.get('odd') or 0):.2f}"
                )
            title = "💎 SUPER 10" if mode == "SUPER" else "🔗 ЭКСПРЕСС"
            caption = (
                f"{title} · <b>GOOL V4</b>\n"
                f"Общий кэф: <b>{float(parent.get('odd') or 0):.2f}</b>\n\n"
                + "\n\n".join(leg_lines)
            )
            png = render_v4_parlay_card(parent)
            delivered = telegram.broadcast_photo(png, caption=caption, reply_markup=telegram.MENU_KEYBOARD)
            if delivered:
                parent["telegram_sent"] = True
                parent["telegram_sent_at"] = _now()
                parent["telegram_delivery_count"] = int(delivered)
            sent["parlays"] += 1
        save_signal_journal(journal_path, rows)
    return sent


def emit_parlay_result(row: dict[str, Any]) -> int:
    png = render_v4_parlay_card(row, result=True)
    result = str(row.get("result") or "void").lower()
    icon = {"won": "✅", "lost": "❌", "push": "↩️", "void": "↩️"}.get(result, "ℹ️")
    label = {"won": "ЗАШЁЛ", "lost": "НЕ ЗАШЁЛ", "push": "ВОЗВРАТ", "void": "VOID"}.get(result, "РЕЗУЛЬТАТ")
    kind = "SUPER 10" if str(row.get("kind") or "").upper() == "SUPER" else "ЭКСПРЕСС"
    return telegram.broadcast_photo(
        png,
        caption=f"{icon} <b>{kind} · {label}</b>\nИтоговый кэф: <b>{float(row.get('effective_odd') or row.get('odd') or 0):.2f}</b>",
        reply_markup=telegram.MENU_KEYBOARD,
    )
