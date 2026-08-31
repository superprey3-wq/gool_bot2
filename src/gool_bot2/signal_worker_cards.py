from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import signal_worker_all as swa
from .gool_live_cards import render_gool_live_result_card, render_gool_live_signal_card
from .signal_cards import flashscore_meta, stats_snapshot
from .signal_policy import exposure_gate, post_goal_gate
from .telegram import broadcast, broadcast_photo, signal_keyboard


def _patched_emit_gool_live_signal(
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
    home_score, away_score = swa._reconciled_score(record)
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

    reasons.extend(exposure_gate(match_id, journal, max_entries=2, max_open=2).reasons)
    reasons.extend(post_goal_gate(minute, swa._last_goal_minute(record), cooldown_minutes=5).reasons)
    duplicate = any(
        str(row.get("match_id")) == match_id
        and str(row.get("head")) == head
        and str(row.get("result") or "pending").lower() == "pending"
        for row in journal
    )
    if duplicate:
        reasons.append("duplicate_pending_signal")

    allowed = not reasons
    swa.append_analysis(self.analysis_path, {
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
    label = swa.HEAD_LABELS[head]
    fs_meta = flashscore_meta(record)
    stat_snap = stats_snapshot(record)

    try:
        png = render_gool_live_signal_card(record, head, confidence, pressure, cards)
        sent = broadcast_photo(
            png,
            caption=f"🔥 <b>{label}</b> · GOOL LIVE pressure {pressure:.2f}",
            reply_markup=signal_keyboard(match_id, head),
        )
    except Exception as exc:
        print(f"gool_live_card_error={type(exc).__name__}:{exc}", flush=True)
        sent = 0

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
    swa.save_signal_journal(self.journal_path, journal)
    print(f"GOOL_LIVE_SIGNAL head={head} match={match_id} minute={minute} pressure={pressure:.2f} card={int(sent>0)}", flush=True)
    return int(sent > 0)


def _patched_two_more_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        result = str(row.get("result") or "lost")
        label = swa.HEAD_LABELS.get(str(row.get("head") or "two_more_goals"), "ЕЩЁ +2 ГОЛА")
        try:
            png = render_gool_live_result_card(row, result)
            sent = broadcast_photo(
                png,
                caption=("✅ <b>ЗАШЁЛ</b>" if result == "won" else "❌ <b>НЕ ЗАШЁЛ</b>") + f" · {label}",
            )
        except Exception as exc:
            print(f"gool_live_result_card_error={type(exc).__name__}:{exc}", flush=True)
            sent = 0
        if sent == 0:
            score = row.get("settled_score") or [0, 0]
            broadcast(
                ("✅ <b>ЗАШЁЛ</b>" if result == "won" else "❌ <b>НЕ ЗАШЁЛ</b>")
                + f" · {label}\n{row.get('home','?')} — {row.get('away','?')} · "
                f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
            )


# The four trained systems already use signal_cards.render_signal_card and
# signal_cards.render_result_card. Patch the two independent GOOL LIVE systems
# so ALL active strategies now have image cards and text only as a fallback.
swa.AllMatchSignalWorker._emit_gool_live_signal = _patched_emit_gool_live_signal
swa._send_two_more_results = _patched_two_more_results


def main() -> None:
    swa.main()


if __name__ == "__main__":
    main()
