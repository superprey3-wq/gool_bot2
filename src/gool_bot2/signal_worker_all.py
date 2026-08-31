from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import append_analysis, load_signal_journal, save_signal_journal
from .live_gool_analyzer import analyze_live_btts, analyze_two_more_goals
from .match_context import card_context, provider_pair
from .signal_cards import flashscore_meta, render_signal_card, stats_snapshot
from .signal_policy import combine_gates, exposure_gate, market_state_gate, model_threshold_gate, post_goal_gate, time_gate
from .telegram import broadcast, broadcast_photo, poll_telegram_updates, send_startup_status, signal_keyboard
from .signal_worker import (
    HEAD_LABELS as BASE_HEAD_LABELS,
    SignalWorker,
    _correct_false_losses,
    _last_goal_minute,
    _message,
    _reconciled_score,
    _send_result_cards,
    _settle_pending,
)


HEAD_LABELS = {**BASE_HEAD_LABELS, "two_more_goals": "ЕЩЁ +2 ГОЛА"}
TRAINED_HEADS = tuple(BASE_HEAD_LABELS)


def _safe_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return max(0.0, float(current) - float(previous))


def _pair_snapshot(record: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    try:
        return provider_pair(record, key)
    except Exception:
        return None, None


def _settle_two_more(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return []
    minute = int(match.get("minute") or 0)
    home_score, away_score = _reconciled_score(record)
    total = home_score + away_score
    finished = bool(match.get("is_finished"))
    settled: list[dict[str, Any]] = []
    for row in journal:
        if str(row.get("match_id")) != match_id or str(row.get("head")) != "two_more_goals":
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        entry = row.get("score") or [0, 0]
        entry_total = int(entry[0] or 0) + int(entry[1] or 0)
        result = "won" if total >= entry_total + 2 else ("lost" if finished else None)
        if result is None:
            continue
        row.update({
            "result": result,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "settled_minute": minute,
            "settled_score": [home_score, away_score],
        })
        settled.append(dict(row))
    return settled


def _send_two_more_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        result = str(row.get("result") or "lost")
        score = row.get("settled_score") or [0, 0]
        icon = "✅" if result == "won" else "❌"
        text = (
            f"{icon} <b>{'ЗАШЁЛ' if result == 'won' else 'НЕ ЗАШЁЛ'}</b> · ЕЩЁ +2 ГОЛА\n"
            f"{row.get('home','?')} — {row.get('away','?')} · "
            f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
        )
        broadcast(text)


class AllMatchSignalWorker(SignalWorker):
    """Analyze every Flashscore live match through 75' with models + GOOL momentum."""

    def __init__(self, journal_path: Path, max_disagreement: float = 0.20, analysis_path: Path | None = None) -> None:
        super().__init__(journal_path, max_disagreement=max_disagreement, analysis_path=analysis_path)
        self._live_history: dict[str, list[dict[str, Any]]] = {}

    def _attach_momentum(self, record: dict[str, Any], match_id: str) -> None:
        match = record.get("match") or {}
        minute = int(match.get("minute") or 0)
        if minute <= 0:
            return

        current: dict[str, Any] = {"minute": minute}
        for key, alias in (
            ("shots", "shots"),
            ("shots_on_target", "sot"),
            ("xg", "xg"),
            ("big_chances", "big"),
            ("dangerous_attacks", "danger"),
        ):
            home, away = _pair_snapshot(record, key)
            current[f"home_{alias}"] = home
            current[f"away_{alias}"] = away

        history = self._live_history.setdefault(match_id, [])
        # Replace a duplicate snapshot for the same displayed minute.
        history = [row for row in history if int(row.get("minute") or -1) != minute]
        history.append(current)
        history.sort(key=lambda row: int(row.get("minute") or 0))
        history = history[-24:]
        self._live_history[match_id] = history

        momentum: dict[str, float | None] = {}
        for window in (5, 10):
            target = minute - window
            previous = None
            for row in reversed(history[:-1]):
                if int(row.get("minute") or 0) <= target:
                    previous = row
                    break
            if previous is None:
                continue
            for alias in ("shots", "sot", "xg", "big", "danger"):
                for side in ("home", "away"):
                    value = _safe_delta(current.get(f"{side}_{alias}"), previous.get(f"{side}_{alias}"))
                    momentum[f"{side}_{alias}_last_{window}m"] = value
                h = momentum.get(f"home_{alias}_last_{window}m")
                a = momentum.get(f"away_{alias}_last_{window}m")
                momentum[f"{alias}_total_last_{window}m"] = None if h is None or a is None else float(h + a)
        record["live_momentum"] = momentum

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
        match = record.get("match") or {}
        match_id = str(match.get("flashscore_event_id") or "")
        minute = int(match.get("minute") or 0)
        home_score, away_score = _reconciled_score(record)
        base = self._base_analysis(record, match_id)
        reasons: list[str] = []

        if minute < 10:
            reasons.append("warmup_until_10")
        if minute > 75:
            reasons.append("entry_window_closed_75")
        if head == "both_teams_to_score":
            if home_score > 0 and away_score > 0:
                reasons.append("btts_already_won")
            if home_score == 0 and away_score == 0:
                reasons.append("btts_live_requires_one_team_already_scored")
        if not bool(analyzer.get("passed")):
            reasons.append(
                f"gool_pressure={float(analyzer.get('pressure_score') or 0):.2f}<"
                f"{float(analyzer.get('minimum') or 0):.2f}"
            )

        exposure = exposure_gate(match_id, journal, max_entries=2, max_open=2)
        reasons.extend(exposure.reasons)
        cooldown = post_goal_gate(minute, _last_goal_minute(record), cooldown_minutes=5)
        reasons.extend(cooldown.reasons)
        duplicate = any(
            str(row.get("match_id")) == match_id
            and str(row.get("head")) == head
            and str(row.get("result") or "pending").lower() == "pending"
            for row in journal
        )
        if duplicate:
            reasons.append("duplicate_pending_signal")

        allowed = not reasons
        append_analysis(self.analysis_path, {
            **base,
            "head": head,
            "probability": None,
            "gool_confidence": confidence,
            "gool_live_analysis": analyzer,
            "model_disagreement": None,
            "decision": "SIGNAL" if allowed else "WAIT",
            "blocks": reasons,
        })
        if not allowed:
            return 0

        pressure = float(analyzer.get("pressure_score") or 0.0)
        label = HEAD_LABELS[head]
        fs_meta = flashscore_meta(record)
        stat_snap = stats_snapshot(record)
        sent = 0

        if head == "both_teams_to_score":
            try:
                png = render_signal_card(record, head, confidence, model_result, cards)
                sent = broadcast_photo(
                    png,
                    caption=f"🔥 <b>{label}</b> · GOOL LIVE pressure {pressure:.2f}",
                    reply_markup=signal_keyboard(match_id, head),
                )
            except Exception as exc:
                print(f"gool_live_card_error={type(exc).__name__}:{exc}", flush=True)
        if sent == 0:
            sent = broadcast(
                f"🔥 <b>{label}</b>\n{match.get('home','?')} — {match.get('away','?')}\n"
                f"{minute}' · {home_score}:{away_score}\n"
                f"GOOL LIVE pressure: <b>{pressure:.2f}</b> · confidence {confidence*100:.0f}%",
                reply_markup=signal_keyboard(match_id, head),
            )

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
            "provider_count": len(record.get("providers") or {}),
            "flashscore_meta": fs_meta,
            "stats_snapshot": stat_snap,
            "result": "pending",
            "in_game": False,
        })
        save_signal_journal(self.journal_path, journal)
        print(f"GOOL_LIVE_SIGNAL head={head} match={match_id} minute={minute} pressure={pressure:.2f}", flush=True)
        return int(sent > 0)

    def _process(self, record: dict[str, Any]) -> int:
        match = record.get("match") or {}
        match_id = str(match.get("flashscore_event_id") or "")
        if not match_id:
            return 0

        journal = load_signal_journal(self.journal_path)
        corrected = _correct_false_losses(record, journal)
        settled = _settle_pending(record, journal)
        two_more_settled = _settle_two_more(record, journal)
        if corrected or settled or two_more_settled:
            save_signal_journal(self.journal_path, journal)
            if corrected:
                _send_result_cards(corrected)
            if settled:
                _send_result_cards(settled)
            if two_more_settled:
                _send_two_more_results(two_more_settled)

        minute = int(match.get("minute") or 0)
        is_halftime = bool(match.get("is_halftime"))
        is_finished = bool(match.get("is_finished"))
        candidate = bool((record.get("prefilter") or {}).get("candidate"))
        base = self._base_analysis(record, match_id)

        if is_finished or (not is_halftime and minute > 75):
            append_analysis(self.analysis_path, {
                **base,
                "head": "analysis_scope",
                "probability": None,
                "model_disagreement": None,
                "decision": "WAIT",
                "blocks": ["analysis_window_closed_75" if not is_finished else "match_finished"],
            })
            return 0

        self._attach_momentum(record, match_id)

        append_analysis(self.analysis_path, {
            **base,
            "head": "prefilter",
            "probability": None,
            "model_disagreement": None,
            "decision": "INFO",
            "blocks": [] if candidate else ["prefilter_not_candidate_but_models_still_run"],
        })

        if not self._ensure_model():
            append_analysis(self.analysis_path, {
                **base,
                "head": "model",
                "probability": None,
                "model_disagreement": None,
                "decision": "WAIT",
                "blocks": ["model_unavailable"],
            })
            return 0

        model_result = self.model.predict(record)
        cards = card_context(record)
        emitted = 0
        home_score, away_score = _reconciled_score(record)

        # Existing trained heads keep their validated semantics. Every match still
        # reaches the stack; HT-only heads simply return no trained output outside HT.
        for head in TRAINED_HEADS:
            trained_probability = model_result.get("trained_probability", {}).get(head)
            probability = model_result.get("blended", {}).get(head)
            disagreement = model_result.get("disagreement", {}).get(head)
            analyzer = model_result.get("gool_analyzer", {}).get(head) or {}

            if trained_probability is None or disagreement is None:
                reason = "halftime_model_only" if head in {"over_2_5", "both_teams_to_score"} and not is_halftime else "model_output_missing"
                append_analysis(self.analysis_path, {
                    **base,
                    "head": head,
                    "probability": None,
                    "model_disagreement": disagreement,
                    "decision": "WAIT",
                    "blocks": [reason],
                    "gool_analyzer": analyzer,
                })
                continue

            if probability is None and analyzer.get("required") and not analyzer.get("passed"):
                append_analysis(self.analysis_path, {
                    **base,
                    "head": head,
                    "probability": float(trained_probability),
                    "model_disagreement": float(disagreement),
                    "decision": "WAIT",
                    "blocks": ["gool_analyzer_rejected"],
                    "gool_analyzer": analyzer,
                })
                continue

            probability = float(trained_probability if probability is None else probability)
            cooldown = int(os.getenv("LIVE_COOLDOWN_MINUTES", "12")) if head == "another_goal" else (0 if head in {"over_2_5", "both_teams_to_score"} else 5)
            gates = combine_gates(
                time_gate(head, minute, is_halftime=is_halftime, is_reentry=False),
                market_state_gate(head, home_score, away_score),
                post_goal_gate(minute, _last_goal_minute(record), cooldown_minutes=cooldown),
                exposure_gate(match_id, journal, max_entries=2, max_open=2),
                model_threshold_gate(head, probability, probability * 100.0),
            )
            reasons = list(gates.reasons)
            if float(disagreement) > self.max_disagreement:
                reasons.append(f"model_disagreement={float(disagreement):.3f}>{self.max_disagreement:.3f}")
            duplicate = any(
                str(row.get("match_id")) == match_id
                and str(row.get("head")) == head
                and str(row.get("result") or "pending").lower() == "pending"
                for row in journal
            )
            if duplicate:
                reasons.append("duplicate_pending_signal")

            allowed = gates.allowed and float(disagreement) <= self.max_disagreement and not duplicate
            append_analysis(self.analysis_path, {
                **base,
                "head": head,
                "probability": probability,
                "direct_probability": model_result.get("direct", {}).get(head),
                "hazard_probability": model_result.get("hazard", {}).get(head),
                "football_data_probability": model_result.get("football_data", {}).get(head),
                "first_half_analysis": model_result.get("first_half_analysis") if head == "goal_before_ht" else None,
                "second_half_analysis": model_result.get("second_half_analysis") if head == "over_2_5" else None,
                "btts_analysis": model_result.get("btts_analysis") if head == "both_teams_to_score" else None,
                "gool_analyzer": analyzer,
                "live_momentum": record.get("live_momentum") or {},
                "model_disagreement": float(disagreement),
                "cards": cards,
                "decision": "SIGNAL" if allowed else "WAIT",
                "blocks": reasons,
            })
            if not allowed:
                continue

            fs_meta = flashscore_meta(record)
            stat_snap = stats_snapshot(record)
            try:
                png = render_signal_card(record, head, probability, model_result, cards)
                sent = broadcast_photo(
                    png,
                    caption=f"🎯 <b>{HEAD_LABELS[head]}</b> · P {probability*100:.1f}%",
                    reply_markup=signal_keyboard(match_id, head),
                )
            except Exception as exc:
                print(f"signal_card_error={type(exc).__name__}:{exc}", flush=True)
                sent = 0
            if sent == 0:
                sent = broadcast(_message(record, head, probability, model_result, cards), reply_markup=signal_keyboard(match_id, head))

            journal.append({
                "created_at": datetime.now(timezone.utc).isoformat(),
                "match_id": match_id,
                "head": head,
                "minute": minute,
                "home": match.get("home"),
                "away": match.get("away"),
                "league": match.get("league"),
                "score": [home_score, away_score],
                "probability": probability,
                "provider_count": len(record.get("providers") or {}),
                "flashscore_meta": fs_meta,
                "stats_snapshot": stat_snap,
                "result": "pending",
                "in_game": False,
            })
            save_signal_journal(self.journal_path, journal)
            emitted += int(sent > 0)

        # Independent GOOL LIVE layer: any score for +2 goals; BTTS when exactly
        # one side has not scored. It uses recent 5m/10m acceleration through 75'.
        two_more = analyze_two_more_goals(record)
        btts_live = analyze_live_btts(record)
        print(
            f"GOOL_LIVE_CHECK match={match.get('home','?')} - {match.get('away','?')} stage={'HT' if is_halftime else str(minute)} "
            f"two_more={two_more.get('pressure_score')} pass={int(bool(two_more.get('passed')))} "
            f"btts={btts_live.get('pressure_score')} pass={int(bool(btts_live.get('passed')))}",
            flush=True,
        )

        if two_more.get("confidence_score") is not None:
            emitted += self._emit_gool_live_signal(
                record, journal, "two_more_goals", float(two_more["confidence_score"]), two_more, model_result, cards
            )
            journal = load_signal_journal(self.journal_path)

        # At halftime the validated BTTS model keeps priority. Outside halftime,
        # GOOL LIVE is allowed to create the BTTS signal from current pressure.
        if not is_halftime and btts_live.get("confidence_score") is not None:
            emitted += self._emit_gool_live_signal(
                record, journal, "both_teams_to_score", float(btts_live["confidence_score"]), btts_live, model_result, cards
            )

        return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze every GOOL live match through 75 minutes")
    parser.add_argument("--raw-dir", default=os.getenv("RAW_LIVE_DIR", "data/raw/live"))
    parser.add_argument("--journal", default=os.getenv("SIGNAL_JOURNAL", "data/live/gool_bot2_signals.json"))
    parser.add_argument("--analysis", default=os.getenv("SIGNAL_ANALYSIS_PATH", "data/live/gool_bot2_analysis.jsonl"))
    parser.add_argument("--sleep", type=float, default=float(os.getenv("SIGNAL_WORKER_SLEEP", "3")))
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    journal_path = Path(args.journal)
    analysis_path = Path(args.analysis)
    raw_dir.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.parent.mkdir(parents=True, exist_ok=True)

    worker = AllMatchSignalWorker(journal_path, analysis_path=analysis_path)
    send_startup_status()
    telegram_offset = 0
    print(
        f"signal_worker_all=started raw_dir={raw_dir} journal={journal_path} analysis={analysis_path} "
        "max_minute=75 max_signals_per_match=2 gool_live=momentum_5m_10m",
        flush=True,
    )
    while True:
        emitted = worker.run_once(raw_dir)
        telegram_offset, telegram_actions = poll_telegram_updates(journal_path, offset=telegram_offset, timeout=0)
        if emitted or telegram_actions:
            print(f"signal_worker_all emitted={emitted} telegram_actions={telegram_actions}", flush=True)
        time.sleep(max(0.5, args.sleep))


if __name__ == "__main__":
    main()
