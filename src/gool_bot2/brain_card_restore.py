from __future__ import annotations

import os
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


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _score_pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0] or 0), int(value[1] or 0)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        if "home_score" in value or "away_score" in value:
            try:
                return int(value.get("home_score") or 0), int(value.get("away_score") or 0)
            except (TypeError, ValueError):
                return None
        return _score_pair(value.get("score"))
    return None


def _latest_timeline_score(timeline: Any) -> tuple[int, int] | None:
    """Return the newest confirmed cumulative score from Flashscore goal incidents."""
    latest: tuple[int, int] | None = None
    latest_minute = -1
    for raw in timeline or []:
        if not isinstance(raw, dict) or str(raw.get("event_type") or "goal").lower() != "goal":
            continue
        score = _score_pair(raw.get("score"))
        if score is None:
            continue
        try:
            minute = int(raw.get("minute") or 0)
        except (TypeError, ValueError):
            minute = 0
        if latest is None or minute >= latest_minute:
            latest = score
            latest_minute = minute
    return latest


def _fresh_flashscore_state(match_id: str) -> dict[str, Any] | None:
    """Fetch one last authoritative state and reconcile it with confirmed goals.

    Flashscore's master feed can lag its incident/summary feed by a few minutes.
    A Brain card must therefore not trust the master scoreboard alone: when the
    confirmed goal timeline already contains a later cumulative score, that score
    wins for the pre-send stale check. If master and timeline disagree at the same
    total, mark a conflict so production can fail closed instead of guessing.
    """
    if not match_id or not _truthy("GOOL_BRAIN_PRE_SEND_SCORE_CHECK", True):
        return None
    try:
        from .providers.flashscore import FlashscoreProvider

        provider = FlashscoreProvider()
        state = (provider.event_states({match_id}) or {}).get(match_id)
        if not isinstance(state, dict):
            return None
        fresh = dict(state)
    except Exception as exc:
        print(
            f"GOOL_BRAIN_PRE_SEND_SCORE_CHECK_ERROR match={match_id} "
            f"error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return None

    try:
        timeline_score = _latest_timeline_score(provider.fetch_goal_timeline(match_id))
    except Exception as exc:
        print(
            f"GOOL_BRAIN_PRE_SEND_TIMELINE_ERROR match={match_id} "
            f"error={type(exc).__name__}:{exc}",
            flush=True,
        )
        timeline_score = None

    master_score = _score_pair(fresh)
    fresh["score_source"] = "master"
    if timeline_score is not None:
        fresh["goal_timeline_score"] = [timeline_score[0], timeline_score[1]]
        if master_score is None or sum(timeline_score) > sum(master_score):
            fresh["home_score"], fresh["away_score"] = timeline_score
            fresh["score_source"] = "goal_timeline"
            print(
                f"GOOL_BRAIN_PRE_SEND_SCORE_ADVANCE match={match_id} "
                f"master={master_score} timeline={timeline_score}",
                flush=True,
            )
        elif sum(timeline_score) == sum(master_score) and timeline_score != master_score:
            fresh["score_conflict"] = True
            print(
                f"GOOL_BRAIN_PRE_SEND_SCORE_CONFLICT match={match_id} "
                f"master={master_score} timeline={timeline_score}",
                flush=True,
            )
    return fresh


def _pre_send_stale_reason(
    record: dict[str, Any],
    decision: RouterDecision,
    entry: dict[str, Any],
) -> str | None:
    """Reject a signal whose football state changed after Brain calculation.

    Updating only the pixels would be unsafe: if 1:0 became 1:1, the target total
    calculated by the Brain also changed. The whole stale signal must be dropped
    so the next collector snapshot can rebuild it from the new score.
    """
    match = record.get("match") or {}
    match_id = str(entry.get("match_id") or match.get("flashscore_event_id") or "")
    state = _fresh_flashscore_state(match_id)
    if not state:
        return None

    if bool(state.get("is_finished")):
        return "match_finished"
    if bool(state.get("score_conflict")):
        return "flashscore_score_conflict"

    expected = _score_pair(decision.score) or _score_pair(entry.get("score")) or _score_pair(match)
    fresh = _score_pair(state)
    if expected is not None and fresh is not None and fresh != expected:
        return f"score_changed_{expected[0]}-{expected[1]}_to_{fresh[0]}-{fresh[1]}"
    return None


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

    Immediately before delivery the score is revalidated against Flashscore. A
    score-changed signal is dropped rather than visually patched, because its
    target total was calculated from the old football state too.
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

    stale_reason = _pre_send_stale_reason(record, decision, entry)
    if stale_reason:
        print(
            f"GOOL_BRAIN_SIGNAL_STALE_DROP match={entry.get('match_id')} "
            f"strategy={entry.get('strategy')} reason={stale_reason}",
            flush=True,
        )
        # Do not persist the dedupe marker: the next fresh worker snapshot must
        # be allowed to rebuild and deliver a signal using the new score/line.
        return 0

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
        print("GOOL_BRAIN_CARD restored png=on missing_price=dash presend_score_check=master+timeline", flush=True)


__all__ = [
    "emit_brain_card_signal",
    "install_brain_card_patch",
]
