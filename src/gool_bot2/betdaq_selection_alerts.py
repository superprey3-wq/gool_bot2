from __future__ import annotations

import html
import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import telegram

_HISTORY_LIMIT = 24
_LOCK = threading.Lock()


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


def _parse_ts(value: Any) -> float:
    raw = str(value or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).timestamp()
        except Exception:
            pass
    return time.time()


def _mid_probability(runner: dict[str, Any]) -> float | None:
    values: list[float] = []
    for key in ("best_back", "best_lay"):
        row = runner.get(key) or {}
        odd = _number(row.get("odds", row.get("odd")))
        if odd > 1.0:
            values.append(1.0 / odd)
    return sum(values) / len(values) if values else None


def _best_back(runner: dict[str, Any]) -> float:
    row = runner.get("best_back") or {}
    return _number(row.get("odds", row.get("odd")))


def _best_lay(runner: dict[str, Any]) -> float:
    row = runner.get("best_lay") or {}
    return _number(row.get("odds", row.get("odd")))


def _runner_snapshot(runner: dict[str, Any], ts: float) -> dict[str, float] | None:
    if not bool(runner.get("selection_matched_ready")):
        return None
    prob = _mid_probability(runner)
    back = _best_back(runner)
    lay = _best_lay(runner)
    if prob is None or back <= 1.0 or lay <= 1.0 or lay < back:
        return None
    return {
        "ts": float(ts),
        "for": max(0.0, _number(runner.get("matched_for_gbp"))),
        "against": max(0.0, _number(runner.get("matched_against_gbp"))),
        "prob": float(prob),
        "back": float(back),
        "lay": float(lay),
        "back_depth": max(0.0, _number(runner.get("back_depth_gbp"))),
        "lay_depth": max(0.0, _number(runner.get("lay_depth_gbp"))),
    }


def _prior(history: deque[dict[str, float]], now: float, seconds: float) -> dict[str, float] | None:
    rows = [row for row in history if now - float(row.get("ts") or 0.0) >= seconds]
    return rows[-1] if rows else None


def _label_for_match_odds(runner: dict[str, Any], event: dict[str, Any]) -> str:
    outcome = str(runner.get("outcome") or "")
    if outcome == "P1":
        return f"П1 {event.get('home') or runner.get('label') or '?'}"
    if outcome == "X":
        return "X · Ничья"
    if outcome == "P2":
        return f"П2 {event.get('away') or runner.get('label') or '?'}"
    return str(runner.get("label") or runner.get("name") or "?")


def _market_matched(market: dict[str, Any]) -> float:
    return max(0.0, _number(market.get("matched_gbp", market.get("volume"))))


def _event_scale(event: dict[str, Any]) -> float:
    """Return a league-agnostic liquidity proxy for this event.

    We deliberately do not sum unrelated markets. The largest real BETDAQ matched
    market is used only as a scale reference, so a Champions-League-sized event
    needs a materially larger selection delta than a thin lower-league event.
    """
    values: list[float] = []
    match_odds = event.get("match_odds") or {}
    if isinstance(match_odds, dict):
        values.append(_market_matched(match_odds))
    for market in (event.get("totals") or {}).values():
        if isinstance(market, dict):
            values.append(_market_matched(market))
    return max(values, default=0.0)


