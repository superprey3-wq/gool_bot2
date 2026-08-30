from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .local_ensemble import LocalFootballEnsemble
from .signal_policy import combine_gates, exposure_gate, model_threshold_gate, post_goal_gate, time_gate
from .telegram import broadcast

HEAD_LABELS = {
    "another_goal": "ЕЩЁ ГОЛ",
    "goal_before_ht": "ГОЛ ДО ПЕРЕРЫВА",
    "two_plus_goals_second_half": "2+ ГОЛА ВО 2 ТАЙМЕ",
}


def _load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _save_journal(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _goal_timeline(record: dict[str, Any]) -> list[dict[str, Any]]:
    providers = record.get("providers") or {}
    return ((providers.get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or []


def _last_goal_minute(record: dict[str, Any]) -> int | None:
    minutes = []
    for goal in _goal_timeline(record):
        try:
            minutes.append(int(float(goal.get("minute"))))
        except (TypeError, ValueError):
            continue
    return max(minutes) if minutes else None


def _second_half_goal_count(record: dict[str, Any]) -> int:
    total = 0
    for goal in _goal_timeline(record):
        try:
            if float(goal.get("minute")) > 45:
                total += 1
        except (TypeError, ValueError):
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


def _message(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any]) -> str:
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
        f"sources={len(record.get('providers') or {})}"
    )


class SignalWorker:
    """Consumes append-only live snapshots and emits only locally-modelled signals."""

    def __init__(self, journal_path: Path, max_disagreement: float = 0.20) -> None:
        self.journal_path = journal_path
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

        journal = _load_journal(self.journal_path)
        if _settle_pending(record, journal):
            _save_journal(self.journal_path, journal)

        if not (record.get("prefilter") or {}).get("candidate"):
            return 0
        if not self._ensure_model():
            return 0

        minute = int(match.get("minute") or 0)
        is_halftime = bool(match.get("is_halftime"))
        model_result = self.model.predict(record)
        emitted = 0

        for head in HEAD_LABELS:
            probability = model_result.get("blended", {}).get(head)
            disagreement = model_result.get("disagreement", {}).get(head)
            if probability is None or disagreement is None:
                continue
            if float(disagreement) > self.max_disagreement:
                continue

            cooldown = int(os.getenv("LIVE_COOLDOWN_MINUTES", "12")) if head == "another_goal" else 5
            gates = combine_gates(
                time_gate(head, minute, is_halftime=is_halftime, is_reentry=False),
                post_goal_gate(minute, _last_goal_minute(record), cooldown_minutes=cooldown),
                exposure_gate(match_id, journal, max_entries=2, max_open=1),
                model_threshold_gate(head, float(probability), float(probability) * 100.0),
            )
            if not gates.allowed:
                continue

            duplicate = any(
                str(row.get("match_id")) == match_id
                and str(row.get("head")) == head
                and str(row.get("result") or "pending").lower() == "pending"
                for row in journal
            )
            if duplicate:
                continue

            message = _message(record, head, float(probability), model_result)
            sent = broadcast(message)
            entry = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "match_id": match_id,
                "head": head,
                "minute": minute,
                "home": match.get("home"),
                "away": match.get("away"),
                "score": [match.get("home_score", 0), match.get("away_score", 0)],
                "probability": float(probability),
                "direct_probability": model_result.get("direct", {}).get(head),
                "hazard_probability": model_result.get("hazard", {}).get(head),
                "model_disagreement": float(disagreement),
                "telegram_deliveries": sent,
                "result": "pending",
            }
            journal.append(entry)
            _save_journal(self.journal_path, journal)
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
    parser.add_argument("--poll", type=int, default=5)
    parser.add_argument("--max-disagreement", type=float, default=float(os.getenv("MODEL_MAX_DISAGREEMENT", "0.20")))
    args = parser.parse_args()

    worker = SignalWorker(Path(args.journal), max_disagreement=args.max_disagreement)
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
