from __future__ import annotations

import html
import threading
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_public_metrics import source_label
from .providers.flashscore import FlashscoreProvider


_LOCK = threading.RLock()
_INSTALLED = False


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _score(value: Any) -> list[int]:
    try:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [int(value[0] or 0), int(value[1] or 0)]
    except Exception:
        pass
    return [0, 0]


def _confidence(row: dict[str, Any]) -> float:
    try:
        return float(row.get("confidence_score") or row.get("rating") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_score(row: dict[str, Any]) -> float:
    try:
        value = row.get("event_score")
        if value is not None:
            return float(value)
        probability = float(row.get("probability") or 0.0)
        return probability * 100.0 if probability <= 1.0 else probability
    except (TypeError, ValueError):
        return 0.0


def _odd_text(row: dict[str, Any]) -> str:
    try:
        odd = float(row.get("odd") or 0.0)
    except (TypeError, ValueError):
        odd = 0.0
    return f"{odd:.2f}" if odd > 1.0 else "—"


def _current_live() -> dict[str, dict[str, Any]]:
    """Return only matches Flashscore confirms as LIVE right now.

    `event_states()` can omit already-finished events from the master feed. The
    old menu treated an omitted event as still pending and fell back to stale
    analysis JSONL. `live_matches()` is a stricter source for this UI: absence
    means the event is not allowed to appear in the "В игре" list.
    """
    try:
        matches = FlashscoreProvider().live_matches()
    except Exception as exc:
        print(f"GOOL_IN_GAME_LIVE_CHECK_ERROR {type(exc).__name__}:{exc}", flush=True)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for match in matches:
        match_id = str(getattr(match, "provider_match_id", "") or "")
        if not match_id:
            continue
        out[match_id] = {
            "minute": int(getattr(match, "minute", 0) or 0),
            "score": [
                int(getattr(match, "home_score", 0) or 0),
                int(getattr(match, "away_score", 0) or 0),
            ],
            "is_halftime": bool(getattr(match, "is_halftime", False)),
        }
    return out


def _is_open(row: dict[str, Any]) -> bool:
    result = str(row.get("result") or "pending").strip().lower()
    if result not in {"pending", "tracking"}:
        return False
    if str(row.get("mode") or "active").lower() == "shadow":
        return False
    return bool(str(row.get("match_id") or ""))


def _valid_for_live_period(row: dict[str, Any], live: dict[str, Any]) -> bool:
    strategy = str(row.get("strategy") or row.get("head") or "")
    minute = int(live.get("minute") or 0)
    if strategy == "goal_before_ht" and (bool(live.get("is_halftime")) or minute > 45):
        return False
    return True


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("entry_key") or row.get("brain_signal_key") or "")
        if not key:
            score = _score(row.get("score"))
            key = (
                f"{row.get('match_id')}:{row.get('strategy') or row.get('head')}:"
                f"{row.get('minute')}:{score[0]}-{score[1]}:{row.get('market')}"
            )
        previous = latest.get(key)
        if previous is None or str(row.get("created_at") or "") >= str(previous.get("created_at") or ""):
            latest[key] = row
    return list(latest.values())


def strict_in_game_sections(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    """Render "В игре" from current Flashscore LIVE membership only.

    Journal rows remain historical records. They are never considered LIVE merely
    because their result is still `pending`. This prevents old finished events
    from surviving indefinitely when Flashscore no longer returns their event
    state after the final whistle.
    """
    del analysis_path

    # Keep the existing settlement pipeline active first. It may turn a pending
    # row into won/lost before we render the list.
    try:
        from . import multi_menu

        multi_menu.reconcile_pending()
    except Exception as exc:
        print(f"GOOL_IN_GAME_RECONCILE_ERROR {type(exc).__name__}:{exc}", flush=True)

    rows = _dedupe([row for row in load_signal_journal(Path(journal_path)) if _is_open(row)])
    live_map = _current_live()

    visible: list[tuple[dict[str, Any], dict[str, Any]]] = []
    hidden_not_live = 0
    hidden_period = 0
    for row in rows:
        match_id = str(row.get("match_id") or "")
        live = live_map.get(match_id)
        if live is None:
            hidden_not_live += 1
            continue
        if not _valid_for_live_period(row, live):
            hidden_period += 1
            continue
        visible.append((row, live))

    visible.sort(key=lambda item: str(item[0].get("created_at") or ""), reverse=True)
    print(
        f"GOOL_IN_GAME_STRICT open_rows={len(rows)} live_visible={len(visible)} "
        f"hidden_not_live={hidden_not_live} hidden_period={hidden_period}",
        flush=True,
    )

    if not visible:
        return [
            "🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\n"
            "Сейчас нет сигналов на матчах, подтверждённых Flashscore как LIVE."
        ]

    parts = [f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\nОткрыто: <b>{len(visible)}</b>"]
    for index, (row, live) in enumerate(visible, 1):
        entry_score = _score(row.get("score"))
        live_score = _score(live.get("score"))
        pressure = 0.0
        try:
            pressure = float(row.get("market_pressure_pp") or 0.0)
        except (TypeError, ValueError):
            pressure = 0.0
        reason = str(row.get("selection_reason") or row.get("reason") or "")
        source = source_label(row.get("signal_source") or row.get("source"))
        block = (
            f"<b>{index}. {_h(row.get('home'))} — {_h(row.get('away'))}</b>\n"
            f"сейчас <b>{int(live.get('minute') or 0)}' · {live_score[0]}:{live_score[1]}</b>\n"
            f"🎯 <b>{_h(row.get('market'))} @ {_odd_text(row)}</b>\n"
            f"🧠 событие <b>{_event_score(row):.0f}/100</b> · уверенность <b>{_confidence(row):.0f}/100</b> · {_h(source)}\n"
            f"📈 1xBet {pressure:+.1f} п.п. · вход {int(row.get('minute') or 0)}' {entry_score[0]}:{entry_score[1]}"
        )
        if reason:
            block += f"\n↳ {_h(reason)}"
        if len("\n\n".join(parts + [block])) > 3800:
            break
        parts.append(block)
    return ["\n\n".join(parts)]


def install_strict_in_game_live() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import telegram

        telegram.in_game_sections = strict_in_game_sections
        _INSTALLED = True
        print("GOOL_IN_GAME_STRICT installed source=flashscore_live_only fail_closed=on", flush=True)


__all__ = ["install_strict_in_game_live", "strict_in_game_sections"]