def _selection_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in state.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "")
        if not event_id:
            continue
        event_scale_gbp = _event_scale(event)

        match_odds = event.get("match_odds") or {}
        if bool(match_odds.get("labels_valid")):
            market_id = str(match_odds.get("id") or "")
            market_matched_gbp = _market_matched(match_odds)
            for runner in match_odds.get("runners") or []:
                if not isinstance(runner, dict) or str(runner.get("outcome") or "") not in {"P1", "X", "P2"}:
                    continue
                sid = str(runner.get("id") or "")
                if not market_id or not sid:
                    continue
                rows.append(
                    {
                        "event": event,
                        "event_id": event_id,
                        "market_id": market_id,
                        "market_matched_gbp": market_matched_gbp,
                        "event_scale_gbp": event_scale_gbp,
                        "selection_id": sid,
                        "runner": runner,
                        "family": "match_odds",
                        "group": f"{event_id}:match_odds",
                        "selection": str(runner.get("outcome") or ""),
                        "label": _label_for_match_odds(runner, event),
                        "period": "FT",
                        "line": None,
                    }
                )

        for market in (event.get("totals") or {}).values():
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("id") or "")
            if not market_id:
                continue
            period = str(market.get("period") or "FT")
            line = _number(market.get("line"))
            market_matched_gbp = _market_matched(market)
            for side, key, ru in (("over", "TB", "ТБ"), ("under", "TM", "ТМ")):
                runner = market.get(side) or {}
                if not isinstance(runner, dict):
                    continue
                sid = str(runner.get("id") or "")
                if not sid:
                    continue
                suffix = f" · {period}" if period != "FT" else ""
                rows.append(
                    {
                        "event": event,
                        "event_id": event_id,
                        "market_id": market_id,
                        "market_matched_gbp": market_matched_gbp,
                        "event_scale_gbp": event_scale_gbp,
                        "selection_id": sid,
                        "runner": runner,
                        "family": "total",
                        "group": f"{event_id}:total:{period}:{key}",
                        "selection": key,
                        "label": f"{ru} {line:g}{suffix}",
                        "period": period,
                        "line": line,
                    }
                )
    return rows


def _adaptive_min_delta(row: dict[str, Any], base_delta: float, scale_pct: float) -> tuple[float, float]:
    event_scale_gbp = max(
        _number(row.get("event_scale_gbp")),
        _number(row.get("market_matched_gbp")),
    )
    dynamic = event_scale_gbp * max(0.0, scale_pct) / 100.0
    cap = _threshold("BETDAQ_SELECTION_PUSH_MAX_DYNAMIC_DELTA_GBP", 10000.0)
    if cap > 0.0:
        dynamic = min(dynamic, cap)
    return max(float(base_delta), dynamic), event_scale_gbp


