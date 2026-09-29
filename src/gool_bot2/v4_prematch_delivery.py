from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from hashlib import sha256
import json
from .production_journal_serialization import _locked
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
    entry.setdefault("telegram_sent", False)
    entry.setdefault("lifecycle", "scheduled")
    entry.setdefault("entry_id", str(entry.get("event_id") or f"prematch-{len(rows)+1}"))
    existing = next((x for x in rows if str(x.get("entry_id") or "") == str(entry["entry_id"])), None)
    if existing is not None:
        return existing
    rows.append(entry)
    save_signal_journal(path, rows)
    return entry


def mark_prematch_in_game(path: Path, entry_id: str, chat_id: str | int | None = None) -> bool:
    with _locked(path):
        return _mark_prematch_in_game(path, entry_id, chat_id)


def _mark_prematch_in_game(path: Path, entry_id: str, chat_id: str | int | None = None) -> bool:
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
    with _locked(journal_path):
        return _emit_prematch_signal(row, journal_path)


def _emit_prematch_signal(row: dict[str, Any], journal_path: Path) -> int:
    entry = append_prematch_entry(journal_path, row)
    if entry.get("telegram_sent") or str(entry.get("result") or "pending").lower() != "pending":
        return 0
    png = render_v4_prematch_card(entry)
    markup = prematch_keyboard(str(entry["entry_id"]))
    caption = (
        f"⚽ <b>GOOL V4 · PREMATCH</b>\n"
        f"{escape(str(entry.get('home','?')))} — {escape(str(entry.get('away','?')))}\n"
        f"<b>{escape(str(entry.get('market','?')))} @ {float(entry.get('odd') or 0):.2f}</b>"
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



def retry_pending_prematch_deliveries(journal_path: Path, *, limit: int = 20) -> int:
    """Retry journaled PREMATCH singles whose Telegram photo was not confirmed."""
    delivered = 0
    with _locked(journal_path):
        rows = load_signal_journal(journal_path)
        pending = [
            dict(row) for row in rows
            if str(row.get("origin") or "") == "prematch"
            and str(row.get("result") or "pending").lower() == "pending"
            and not bool(row.get("telegram_sent"))
        ][:max(1, int(limit))]
        for row in pending:
            try:
                delivered += int(bool(_emit_prematch_signal(row, journal_path)))
            except Exception as exc:
                # One bad render/network send must not prevent later pending cards from retrying.
                print(f"PREMATCH_RETRY_ERROR entry_id={row.get('entry_id')} error={type(exc).__name__}:{exc}", flush=True)
    return delivered

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
        f"{escape(str(row.get('home','?')))} — {escape(str(row.get('away','?')))}\n"
        f"<b>{escape(str(row.get('market','?')))} @ {float(row.get('odd') or 0):.2f}</b>"
    )
    return telegram.broadcast_photo(png, caption=caption)


def prematch_row_from_pick(pick: Any, *, tier: str = "NORMAL", bookmaker: str = "", flashscore_meta: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "event_source": "flashscore",
        "match_id": str(pick.event_id),
        "home": str(pick.home),
        "away": str(pick.away),
        "league": str(getattr(pick, "league", "") or "FOOTBALL"),
        "scheduled_start": scheduled,
        "kickoff_ts": kickoff_ts,
        "market": str(pick.market),
        "market_family": ("first_half_total" if "1H_" in str(pick.market).upper() else "second_half_total" if "2H_" in str(pick.market).upper() else "btts" if "BTTS" in str(pick.market).upper() else "match_total"),
        "market_key": ("btts:yes" if "BTTS" in str(pick.market).upper() else ("over:" if "OVER" in str(pick.market).upper() else "under:") + str(pick.market).split("_")[-1].replace("_", ".")),
        "selection": str(pick.selection),
        "odd": float(pick.odds),
        "bookmaker": str(bookmaker or ""),
        "flashscore_meta": dict(flashscore_meta or {}),
        "model_probability": float(pick.model_probability),
        "probability": float(pick.model_probability),
        "market_probability": float(pick.market_probability),
        "edge": float(pick.edge),
        "data_quality": float(pick.data_quality),
        "tier": str(tier or "NORMAL"),
        "origin": "prematch",
        "card_family": "prematch_single",
        "head": "prematch",
        "result": "pending",
        "lifecycle": "scheduled",
    }


def _market_label(value: str) -> str:
    key = str(value or "").upper()
    labels = {
        "FT_OVER_2.5": "ТБ 2.5", "FT_UNDER_2.5": "ТМ 2.5",
        "BTTS_YES": "Обе забьют — Да",
        "1H_OVER_0.5": "1-й тайм: ТБ 0.5", "1H_OVER_1.5": "1-й тайм: ТБ 1.5",
        "2H_OVER_0.5": "2-й тайм: ТБ 0.5", "2H_OVER_1.5": "2-й тайм: ТБ 1.5",
    }
    return labels.get(key, str(value or "?").replace("_", " "))


def emit_delivery_selection(delivery: dict[str, Any], meta: dict[str, Any], journal_path: Path) -> dict[str, int]:
    with _locked(journal_path):
        return _emit_delivery_selection(delivery, meta, journal_path)


