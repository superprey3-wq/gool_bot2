from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_delivery import was_publicly_sent
from .multi_public_metrics import source_label


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


def _odd(row: dict[str, Any]) -> str:
    try:
        value = float(row.get("odd") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return f"{value:.2f}" if value > 1.0 else "—"


def _latest_analysis(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    try:
        for line in path.open("r", encoding="utf-8"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            mid = str(row.get("match_id") or "")
            if not mid:
                continue
            previous = latest.get(mid)
            stamp = str(row.get("created_at") or row.get("captured_at") or "")
            old_stamp = str((previous or {}).get("created_at") or (previous or {}).get("captured_at") or "")
            if previous is None or stamp >= old_stamp:
                latest[mid] = row
    except Exception:
        return {}
    return latest


def _is_open(row: dict[str, Any]) -> bool:
    if str(row.get("mode") or "active").lower() != "active":
        return False
    if str(row.get("result") or "pending").lower() not in {"pending", "tracking"}:
        return False
    return was_publicly_sent(row)


def journal_in_game_sections(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    """Show delivered journal entries that are not settled yet.

    The journal is the source of truth for membership. Flashscore may be used by
    reconciliation to update/settle a journal row, but an unrelated current LIVE
    list never decides whether a bet existed.
    """
    from . import multi_menu

    try:
        multi_menu.reconcile_pending()
    except Exception as exc:
        print(f"GOOL_IN_GAME_RECONCILE_ERROR {type(exc).__name__}:{exc}", flush=True)

    rows = [dict(row) for row in load_signal_journal(Path(journal_path)) if _is_open(row)]
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("entry_key") or row.get("brain_signal_key") or "")
        if not key:
            key = f"{row.get('match_id')}:{row.get('strategy')}:{row.get('minute')}:{row.get('market')}"
        previous = latest.get(key)
        if previous is None or str(row.get("created_at") or "") >= str(previous.get("created_at") or ""):
            latest[key] = row
    rows = list(latest.values())
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)

    if not rows:
        return ["🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\nОткрытых сигналов сейчас нет."]

    states = _latest_analysis(analysis_path)
    parts = [f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\nОткрыто: <b>{len(rows)}</b>"]
    for index, row in enumerate(rows, 1):
        state = states.get(str(row.get("match_id") or "")) or {}
        entry_score = _score(row.get("score"))
        current_score = _score(state.get("score")) if state.get("score") is not None else entry_score
        try:
            current_minute = int(state.get("minute") or row.get("minute") or 0)
        except (TypeError, ValueError):
            current_minute = int(row.get("minute") or 0)
        try:
            rating = float(row.get("confidence_score") or row.get("rating") or row.get("event_score") or 0.0)
        except (TypeError, ValueError):
            rating = 0.0
        reason = str(row.get("selection_reason") or row.get("reason") or "")
        source = source_label(row.get("signal_source") or row.get("source"))
        block = (
            f"<b>{index}. {_h(row.get('home'))} — {_h(row.get('away'))}</b>\n"
            f"сейчас <b>{current_minute}' · {current_score[0]}:{current_score[1]}</b>\n"
            f"🎯 <b>{_h(row.get('market'))} @ {_odd(row)}</b>\n"
            f"🧠 <b>{rating:.0f}/100</b> · {_h(source)}\n"
            f"↳ вход {int(row.get('minute') or 0)}' · {entry_score[0]}:{entry_score[1]}"
        )
        if reason:
            block += f"\n{_h(reason)}"
        if len("\n\n".join(parts + [block])) > 3800:
            break
        parts.append(block)
    return ["\n\n".join(parts)]


def install_journal_in_game() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import telegram

    telegram.in_game_sections = journal_in_game_sections
    _INSTALLED = True
    print("GOOL_IN_GAME installed source=delivered_journal settlement=flashscore", flush=True)


__all__ = ["install_journal_in_game", "journal_in_game_sections"]
