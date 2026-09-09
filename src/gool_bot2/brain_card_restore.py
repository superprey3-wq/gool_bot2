from __future__ import annotations

import threading
from typing import Any

from .multi_router import RouterDecision


_LOCK = threading.Lock()
_INSTALLED = False
_ORIGINAL_EMITTER: Any = None


def _is_brain_primary(decision: RouterDecision, entry: dict[str, Any] | None) -> bool:
    winner = decision.winner
    return bool(
        entry is not None
        and winner is not None
        and str(getattr(winner, "source", "") or "").startswith("brain_primary:")
    )


def emit_brain_card_signal(
    record: dict[str, Any],
    decision: RouterDecision,
    entry: dict[str, Any] | None,
    *,
    market_row: dict[str, Any] | None = None,
) -> int:
    """Render ordinary Brain-primary signals with the normal GOOL PNG card.

    1xBet remains optional information only. When no usable price exists, the
    normal card renderer receives ``None`` temporarily and displays an em dash
    instead of a fake 0.00 coefficient. Brain-only signals stay out of the
    betting P/L journal and therefore do not receive a virtual-bank strip.
    """
    if not _is_brain_primary(decision, entry):
        if _ORIGINAL_EMITTER is None:
            return 0
        return _ORIGINAL_EMITTER(record, decision, entry, market_row=market_row)

    from . import brain_primary_mode as brain
    from . import telegram
    from .multi_steam_card import render_multi_signal_card
    from .multi_telegram import is_multi_telegram_active

    if not is_multi_telegram_active():
        return 0

    assert entry is not None
    winner = decision.winner
    assert winner is not None

    fallback = brain._brain_text(record, entry)
    original_odd = getattr(winner, "odd", None)
    price_available = False
    try:
        price_available = float(original_odd or 0.0) > 1.0
    except (TypeError, ValueError):
        price_available = False

    sent = 0
    try:
        if not price_available:
            winner.odd = None  # renderer shows "—"; restore immediately below
        png = render_multi_signal_card(record, decision, entry=entry, market_row=market_row)
        sent = telegram.broadcast_photo(png)
    except Exception as exc:
        print(f"GOOL_BRAIN_SIGNAL_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
    finally:
        winner.odd = original_odd

    if sent == 0:
        sent = telegram.broadcast(fallback)

    brain._mark_brain_sent(entry, sent)
    print(
        f"GOOL_BRAIN_SIGNAL_SENT match={entry.get('match_id')} strategy={entry.get('strategy')} "
        f"score={entry.get('confidence_score')} price_available={int(price_available)} "
        f"card=1 sent={sent}",
        flush=True,
    )
    return sent


def install_brain_card_patch() -> None:
    """Install after Brain-primary runtime routing so every Brain path uses PNG."""
    global _INSTALLED, _ORIGINAL_EMITTER
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import brain_primary_mode as brain
        from . import multi_runtime as runtime

        _ORIGINAL_EMITTER = runtime.emit_multi_signal
        runtime.emit_multi_signal = emit_brain_card_signal
        # apply_steam_preserving_brain resolves this module global dynamically,
        # so patch it too for the rare Brain + autonomous STEAM same-tick path.
        brain.emit_brain_or_market_signal = emit_brain_card_signal
        _INSTALLED = True
        print("GOOL_BRAIN_CARD restored png=on missing_price=dash", flush=True)


__all__ = ["emit_brain_card_signal", "install_brain_card_patch"]
