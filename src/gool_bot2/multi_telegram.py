from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from . import telegram
from .multi_card import render_multi_card, render_multi_result_card
from .multi_router import RouterDecision


ACTIVE_MODE = "active"


def is_multi_telegram_active() -> bool:
    return str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() == ACTIVE_MODE


@contextmanager
def silence_legacy_telegram() -> Iterator[None]:
    """Prevent old per-strategy cards from reaching Telegram during Multi cutover.

    The legacy worker still runs so GOOL models/prematch/live analyzers are
    calculated for Multi. Telegram transport reads TELEGRAM_BOT_TOKEN at send
    time, so hiding it for the duration of the legacy _process call suppresses
    only legacy delivery. The token is restored before the Multi card is sent.
    """
    if not is_multi_telegram_active():
        yield
        return
    had_token = "TELEGRAM_BOT_TOKEN" in os.environ
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    try:
        yield
    finally:
        if had_token:
            os.environ["TELEGRAM_BOT_TOKEN"] = token
        else:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)


def _signal_source(entry: dict[str, Any]) -> str:
    return str(entry.get("signal_source") or "GOOL")


def _signal_caption(entry: dict[str, Any]) -> str:
    return (
        "🎯 <b>GOOL MULTI · BEST BET</b>\n"
        f"{entry.get('home','?')} — {entry.get('away','?')}\n"
        f"{int(entry.get('minute') or 0)}' · {int((entry.get('score') or [0,0])[0])}:{int((entry.get('score') or [0,0])[1])}\n"
        f"<b>{entry.get('market','?')} @ {float(entry.get('odd') or 0):.2f}</b>\n"
        f"GOOL {float(entry.get('probability') or 0)*100:.1f}% · rating {float(entry.get('rating') or 0):.0f}/100 · {_signal_source(entry)}"
    )


def _result_caption(row: dict[str, Any]) -> str:
    result = str(row.get("result") or "void").lower()
    if result == "won":
        icon, label = "✅", "ЗАШЁЛ"
    elif result == "lost":
        icon, label = "❌", "НЕ ЗАШЁЛ"
    else:
        icon, label = "↩️", "ВОЗВРАТ / VOID"
    score = list(row.get("settled_score") or [0, 0])
    profit = row.get("virtual_profit_rub")
    try:
        pl = float(profit)
        pl_text = f"{pl:+.0f} ₽"
    except (TypeError, ValueError):
        pl_text = "—"
    return (
        f"{icon} <b>{label} · GOOL MULTI</b>\n"
        f"{row.get('home','?')} — {row.get('away','?')}\n"
        f"{row.get('market','?')} @ {float(row.get('odd') or 0):.2f}\n"
        f"Расчёт {int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)} · P/L {pl_text}"
    )


def emit_multi_signal(
    record: dict[str, Any],
    decision: RouterDecision,
    entry: dict[str, Any] | None,
    *,
    market_row: dict[str, Any] | None = None,
) -> int:
    if not is_multi_telegram_active() or entry is None or decision.winner is None:
        return 0
    active_entry = dict(entry)
    active_entry["mode"] = "active"
    caption = _signal_caption(active_entry)
    sent = 0
    try:
        sent = telegram.broadcast_photo(
            render_multi_card(record, decision, entry=active_entry, market_row=market_row),
            caption=caption,
        )
    except Exception as exc:
        print(f"GOOL_MULTI_SIGNAL_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
    if sent == 0:
        sent = telegram.broadcast(caption)
    print(f"GOOL_MULTI_SIGNAL_SENT match={entry.get('match_id')} sent={sent}", flush=True)
    return sent


def emit_multi_results(record: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    if not is_multi_telegram_active() or not rows:
        return 0
    total = 0
    for row in rows:
        caption = _result_caption(row)
        sent = 0
        try:
            sent = telegram.broadcast_photo(render_multi_result_card(row, record), caption=caption)
        except Exception as exc:
            print(f"GOOL_MULTI_RESULT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        if sent == 0:
            sent = telegram.broadcast(caption)
        total += sent
        print(
            f"GOOL_MULTI_RESULT_SENT match={row.get('match_id')} result={row.get('result')} sent={sent}",
            flush=True,
        )
    return total
