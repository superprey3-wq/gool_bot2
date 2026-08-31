from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import append_analysis, load_signal_journal, save_signal_journal
from .match_context import card_context
from .signal_cards import flashscore_meta, render_signal_card, stats_snapshot
from .signal_policy import combine_gates, exposure_gate, market_state_gate, model_threshold_gate, post_goal_gate, time_gate
from .telegram import broadcast, broadcast_photo, poll_telegram_updates, send_startup_status, signal_keyboard
from .signal_worker import (
    HEAD_LABELS,
    SignalWorker,
    _correct_false_losses,
    _last_goal_minute,
    _message,
    _reconciled_score,
    _send_result_cards,
    _settle_pending,
)


class AllMatchSignalWorker(SignalWorker):
    """Run the local models for every Flashscore live snapshot through 75'.

    The legacy prefilter is retained as context and as a selector for expensive
    secondary-provider enrichment in the collector, but it is no longer allowed
    to prevent the local trained models from seeing a live match.
    """

    def _process(self, record: dict[str, Any]) -> int:
        match = record.get("match") or {}
        match_id = str(match.get("flashscore_event_id") or "")
        if not match_id:
            return 0

        journal = load_signal_journal(self.journal_path)
        corrected = _correct_false_losses(record, journal)
        settled = _settle_pending(record, journal)
        if corrected or settled:
            save_signal_journal(self.journal_path, journal)
            if corrected:
                _send_result_cards(corrected)
            if settled:
                _send_result_cards(settled)

        minute = int(match.get("minute") or 0)
        is_halftime = bool(match.get("is_halftime"))
        is_finished = bool(match.get("is_finished"))
        candidate = bool((record.get("prefilter") or {}).get("candidate"))
        base = self._base_analysis(record, match_id)

        # Finished and post-75 snapshots are still consumed for settlement above,
        # but no new betting analysis/signal is started after the requested window.
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

        # Every live match reaches the trained model stack, regardless of prefilter.
        model_result = self.model.predict(record)
        cards = card_context(record)
        emitted = 0
        home_score, away_score = _reconciled_score(record)

        for head in HEAD_LABELS:
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

            # For the two strategies understood by the legacy GOOL analyzer,
            # a trained-model result can be present while GOOL independently vetoes it.
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

            # Hard portfolio rule: at most TWO emitted signals total for one match,
            # irrespective of strategy or whether the user pressed "В игре".
            # Two simultaneous pending signals are allowed; a third is impossible.
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
            analysis = {
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
                "model_disagreement": float(disagreement),
                "cards": cards,
                "decision": "SIGNAL" if allowed else "WAIT",
                "blocks": reasons,
            }
            append_analysis(self.analysis_path, analysis)
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
    print(f"signal_worker_all=started raw_dir={raw_dir} journal={journal_path} analysis={analysis_path} max_minute=75 max_signals_per_match=2", flush=True)
    while True:
        emitted = worker.run_once(raw_dir)
        telegram_offset, telegram_actions = poll_telegram_updates(journal_path, offset=telegram_offset, timeout=0)
        if emitted or telegram_actions:
            print(f"signal_worker_all emitted={emitted} telegram_actions={telegram_actions}", flush=True)
        time.sleep(max(0.5, args.sleep))


if __name__ == "__main__":
    main()