def _emit_delivery_selection(delivery: dict[str, Any], meta: dict[str, Any], journal_path: Path) -> dict[str, int]:
    """Send independent singles plus independent parlay products."""
    sent = {"cards": 0, "entries": 0, "parlays": 0}

    # Singles are their own product, regardless of whether a separate parlay exists.
    seen: set[str] = set()
    for item in delivery.get("singles") or []:
        pick, tier = item if isinstance(item, tuple) else (item, "NORMAL")
        key = f"{pick.event_id}:{pick.market}"
        if key in seen:
            continue
        seen.add(key)
        info = meta.get(str(pick.event_id)) or {}
        row = prematch_row_from_pick(pick, tier=str(tier), bookmaker=str(info.get("bookmaker") or ""), flashscore_meta=dict(info.get("flashscore_meta") or {}))
        before = len(load_signal_journal(journal_path))
        delivered = _emit_prematch_signal(row, journal_path)
        after = len(load_signal_journal(journal_path))
        if delivered:
            sent["entries"] += int(after > before)
            sent["cards"] += 1

    mode = str(delivery.get("mode") or "NO_BET").upper()
    groups = []
    if delivery.get("super"):
        groups.append(("SUPER", delivery["super"]))
    for acc in delivery.get("doubles") or []:
        groups.append(("DOUBLES", acc))

    rows = load_signal_journal(journal_path)
    for idx, (kind, acc) in enumerate(groups, 1):
        legs = []
        for p in acc.get("legs") or []:
            info = meta.get(str(p.event_id)) or {}
            legs.append(prematch_row_from_pick(p, tier="STRONG", bookmaker=str(info.get("bookmaker") or ""), flashscore_meta=dict(info.get("flashscore_meta") or {})))
        if not legs:
            continue
        # A parlay is an independent betting product. Its legs may also have been
        # published as singles; the parent entry remains separately journaled/settled.
        # Stable identity includes every selection, independent of list order.
        def identity(items):
            return sorted((str(x.get("event_id") or ""), str(x.get("market") or ""), str(x.get("selection") or "")) for x in items)
        key = identity(legs)
        digest = sha256(json.dumps(key).encode()).hexdigest()[:24]
        pid = f"parlay:{kind.lower()}:{digest}"
        parent = next((x for x in rows if str(x.get("origin") or "") in {"prematch_parlay", "parlay"} and identity(x.get("legs") or []) == key), None)
        if parent is not None:
            if parent.get("telegram_sent") or str(parent.get("result") or "pending") != "pending":
                continue
        else:
            parent = {
                "entry_id": pid, "origin": "prematch_parlay", "card_family": "prematch_parlay", "head": "prematch",
                "kind": kind, "result": "pending", "lifecycle": "scheduled",
                "legs": legs, "odd": float(acc.get("combined_odds") or 0.0),
                "effective_odd": float(acc.get("combined_odds") or 0.0),
                "probability": float(acc.get("combined_probability") or 0.0),
                "telegram_sent": False, "created_at": _now(),
            }
            rows.append(parent)
            save_signal_journal(journal_path, rows)
        legs = parent["legs"]
        caption_lines = [
            f"🔗 <b>{'SUPER 10' if kind == 'SUPER' else 'ЭКСПРЕСС'} · GOOL V4</b>",
            f"Общий кэф: <b>{parent['odd']:.2f}</b>", "",
        ]
        for n, leg in enumerate(legs, 1):
            caption_lines += [
                f"<b>{n}. {escape(str(leg['home']))} — {escape(str(leg['away']))}</b>",
                f"{escape(str(leg['league']))} · {escape(str(leg['scheduled_start']))}",
                f"{_market_label(leg['market'])} @ {leg['odd']:.2f}", "",
            ]
        png = render_v4_parlay_card(parent)
        delivered = telegram.broadcast_photo(png, caption="\n".join(caption_lines).strip())
        if delivered:
            parent["telegram_sent"] = True
            parent["telegram_sent_at"] = _now()
            parent["telegram_delivery_count"] = int(delivered)
            save_signal_journal(journal_path, rows)
            sent["parlays"] += 1
            sent["cards"] += 1
    return sent


def emit_parlay_result(row: dict[str, Any]) -> int:
    png = render_v4_parlay_card(row, result=True)
    result = str(row.get("result") or "void").lower()
    icon = {"won": "✅", "lost": "❌", "push": "↩️", "void": "↩️"}.get(result, "ℹ️")
    label = {"won": "ЗАШЁЛ", "lost": "НЕ ЗАШЁЛ", "push": "ВОЗВРАТ", "void": "ВОЗВРАТ"}.get(result, "РЕЗУЛЬТАТ")
    kind = "SUPER 10" if str(row.get("kind") or "").upper() == "SUPER" else "ЭКСПРЕСС"
    return telegram.broadcast_photo(
        png,
        caption=f"{icon} <b>{kind} · {label}</b>\nИтоговый кэф: <b>{float(row.get('effective_odd') or row.get('odd') or 0):.2f}</b>",
        reply_markup=telegram.MENU_KEYBOARD,
    )
