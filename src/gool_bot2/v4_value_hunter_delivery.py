from __future__ import annotations

import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable

from . import telegram
from .journal import load_signal_journal, save_signal_journal
from .production_journal_serialization import _locked
from .v4_prematch_card import render_v4_prematch_card
from .v4_prematch_delivery import prematch_row_from_pick, prematch_keyboard, _market_label


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _already_public_single(rows: list[dict[str, Any]], event_id: str) -> bool:
    for row in rows:
        if str(row.get("origin") or "").casefold() not in {"prematch", "prematch_value"}:
            continue
        if str(row.get("event_id") or row.get("match_id") or "") != str(event_id):
            continue
        if bool(row.get("telegram_sent")) or str(row.get("result") or "pending").casefold() != "pending":
            return True
        # A journaled unsent row is still reserved for retry and must not race
        # another product for the same fixture.
        return True
    return False


def emit_value_hunter(
    candidates: Iterable[tuple[Any, dict[str, Any]]],
    journal_path: Path,
) -> dict[str, int]:
    """Publish a capped set of high-odds VALUE picks.

    One fixture can have only one public PREMATCH single across normal and VALUE
    products. This prevents contradictory or highly correlated duplicate cards.
    """
    if str(os.getenv("GOOL_VALUE_HUNTER_ACTIVE", "1")).strip().casefold() not in {"1", "true", "yes", "on"}:
        return {"cards": 0, "entries": 0, "skipped_existing": 0}

    ranked = list(candidates)
    ranked.sort(
        key=lambda item: (
            float(item[1].get("value_score") or 0.0),
            float(getattr(item[0], "edge", 0.0)),
            float(getattr(item[0], "expected_value", 0.0)),
        ),
        reverse=True,
    )
    cap = max(1, _i("GOOL_VALUE_HUNTER_MAX_PER_CYCLE", 5))
    ranked = ranked[:cap]

    sent = {"cards": 0, "entries": 0, "skipped_existing": 0}
    with _locked(journal_path):
        rows = load_signal_journal(journal_path)
        for pick, meta in ranked:
            event_id = str(pick.event_id)
            if _already_public_single(rows, event_id):
                sent["skipped_existing"] += 1
                continue

            row = prematch_row_from_pick(
                pick,
                tier="VALUE",
                bookmaker=str(meta.get("bookmaker") or ""),
                flashscore_meta=dict(meta.get("flashscore_meta") or {}),
            )
            row.update({
                "entry_id": f"value:{event_id}:{pick.market}",
                "origin": "prematch_value",
                "product": "value_hunter",
                "card_family": "prematch_value",
                "head": "prematch_value",
                "value_score": float(meta.get("value_score") or 0.0),
                "value_probability_low": meta.get("adjusted_probability_low"),
                "value_robust_edge": meta.get("robust_edge"),
                "value_edge_gate": meta.get("edge_gate"),
                "value_ev_gate": meta.get("ev_gate"),
                "expected_value": float(pick.expected_value),
                "calibration_confidence": str(meta.get("calibration_confidence") or ""),
                "profile_sample": int(meta.get("profile_sample") or 0),
                "telegram_sent": False,
                "created_at": _now(),
            })
            rows.append(row)
            save_signal_journal(journal_path, rows)

            png = render_v4_prematch_card(row)
            fair = 1.0 / max(.0001, float(pick.model_probability))
            caption = (
                f"🔥 <b>GOOL VALUE HUNTER · HIGH ODDS</b>\n"
                f"{escape(str(pick.home))} — {escape(str(pick.away))}\n"
                f"<b>{escape(_market_label(str(pick.selection or pick.market)))} @ {float(pick.odds):.2f}</b>\n"
                f"🧠 GOOL {float(pick.model_probability)*100:.1f}% · рынок {float(pick.market_probability)*100:.1f}%\n"
                f"⚖️ Fair GOOL {fair:.2f} · edge {float(pick.edge)*100:+.1f} п.п. · EV {float(pick.expected_value)*100:+.1f}%"
            )
            delivered = telegram.broadcast_photo(
                png,
                caption=caption,
                reply_markup=prematch_keyboard(str(row["entry_id"])),
            )
            if delivered:
                row["telegram_sent"] = True
                row["telegram_sent_at"] = _now()
                row["telegram_delivery_count"] = int(delivered)
                sent["cards"] += 1
                sent["entries"] += 1
                save_signal_journal(journal_path, rows)
                print(
                    f"VALUE_HUNTER_SENT match={pick.home}--{pick.away} "
                    f"selection={pick.selection} odd={pick.odds:.2f} "
                    f"p={pick.model_probability:.3f} edge={pick.edge*100:+.1f}pp "
                    f"ev={pick.expected_value*100:+.1f}% score={float(meta.get('value_score') or 0):.1f}",
                    flush=True,
                )
            else:
                print(f"VALUE_HUNTER_TELEGRAM_FAIL event={event_id}", flush=True)

    return sent


def value_hunter_report_text(journal_path: Path) -> str:
    rows = [
        row for row in load_signal_journal(journal_path)
        if str(row.get("origin") or "").casefold() == "prematch_value"
    ]
    if not rows:
        return "🔥 <b>GOOL VALUE HUNTER</b>\n\nПока сигналов нет."
    settled = [r for r in rows if str(r.get("result") or "") in {"won", "lost", "push", "void"}]
    won = sum(1 for r in settled if r.get("result") == "won")
    lost = sum(1 for r in settled if r.get("result") == "lost")
    pending = sum(1 for r in rows if str(r.get("result") or "pending") == "pending")
    profit = 0.0
    for r in settled:
        if r.get("result") == "won":
            profit += float(r.get("odd") or 1.0) - 1.0
        elif r.get("result") == "lost":
            profit -= 1.0
    roi = profit / max(1, len([r for r in settled if r.get("result") in {"won", "lost"}])) * 100.0
    avg_odd = sum(float(r.get("odd") or 0.0) for r in rows) / max(1, len(rows))
    return (
        "🔥 <b>GOOL VALUE HUNTER</b>\n\n"
        f"Всего: <b>{len(rows)}</b> · ✅ {won} · ❌ {lost} · ⏳ {pending}\n"
        f"Ср.кэф: <b>{avg_odd:.2f}</b> · P/L <b>{profit:+.2f}u</b> · ROI <b>{roi:+.1f}%</b>"
    )


__all__ = ["emit_value_hunter", "value_hunter_report_text"]
