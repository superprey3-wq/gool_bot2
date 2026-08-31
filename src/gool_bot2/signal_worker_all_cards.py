from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import signal_worker_all as base
from .gool_live_cards import render_gool_live_result_card, render_gool_live_signal_card
from .journal import append_analysis, save_signal_journal
from .signal_cards import flashscore_meta, stats_snapshot
from .signal_policy import exposure_gate, post_goal_gate
from .telegram import broadcast, broadcast_photo, signal_keyboard


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
            broadcast(
                caption
                + f"\n{row.get('home','?')} — {row.get('away','?')} · "
                + f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
            )


class CardAllMatchSignalWorker(base.AllMatchSignalWorker):
    """Same all-match engine, with photo cards for every GOOL LIVE signal."""

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
        home_score, away_score = base._reconciled_score(record)
        analysis_base = self._base_analysis(record, match_id)
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

        # GOOL LIVE confidence is a heuristic signal-strength score, not a calibrated
        # probability. Only sufficiently strong situations are allowed to Telegram.
        min_strength = float(os.getenv("GOOL_LIVE_MIN_STRENGTH", "0.70"))
        if confidence < min_strength:
            reasons.append(f"gool_strength={confidence:.3f}<{min_strength:.3f}")

        exposure = exposure_gate(match_id, journal, max_entries=2, max_open=2)
        reasons.extend(exposure.reasons)
        cooldown = post_goal_gate(minute, base._last_goal_minute(record), cooldown_minutes=5)
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
            **analysis_base,
            "head": head,
            "probability": None,
            "gool_confidence": confidence,
            "gool_signal_strength": confidence,
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
            sent = broadcast_photo(
                png,
                caption=f"🔥 <b>{label}</b> · GOOL LIVE pressure {pressure:.2f} · сила {confidence*100:.0f}/100",
                reply_markup=signal_keyboard(match_id, head),
            )
        except Exception as exc:
            print(f"gool_live_card_error={type(exc).__name__}:{exc}", flush=True)

        if sent == 0:
            sent = broadcast(
                f"🔥 <b>{label}</b>\n{match.get('home','?')} — {match.get('away','?')}\n"
                f"{minute}' · {home_score}:{away_score}\n"
                f"GOOL LIVE pressure: <b>{pressure:.2f}</b> · сила сигнала {confidence*100:.0f}/100",
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
            "gool_signal_strength": confidence,
            "provider_count": len(record.get("providers") or {}),
            "flashscore_meta": fs_meta,
            "stats_snapshot": stat_snap,
            "result": "pending",
            "in_game": False,
        })
        save_signal_journal(self.journal_path, journal)
        print(
            f"GOOL_LIVE_SIGNAL head={head} match={match_id} minute={minute} "
            f"pressure={pressure:.2f} strength={confidence:.2f} card={int(sent > 0)}",
            flush=True,
        )
        return int(sent > 0)


# The trained systems already use the standard signal/result image cards.
# Patch the two independent GOOL LIVE systems so all active strategies use photos.
base.AllMatchSignalWorker = CardAllMatchSignalWorker
base._send_two_more_results = _send_two_more_results


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
