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
    "two_plus_goals_second_half": "2+ ГОЛА ВО 2 ТАЙМЕ",
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


def _second_half_goal_count(record: dict[str, Any]) -> int:
    total = 0
    for goal in _goal_timeline(record):
        try:
            if float(goal.get("minute")) > 45:
                total += 1
        except (TypeError, ValueError, AttributeError):
            continue
    return total


def _settle_pending(record: dict[str, Any], journal: list[dict[str, Any]]) -> bool:
    """Update pending outcomes using only the current/later live snapshot."""
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return False
    minute = int(match.get("minute") or 0)
    is_halftime = bool(match.get("is_halftime"))
    current_total = int(match.get("home_score") or 0) + int(match.get("away_score") or 0)
    second_half_goals = _second_half_goal_count(record)
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
        elif head == "two_plus_goals_second_half":
            if second_half_goals >= 2:
                result = "won"
            elif minute >= 90:
                result = "lost"

        if result:
            row["result"] = result
            row["settled_at"] = datetime.now(timezone.utc).isoformat()
            row["settled_minute"] = minute
            row["settled_score"] = [match.get("home_score", 0), match.get("away_score", 0)]
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
    direct = model_result.get("direct", {}).get(head)
    hazard = model_result.get("hazard", {}).get(head)
    disagreement = model_result.get("disagreement", {}).get(head)
    return (
        f"⚽ <b>{HEAD_LABELS[head]}</b>\n"
        f"{match.get('home', '?')} — {match.get('away', '?')}\n"
        f"{int(match.get('minute') or 0)}' | {match.get('home_score', 0)}:{match.get('away_score', 0)}\n"
        f"P: <b>{probability * 100:.1f}%</b>\n"
        f"direct={float(direct) * 100:.1f}% | hazard={float(hazard) * 100:.1f}% | Δ={float(disagreement) * 100:.1f}%\n"
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

            cooldown = int(os.getenv("LIVE_COOLDOWN_MINUTES", "12")) if head == "another_goal" else 5
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
            print(json.dumps({"signal": entry}, ensure_ascii=False), flush=True)
            emitted += 1
        return emitted

    def process_file(self, path: Path) -> int:
        key = str(path.resolve())
        offset = self._offsets.get(key, 0)
        if not path.exists():
            return 0
        emitted = 0
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                try:
                    emitted += self._process(json.loads(line))
                except Exception as exc:
                    print(f"signal_worker_record_error={type(exc).__name__}:{exc}", flush=True)
            self._offsets[key] = handle.tell()
        return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description="GOOL local-model signal worker")
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser.add_argument("--live-dir", default=str(runtime / "raw/live"))
    parser.add_argument("--journal", default=os.getenv("SIGNAL_JOURNAL_FILE", str(runtime / "gool_bot2_signal_journal.json")))
    parser.add_argument("--analysis-journal", default=os.getenv("ANALYSIS_JOURNAL_FILE", str(runtime / "gool_bot2_analysis.jsonl")))
    parser.add_argument("--poll", type=int, default=5)
    parser.add_argument("--max-disagreement", type=float, default=float(os.getenv("MODEL_MAX_DISAGREEMENT", "0.20")))
    args = parser.parse_args()

    worker = SignalWorker(Path(args.journal), max_disagreement=args.max_disagreement, analysis_path=Path(args.analysis_journal))
    live_dir = Path(args.live_dir)
    while True:
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            worker.process_file(live_dir / f"{today}.jsonl")
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"signal_worker_error={type(exc).__name__}:{exc}", flush=True)
        time.sleep(max(1, args.poll))


if __name__ == "__main__":
    main()
