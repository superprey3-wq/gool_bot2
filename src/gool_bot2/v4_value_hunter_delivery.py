from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape
from pathlib import Path
from typing import Any, Iterable

from . import telegram
from .journal import load_signal_journal, save_signal_journal
from .prematch_status import prematch_status_data
from .production_journal_serialization import _locked
from .v4_prematch_card import render_v4_prematch_card
from .v4_prematch_delivery import prematch_row_from_pick, prematch_keyboard, _market_label


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MSK = ZoneInfo("Europe/Moscow")


def _moscow_day(value: Any | None = None):
    if value is None:
        return datetime.now(MSK).date()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK).date()
    except Exception:
        return None


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _caption(row: dict[str, Any]) -> str:
    p = float(row.get("model_probability") or row.get("probability") or 0.0)
    mp = float(row.get("market_probability") or 0.0)
    odd = float(row.get("odd") or 0.0)
    fair = 1.0 / max(.0001, p)
    edge = p - mp
    ev = p * odd - 1.0
    return (
        f"🔥 <b>GOOL VALUE HUNTER · HIGH ODDS</b>\n"
        f"{escape(str(row.get('home') or '?'))} — {escape(str(row.get('away') or '?'))}\n"
        f"<b>{escape(_market_label(str(row.get('selection') or row.get('market') or '?')))} @ {odd:.2f}</b>\n"
        f"🧠 GOOL {p*100:.1f}% · рынок {mp*100:.1f}%\n"
        f"⚖️ Fair GOOL {fair:.2f} · edge {edge*100:+.1f} п.п. · EV {ev*100:+.1f}%"
    )


def _retry_pending(rows: list[dict[str, Any]], journal_path: Path) -> int:
    delivered = 0
    for row in rows:
        if str(row.get("origin") or "").casefold() != "prematch_value":
            continue
        if bool(row.get("telegram_sent")):
            continue
        if str(row.get("result") or "pending").casefold() != "pending":
            continue
        try:
            png = render_v4_prematch_card(row)
            sent = telegram.broadcast_photo(
                png,
                caption=_caption(row),
                reply_markup=prematch_keyboard(str(row.get("entry_id") or "")),
            )
        except Exception as exc:
            print(f"VALUE_HUNTER_RETRY_ERROR entry={row.get('entry_id')} error={type(exc).__name__}:{exc}", flush=True)
            sent = 0
        if sent:
            row["telegram_sent"] = True
            row["telegram_sent_at"] = _now()
            row["telegram_delivery_count"] = int(sent)
            delivered += 1
    if delivered:
        save_signal_journal(journal_path, rows)
        print(f"VALUE_HUNTER_RETRY delivered={delivered}", flush=True)
    return delivered


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

    sent = {"cards": 0, "entries": 0, "skipped_existing": 0, "retried": 0}
    with _locked(journal_path):
        rows = load_signal_journal(journal_path)
        sent["retried"] = _retry_pending(rows, journal_path)
        sent["cards"] += sent["retried"]
        today = _moscow_day()
        daily_cap = max(1, _i("GOOL_VALUE_HUNTER_MAX_PER_DAY", 8))
        sent_today = sum(
            1 for row in rows
            if str(row.get("origin") or "").casefold() == "prematch_value"
            and bool(row.get("telegram_sent"))
            and _moscow_day(row.get("created_at")) == today
        )
        remaining_today = max(0, daily_cap - sent_today)
        for pick, meta in ranked[:remaining_today]:
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
            delivered = telegram.broadcast_photo(
                png,
                caption=_caption(row),
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
    status = prematch_status_data()
    rejects = status.get("value_hunter_rejects") or {}
    reject_labels = {
        "odds": "кэф вне 2.20–6.00",
        "quality": "quality ниже порога",
        "probability": "модельная p ниже порога",
        "profile_sample": "мало истории",
        "edge": "edge ниже порога",
        "ev": "EV ниже порога",
        "probability_low_missing": "нет нижней границы p",
        "lower_bound_not_above_market": "нижняя граница не выше рынка",
        "asian_quarter_line": "азиатская четвертная линия",
    }

    running = bool(status.get("running"))
    prematch_stage = str(status.get("stage") or "—")
    hunter_stage = str(status.get("value_hunter_stage") or "not_recorded")
    scan_lines = [
        "<b>Состояние</b>",
        f"PREMATCH: <b>{'RUNNING' if running else 'IDLE'}</b> · этап <b>{prematch_stage}</b>",
        f"VALUE HUNTER: <b>{hunter_stage}</b>",
        f"старт цикла: <code>{status.get('last_cycle_started_at') or '—'}</code>",
        f"завершение: <code>{status.get('last_cycle_finished_at') or '—'}</code>",
        "",
        "<b>Последний скан</b>",
        f"VALUE-пул: <b>{status.get('value_hunter_scan_pool','—')}</b>",
        f"матчей проверено: <b>{status.get('value_hunter_scanned_matches','—')}</b>",
        f"смоделировано рынков: <b>{status.get('value_hunter_modeled_markets','—')}</b>",
        f"high-odds 2.20–6.00: <b>{status.get('value_hunter_high_odds_markets','—')}</b>",
        f"прошли VALUE-фильтры: <b>{status.get('value_hunter_qualified','—')}</b>",
        f"кандидатов после выбора 1 на матч: <b>{status.get('value_hunter_candidates','—')}</b>",
        f"отправлено в последнем цикле: <b>{status.get('value_hunter_sent','—')}</b>",
    ]
    if isinstance(rejects, dict) and rejects:
        top = sorted(rejects.items(), key=lambda kv: int(kv[1]), reverse=True)[:6]
        scan_lines.append("")
        scan_lines.append("<b>Главные причины отсева</b>")
        for key, count in top:
            scan_lines.append(f"• {reject_labels.get(str(key), str(key))}: <b>{count}</b>")

    if not rows:
        return (
            "🔥 <b>GOOL VALUE HUNTER</b>\n\n"
            + "\n".join(scan_lines)
            + "\n\nПока публичных VALUE-сигналов нет."
        )

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
        + "\n".join(scan_lines)
        + "\n\n"
        f"<b>История сигналов</b>\n"
        f"Всего: <b>{len(rows)}</b> · ✅ {won} · ❌ {lost} · ⏳ {pending}\n"
        f"Ср.кэф: <b>{avg_odd:.2f}</b> · P/L <b>{profit:+.2f}u</b> · ROI <b>{roi:+.1f}%</b>"
    )


__all__ = ["emit_value_hunter", "value_hunter_report_text"]
