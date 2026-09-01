from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import signal_worker_all as base
from . import signal_cards as trained_cards
from .gool_live_cards import render_gool_live_result_card, render_gool_live_signal_card
from .journal import append_analysis, save_signal_journal
from .match_context import provider_pair
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
    """Settle stale pending model signals as soon as Flashscore marks a match FINISHED."""
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


def _pair_total(record: dict[str, Any], key: str) -> float | None:
    try:
        home, away = provider_pair(record, key)
    except Exception:
        return None
    if home is None or away is None:
        return None
    return float(home + away)


def _weighted_ratio(values: dict[str, float | None], expectations: dict[str, tuple[float, float]]) -> tuple[float | None, int]:
    parts: list[tuple[float, float]] = []
    for key, (expected, weight) in expectations.items():
        value = values.get(key)
        if value is None or expected <= 0:
            continue
        ratio = max(0.0, min(2.5, float(value) / expected))
        parts.append((ratio, weight))
    if not parts:
        return None, 0
    total_weight = sum(weight for _, weight in parts)
    return sum(ratio * weight for ratio, weight in parts) / total_weight, len(parts)


def _another_goal_live_confirmation(record: dict[str, Any]) -> dict[str, Any]:
    """Independent LIVE judge for another_goal.

    The trained model estimates the historical chance of another goal. This
    analyzer answers a different question: is the current match producing enough
    sustained attacking evidence right now? It uses both cumulative match pressure
    and real 5m/10m deltas collected from the first live snapshots.
    """
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    momentum = record.get("live_momentum") or {}

    cumulative = {
        "xg": _pair_total(record, "xg"),
        "shots": _pair_total(record, "shots"),
        "sot": _pair_total(record, "shots_on_target"),
        "big": _pair_total(record, "big_chances"),
        "danger": _pair_total(record, "dangerous_attacks"),
        "corners": _pair_total(record, "corners"),
    }
    progress = max(0.03, min(1.0, float(minute) / 90.0))
    cumulative_expectations = {
        "xg": (2.30 * progress, 0.30),
        "shots": (24.0 * progress, 0.14),
        "sot": (8.0 * progress, 0.22),
        "big": (3.2 * progress, 0.12),
        "danger": (96.0 * progress, 0.12),
        "corners": (10.0 * progress, 0.10),
    }
    cumulative_pressure, cumulative_evidence = _weighted_ratio(cumulative, cumulative_expectations)

    recent5 = {
        "xg": momentum.get("xg_total_last_5m"),
        "shots": momentum.get("shots_total_last_5m"),
        "sot": momentum.get("sot_total_last_5m"),
        "big": momentum.get("big_total_last_5m"),
        "danger": momentum.get("danger_total_last_5m"),
    }
    recent10 = {
        "xg": momentum.get("xg_total_last_10m"),
        "shots": momentum.get("shots_total_last_10m"),
        "sot": momentum.get("sot_total_last_10m"),
        "big": momentum.get("big_total_last_10m"),
        "danger": momentum.get("danger_total_last_10m"),
    }
    five_expectations = {
        "xg": (0.13, 0.34), "shots": (1.35, 0.16), "sot": (0.45, 0.24),
        "big": (0.18, 0.14), "danger": (5.3, 0.12),
    }
    ten_expectations = {
        "xg": (0.26, 0.34), "shots": (2.70, 0.16), "sot": (0.90, 0.24),
        "big": (0.36, 0.14), "danger": (10.6, 0.12),
    }
    pressure5, evidence5 = _weighted_ratio(recent5, five_expectations)
    pressure10, evidence10 = _weighted_ratio(recent10, ten_expectations)

    min_cumulative = float(os.getenv("ANOTHER_GOAL_LIVE_MIN_CUMULATIVE", "0.90"))
    min_5m = float(os.getenv("ANOTHER_GOAL_LIVE_MIN_5M", "1.05"))
    min_10m = float(os.getenv("ANOTHER_GOAL_LIVE_MIN_10M", "1.00"))
    min_evidence = int(os.getenv("ANOTHER_GOAL_LIVE_MIN_EVIDENCE", "3"))

    enough_history = evidence5 >= min_evidence and evidence10 >= min_evidence
    direct_threat = (
        (recent5.get("xg") is not None and float(recent5.get("xg") or 0) >= 0.12)
        or (recent5.get("sot") is not None and float(recent5.get("sot") or 0) >= 1.0)
        or (recent5.get("big") is not None and float(recent5.get("big") or 0) >= 1.0)
    )
    passed = bool(
        enough_history
        and cumulative_pressure is not None and cumulative_pressure >= min_cumulative
        and pressure5 is not None and pressure5 >= min_5m
        and pressure10 is not None and pressure10 >= min_10m
        and direct_threat
    )
    pressures = [x for x in (cumulative_pressure, pressure5, pressure10) if x is not None]
    combined = sum(pressures) / len(pressures) if pressures else None
    return {
        "passed": passed,
        "pressure_score": combined,
        "combined_pressure": combined,
        "cumulative_pressure": cumulative_pressure,
        "pressure_5m": pressure5,
        "pressure_10m": pressure10,
        "minimum": min_5m,
        "minimum_cumulative": min_cumulative,
        "minimum_5m": min_5m,
        "minimum_10m": min_10m,
        "evidence_5m": evidence5,
        "evidence_10m": evidence10,
        "minimum_evidence": min_evidence,
        "enough_history": enough_history,
        "direct_threat": direct_threat,
        "recent_5m": recent5,
        "recent_10m": recent10,
        "cumulative": cumulative,
    }


