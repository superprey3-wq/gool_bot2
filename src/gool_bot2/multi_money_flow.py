from __future__ import annotations

import html
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import telegram
from .journal import load_signal_journal, save_signal_journal
from .multi_journal import settle_entry
from .signal_cards import flashscore_meta, stats_snapshot


FINAL_RESULTS = {"won", "lost", "push", "void"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def money_flow_journal_path() -> Path:
    raw = os.getenv("GOOL_MONEY_FLOW_JOURNAL_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_money_flow_journal.json"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _last_goal_minute(record: dict[str, Any]) -> int | None:
    timeline = (
        ((((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or [])
    )
    minutes: list[int] = []
    for row in timeline:
        try:
            minutes.append(int(float(row.get("minute"))))
        except (TypeError, ValueError, AttributeError):
            continue
    return max(minutes) if minutes else None


def _active_system(record: dict[str, Any]) -> tuple[str, str, str] | None:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    if 1 <= minute <= 30:
        return "goal_before_ht", "1H", "first_half_total"
    if 46 <= minute <= 75:
        return "another_goal", "FT", "match_total"
    return None


def _threshold(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


def evaluate_money_flow(record: dict[str, Any]) -> dict[str, Any]:
    active = _active_system(record)
    if active is None:
        return {"eligible": False, "reason": "outside_money_flow_window"}
    strategy, period, family = active
    exchange = record.get("matchbook_exchange") or {}
    context = ((exchange.get("systems") or {}).get(strategy) or {})
    if not bool(exchange.get("available")) or not bool(context.get("available")):
        return {"eligible": False, "reason": "matchbook_market_unavailable"}
    if not bool(context.get("liquid")):
        return {"eligible": False, "reason": "matchbook_low_liquidity"}
    status = str(context.get("market_status") or "").strip().lower()
    if status and status not in {"open", "active"}:
        return {"eligible": False, "reason": f"matchbook_market_{status}"}

    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    last_goal = _last_goal_minute(record)
    reset_minutes = int(_threshold("MATCHBOOK_FLOW_POST_GOAL_RESET_MINUTES", 3))
    if last_goal is not None and minute - last_goal < reset_minutes:
        return {"eligible": False, "reason": "post_goal_exchange_reset", "last_goal_minute": last_goal}

    flow = context.get("flow") or {}
    market_volume = _number(context.get("volume"))
    fair_over = _number(context.get("fair_over"), -1.0)
    over = context.get("over") or {}
    best_back = _number((over.get("best_back") or {}).get("odds"))
    best_lay = _number((over.get("best_lay") or {}).get("odds"))
    if fair_over <= 0.0 or best_back < _threshold("MATCHBOOK_FLOW_BET_MIN_ODD", 1.50):
        return {"eligible": False, "reason": "money_flow_price_not_tradeable", "odd": best_back}
    max_spread = _threshold("MATCHBOOK_FLOW_BET_MAX_SPREAD", 0.18)
    if best_lay > 0.0 and best_lay - best_back > max_spread:
        return {"eligible": False, "reason": "money_flow_spread_too_wide"}

    min_market_volume = _threshold("MATCHBOOK_FLOW_BET_MIN_MARKET_VOLUME", 500.0)
    min_relative = _threshold("MATCHBOOK_FLOW_BET_MIN_RELATIVE_PCT", 8.0)
    min_pp = _threshold("MATCHBOOK_FLOW_BET_MIN_FAIR_PP", 2.0)
    windows = (
        ("30s", _threshold("MATCHBOOK_FLOW_BET_MIN_DELTA_30S", 250.0)),
        ("60s", _threshold("MATCHBOOK_FLOW_BET_MIN_DELTA_60S", 500.0)),
    )

    chosen: dict[str, Any] | None = None
    for label, min_delta in windows:
        if not bool(flow.get(f"window_ready_{label}")):
            continue
        delta = _number(flow.get(f"volume_delta_{label}"))
        delta_pp = _number(flow.get(f"fair_over_delta_pp_{label}"))
        prior_volume = max(1.0, market_volume - delta)
        relative_pct = delta / prior_volume * 100.0
        if (
            market_volume >= min_market_volume
            and delta >= min_delta
            and relative_pct >= min_relative
            and delta_pp >= min_pp
        ):
            chosen = {
                "window": label,
                "volume_delta": delta,
                "relative_pct": relative_pct,
                "fair_delta_pp": delta_pp,
            }
            break

    if chosen is None:
        return {
            "eligible": False,
            "reason": "money_flow_threshold_not_reached",
            "market_volume": market_volume,
            "flow": flow,
        }

    extreme_delta = _threshold("MATCHBOOK_FLOW_BET_EXTREME_DELTA", 750.0)
    extreme_pp = _threshold("MATCHBOOK_FLOW_BET_EXTREME_FAIR_PP", 3.5)
    level = (
        "EXTREME_FLOW"
        if chosen["volume_delta"] >= extreme_delta and chosen["fair_delta_pp"] >= extreme_pp
        else "HEAVY_FLOW"
    )
    score = min(
        99.0,
        76.0
        + min(10.0, chosen["fair_delta_pp"] * 1.8)
        + min(8.0, chosen["relative_pct"] * 0.35)
        + min(5.0, chosen["volume_delta"] / 250.0),
    )
    if level == "EXTREME_FLOW":
        score = max(score, 90.0)

    line = _number(context.get("line"))
    previous_fair = max(0.0, fair_over - chosen["fair_delta_pp"] / 100.0)
    return {
        "eligible": True,
        "level": level,
        "score": round(score, 1),
        "strategy": "money_flow",
        "reference_strategy": strategy,
        "period": period,
        "market_family": family,
        "line": line,
        "odd": best_back,
        "lay_odd": best_lay,
        "fair_over": fair_over,
        "previous_fair_over": previous_fair,
        "market_volume": market_volume,
        "market_id": context.get("market_id"),
        "market_name": context.get("market_name"),
        "matchbook_event_id": ((exchange.get("event") or {}).get("id")),
        **chosen,
    }


def _entry_key(record: dict[str, Any], info: dict[str, Any]) -> str:
    match = record.get("match") or {}
    score = [int(match.get("home_score") or 0), int(match.get("away_score") or 0)]
    return (
        f"money_flow:{match.get('flashscore_event_id')}:{score[0]}-{score[1]}:"
        f"{info.get('period')}:{float(info.get('line') or 0):g}"
    )


def _entry(record: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    hs, aws = int(match.get("home_score") or 0), int(match.get("away_score") or 0)
    line = float(info.get("line") or 0.0)
    odd = float(info.get("odd") or 0.0)
    fair = float(info.get("fair_over") or 0.0)
    period_label = "1Т " if str(info.get("period")) == "1H" else ""
    market = f"{period_label}ТБ{line:g}"
    return {
        "created_at": _now(),
        "mode": "active",
        "head": "money_flow",
        "match_id": str(match.get("flashscore_event_id") or ""),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": int(match.get("minute") or 0),
        "score": [hs, aws],
        "entry_key": _entry_key(record, info),
        "market_key": f"{info.get('market_family')}:{line:g}",
        "market_family": info.get("market_family"),
        "market": market,
        "strategy": "money_flow",
        "odd": round(odd, 4),
        "probability": round(fair, 6),
        "push_probability": 0.0,
        "market_probability": round(fair, 6),
        "rating": float(info.get("score") or 0.0),
        "expected_roi": round(fair * odd - 1.0, 6),
        "value_edge_pp": round((fair - (1.0 / odd if odd > 1.0 else 0.0)) * 100.0, 3),
        "market_pressure_pp": round(float(info.get("fair_delta_pp") or 0.0), 3),
        "market_level": info.get("level"),
        "signal_source": "MATCHBOOK_FLOW",
        "source": "matchbook:money_flow",
        "data_quality": None,
        "reason": "Аномальный проторгованный объём Matchbook подтверждён движением fair probability в сторону следующего гола.",
        "reason_tags": [
            "matchbook_money_flow",
            f"flow_window:{info.get('window')}",
            f"flow_level:{info.get('level')}",
        ],
        "expert_passed": False,
        "expert_blocks": [],
        "experts": {},
        "alternatives": [],
        "flashscore_meta": flashscore_meta(record),
        "stats_snapshot": stats_snapshot(record),
        "cards": dict(record.get("cards") or {}),
        "matchbook_flow": dict(info),
        "result": "pending",
        "profit_units": None,
    }


def _signal_text(row: dict[str, Any]) -> str:
    flow = row.get("matchbook_flow") or {}
    icon = "🚨" if str(flow.get("level")) == "EXTREME_FLOW" else "🔥"
    line = float(flow.get("line") or 0.0)
    prior = float(flow.get("previous_fair_over") or 0.0) * 100.0
    current = float(flow.get("fair_over") or 0.0) * 100.0
    return (
        f"💸 <b>GOOL · MONEY FLOW</b> {icon}\n"
        f"{html.escape(str(row.get('home') or '?'))} — {html.escape(str(row.get('away') or '?'))}\n"
        f"{int(row.get('minute') or 0)}' · {int((row.get('score') or [0, 0])[0])}:{int((row.get('score') or [0, 0])[1])}\n"
        f"🎯 <b>{html.escape(str(row.get('market') or f'ТБ{line:g}'))} @ {float(row.get('odd') or 0):.2f}</b>\n"
        f"💷 проторговано за {flow.get('window')}: <b>+£{float(flow.get('volume_delta') or 0):,.0f}</b> "
        f"({float(flow.get('relative_pct') or 0):+.1f}%)\n"
        f"📈 fair Over: {prior:.1f}% → <b>{current:.1f}%</b> "
        f"({float(flow.get('fair_delta_pp') or 0):+.1f} п.п.)\n"
        f"📚 объём рынка: £{float(flow.get('market_volume') or 0):,.0f} · "
        f"LAY {float(flow.get('lay_odd') or 0):.2f}\n"
        f"{icon} <b>{flow.get('level')}</b> · flow score {float(row.get('rating') or 0):.0f}/100"
    )


def _result_text(row: dict[str, Any]) -> str:
    result = str(row.get("result") or "void").lower()
    if result == "won":
        icon, label = "✅", "ЗАШЁЛ"
    elif result == "lost":
        icon, label = "❌", "НЕ ЗАШЁЛ"
    else:
        icon, label = "↩️", "VOID"
    score = list(row.get("settled_score") or [0, 0])
    return (
        f"{icon} <b>{label} · MONEY FLOW</b>\n"
        f"{html.escape(str(row.get('home') or '?'))} — {html.escape(str(row.get('away') or '?'))}\n"
        f"{html.escape(str(row.get('market') or '?'))} @ {float(row.get('odd') or 0):.2f}\n"
        f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
    )


def _settle_and_notify(record: dict[str, Any], path: Path) -> None:
    rows = load_signal_journal(path)
    changed = False
    match_id = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
    for row in rows:
        if str(row.get("match_id") or "") != match_id:
            continue
        if str(row.get("result") or "pending").lower() == "pending" and settle_entry(row, record):
            changed = True
        if (
            str(row.get("result") or "").lower() in FINAL_RESULTS
            and bool(row.get("telegram_sent"))
            and not bool(row.get("result_telegram_sent"))
        ):
            sent = telegram.broadcast(_result_text(row))
            if sent > 0:
                row["result_telegram_sent"] = True
                row["result_telegram_sent_at"] = _now()
                changed = True
    if changed:
        save_signal_journal(path, rows)


def maybe_emit_money_flow(record: dict[str, Any]) -> dict[str, Any] | None:
    """Settle old FLOW rows and create one independent exchange-flow bet if warranted."""
    path = money_flow_journal_path()
    _settle_and_notify(record, path)

    if str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() != "active":
        return None
    info = evaluate_money_flow(record)
    if not bool(info.get("eligible")):
        return None

    candidate = _entry(record, info)
    rows = load_signal_journal(path)
    if any(str(row.get("entry_key") or "") == candidate["entry_key"] for row in rows):
        return None
    if any(
        str(row.get("match_id") or "") == candidate["match_id"]
        and str(row.get("strategy") or "") == "money_flow"
        and str(row.get("result") or "pending").lower() == "pending"
        for row in rows
    ):
        return None

    sent = telegram.broadcast(_signal_text(candidate))
    if sent <= 0:
        print(f"GOOL_MONEY_FLOW_SEND_FAILED match={candidate['match_id']}", flush=True)
        return None
    candidate["telegram_sent"] = True
    candidate["telegram_sent_at"] = _now()
    candidate["telegram_delivery_count"] = int(sent)
    rows.append(candidate)
    save_signal_journal(path, rows)
    print(
        f"GOOL_MONEY_FLOW_BET match={candidate['match_id']} minute={candidate['minute']} "
        f"market={candidate['market']} odd={candidate['odd']:.2f} level={info.get('level')} "
        f"delta=£{float(info.get('volume_delta') or 0):.0f}/{info.get('window')} "
        f"fair_pp={float(info.get('fair_delta_pp') or 0):+.2f}",
        flush=True,
    )
    return candidate


def money_flow_report_line(path: Path | None = None) -> str:
    rows = load_signal_journal(path or money_flow_journal_path())
    counts = Counter(str(row.get("result") or "pending").lower() for row in rows)
    settled = counts["won"] + counts["lost"]
    hit = "—" if settled <= 0 else f"{counts['won'] / settled * 100:.1f}%"
    profit = sum(
        float(row.get("profit_units") or 0.0)
        for row in rows
        if str(row.get("result") or "").lower() in FINAL_RESULTS
    )
    roi = "—" if settled <= 0 else f"{profit / settled * 100:+.1f}%"
    return (
        f"💸 Matchbook MONEY FLOW: ✅ <b>{counts['won']}</b> · ❌ <b>{counts['lost']}</b> · "
        f"⏳ <b>{counts['pending']}</b> · ↩️ <b>{counts['void'] + counts['push']}</b> · "
        f"проход <b>{hit}</b> · P/L <b>{profit:+.2f}u</b> · ROI <b>{roi}</b>"
    )
