from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import telegram
from .betdaq_exchange import betdaq_context
from .journal import load_signal_journal, save_signal_journal
from .multi_journal import settle_entry
from . import multi_money_flow as matchbook_flow


FINAL_RESULTS = {"won", "lost", "push", "void"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def betdaq_money_flow_journal_path() -> Path:
    raw = os.getenv("GOOL_BETDAQ_MONEY_FLOW_JOURNAL_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_betdaq_money_flow_journal.json"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _threshold(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


def _flag(name: str, default: bool = True) -> bool:
    fallback = "1" if default else "0"
    return str(os.getenv(name, fallback)).strip().lower() not in {"0", "false", "no", "off"}


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


def evaluate_betdaq_money_flow(record: dict[str, Any], exchange: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate BETDAQ as a completely separate autonomous exchange-flow system.

    The rules intentionally mirror the proven Matchbook FLOW shape, but every
    threshold has its own BETDAQ_* environment key and the input comes only from
    BETDAQ. This function never mutates Matchbook state or Brain V3 inputs.
    """
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    if minute <= 0 or bool(match.get("is_finished")):
        return {"eligible": False, "reason": "betdaq_money_flow_match_not_live"}

    payload = dict(exchange) if isinstance(exchange, dict) else betdaq_context(record)
    systems = payload.get("systems") or {}
    context = dict(systems.get("money_flow") or {})
    if not context:
        legacy_key = "goal_before_ht" if minute <= 45 else "another_goal"
        context = dict(systems.get(legacy_key) or {})
    period = str(context.get("period") or "FT")
    family = "first_half_total" if period == "1H" else "match_total"

    if not bool(payload.get("available")) or not bool(context.get("available")):
        return {"eligible": False, "reason": "betdaq_market_unavailable"}
    if not bool(context.get("liquid")):
        return {"eligible": False, "reason": "betdaq_low_liquidity"}
    status = str(context.get("market_status") or "").strip().lower()
    if status and status not in {"open", "active"}:
        return {"eligible": False, "reason": f"betdaq_market_{status}"}

    last_goal = _last_goal_minute(record)
    reset_minutes = int(_threshold("BETDAQ_FLOW_POST_GOAL_RESET_MINUTES", 3))
    if last_goal is not None and minute - last_goal < reset_minutes:
        return {"eligible": False, "reason": "post_goal_betdaq_reset", "last_goal_minute": last_goal}

    flow = context.get("flow") or {}
    market_volume = _number(context.get("volume"))
    if (
        _flag("BETDAQ_FLOW_ORDERBOOK_GATE", True)
        and bool(flow.get("orderbook_ready"))
        and not bool(flow.get("orderbook_confirmed"))
    ):
        if bool(flow.get("transient_liquidity_spike")):
            reason = "betdaq_flow_transient_liquidity_spike"
        elif bool(flow.get("liquidity_pull")):
            reason = "betdaq_flow_liquidity_pulled"
        else:
            reason = "betdaq_flow_orderbook_unconfirmed"
        return {"eligible": False, "reason": reason, "market_volume": market_volume, "flow": flow}

    fair_over = _number(context.get("fair_over"), -1.0)
    over = context.get("over") or {}
    best_back = _number((over.get("best_back") or {}).get("odds"))
    best_lay = _number((over.get("best_lay") or {}).get("odds"))
    if fair_over <= 0.0 or best_back < _threshold("BETDAQ_FLOW_BET_MIN_ODD", 1.50):
        return {"eligible": False, "reason": "betdaq_flow_price_not_tradeable", "odd": best_back}
    max_spread = _threshold("BETDAQ_FLOW_BET_MAX_SPREAD", 0.18)
    if best_lay > 0.0 and best_lay - best_back > max_spread:
        return {"eligible": False, "reason": "betdaq_flow_spread_too_wide"}

    min_market_volume = _threshold("BETDAQ_FLOW_BET_MIN_MARKET_VOLUME", 500.0)
    min_relative = _threshold("BETDAQ_FLOW_BET_MIN_RELATIVE_PCT", 8.0)
    min_pp = _threshold("BETDAQ_FLOW_BET_MIN_FAIR_PP", 2.0)
    windows = (
        ("30s", _threshold("BETDAQ_FLOW_BET_MIN_DELTA_30S", 250.0)),
        ("60s", _threshold("BETDAQ_FLOW_BET_MIN_DELTA_60S", 500.0)),
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
            "reason": "betdaq_flow_threshold_not_reached",
            "market_volume": market_volume,
            "flow": flow,
        }

    back_wom = _number(flow.get("back_wom"), 0.5)
    orderflow_imbalance = _number(flow.get("orderflow_imbalance"))
    orderbook_streak = int(_number(flow.get("orderbook_support_streak")))
    orderbook_confirmations = int(_number(flow.get("orderbook_confirmation_count")))
    extreme_delta = _threshold("BETDAQ_FLOW_BET_EXTREME_DELTA", 750.0)
    extreme_pp = _threshold("BETDAQ_FLOW_BET_EXTREME_FAIR_PP", 3.5)
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
        + min(5.0, chosen["volume_delta"] / 250.0)
        + min(3.0, max(0.0, back_wom - 0.50) * 15.0)
        + min(3.0, max(0.0, orderflow_imbalance) * 3.0)
        + min(2.0, max(0, orderbook_streak - 1) * 0.75),
    )
    if level == "EXTREME_FLOW":
        score = max(score, 90.0)

    line = _number(context.get("line"))
    previous_fair = max(0.0, fair_over - chosen["fair_delta_pp"] / 100.0)
    return {
        "eligible": True,
        "level": level,
        "score": round(score, 1),
        "strategy": "betdaq_money_flow",
        "reference_strategy": "money_flow",
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
        "betdaq_event_id": ((payload.get("event") or {}).get("id")),
        "orderbook_ready": bool(flow.get("orderbook_ready")),
        "orderbook_confirmed": bool(flow.get("orderbook_confirmed")),
        "orderbook_confirmations": orderbook_confirmations,
        "back_wom": back_wom,
        "book_imbalance": _number(flow.get("book_imbalance")),
        "orderflow_imbalance": orderflow_imbalance,
        "orderbook_support_streak": orderbook_streak,
        "back_depth_weighted": _number(flow.get("back_depth_weighted")),
        "lay_depth_weighted": _number(flow.get("lay_depth_weighted")),
        **chosen,
    }


def _entry(record: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    # Reuse only the mature generic total-entry shape, then make source metadata
    # explicitly BETDAQ. No Matchbook state is read or changed here.
    row = dict(matchbook_flow._entry(record, info))
    row["head"] = "betdaq_money_flow"
    row["strategy"] = "betdaq_money_flow"
    row["entry_key"] = str(row.get("entry_key") or "").replace("money_flow:", "betdaq_money_flow:", 1)
    row["signal_source"] = "BETDAQ_FLOW"
    row["source"] = "betdaq:money_flow"
    row["reason"] = (
        "Аномальный проторгованный объём BETDAQ подтверждён движением fair probability "
        "и устойчивым давлением биржевого стакана в сторону следующего гола."
    )
    tags: list[str] = []
    for tag in row.get("reason_tags") or []:
        text = str(tag).replace("matchbook_", "betdaq_")
        tags.append(text)
    if "betdaq_money_flow" not in tags:
        tags.append("betdaq_money_flow")
    row["reason_tags"] = tags
    row["betdaq_flow"] = dict(info)
    row.pop("matchbook_flow", None)
    return row


def _signal_text(row: dict[str, Any]) -> str:
    flow = row.get("betdaq_flow") or {}
    icon = "🚨" if str(flow.get("level")) == "EXTREME_FLOW" else "🔥"
    score = row.get("score") or [0, 0]
    prior = _number(flow.get("previous_fair_over")) * 100.0
    current = _number(flow.get("fair_over")) * 100.0
    return (
        f"💰 <b>GOOL · BETDAQ MONEY FLOW</b> {icon}\n"
        f"{html.escape(str(row.get('home') or '?'))} — {html.escape(str(row.get('away') or '?'))}\n"
        f"{int(row.get('minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}\n"
        f"🎯 <b>{html.escape(str(row.get('market') or '?'))} @ {_number(row.get('odd')):.2f}</b>\n"
        f"💷 matched за {flow.get('window')}: <b>+£{_number(flow.get('volume_delta')):,.0f}</b> "
        f"({_number(flow.get('relative_pct')):+.1f}%)\n"
        f"📈 fair Over: {prior:.1f}% → <b>{current:.1f}%</b> "
        f"({_number(flow.get('fair_delta_pp')):+.1f} п.п.)\n"
        f"📚 рынок: £{_number(flow.get('market_volume')):,.0f} · LAY {_number(flow.get('lay_odd')):.2f}\n"
        f"{icon} <b>{html.escape(str(flow.get('level') or 'FLOW'))}</b> · score {_number(row.get('rating')):.0f}/100"
    )


def _result_text(row: dict[str, Any]) -> str:
    result = str(row.get("result") or "void").lower()
    icon, label = ("✅", "ЗАШЁЛ") if result == "won" else (("❌", "НЕ ЗАШЁЛ") if result == "lost" else ("↩️", "VOID"))
    score = list(row.get("settled_score") or [0, 0])
    return (
        f"{icon} <b>{label} · BETDAQ MONEY FLOW</b>\n"
        f"{html.escape(str(row.get('home') or '?'))} — {html.escape(str(row.get('away') or '?'))}\n"
        f"{html.escape(str(row.get('market') or '?'))} @ {_number(row.get('odd')):.2f}\n"
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
                row["result_telegram_delivery_count"] = int(sent)
                changed = True
    if changed:
        save_signal_journal(path, rows)


def maybe_emit_betdaq_money_flow(record: dict[str, Any]) -> dict[str, Any] | None:
    """Settle and emit the third, independent BETDAQ MONEY FLOW system."""
    path = betdaq_money_flow_journal_path()
    _settle_and_notify(record, path)

    if str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() != "active":
        return None
    exchange = betdaq_context(record)
    info = evaluate_betdaq_money_flow(record, exchange)
    if not bool(info.get("eligible")):
        return None

    candidate = _entry(record, info)
    rows = load_signal_journal(path)
    if any(str(row.get("entry_key") or "") == candidate["entry_key"] for row in rows):
        return None
    if any(
        str(row.get("match_id") or "") == candidate["match_id"]
        and str(row.get("strategy") or "") == "betdaq_money_flow"
        and str(row.get("result") or "pending").lower() == "pending"
        for row in rows
    ):
        return None

    sent = telegram.broadcast(_signal_text(candidate))
    if sent <= 0:
        print(f"GOOL_BETDAQ_MONEY_FLOW_SEND_FAILED match={candidate['match_id']}", flush=True)
        return None
    candidate["telegram_sent"] = True
    candidate["telegram_sent_at"] = _now()
    candidate["telegram_delivery_count"] = int(sent)
    rows.append(candidate)
    save_signal_journal(path, rows)
    print(
        f"GOOL_BETDAQ_MONEY_FLOW_BET match={candidate['match_id']} minute={candidate['minute']} "
        f"market={candidate['market']} odd={candidate['odd']:.2f} level={info.get('level')} "
        f"delta=£{_number(info.get('volume_delta')):.0f}/{info.get('window')} "
        f"fair_pp={_number(info.get('fair_delta_pp')):+.2f}",
        flush=True,
    )
    return candidate


__all__ = [
    "betdaq_money_flow_journal_path",
    "evaluate_betdaq_money_flow",
    "maybe_emit_betdaq_money_flow",
]
