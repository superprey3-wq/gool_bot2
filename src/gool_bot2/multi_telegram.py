from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from . import telegram
from .multi_card import _gool_metric_text, render_multi_result_card
from .multi_delivery import was_publicly_sent
from .multi_router import RouterDecision
from .multi_steam_card import is_strong_steam, render_multi_signal_card


ACTIVE_MODE = "active"


def is_multi_telegram_active() -> bool:
    return str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() == ACTIVE_MODE


@contextmanager
def silence_legacy_telegram() -> Iterator[None]:
    """Prevent old per-strategy cards/journal entries during active Multi cutover."""
    if not is_multi_telegram_active():
        yield
        return
    had_token = "TELEGRAM_BOT_TOKEN" in os.environ
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    had_journal_flag = "GOOL_LEGACY_JOURNAL_SILENT" in os.environ
    journal_flag = os.environ.get("GOOL_LEGACY_JOURNAL_SILENT", "")
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["GOOL_LEGACY_JOURNAL_SILENT"] = "1"
    try:
        yield
    finally:
        if had_token:
            os.environ["TELEGRAM_BOT_TOKEN"] = token
        else:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        if had_journal_flag:
            os.environ["GOOL_LEGACY_JOURNAL_SILENT"] = journal_flag
        else:
            os.environ.pop("GOOL_LEGACY_JOURNAL_SILENT", None)


def _signal_caption(entry: dict[str, Any], *, strong_steam: bool = False) -> str:
    """Text fallback used only when Telegram fails to accept the PNG card."""
    title = "🔥 <b>GOOL MULTI · ПРОГРУЗ 1xBet</b>" if strong_steam else "🎯 <b>GOOL MULTI · BEST BET</b>"
    metric = _gool_metric_text(entry, entry.get("probability"))
    steam = "\n🔥 <b>ПРОГРУЗ 1xBet</b>" if strong_steam else ""
    return (
        f"{title}\n"
        f"{entry.get('home','?')} — {entry.get('away','?')}\n"
        f"{int(entry.get('minute') or 0)}' · {int((entry.get('score') or [0,0])[0])}:{int((entry.get('score') or [0,0])[1])}\n"
        f"<b>{entry.get('market','?')} @ {float(entry.get('odd') or 0):.2f}</b>\n"
        f"<b>{metric}</b>{steam}"
    )


def _result_caption(row: dict[str, Any]) -> str:
    """Text fallback used only when Telegram fails to accept the result PNG."""
    result = str(row.get("result") or "void").lower()
    if result == "won":
        icon, label = "✅", "ЗАШЁЛ"
    elif result == "lost":
        icon, label = "❌", "НЕ ЗАШЁЛ"
    else:
        icon, label = "↩️", "ВОЗВРАТ / VOID"
    score = list(row.get("settled_score") or [0, 0])
    return (
        f"{icon} <b>{label} · GOOL MULTI</b>\n"
        f"{row.get('home','?')} — {row.get('away','?')}\n"
        f"{row.get('market','?')} @ {float(row.get('odd') or 0):.2f}\n"
        f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
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

    # The production decision/rating gate is authoritative. Do not apply a
    # second hidden "probability >= 70%" gate here: Goal State strength is a
    # confidence metric, not a calibrated betting probability.
    active_entry = dict(entry)
    active_entry["mode"] = "active"
    strong_steam = is_strong_steam(decision)
    fallback = _signal_caption(active_entry, strong_steam=strong_steam)
    sent = 0
    try:
        # The PNG already contains match, score, market, price and strength.
        # Do not duplicate the same information as a Telegram photo caption.
        sent = telegram.broadcast_photo(
            render_multi_signal_card(record, decision, entry=active_entry, market_row=market_row),
        )
    except Exception as exc:
        print(f"GOOL_MULTI_SIGNAL_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
    if sent == 0:
        sent = telegram.broadcast(fallback)
    print(
        f"GOOL_MULTI_SIGNAL_SENT match={entry.get('match_id')} sent={sent} strong_steam={int(strong_steam)}",
        flush=True,
    )
    return sent


def emit_multi_results(record: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    if not is_multi_telegram_active() or not rows:
        return 0
    total = 0
    for row in rows:
        if not was_publicly_sent(row):
            print(
                f"GOOL_MULTI_RESULT_SUPPRESSED match={row.get('match_id')} "
                f"result={row.get('result')} reason=signal_was_not_publicly_sent",
                flush=True,
            )
            continue
        fallback = _result_caption(row)
        sent = 0
        try:
            sent = telegram.broadcast_photo(render_multi_result_card(row, record))
        except Exception as exc:
            print(f"GOOL_MULTI_RESULT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        if sent == 0:
            sent = telegram.broadcast(fallback)
        total += sent
        print(
            f"GOOL_MULTI_RESULT_SENT match={row.get('match_id')} result={row.get('result')} sent={sent}",
            flush=True,
        )
    return total
