from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()
_LAST_RECONCILE = 0.0


def _state_path() -> Path:
    raw = os.getenv("GOOL_BETDAQ_SELECTION_PUSH_STATE_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "gool_betdaq_selection_push_state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return {"version": 1, "signals": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "signals": {}}
    if not isinstance(payload.get("signals"), dict):
        payload["signals"] = {}
    return payload


def _save_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").casefold()
    tokens = re.findall(r"[a-z0-9]+", text)
    generic = {"fc", "cf", "sc", "afc", "fk", "ac", "club", "football", "soccer"}
    tokens = [token for token in tokens if token not in generic]
    return " ".join(tokens)


def _similarity(left: Any, right: Any) -> float:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq = SequenceMatcher(None, a, b).ratio()
    at, bt = set(a.split()), set(b.split())
    jac = len(at & bt) / max(1, len(at | bt))
    return max(seq, 0.65 * seq + 0.35 * jac)


def _provider_row(match: Any) -> dict[str, Any]:
    return {
        "event_id": str(getattr(match, "provider_match_id", "") or ""),
        "home": str(getattr(match, "home", "") or ""),
        "away": str(getattr(match, "away", "") or ""),
        "minute": int(getattr(match, "minute", 0) or 0),
        "home_score": int(getattr(match, "home_score", 0) or 0),
        "away_score": int(getattr(match, "away_score", 0) or 0),
        "is_halftime": bool(getattr(match, "is_halftime", False)),
    }


def _best_live_match(alert: dict[str, Any], live_matches: list[Any]) -> dict[str, Any] | None:
    home, away = str(alert.get("home") or ""), str(alert.get("away") or "")
    best: tuple[float, dict[str, Any]] | None = None
    for item in live_matches:
        row = _provider_row(item)
        hs = _similarity(home, row["home"])
        aws = _similarity(away, row["away"])
        # Never reverse home/away: that would invert P1/P2 settlement.
        if hs < 0.72 or aws < 0.72:
            continue
        score = (hs + aws) / 2.0
        if score < 0.80:
            continue
        if best is None or score > best[0]:
            best = (score, row)
    return dict(best[1]) if best else None


def _attach_flashscore(row: dict[str, Any], live_matches: list[Any]) -> bool:
    if row.get("flashscore_event_id"):
        return False
    matched = _best_live_match(row, live_matches)
    if not matched:
        row["flashscore_mapping"] = "unmapped"
        return False
    row.update(
        {
            "flashscore_event_id": matched["event_id"],
            "flashscore_home": matched["home"],
            "flashscore_away": matched["away"],
            "flashscore_mapping": "team_pair",
            "mapping_score": round(
                (_similarity(row.get("home"), matched["home"]) + _similarity(row.get("away"), matched["away"])) / 2.0,
                3,
            ),
            "mapped_at": _now_iso(),
            "entry_live_score": [matched["home_score"], matched["away_score"]],
            "entry_live_minute": matched["minute"],
        }
    )
    return True


def record_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not alerts:
        return []
    try:
        from .providers.flashscore import FlashscoreProvider

        live_matches = FlashscoreProvider().live_matches()
    except Exception as exc:
        print(f"BETDAQ_SELECTION_PUSH mapping_error={type(exc).__name__}:{exc}", flush=True)
        live_matches = []

    created: list[dict[str, Any]] = []
    with _LOCK:
        state = _load_state()
        signals = state.get("signals") or {}
        for alert in alerts:
            row = dict(alert)
            signal_id = f"bdq-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
            row.update(
                {
                    "signal_id": signal_id,
                    "created_at": _now_iso(),
                    "result": "pending",
                    "result_notified": False,
                    "signal_source": "BETDAQ_SELECTION_FLOW",
                }
            )
            _attach_flashscore(row, live_matches)
            signals[signal_id] = row
            created.append(dict(row))
        ordered = sorted(
            (value for value in signals.values() if isinstance(value, dict)),
            key=lambda value: str(value.get("created_at") or ""),
            reverse=True,
        )[:2000]
        state = {
            "version": 1,
            "updated_at": _now_iso(),
            "signals": {str(row.get("signal_id")): row for row in ordered if row.get("signal_id")},
        }
        _save_state(state)
    return created


def _first_half_score(provider: Any, event_id: str) -> list[int] | None:
    try:
        timeline = provider.fetch_goal_timeline(event_id)
    except Exception:
        return None
    score = [0, 0]
    for goal in timeline or []:
        if str(goal.get("period") or "").upper() != "1H":
            continue
        current = goal.get("score") or []
        try:
            if len(current) >= 2:
                score = [int(current[0] or 0), int(current[1] or 0)]
        except Exception:
            continue
    return score


def _settle_result(row: dict[str, Any], score: list[int]) -> str:
    home, away = int(score[0] or 0), int(score[1] or 0)
    family = str(row.get("family") or "")
    selection = str(row.get("selection") or "").upper()
    if family == "match_odds":
        winner = "P1" if home > away else ("P2" if away > home else "X")
        return "won" if selection == winner else "lost"

    if family == "total":
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            return "unknown"
        total = home + away
        if abs(total - line) < 1e-9:
            return "void"
        if selection == "TB":
            return "won" if total > line else "lost"
        if selection == "TM":
            return "won" if total < line else "lost"
    return "unknown"


def reconcile_pending(*, force: bool = False) -> list[dict[str, Any]]:
    global _LAST_RECONCILE
    try:
        interval = max(10.0, float(os.getenv("BETDAQ_SELECTION_RESULT_CHECK_SECONDS", "30")))
    except (TypeError, ValueError):
        interval = 30.0
    now_mono = time.monotonic()
    if not force and now_mono - _LAST_RECONCILE < interval:
        return []
    _LAST_RECONCILE = now_mono

    with _LOCK:
        state = _load_state()
        signals = state.get("signals") or {}
        pending = [row for row in signals.values() if isinstance(row, dict) and str(row.get("result") or "pending") == "pending"]
        if not pending:
            return []

        try:
            from .providers.flashscore import FlashscoreProvider

            provider = FlashscoreProvider()
            live_matches = provider.live_matches()
        except Exception as exc:
            print(f"BETDAQ_SELECTION_RESULT provider_error={type(exc).__name__}:{exc}", flush=True)
            return []

        changed = False
        for row in pending:
            if not row.get("flashscore_event_id") and _attach_flashscore(row, live_matches):
                changed = True

        ids = {str(row.get("flashscore_event_id") or "") for row in pending if row.get("flashscore_event_id")}
        try:
            states = provider.event_states(ids) if ids else {}
        except Exception as exc:
            print(f"BETDAQ_SELECTION_RESULT state_error={type(exc).__name__}:{exc}", flush=True)
            states = {}

        settled: list[dict[str, Any]] = []
        for row in pending:
            event_id = str(row.get("flashscore_event_id") or "")
            live = states.get(event_id) or {}
            if not live:
                continue
            live_score = [int(live.get("home_score") or 0), int(live.get("away_score") or 0)]
            row["last_live_score"] = live_score
            row["last_flashscore_status"] = str(live.get("status_code") or "")
            changed = True

            period = str(row.get("period") or "FT").upper()
            settle_score: list[int] | None = None
            settlement_source = ""
            if period == "1H":
                first_half_complete = bool(live.get("is_finished")) or str(live.get("status_code") or "") in {"38", "13", "6"}
                if first_half_complete:
                    settle_score = _first_half_score(provider, event_id)
                    settlement_source = "flashscore_first_half"
            elif bool(live.get("is_finished")):
                settle_score = live_score
                settlement_source = "flashscore_final"

            if settle_score is None:
                continue
            result = _settle_result(row, settle_score)
            if result == "unknown":
                continue
            row.update(
                {
                    "result": result,
                    "settled_at": _now_iso(),
                    "settled_score": settle_score,
                    "settlement_source": settlement_source,
                }
            )
            settled.append(dict(row))

        if changed:
            state["updated_at"] = _now_iso()
            _save_state(state)
        return settled


def pending_notifications() -> list[dict[str, Any]]:
    with _LOCK:
        state = _load_state()
        return [
            dict(row)
            for row in (state.get("signals") or {}).values()
            if isinstance(row, dict)
            and str(row.get("result") or "") in {"won", "lost", "void"}
            and not bool(row.get("result_notified"))
        ]


def mark_notified(signal_ids: list[str]) -> None:
    wanted = {str(value) for value in signal_ids if str(value)}
    if not wanted:
        return
    with _LOCK:
        state = _load_state()
        changed = False
        for signal_id, row in (state.get("signals") or {}).items():
            if signal_id in wanted and isinstance(row, dict) and not row.get("result_notified"):
                row["result_notified"] = True
                row["result_notified_at"] = _now_iso()
                changed = True
        if changed:
            state["updated_at"] = _now_iso()
            _save_state(state)


def result_text(row: dict[str, Any]) -> str:
    result = str(row.get("result") or "")
    icon = "✅" if result == "won" else ("❌" if result == "lost" else "↔️")
    title = "ЗАШЁЛ" if result == "won" else ("НЕ ЗАШЁЛ" if result == "lost" else "ВОЗВРАТ")
    score = row.get("settled_score") or [0, 0]
    phase = "1-й тайм" if str(row.get("period") or "FT").upper() == "1H" else "финал"
    return (
        f"{icon} <b>GOOL · BETDAQ ПРОГРУЗ · {title}</b>\n"
        f"<b>{html.escape(str(row.get('event_name') or '?'))}</b>\n"
        f"🎯 {html.escape(str(row.get('label') or '?'))}\n"
        f"🏁 {phase}: <b>{int(score[0] or 0)}:{int(score[1] or 0)}</b>\n"
        f"💰 сигнал: {html.escape(str(row.get('level') or 'SELECTION_FLOW'))} · "
        f"score {float(row.get('score') or 0):.0f}/100"
    )


def load_signals() -> list[dict[str, Any]]:
    with _LOCK:
        state = _load_state()
        return [dict(row) for row in (state.get("signals") or {}).values() if isinstance(row, dict)]


__all__ = [
    "load_signals",
    "mark_notified",
    "pending_notifications",
    "reconcile_pending",
    "record_alerts",
    "result_text",
]
