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


def _minimum_rating() -> float:
    try:
        return max(0.0, min(100.0, float(os.getenv("GOOL_MULTI_MIN_RATING", "70"))))
    except (TypeError, ValueError):
        return 70.0


def _enforce_min_rating(decision: Any) -> Any:
    """Final production gate: never journal/send a Multi BET below the floor."""
    if str(getattr(decision, "status", "")) != "BET" or getattr(decision, "winner", None) is None:
        return decision

    floor = _minimum_rating()
    winner = decision.winner
    if float(getattr(winner, "rating", 0.0) or 0.0) >= floor:
        return decision

    # Keep the historical block token so the existing short report translates it
    # to the familiar "сигнал пока слабый" wording.
    if "router_rating_below_62" not in winner.blocks:
        winner.blocks.append("router_rating_below_62")
    winner.reason_tags.append("production_rating_floor")
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)

    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = f"WAIT: финальный рейтинг ниже {floor:.0f}/100 — реальную ставку не отправляем."
    return decision


def _ensure_any_goal_coverage_proxy(experts: dict[str, Any]) -> None:
    """Create a conservative broad-goal expert when only a team goal passed.

    A passed team-goal signal implies the broader event "either team scores" is
    at least as well covered. Previously the router could see ИТБ2 1.5 but not
    ТБ 2.5 at the same 1:1 score because `another_goal` was absent from the
    calibrated model snapshot. The card still displayed ТБ 2.5 from raw 1xBet
    odds, which made the BEST BET look irrational.

    We reuse the strongest *passed* side signal as a lower-bound proxy. Its
    metric is preserved: heuristic confidence stays confidence (no fake EV),
    while a calibrated side probability remains a conservative probability.
    """
    if experts.get("another_goal"):
        return

    sides: list[tuple[str, dict[str, Any], float]] = []
    for key in ("home_goal", "away_goal"):
        row = dict(experts.get(key) or {})
        if not row or not bool(row.get("passed")):
            continue
        try:
            value = float(row.get("probability"))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= value <= 1.0:
            continue
        sides.append((key, row, value))
    if not sides:
        return

    # Do not compare confidence and probability numerically. Prefer a calibrated
    # side probability when one exists; otherwise use the strongest confidence.
    calibrated = [item for item in sides if str(item[1].get("metric") or "probability").lower() == "probability"]
    pool = calibrated or sides
    key, row, value = max(pool, key=lambda item: item[2])
    metric = str(row.get("metric") or "probability").strip().lower()
    source = str(row.get("source") or key)
    experts["another_goal"] = {
        "probability": value,
        "metric": metric,
        "source": f"coverage_proxy:{key}:{source}",
        "passed": True,
        "blocks": [],
        "coverage_proxy": True,
        "proxy_from": key,
    }


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
    _ensure_any_goal_coverage_proxy(experts)

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
    # `another_goal` and must not win merely because its odds are larger. The
    # final production floor is applied after coverage so a safer replacement
    # market cannot bypass the 70/100 minimum.
    before_key = None if decision.winner is None else decision.winner.key
    decision = enforce_goal_coverage(decision, experts)
    decision = _enforce_min_rating(decision)
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
        # Use the exact bookmaker snapshot that produced the decision so the
        # informational total alternatives on the Telegram card keep real odds
        # from the same score/minute instead of a later refresh.
        emit_multi_signal(record, decision, created, market_row=market)

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
