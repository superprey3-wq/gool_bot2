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
from .signal_cards import render_result_card, render_signal_card
from .signal_policy import combine_gates, exposure_gate, market_state_gate, model_threshold_gate, post_goal_gate, time_gate
from .telegram import broadcast, broadcast_photo, poll_telegram_updates, send_startup_status, signal_keyboard

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
    minutes: list[int] = []
    for goal in _goal_timeline(record):
        try:
            minutes.append(int(float(goal.get("minute"))))
        except (TypeError, ValueError, AttributeError):
            continue
    return max(minutes) if minutes else None


def _settle_pending(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return []
    minute = int(match.get("minute") or 0)
    is_halftime = bool(match.get("is_halftime"))
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    current_total = home_score + away_score
    settled: list[dict[str, Any]] = []
    for row in journal:
        if str(row.get("match_id")) != match_id or str(row.get("result") or "pending").lower() != "pending":
            continue
        signal_score = row.get("score") or [0, 0]
        signal_total = int(signal_score[0] or 0) + int(signal_score[1] or 0)
        head = str(row.get("head") or "")
        result: str | None = None
        if head == "another_goal":
            result = "won" if current_total > signal_total else ("lost" if minute >= 90 else None)
        elif head == "goal_before_ht":
            result = "won" if current_total > signal_total and minute <= 45 else ("lost" if is_halftime or minute > 45 else None)
        elif head == "over_2_5":
            result = "won" if current_total >= 3 else ("lost" if minute >= 90 else None)
        elif head == "both_teams_to_score":
            result = "won" if home_score > 0 and away_score > 0 else ("lost" if minute >= 90 else None)
        if result:
            row.update({
                "result": result,
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "settled_minute": minute,
                "settled_score": [home_score, away_score],
            })
            settled.append(dict(row))
    return settled


def _send_result_cards(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        result = str(row.get("result") or "lost")
        settled_score = row.get("settled_score") or [0, 0]
        minute = int(row.get("settled_minute") or 0)
        home_score = int(settled_score[0] or 0)
        away_score = int(settled_score[1] or 0)
        try:
            png = render_result_card(row, result, minute, home_score, away_score)
            caption = (
                "✅ <b>ЗАШЁЛ</b>" if result == "won" else "❌ <b>НЕ ЗАШЁЛ</b>"
            ) + f" · {HEAD_LABELS.get(str(row.get('head')), str(row.get('head')))}"
            sent = broadcast_photo(png, caption=caption)
            print(f"result_card={result} deliveries={sent} match={row.get('match_id')}", flush=True)
        except Exception as exc:
            print(f"result_card_error={type(exc).__name__}:{exc}", flush=True)


def _fmt_cards(cards: dict[str, Any]) -> str:
    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    return f"🟨 {'? : ?' if hy is None or ay is None else f'{hy}:{ay}'} | 🟥 {'? : ?' if hr is None or ar is None else f'{hr}:{ar}'}"


def _message(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> str:
    match = record.get("match") or {}
    disagreement = model_result.get("disagreement", {}).get(head)
    if head in {"over_2_5", "both_teams_to_score"}:
        model_line = f"HT-model={probability * 100:.1f}%"
    else:
        direct = model_result.get("direct", {}).get(head)
        hazard = model_result.get("hazard", {}).get(head)
        model_line = "model=available" if direct is None or hazard is None else f"direct={float(direct)*100:.1f}% | hazard={float(hazard)*100:.1f}% | Δ={float(disagreement or 0)*100:.1f}%"
    first_half_line = ""
    if head == "goal_before_ht":
        fh = model_result.get("first_half_analysis") or {}
        pressure, adjustment, auc = fh.get("pressure_score"), fh.get("probability_adjustment"), fh.get("baseline_auc")
        if pressure is not None and adjustment is not None:
            first_half_line = f"\nGOOL 1T: pressure={float(pressure):.2f} | corr={float(adjustment)*100:+.1f} п.п." + (f" | base AUC={float(auc):.4f}" if auc is not None else "")
    return (
        f"⚽ <b>{HEAD_LABELS[head]}</b>\n{match.get('home','?')} — {match.get('away','?')}\n"
        f"{int(match.get('minute') or 0)}' | {match.get('home_score',0)}:{match.get('away_score',0)}\n"
        f"P: <b>{probability*100:.1f}%</b>\n{model_line}{first_half_line}\n{_fmt_cards(cards)}\n"
        f"sources={len(record.get('providers') or {})}"
    )


class SignalWorker:
    def __init__(self, journal_path: Path, max_disagreement: float = 0.20, analysis_path: Path | None = None) -> None:
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
            print("local_model=missing", flush=True)
            return False
        except Exception as exc:
            print(f"local_model_error={type(exc).__name__}:{exc}", flush=True)
            return False

    def _base_analysis(self, record: dict[str, Any], match_id: str) -> dict[str, Any]:
        match = record.get("match") or {}
        return {
            "captured_at": record.get("captured_at") or datetime.now(timezone.utc).isoformat(),
            "match_id": match_id,
            "home": match.get("home"),
            "away": match.get("away"),
            "league": match.get("league"),
            "minute": int(match.get("minute") or 0),
            "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
            "prefilter": record.get("prefilter") or {},
            "provider_count": len(record.get("providers") or {}),
        }

    def _process(self, record: dict[str, Any]) -> int:
        match = record.get("match") or {}
        match_id = str(match.get("flashscore_event_id") or "")
        if not match_id:
            return 0

        journal = load_signal_journal(self.journal_path)
        settled = _settle_pending(record, journal)
        if settled:
            save_signal_journal(self.journal_path, journal)
            _send_result_cards(settled)

        minute = int(match.get("minute") or 0)
        is_halftime = bool(match.get("is_halftime"))
        candidate = bool((record.get("prefilter") or {}).get("candidate"))
        base = self._base_analysis(record, match_id)

        append_analysis(self.analysis_path, {
            **base,
            "head": "prefilter",
            "probability": None,
            "model_disagreement": None,
            "decision": "PASS" if (candidate or is_halftime) else "WAIT",
            "blocks": [] if (candidate or is_halftime) else ["prefilter_rejected"],
        })
        if not candidate and not is_halftime:
            return 0

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
        entered = entry_rows(journal)
        home_score = int(match.get("home_score") or 0)
        away_score = int(match.get("away_score") or 0)

        for head in HEAD_LABELS:
            if not candidate and head not in {"over_2_5", "both_teams_to_score"}:
                continue
            probability = model_result.get("blended", {}).get(head)
            disagreement = model_result.get("disagreement", {}).get(head)
            if probability is None or disagreement is None:
                append_analysis(self.analysis_path, {
                    **base,
                    "head": head,
                    "probability": probability,
                    "model_disagreement": disagreement,
                    "decision": "WAIT",
                    "blocks": ["model_output_missing"],
                })
                continue

            cooldown = int(os.getenv("LIVE_COOLDOWN_MINUTES", "12")) if head == "another_goal" else (0 if head in {"over_2_5", "both_teams_to_score"} else 5)
            gates = combine_gates(
                time_gate(head, minute, is_halftime=is_halftime, is_reentry=False),
                market_state_gate(head, home_score, away_score),
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
                **base,
                "head": head,
                "probability": float(probability),
                "direct_probability": model_result.get("direct", {}).get(head),
                "hazard_probability": model_result.get("hazard", {}).get(head),
                "football_data_probability": football_data_probability,
                "first_half_analysis": first_half_analysis,
                "model_disagreement": float(disagreement),
                "cards": cards,
                "decision": "SIGNAL" if allowed else "WAIT",
                "blocks": reasons,
            }
            append_analysis(self.analysis_path, analysis)
            if not allowed:
                continue

            try:
                png = render_signal_card(record, head, float(probability), model_result, cards)
                sent = broadcast_photo(
                    png,
                    caption=f"🎯 <b>{HEAD_LABELS[head]}</b> · P {float(probability)*100:.1f}%",
                    reply_markup=signal_keyboard(match_id, head),
                )
            except Exception as exc:
                print(f"signal_card_error={type(exc).__name__}:{exc}", flush=True)
                sent = 0
            if sent == 0:
                sent = broadcast(
                    _message(record, head, float(probability), model_result, cards),
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
            })
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
    telegram_offset = 0
    startup_sent = send_startup_status()
    print(f"telegram_startup_deliveries={startup_sent}", flush=True)
    print(f"analysis_path={Path(args.analysis)} inbox={inbox}", flush=True)

    while True:
        telegram_offset, handled = poll_telegram_updates(Path(args.journal), offset=telegram_offset, timeout=0)
        if handled:
            print(f"telegram_updates={handled}", flush=True)
        emitted = worker.run_once(inbox)
        if emitted:
            print(f"signals={emitted}", flush=True)
        if args.once:
            break
        time.sleep(max(0.5, args.sleep))


if __name__ == "__main__":
    main()
