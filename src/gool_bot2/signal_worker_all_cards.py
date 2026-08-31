from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import signal_worker_all as base
from . import signal_cards as trained_cards
from .gool_live_cards import render_gool_live_result_card, render_gool_live_signal_card
from .journal import append_analysis, save_signal_journal
from .signal_cards import flashscore_meta, stats_snapshot
from .signal_policy import exposure_gate, post_goal_gate
from .telegram import broadcast, broadcast_photo, signal_keyboard


_LAST_TWO_MORE: dict[str, dict[str, Any]] = {}
_ORIG_TWO_MORE = base.analyze_two_more_goals
_ORIG_SETTLE_PENDING = base._settle_pending


def _match_id(record: dict[str, Any]) -> str:
    return str(((record.get("match") or {}).get("flashscore_event_id") or ""))


def _capture_two_more(record: dict[str, Any]) -> dict[str, Any]:
    result = _ORIG_TWO_MORE(record)
    mid = _match_id(record)
    if mid:
        _LAST_TWO_MORE[mid] = dict(result or {})
    return result


def _disabled_btts(record: dict[str, Any]) -> dict[str, Any]:
    return {"passed": False, "pressure_score": None, "minimum": None, "confidence_score": None, "disabled": True}


def _settle_pending_finished(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Settle stale pending model signals as soon as Flashscore marks a match FINISHED.

    The legacy 90' two-snapshot fallback can leave rows pending because its
    intermediate terminal counter is not persisted unless a settlement happens.
    FINISHED is authoritative, so use it to close any remaining model signal.
    """
    settled = _ORIG_SETTLE_PENDING(record, journal)
    match = record.get("match") or {}
    if not bool(match.get("is_finished")):
        return settled
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return settled
    minute = int(match.get("minute") or 90)
    home_score, away_score = base._reconciled_score(record)
    already = {(str(r.get("match_id") or ""), str(r.get("head") or ""), int(r.get("minute") or 0)) for r in settled}
    for row in journal:
        if str(row.get("match_id") or "") != match_id:
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        head = str(row.get("head") or "")
        if head == "two_more_goals":
            continue
        signal_score = row.get("score") or [0, 0]
        result = "won" if base._is_won(head, signal_score, minute, home_score, away_score) else "lost"
        row.update({
            "result": result,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "settled_minute": minute,
            "settled_score": [home_score, away_score],
            "settlement_source": "flashscore_finished_reconcile",
        })
        row.pop("terminal_seen_score", None)
        row.pop("terminal_seen_count", None)
        key = (match_id, head, int(row.get("minute") or 0))
        if key not in already:
            settled.append(dict(row))
            already.add(key)
    return settled


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "n/a"


def _live_status(analyzer: dict[str, Any] | None, min_strength: float) -> str:
    analyzer = analyzer or {}
    pressure = analyzer.get("pressure_score")
    strength = analyzer.get("confidence_score")
    passed = bool(analyzer.get("passed"))
    if strength is None:
        return "WAIT"
    try:
        strength_f = float(strength)
    except Exception:
        return "WAIT"
    pressure_text = "n/a" if pressure is None else f"{float(pressure):.2f}"
    if not passed:
        state = "BLOCK_PRESSURE"
    elif strength_f < min_strength:
        state = f"BLOCK_CHANCE<{min_strength * 100:.0f}"
    else:
        state = "READY"
    return f"{state} pressure={pressure_text} chance={strength_f * 100:.0f}/100"


def _minute_aware_goal_timing(match: dict[str, Any], probability: float, model_result: dict[str, Any]):
    """Conditional 1T/2T timing split for the next goal.

    The trained goal-before-HT output supplies the first-half propensity, while
    the amount of first-half time still available explicitly scales that
    propensity. Therefore the same model setup at 10' and 40' cannot produce
    the same timing split: at 40' only a small first-half window remains.
    This is a timing allocation, not two independently calibrated probabilities.
    """
    minute = int(match.get("minute") or 0)
    if bool(match.get("is_halftime")) or minute >= 46:
        return 0.0, 100.0
    if minute <= 0:
        return None, None
    try:
        p_any = max(0.01, min(0.99, float(probability)))
        p_ht = float((model_result.get("trained_probability") or {}).get("goal_before_ht"))
    except Exception:
        return None, None

    remaining_1t = max(0.0, 45.0 - float(minute))
    time_factor = remaining_1t / 45.0
    first_mass = max(0.0, min(p_any, p_ht)) * time_factor
    second_mass = max(0.0, p_any - first_mass)
    total = first_mass + second_mass
    if total <= 0:
        return None, None
    first = 100.0 * first_mass / total
    return first, 100.0 - first


def _send_two_more_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        result = str(row.get("result") or "lost")
        score = row.get("settled_score") or [0, 0]
        icon = "✅" if result == "won" else "❌"
        caption = f"{icon} <b>{'ЗАШЁЛ' if result == 'won' else 'НЕ ЗАШЁЛ'}</b> · ЕЩЁ +2 ГОЛА"
        sent = 0
        try:
            png = render_gool_live_result_card(row, result)
            sent = broadcast_photo(png, caption=caption)
        except Exception as exc:
            print(f"gool_two_more_result_card_error={type(exc).__name__}:{exc}", flush=True)
        if sent == 0:
            broadcast(caption + f"\n{row.get('home','?')} — {row.get('away','?')} · {int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}")


class CardAllMatchSignalWorker(base.AllMatchSignalWorker):
    """Two-strategy engine: trained another-goal + GOOL LIVE plus-two."""

    def __init__(self, journal_path: Path, max_disagreement: float = 0.20, analysis_path: Path | None = None) -> None:
        super().__init__(journal_path, max_disagreement=max_disagreement, analysis_path=analysis_path)
        self._diag_model_result: dict[str, Any] = {}
        self._diag_predict_wrapped = False

    def _ensure_model(self) -> bool:
        ok = super()._ensure_model()
        if not ok or self.model is None or self._diag_predict_wrapped:
            return ok
        original_predict = self.model.predict

        def predict_with_capture(record: dict[str, Any]) -> dict[str, Any]:
            result = original_predict(record)
            self._diag_model_result = dict(result or {})
            return result

        self.model.predict = predict_with_capture
        self._diag_predict_wrapped = True
        return ok

    def _print_system_status(self, record: dict[str, Any]) -> None:
        match = record.get("match") or {}
        mid = _match_id(record)
        minute = int(match.get("minute") or 0)
        is_ht = bool(match.get("is_halftime"))
        score = f"{int(match.get('home_score') or 0)}:{int(match.get('away_score') or 0)}"
        trained = (self._diag_model_result.get("trained_probability") or {})
        blended = (self._diag_model_result.get("blended") or {})
        another = blended.get("another_goal")
        if another is None:
            another = trained.get("another_goal")
        min_strength = float(os.getenv("GOOL_LIVE_MIN_STRENGTH", "0.70"))
        two_more_state = _live_status(_LAST_TWO_MORE.get(mid), min_strength)
        print(
            f"GOOL_SYSTEMS match={match.get('home','?')} - {match.get('away','?')} "
            f"stage={'HT' if is_ht else minute} score={score} | "
            f"ANOTHER_GOAL={'ACTIVE ' + _pct(another) if another is not None else 'WAIT'} | "
            f"PLUS2_LIVE={two_more_state} | DISABLED=GOAL_1T,OVER2.5,BTTS",
            flush=True,
        )

    def _process(self, record: dict[str, Any]) -> int:
        self._diag_model_result = {}
        emitted = super()._process(record)
        match = record.get("match") or {}
        minute = int(match.get("minute") or 0)
        if not bool(match.get("is_finished")) and (bool(match.get("is_halftime")) or 0 < minute <= 75):
            self._print_system_status(record)
        return emitted

    def _emit_gool_live_signal(
        self,
        record: dict[str, Any],
        journal: list[dict[str, Any]],
        head: str,
        confidence: float,
        analyzer: dict[str, Any],
        model_result: dict[str, Any],
        cards: dict[str, Any],
    ) -> int:
        if head != "two_more_goals":
            return 0
        match = record.get("match") or {}
        match_id = str(match.get("flashscore_event_id") or "")
        minute = int(match.get("minute") or 0)
        home_score, away_score = base._reconciled_score(record)
        analysis_base = self._base_analysis(record, match_id)
        reasons: list[str] = []
        if minute < 10:
            reasons.append("warmup_until_10")
        if minute > 75:
            reasons.append("entry_window_closed_75")
        if not bool(analyzer.get("passed")):
            reasons.append(f"gool_pressure={float(analyzer.get('pressure_score') or 0):.2f}<{float(analyzer.get('minimum') or 0):.2f}")
        min_strength = float(os.getenv("GOOL_LIVE_MIN_STRENGTH", "0.70"))
        if confidence < min_strength:
            reasons.append(f"gool_strength={confidence:.3f}<{min_strength:.3f}")
        exposure = exposure_gate(match_id, journal, max_entries=2, max_open=2)
        reasons.extend(exposure.reasons)
        cooldown = post_goal_gate(minute, base._last_goal_minute(record), cooldown_minutes=5)
        reasons.extend(cooldown.reasons)
        duplicate = any(str(row.get("match_id")) == match_id and str(row.get("head")) == head and str(row.get("result") or "pending").lower() == "pending" for row in journal)
        if duplicate:
            reasons.append("duplicate_pending_signal")
        allowed = not reasons
        append_analysis(self.analysis_path, {
            **analysis_base,
            "head": head,
            "probability": None,
            "gool_confidence": confidence,
            "gool_signal_strength": confidence,
            "gool_event_chance": confidence,
            "gool_min_strength": min_strength,
            "gool_live_analysis": analyzer,
            "model_disagreement": None,
            "decision": "SIGNAL" if allowed else "WAIT",
            "blocks": reasons,
        })
        if not allowed:
            return 0
        pressure = float(analyzer.get("pressure_score") or 0.0)
        label = base.HEAD_LABELS[head]
        fs_meta = flashscore_meta(record)
        stat_snap = stats_snapshot(record)
        sent = 0
        try:
            png = render_gool_live_signal_card(record, head, confidence, pressure, cards)
            sent = broadcast_photo(png, caption=f"🔥 <b>{label}</b> · шанс события {confidence*100:.0f}/100 · pressure {pressure:.2f}", reply_markup=signal_keyboard(match_id, head))
        except Exception as exc:
            print(f"gool_live_card_error={type(exc).__name__}:{exc}", flush=True)
        if sent == 0:
            sent = broadcast(f"🔥 <b>{label}</b>\n{match.get('home','?')} — {match.get('away','?')}\n{minute}' · {home_score}:{away_score}\nШанс события: <b>{confidence*100:.0f}/100</b> · GOOL pressure {pressure:.2f}", reply_markup=signal_keyboard(match_id, head))
        journal.append({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "match_id": match_id,
            "head": head,
            "minute": minute,
            "home": match.get("home"),
            "away": match.get("away"),
            "league": match.get("league"),
            "score": [home_score, away_score],
            "probability": confidence,
            "signal_source": "gool_live_analyzer",
            "gool_pressure": pressure,
            "gool_signal_strength": confidence,
            "gool_event_chance": confidence,
            "provider_count": len(record.get("providers") or {}),
            "flashscore_meta": fs_meta,
            "stats_snapshot": stat_snap,
            "result": "pending",
            "in_game": False,
        })
        save_signal_journal(self.journal_path, journal)
        print(f"GOOL_LIVE_SIGNAL head={head} match={match_id} minute={minute} pressure={pressure:.2f} chance={confidence:.2f} card={int(sent > 0)}", flush=True)
        return int(sent > 0)


base.TRAINED_HEADS = ("another_goal",)
base.analyze_two_more_goals = _capture_two_more
base.analyze_live_btts = _disabled_btts
base._settle_pending = _settle_pending_finished
trained_cards._goal_timing_split = _minute_aware_goal_timing
base.AllMatchSignalWorker = CardAllMatchSignalWorker
base._send_two_more_results = _send_two_more_results


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
