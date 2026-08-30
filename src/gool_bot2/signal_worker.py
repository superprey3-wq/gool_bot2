from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import append_analysis, entry_rows, load_signal_journal, save_signal_journal
from .local_ensemble import LocalFootballEnsemble
from .match_context import card_context
from .signal_policy import combine_gates, exposure_gate, model_threshold_gate, post_goal_gate, time_gate
from .telegram import broadcast, signal_keyboard

HEAD_LABELS = {
    "another_goal": "ЕЩЁ ГОЛ",
    "goal_before_ht": "ГОЛ ДО ПЕРЕРЫВА",
    "over_2_5": "ТОТАЛ БОЛЬШЕ 2.5",
    "both_teams_to_score": "ОБЕ ЗАБЬЮТ",
}


def _goal_timeline(record: dict[str, Any]) -> list[dict[str, Any]]:
    providers = record.get("providers") or {}
    return ((providers.get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or []


def _last_goal_minute(record: dict[str, Any]) -> int | None:
    minutes = []
    for goal in _goal_timeline(record):
        try:
            minutes.append(int(float(goal.get("minute"))))
        except (TypeError, ValueError, AttributeError):
            continue
    return max(minutes) if minutes else None


def _settle_pending(record: dict[str, Any], journal: list[dict[str, Any]]) -> bool:
    """Update pending outcomes using only the current/later live snapshot."""
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return False
    minute = int(match.get("minute") or 0)
    is_halftime = bool(match.get("is_halftime"))
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    current_total = home_score + away_score
    changed = False

    for row in journal:
        if str(row.get("match_id")) != match_id or str(row.get("result") or "pending").lower() != "pending":
            continue
        signal_score = row.get("score") or [0, 0]
        signal_total = int(signal_score[0] or 0) + int(signal_score[1] or 0)
        head = str(row.get("head") or "")
        result: str | None = None

        if head == "another_goal":
            if current_total > signal_total:
                result = "won"
            elif minute >= 90:
                result = "lost"
        elif head == "goal_before_ht":
            if current_total > signal_total and minute <= 45:
                result = "won"
            elif is_halftime or minute > 45:
                result = "lost"
        elif head == "over_2_5":
            if current_total >= 3:
                result = "won"
            elif minute >= 90:
                result = "lost"
        elif head == "both_teams_to_score":
            if home_score > 0 and away_score > 0:
                result = "won"
            elif minute >= 90:
                result = "lost"

        if result:
            row["result"] = result
            row["settled_at"] = datetime.now(timezone.utc).isoformat()
            row["settled_minute"] = minute
            row["settled_score"] = [home_score, away_score]
            changed = True
    return changed


def _fmt_cards(cards: dict[str, Any]) -> str:
    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    yellow = "? : ?" if hy is None or ay is None else f"{hy}:{ay}"
    red = "? : ?" if hr is None or ar is None else f"{hr}:{ar}"
    return f"🟨 {yellow} | 🟥 {red}"


def _message(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> str:
    match = record.get("match") or {}
    disagreement = model_result.get("disagreement", {}).get(head)

    if head in {"over_2_5", "both_teams_to_score"}:
        model_line = f"HT-model={probability * 100:.1f}%"
    else:
        direct = model_result.get("direct", {}).get(head)
        hazard = model_result.get("hazard", {}).get(head)
        if direct is None or hazard is None:
            model_line = "model=available"
        else:
            model_line = (
                f"direct={float(direct) * 100:.1f}% | "
                f"hazard={float(hazard) * 100:.1f}% | "
                f"Δ={float(disagreement or 0.0) * 100:.1f}%"
            )

    first_half_line = ""
    if head == "goal_before_ht":
        fh = model_result.get("first_half_analysis") or {}
        pressure = fh.get("pressure_score")
        adjustment = fh.get("probability_adjustment")
        auc = fh.get("baseline_auc")
        if pressure is not None and adjustment is not None:
            first_half_line = (
                f"\nGOOL 1T: pressure={float(pressure):.2f} | "
                f"corr={float(adjustment) * 100:+.1f} п.п."
            )
            if auc is not None:
                first_half_line += f" | base AUC={float(auc):.4f}"

    return (
        f"⚽ <b>{HEAD_LABELS[head]}</b>\n"
        f"{match.get('home', '?')} — {match.get('away', '?')}\n"
        f"{int(match.get('minute') or 0)}' | {match.get('home_score', 0)}:{match.get('away_score', 0)}\n"
        f"P: <b>{probability * 100:.1f}%</b>\n"
        f"{model_line}{first_half_line}\n"
        f"{_fmt_cards(cards)}\n"
        f"sources={len(record.get('providers') or {})}"
    )


class SignalWorker:
    """Consumes live snapshots, audits decisions and emits locally-modelled signals."""

    def __init__(
        self,
        journal_path: Path,
        max_disagreement: float = 0.20,
        analysis_path: Path | None = None,
    ) -> None:
        self.journal_path = journal_path
        self.analysis_path = analysis_path or journal_path.with_name("gool_bot2_analysis.jsonl")
        self.max_disagreement = float(max_disagreement)
        self.model: LocalFootballEnsemble | None = None
        self._offsets: dict[str, int] = {}

    def _ensure_model(self) -> bool:
        if self.model is not None:
            return True
        try:
            self.model = LocalFootballEnsemble()
            print("local_model=loaded", flush=True)
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            print(f"local_model_error={type(exc).__name__}:{exc}", flush=True)
            return False

    def _process(self, record: dict[str, Any]) -> int:
        match = record.get("match") or {}
        match_id = str(match.get("flashscore_event_id") or "")
        if not match_id:
            return 0

        journal = load_signal_journal(self.journal_path)
        if _settle_pending(record, journal):
            save_signal_journal(self.journal_path, journal)

        if not (record.get("prefilter") or {}).get("candidate"):
            return 0
        if not self._ensure_model():
            return 0

        minute = int(match.get("minute") or 0)
        is_halftime = bool(match.get("is_halftime"))
        model_result = self.model.predict(record)
        cards = card_context(record)
        emitted = 0
        entered = entry_rows(journal)

        for head in HEAD_LABELS:
            probability = model_result.get("blended", {}).get(head)
            disagreement = model_result.get("disagreement", {}).get(head)
            if probability is None or disagreement is None:
                continue

            if head == "another_goal":
                cooldown = int(os.getenv("LIVE_COOLDOWN_MINUTES", "12"))
            elif head in {"over_2_5", "both_teams_to_score"}:
                cooldown = 0
            else:
                cooldown = 5

            gates = combine_gates(
                time_gate(head, minute, is_halftime=is_halftime, is_reentry=False),
                post_goal_gate(minute, _last_goal_minute(record), cooldown_minutes=cooldown),
                exposure_gate(match_id, entered, max_entries=2, max_open=1),
                model_threshold_gate(head, float(probability), float(probability) * 100.0),
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
            football_data_probability = model_result.get("football_data", {}).get(head)
            first_half_analysis = model_result.get("first_half_analysis") if head == "goal_before_ht" else None
            analysis = {
                "captured_at": record.get("captured_at") or datetime.now(timezone.utc).isoformat(),
                "match_id": match_id,
                "home": match.get("home"),
                "away": match.get("away"),
                "league": match.get("league"),
                "minute": minute,
                "score": [match.get("home_score", 0), match.get("away_score", 0)],
                "head": head,
                "probability": float(probability),
                "direct_probability": model_result.get("direct", {}).get(head),
                "hazard_probability": model_result.get("hazard", {}).get(head),
                "football_data_probability": football_data_probability,
                "first_half_analysis": first_half_analysis,
                "model_disagreement": float(disagreement),
                "cards": cards,
                "prefilter": record.get("prefilter") or {},
                "provider_count": len(record.get("providers") or {}),
                "decision": "SIGNAL" if allowed else "WAIT",
                "blocks": reasons,
            }
            append_analysis(self.analysis_path, analysis)
            if not allowed:
                continue

            message = _message(record, head, float(probability), model_result, cards)
            sent = broadcast(message, reply_markup=signal_keyboard(match_id, head))
            entry = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "match_id": match_id,
                "head": head,
                "minute": minute,
                "home": match.get("home"),
                "away": match.get("away"),
                "league": match.get("league"),
                "score": [match.get("home_score", 0), match.get("away_score", 0)],
                "probability": float(probability),
                "direct_probability": model_result.get("direct", {}).get(head),
                "hazard_probability": model_result.get("hazard", {}).get(head),
                "football_data_probability": football_data_probability,
                "first_half_analysis": first_half_analysis,
                "model_disagreement": float(disagreement),
                "cards": cards,
                "prefilter": record.get("prefilter") or {},
                "provider_count": len(record.get("providers") or {}),
                "telegram_deliveries": sent,
                "in_game": False,
                "result": "pending",
            }
            journal.append(entry)
            save_signal_journal(self.journal_path, journal)
            emitted += 1

        return emitted

    def run_once(self, inbox_dir: Path) -> int:
        total = 0
        for path in sorted(inbox_dir.glob("*.jsonl")):
            key = str(path.resolve())
            offset = self._offsets.get(key, 0)
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    self._offsets[key] = handle.tell()
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total += self._process(record)
        return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GOOL local signal worker")
    runtime_data_dir = os.getenv("RUNTIME_DATA_DIR", "data")
    parser.add_argument("--inbox", default=os.getenv("GOOL_INBOX_DIR", runtime_data_dir + "/raw/live"))
    parser.add_argument("--journal", default=os.getenv("SIGNAL_JOURNAL_PATH", runtime_data_dir + "/live/signal_journal.json"))
    parser.add_argument("--analysis", default=os.getenv("SIGNAL_ANALYSIS_PATH", runtime_data_dir + "/live/gool_bot2_analysis.jsonl"))
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    worker = SignalWorker(Path(args.journal), analysis_path=Path(args.analysis))
    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    while True:
        emitted = worker.run_once(inbox)
        if emitted:
            print(f"signals={emitted}", flush=True)
        if args.once:
            break
        time.sleep(max(0.5, args.sleep))


if __name__ == "__main__":
    main()