def _candidate(row: dict[str, Any], current: dict[str, float], history: deque[dict[str, float]]) -> dict[str, Any] | None:
    min_total = _threshold("BETDAQ_SELECTION_PUSH_MIN_MATCHED_GBP", 250.0)
    min_relative = _threshold("BETDAQ_SELECTION_PUSH_MIN_RELATIVE_PCT", 10.0)
    min_pp = _threshold("BETDAQ_SELECTION_PUSH_MIN_PP", 1.0)
    min_depth = _threshold("BETDAQ_SELECTION_PUSH_MIN_BACK_DEPTH_GBP", 25.0)
    max_spread_pct = _threshold("BETDAQ_SELECTION_PUSH_MAX_SPREAD_PCT", 12.0)

    mid = (current["back"] + current["lay"]) / 2.0
    spread_pct = ((current["lay"] - current["back"]) / mid * 100.0) if mid > 0 else 999.0
    if current["for"] < min_total or current["back_depth"] < min_depth or spread_pct > max_spread_pct:
        return None

    windows = (
        (
            "15s",
            15.0,
            _threshold("BETDAQ_SELECTION_PUSH_MIN_DELTA_15S", 150.0),
            _threshold("BETDAQ_SELECTION_PUSH_EVENT_PCT_15S", 1.5),
        ),
        (
            "30s",
            30.0,
            _threshold("BETDAQ_SELECTION_PUSH_MIN_DELTA_30S", 250.0),
            _threshold("BETDAQ_SELECTION_PUSH_EVENT_PCT_30S", 2.0),
        ),
        (
            "60s",
            60.0,
            _threshold("BETDAQ_SELECTION_PUSH_MIN_DELTA_60S", 500.0),
            _threshold("BETDAQ_SELECTION_PUSH_EVENT_PCT_60S", 3.0),
        ),
    )
    chosen: dict[str, Any] | None = None
    for label, seconds, base_delta, scale_pct in windows:
        old = _prior(history, current["ts"], seconds)
        if old is None:
            continue
        adaptive_min, event_scale_gbp = _adaptive_min_delta(row, base_delta, scale_pct)
        delta_for = max(0.0, current["for"] - float(old.get("for") or 0.0))
        delta_against = max(0.0, current["against"] - float(old.get("against") or 0.0))
        baseline = max(50.0, float(old.get("for") or 0.0))
        relative_pct = delta_for / baseline * 100.0
        pp = (current["prob"] - float(old.get("prob") or 0.0)) * 100.0
        if delta_for >= adaptive_min and relative_pct >= min_relative and pp >= min_pp:
            chosen = {
                "window": label,
                "seconds": seconds,
                "delta_for_gbp": delta_for,
                "delta_against_gbp": delta_against,
                "relative_pct": relative_pct,
                "implied_delta_pp": pp,
                "old_back": float(old.get("back") or 0.0),
                "new_back": current["back"],
                "old_lay": float(old.get("lay") or 0.0),
                "new_lay": current["lay"],
                "adaptive_min_delta_gbp": adaptive_min,
                "event_scale_gbp": event_scale_gbp,
                "event_scale_pct": (delta_for / event_scale_gbp * 100.0) if event_scale_gbp > 0.0 else 0.0,
            }
            break
    if chosen is None:
        return None

    extreme_delta = _threshold("BETDAQ_SELECTION_PUSH_EXTREME_DELTA_GBP", 750.0)
    extreme_pp = _threshold("BETDAQ_SELECTION_PUSH_EXTREME_PP", 2.5)
    extreme_multiplier = _threshold("BETDAQ_SELECTION_PUSH_EXTREME_MULTIPLIER", 2.0)
    adaptive_extreme = max(extreme_delta, chosen["adaptive_min_delta_gbp"] * extreme_multiplier)
    level = (
        "EXTREME_SELECTION_FLOW"
        if chosen["delta_for_gbp"] >= adaptive_extreme and chosen["implied_delta_pp"] >= extreme_pp
        else "SELECTION_FLOW"
    )
    strength_ratio = chosen["delta_for_gbp"] / max(1.0, chosen["adaptive_min_delta_gbp"])
    score = min(
        99.0,
        70.0
        + min(12.0, chosen["implied_delta_pp"] * 1.7)
        + min(8.0, chosen["relative_pct"] * 0.15)
        + min(9.0, max(0.0, strength_ratio - 1.0) * 9.0),
    )
    if level == "EXTREME_SELECTION_FLOW":
        score = max(score, 90.0)

    event = row["event"]
    return {
        "level": level,
        "score": round(score, 1),
        "event_id": row["event_id"],
        "event_name": str(event.get("name") or f"{event.get('home') or '?'} — {event.get('away') or '?'}"),
        "home": str(event.get("home") or ""),
        "away": str(event.get("away") or ""),
        "in_running": bool(event.get("in_running")),
        "market_id": row["market_id"],
        "market_matched_gbp": round(_number(row.get("market_matched_gbp")), 2),
        "selection_id": row["selection_id"],
        "family": row["family"],
        "group": row["group"],
        "selection": row["selection"],
        "label": row["label"],
        "period": row["period"],
        "line": row["line"],
        "matched_for_gbp": round(current["for"], 2),
        "matched_against_gbp": round(current["against"], 2),
        "back_depth_gbp": round(current["back_depth"], 2),
        "lay_depth_gbp": round(current["lay_depth"], 2),
        "spread_pct": round(spread_pct, 2),
        "adaptive_extreme_delta_gbp": round(adaptive_extreme, 2),
        **{key: (round(value, 3) if isinstance(value, float) else value) for key, value in chosen.items()},
    }


