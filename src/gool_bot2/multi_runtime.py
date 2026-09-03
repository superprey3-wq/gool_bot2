from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import signal_worker_all_cards as cards
from .match_context import provider_count, xg_or_proxy_pair
from .multi_experts import build_expert_snapshot
from .multi_goal_coverage import enforce_goal_coverage
from .multi_journal import settle_multi_journal, sync_multi_journal
from .multi_shadow import analyze_and_record, append_shadow_snapshot, decision_snapshot
from .multi_telegram import emit_multi_results, emit_multi_signal
from .shadow_markets import analyze_btts_shadow, side_goal_pressure
from .xbet_market_pressure import load_market_state


def _data_quality(record: dict[str, Any]) -> float:
    providers = min(3, provider_count(record))
    provider_score = providers / 3.0

    try:
        _, _, xg_source, xg_evidence = xg_or_proxy_pair(record)
    except Exception:
        xg_source, xg_evidence = "unavailable", 0
    xg_score = 1.0 if xg_source == "provider_xg" else (0.65 if xg_source == "attack_proxy" and xg_evidence >= 2 else 0.0)

    momentum = record.get("live_momentum") or {}
    momentum_keys = (
        "shots_total_last_5m", "sot_total_last_5m", "xg_total_last_5m",
        "shots_total_last_10m", "sot_total_last_10m", "xg_total_last_10m",
    )
    available = sum(1 for key in momentum_keys if momentum.get(key) is not None)
    momentum_score = available / len(momentum_keys)

    quality = 0.48 * provider_score + 0.27 * xg_score + 0.25 * momentum_score
    return max(0.0, min(1.0, quality))


def _market_row(record: dict[str, Any]) -> dict[str, Any] | None:
    mid = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
    if not mid:
        return None
    return ((load_market_state().get("matches") or {}).get(mid) or None)


def _paths() -> tuple[Path, Path]:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    analysis_raw = os.getenv("GOOL_MULTI_ANALYSIS_PATH", "").strip()
    if not analysis_raw:
        analysis_raw = os.getenv("GOOL_MULTI_SHADOW_PATH", "").strip()
    analysis = Path(analysis_raw) if analysis_raw else runtime / "live" / "gool_multi_analysis.jsonl"
    journal_raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    journal = Path(journal_raw) if journal_raw else runtime / "live" / "gool_multi_journal.json"
    return analysis, journal


def observe_multi_shadow(worker: Any, record: dict[str, Any]) -> None:
    """Feed one production snapshot into GOOL MULTI.

    In shadow mode this only records analysis/journal decisions. When
    GOOL_MULTI_TELEGRAM_MODE=active, the same function emits the single Multi
    BEST BET image and its later settlement image; legacy per-strategy Telegram
    delivery is suppressed by the worker wrapper during cutover.
    """
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    minute = int(match.get("minute") or 0)
    if not mid:
        return

    analysis_path, journal_path = _paths()

    # Settlement must run even on the final row or when model/market data is
    # temporarily unavailable; otherwise a valid Multi entry could stay pending.
    settled = settle_multi_journal(record, journal_path)
    if settled:
        emit_multi_results(record, settled)
    for row in settled:
        print(
            f"GOOL_MULTI_SETTLED match={mid} market={row.get('market')} result={row.get('result')} "
            f"profit={row.get('profit_units')} score={row.get('settled_score')}",
            flush=True,
        )

    if minute <= 0 or bool(match.get("is_finished")):
        return

    model_result = dict(getattr(worker, "_diag_model_result", {}) or {})
    two_more = dict(cards._LAST_TWO_MORE.get(mid) or {})
    home_goal = side_goal_pressure(record, "home")
    away_goal = side_goal_pressure(record, "away")
    btts = analyze_btts_shadow(record)

    experts = build_expert_snapshot(
        model_result=model_result,
        two_more_analysis=two_more,
        home_goal_analysis=home_goal,
        away_goal_analysis=away_goal,
        btts_analysis=btts,
    )
    # A 1st-half market is closed at the interval even though Flashscore reports
    # minute 45. Keep the model in diagnostics before HT, but never allow a
    # halftime 1xBet move to revive a closed first-half bet.
    if bool(match.get("is_halftime")):
        experts.pop("goal_before_ht", None)
    if not experts:
        return

    market = _market_row(record)
    quality = _data_quality(record)
    decision = analyze_and_record(record, market, experts, analysis_path, data_quality=quality)

    # Coverage guard: a confidence-only team +0.5 is a narrower outcome than
    # calibrated `another_goal` and must not win merely because its odds are
    # larger.  If the winner changes, append the corrected decision immediately
    # so the online analysis view also shows the same choice that is journaled.
    before_key = None if decision.winner is None else decision.winner.key
    decision = enforce_goal_coverage(decision, experts)
    after_key = None if decision.winner is None else decision.winner.key
    if before_key != after_key:
        append_shadow_snapshot(
            analysis_path,
            decision_snapshot(record, decision, experts, data_quality=quality, market_row=market),
        )

    _, created = sync_multi_journal(
        record,
        decision,
        experts,
        journal_path,
        data_quality=quality,
    )
    if created is not None:
        emit_multi_signal(record, decision, created)

    winner = None if decision.winner is None else f"{decision.winner.label}@{decision.winner.odd:.2f}"
    source = None if decision.winner is None else (
        "MARKET_OVERRIDE" if decision.winner.market_override and not decision.winner.expert_passed
        else "VALUE_OVERRIDE" if decision.winner.value_override and not decision.winner.expert_passed
        else "GOOL"
    )
    print(
        f"GOOL_MULTI_SHADOW match={mid} minute={minute} score={match.get('home_score',0)}:{match.get('away_score',0)} "
        f"decision={decision.status} best={winner or '-'} source={source or '-'} quality={quality:.2f} "
        f"journal_entry={'yes' if created else 'no'}",
        flush=True,
    )