def _minute_aware_goal_timing(match: dict[str, Any], probability: float, model_result: dict[str, Any]):
    """Return absolute probabilities: goal before HT and goal before FT."""
    minute = int(match.get("minute") or 0)
    try:
        p_any = max(0.01, min(0.99, float(probability)))
    except Exception:
        return None, None
    if bool(match.get("is_halftime")) or minute >= 46:
        return None, 100.0 * p_any
    if minute <= 0:
        return None, 100.0 * p_any
    try:
        p_ht = float((model_result.get("trained_probability") or {}).get("goal_before_ht"))
    except Exception:
        return None, 100.0 * p_any
    p_ht = max(0.0, min(p_any, p_ht))
    return 100.0 * p_ht, 100.0 * p_any


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
            live = _another_goal_live_confirmation(record)
            result["another_goal_live"] = live
            analyzer = result.setdefault("gool_analyzer", {})
            analyzer["another_goal"] = {
                "required": True,
                "name": "another_goal_sustained_live_pressure",
                "score": live.get("combined_pressure"),
                "minimum": live.get("minimum_5m"),
                "passed": bool(live.get("passed")),
                "details": live,
            }
            if not live.get("passed"):
                result.setdefault("blended", {})["another_goal"] = None
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
        trained_another = trained.get("another_goal")
        live = self._diag_model_result.get("another_goal_live") or {}
        min_strength = float(os.getenv("GOOL_LIVE_MIN_STRENGTH", "0.70"))
        two_more_state = _live_status(_LAST_TWO_MORE.get(mid), min_strength)
        live_pressure = live.get("combined_pressure")
        live_text = "WAIT" if live_pressure is None else f"{'PASS' if live.get('passed') else 'WAIT'} {float(live_pressure):.2f}x"
        print(
            f"GOOL_SYSTEMS match={match.get('home','?')} - {match.get('away','?')} "
            f"stage={'HT' if is_ht else minute} score={score} | "
            f"ANOTHER_MODEL={_pct(trained_another) if trained_another is not None else 'WAIT'} "
            f"ANOTHER_LIVE={live_text} "
            f"ANOTHER_GOAL={'READY ' + _pct(another) if another is not None else 'WAIT'} | "
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