class SelectionPushTracker:
    def __init__(self) -> None:
        self.history: dict[str, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=_HISTORY_LIMIT))
        self.last_group_alert: dict[str, float] = {}

    def evaluate(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        ts = _parse_ts(state.get("captured_at"))
        cooldown = _threshold("BETDAQ_SELECTION_PUSH_COOLDOWN_SECONDS", 180.0)
        candidates: list[dict[str, Any]] = []

        for row in _selection_rows(state):
            runner = row["runner"]
            current = _runner_snapshot(runner, ts)
            if current is None:
                continue
            key = f"{row['event_id']}:{row['market_id']}:{row['selection_id']}"
            history = self.history[key]
            if history and current["ts"] <= float(history[-1].get("ts") or 0.0):
                continue
            if history and current["for"] < float(history[-1].get("for") or 0.0):
                history.clear()
            candidate = _candidate(row, current, history) if history else None
            history.append(current)
            if candidate is None:
                continue
            group = str(candidate["group"])
            if ts - float(self.last_group_alert.get(group, 0.0)) < cooldown:
                continue
            candidates.append(candidate)

        best_by_group: dict[str, dict[str, Any]] = {}
        for alert in candidates:
            group = str(alert["group"])
            prior = best_by_group.get(group)
            strength = _number(alert.get("delta_for_gbp")) * (1.0 + _number(alert.get("implied_delta_pp")) / 10.0)
            prior_strength = (
                _number(prior.get("delta_for_gbp")) * (1.0 + _number(prior.get("implied_delta_pp")) / 10.0)
                if prior
                else -1.0
            )
            if prior is None or strength > prior_strength:
                best_by_group[group] = alert

        alerts = sorted(
            best_by_group.values(),
            key=lambda alert: (_number(alert.get("score")), _number(alert.get("delta_for_gbp"))),
            reverse=True,
        )[:4]
        for alert in alerts:
            self.last_group_alert[str(alert["group"])] = ts
        return alerts


_TRACKER = SelectionPushTracker()


def _money(value: Any) -> str:
    amount = max(0.0, _number(value))
    if amount >= 1_000_000:
        return f"£{amount / 1_000_000:.2f}m"
    if amount >= 1_000:
        return f"£{amount / 1_000:.1f}k"
    return f"£{amount:.0f}"


def alert_text(alert: dict[str, Any]) -> str:
    icon = "🚨" if str(alert.get("level")) == "EXTREME_SELECTION_FLOW" else "🔥"
    phase = "🔴 LIVE" if bool(alert.get("in_running")) else "⏳ PREMATCH"
    return (
        f"💰 <b>GOOL · BETDAQ ПРОГРУЗ</b> {icon}\n"
        f"<b>{html.escape(str(alert.get('event_name') or '?'))}</b>\n"
        f"{phase} · 🎯 <b>{html.escape(str(alert.get('label') or '?'))}</b>\n"
        f"💷 FOR matched за {html.escape(str(alert.get('window') or '?'))}: "
        f"<b>+{_money(alert.get('delta_for_gbp'))}</b> "
        f"({_number(alert.get('relative_pct')):+.1f}%)\n"
        f"📉 Back: {_number(alert.get('old_back')):.2f} → <b>{_number(alert.get('new_back')):.2f}</b> · "
        f"implied {_number(alert.get('implied_delta_pp')):+.1f} п.п.\n"
        f"📚 стакан: Back {_money(alert.get('back_depth_gbp'))} · "
        f"Lay {_money(alert.get('lay_depth_gbp'))}\n"
        f"↔️ AGAINST за окно: +{_money(alert.get('delta_against_gbp'))}\n"
        f"{icon} <b>{html.escape(str(alert.get('level') or 'SELECTION_FLOW'))}</b> · "
        f"score {_number(alert.get('score')):.0f}/100"
    )


def _journal_path() -> Path:
    raw = os.getenv("GOOL_BETDAQ_SELECTION_PUSH_JOURNAL_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "gool_betdaq_selection_push.jsonl"


def _append_journal(alert: dict[str, Any]) -> None:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(alert)
    row["created_at"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _dispatch(text: str, sender: Callable[[str], int]) -> None:
    try:
        sender(text)
    except Exception as exc:
        print(f"BETDAQ_SELECTION_PUSH telegram_error={type(exc).__name__}:{exc}", flush=True)


def process_selection_alerts(state: dict[str, Any]) -> list[dict[str, Any]]:
    if not _flag("BETDAQ_SELECTION_PUSH_ENABLED", True):
        return []
    with _LOCK:
        alerts = _TRACKER.evaluate(state)
    if not alerts:
        return []

    active = str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() == "active"
    for alert in alerts:
        try:
            _append_journal(alert)
        except Exception as exc:
            print(f"BETDAQ_SELECTION_PUSH journal_error={type(exc).__name__}:{exc}", flush=True)
        if active:
            text = alert_text(alert)
            threading.Thread(target=_dispatch, args=(text, telegram.broadcast), daemon=True).start()
    return alerts


__all__ = ["SelectionPushTracker", "alert_text", "process_selection_alerts"]