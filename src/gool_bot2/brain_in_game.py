from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_ORIGINAL_IN_GAME: Callable[..., list[str]] | None = None
_INSTALLED = False


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _brain_state_path() -> Path:
    raw = os.getenv("GOOL_BRAIN_SIGNAL_STATE_PATH", "").strip()
    if raw:
        return Path(raw)
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return runtime / "live" / "gool_brain_signal_state.json"


def _load_brain_rows() -> list[dict[str, Any]]:
    try:
        payload = json.loads(_brain_state_path().read_text("utf-8"))
    except Exception:
        return []
    signals = payload.get("signals") if isinstance(payload, dict) else None
    if not isinstance(signals, dict):
        return []
    return [dict(row) for row in signals.values() if isinstance(row, dict)]


def _latest_states(path: Path | None) -> dict[str, dict[str, Any]]:
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
            match_id = str(row.get("match_id") or "")
            if not match_id:
                continue
            old = latest.get(match_id)
            if old is None or str(row.get("captured_at") or "") >= str(old.get("captured_at") or ""):
                latest[match_id] = row
    except Exception:
        return {}
    return latest


def _fresh_flashscore_states(match_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Fetch authoritative current/finished state for Brain signals shown in-game.

    Analysis JSONL normally stops receiving rows once a match leaves the working
    minute window. Without this direct refresh a finished match can remain shown
    with its last LIVE minute until the age fallback expires. One batched master
    feed lookup keeps the menu current without adding work to the normal signal loop.
    """
    ids = {str(match_id) for match_id in match_ids if str(match_id)}
    if not ids:
        return {}
    try:
        from .providers.flashscore import FlashscoreProvider

        payload = FlashscoreProvider().event_states(ids)
    except Exception as exc:
        print(
            f"GOOL_BRAIN_IN_GAME_STATE_ERROR error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(match_id): dict(state)
        for match_id, state in payload.items()
        if isinstance(state, dict)
    }


def _score(value: Any, fallback: list[int] | None = None) -> list[int]:
    fallback = list(fallback or [0, 0])
    try:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [int(value[0] or 0), int(value[1] or 0)]
    except Exception:
        pass
    return fallback[:2]


def _state_score(state: dict[str, Any], entry_score: list[int]) -> list[int]:
    direct = state.get("score")
    if isinstance(direct, (list, tuple)) and len(direct) >= 2:
        return _score(direct, entry_score)
    try:
        if "home_score" in state or "away_score" in state:
            return [int(state.get("home_score") or 0), int(state.get("away_score") or 0)]
    except Exception:
        pass
    return entry_score


def _is_finished(state: dict[str, Any]) -> bool:
    if bool(state.get("is_finished")):
        return True
    coarse = str(state.get("coarse_status") or "").strip()
    if coarse == "3":
        return True
    status = str(state.get("status") or state.get("phase") or "").strip().upper()
    return status in {"FINISHED", "FT", "AFTER EXTRA TIME", "AET", "ENDED"}


def _is_halftime(state: dict[str, Any]) -> bool:
    if bool(state.get("is_halftime")):
        return True
    status_code = str(state.get("status_code") or "").strip()
    if status_code == "38":
        return True
    status = str(state.get("status") or state.get("phase") or "").strip().upper()
    return status in {"HALFTIME", "HALF TIME", "HT"}


def _active_brain_rows(analysis_path: Path | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    now = datetime.now(timezone.utc)
    try:
        max_age_minutes = max(15.0, float(os.getenv("GOOL_BRAIN_IN_GAME_MAX_AGE_MINUTES", "180")))
    except (TypeError, ValueError):
        max_age_minutes = 180.0

    candidates: list[dict[str, Any]] = []
    for row in _load_brain_rows():
        if not bool(row.get("telegram_sent")):
            continue
        strategy = str(row.get("strategy") or row.get("head") or "")
        if strategy not in {"goal_before_ht", "another_goal"}:
            continue
        created = _parse_dt(row.get("telegram_sent_at") or row.get("created_at"))
        if created is None:
            continue
        age_minutes = (now - created.astimezone(timezone.utc)).total_seconds() / 60.0
        if age_minutes < -2.0 or age_minutes > max_age_minutes:
            continue
        candidates.append(row)

    if not candidates:
        return []

    states = _latest_states(analysis_path)
    match_ids = {str(row.get("match_id") or "") for row in candidates}
    fresh_states = _fresh_flashscore_states(match_ids)
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for row in candidates:
        match_id = str(row.get("match_id") or "")
        state = dict(states.get(match_id) or {})
        fresh = fresh_states.get(match_id)
        if fresh:
            # The master-feed state is authoritative for finished/HT flags and
            # current score. Preserve the last analysis minute only because the
            # compact event state intentionally does not calculate a minute.
            state.update(fresh)

        if _is_finished(state):
            continue

        entry_score = _score(row.get("score"))
        live_score = _state_score(state, entry_score)
        if sum(live_score) > sum(entry_score):
            # The requested next goal has already happened, so the signal is no
            # longer an open item even though it remains in the signal archive.
            continue

        try:
            entry_minute = int(row.get("minute") or 0)
        except (TypeError, ValueError):
            entry_minute = 0
        try:
            live_minute = int(state.get("minute") or entry_minute)
        except (TypeError, ValueError):
            live_minute = entry_minute
        live_minute = max(entry_minute, live_minute)

        strategy = str(row.get("strategy") or row.get("head") or "")
        if strategy == "goal_before_ht" and (_is_halftime(state) or live_minute > 45):
            continue

        view = {
            "minute": live_minute,
            "score": live_score,
        }
        out.append((row, view))

    out.sort(key=lambda item: str(item[0].get("created_at") or ""), reverse=True)
    return out


def _journal_has_pending(journal_path: Path) -> bool:
    try:
        rows = json.loads(journal_path.read_text("utf-8")) if journal_path.exists() else []
    except Exception:
        return False
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, dict)
        and str(row.get("result") or "pending").lower() == "pending"
        and str(row.get("head") or "") in {"another_goal", "two_more_goals"}
        for row in rows
    )


def _odd_text(row: dict[str, Any]) -> str:
    try:
        odd = float(row.get("odd") or 0.0)
    except (TypeError, ValueError):
        odd = 0.0
    return f"{odd:.2f}" if odd > 1.0 else "—"


def _brain_section(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    parts = [f"🧠 <b>GOOL BRAIN · В ИГРЕ · {len(rows)}</b>"]
    for index, (row, live) in enumerate(rows, 1):
        score = _score(live.get("score"), _score(row.get("score")))
        entry_score = _score(row.get("score"))
        strategy = str(row.get("strategy") or "")
        label = "Гол до перерыва" if strategy == "goal_before_ht" else "Ещё один гол"
        try:
            strength = float(row.get("confidence_score") or row.get("event_score") or 0.0)
        except (TypeError, ValueError):
            strength = 0.0
        parts.append(
            f"<b>{index}.</b> {_h(row.get('home','?'))} — {_h(row.get('away','?'))}\n"
            f"сейчас {int(live.get('minute') or row.get('minute') or 0)}' · {score[0]}:{score[1]}\n"
            f"🎯 {_h(label)} · {_h(row.get('market','?'))}\n"
            f"🧠 <b>{strength:.0f}/100 · PASS</b> · кэф {_odd_text(row)}\n"
            f"↳ сигнал: {int(row.get('minute') or 0)}' · {entry_score[0]}:{entry_score[1]}"
        )
    return "\n\n".join(parts)


def in_game_with_brain(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    brain_rows = _active_brain_rows(analysis_path)
    has_bets = _journal_has_pending(journal_path)

    base: list[str] = []
    if has_bets and _ORIGINAL_IN_GAME is not None:
        base = list(_ORIGINAL_IN_GAME(journal_path, analysis_path))

    if not brain_rows:
        if base:
            return base
        if _ORIGINAL_IN_GAME is not None:
            return list(_ORIGINAL_IN_GAME(journal_path, analysis_path))
        return ["🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\nАктивных сигналов сейчас нет."]

    total = len(brain_rows)
    if has_bets:
        # The original renderer has its own count for betting-journal rows; keep
        # its details and append the separate Brain source without mixing money.
        base.append(_brain_section(brain_rows))
        return base

    return [
        f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\nАктивных сигналов: <b>{total}</b>",
        _brain_section(brain_rows),
    ]


def install_brain_in_game_patch() -> None:
    global _INSTALLED, _ORIGINAL_IN_GAME
    if _INSTALLED:
        return
    from . import telegram

    _ORIGINAL_IN_GAME = telegram.in_game_sections
    telegram.in_game_sections = in_game_with_brain
    _INSTALLED = True
    print("GOOL_BRAIN_IN_GAME installed source=signal_state fresh_flashscore=on", flush=True)


__all__ = ["in_game_with_brain", "install_brain_in_game_patch"]
