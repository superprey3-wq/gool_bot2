from __future__ import annotations

import os
from statistics import median
from typing import Any


_TOP_LEAGUE_MARKERS = (
    "premier league",
    "laliga",
    "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "champions league",
    "europa league",
    "conference league",
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


def _tier(league: Any) -> str:
    text = str(league or "").casefold()
    return "top" if any(marker in text for marker in _TOP_LEAGUE_MARKERS) else "non_top"


def _league_key(event: dict[str, Any]) -> str:
    country = str(event.get("country") or "").strip()
    league = str(event.get("league") or "").strip()
    return f"{country}|{league}".casefold() if country or league else "__unknown__"


def _selection_money(market: dict[str, Any]) -> tuple[float, float, float, float]:
    over = dict(market.get("over") or {})
    under = dict(market.get("under") or {})
    over_money = max(0.0, _num(market.get("over_matched"), _num(over.get("volume"))))
    under_money = max(0.0, _num(market.get("under_matched"), _num(under.get("volume"))))
    total = over_money + under_money
    if total <= 0.0:
        return over_money, under_money, 0.0, 0.0
    return (
        over_money,
        under_money,
        over_money / total * 100.0,
        under_money / total * 100.0,
    )


def _flow_snapshot(market: dict[str, Any]) -> dict[str, Any]:
    flow = dict(market.get("flow") or {})
    chosen = ""
    for label in ("300s", "120s", "60s", "30s"):
        if bool(flow.get(f"window_ready_{label}")):
            chosen = label
            break
    if not chosen:
        return {
            "window": "",
            "volume_delta": 0.0,
            "relative_pct": 0.0,
            "fair_delta_pp": 0.0,
            "old_over_odd": 0.0,
            "new_over_odd": _num(((market.get("over") or {}).get("best_back") or {}).get("odds")),
            "direction": "WAIT",
            "direction_consistency": 0.0,
        }

    volume = max(0.0, _num(market.get("volume")))
    delta = max(0.0, _num(flow.get(f"volume_delta_{chosen}")))
    prior = max(1.0, volume - delta)
    fair_pp = _num(flow.get(f"fair_over_delta_pp_{chosen}"))
    old_over_odd = _num(flow.get(f"over_back_old_{chosen}"))
    new_over_odd = _num(
        flow.get("over_back_new"),
        _num(((market.get("over") or {}).get("best_back") or {}).get("odds")),
    )

    min_pp = _env("MATCHBOOK_RADAR_MIN_DIRECTION_PP", 0.55)
    min_consistency = _env("MATCHBOOK_RADAR_MIN_DIRECTION_CONSISTENCY", 0.60)
    over_consistency = _num(flow.get("long_over_consistency"), 0.5)
    under_consistency = _num(flow.get("long_under_consistency"), 0.5)
    if chosen not in {"120s", "300s"}:
        over_consistency = under_consistency = 1.0

    if fair_pp >= min_pp and over_consistency >= min_consistency:
        direction = "TB"
        consistency = over_consistency
    elif fair_pp <= -min_pp and under_consistency >= min_consistency:
        direction = "TM"
        consistency = under_consistency
    else:
        direction = "WAIT"
        consistency = max(over_consistency, under_consistency)

    return {
        "window": chosen,
        "volume_delta": delta,
        "relative_pct": delta / prior * 100.0,
        "fair_delta_pp": fair_pp,
        "old_over_odd": old_over_odd,
        "new_over_odd": new_over_odd,
        "direction": direction,
        "direction_consistency": consistency,
    }


def _raw_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in state.get("events") or []:
        if not isinstance(event, dict):
            continue
        for market in (event.get("totals") or {}).values():
            if not isinstance(market, dict):
                continue
            if str(market.get("period") or "FT").upper() != "FT":
                continue
            volume = max(0.0, _num(market.get("volume")))
            if volume <= 0.0:
                continue
            rows.append(
                {
                    "event": event,
                    "market": market,
                    "volume": volume,
                    "tier": _tier(event.get("league")),
                    "league_key": _league_key(event),
                }
            )
    return rows


def _baselines(rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, float]]:
    by_league: dict[str, list[float]] = {}
    by_tier: dict[str, list[float]] = {"top": [], "non_top": []}
    for row in rows:
        volume = float(row["volume"])
        by_league.setdefault(str(row["league_key"]), []).append(volume)
        by_tier.setdefault(str(row["tier"]), []).append(volume)

    league_medians = {
        key: float(median(values))
        for key, values in by_league.items()
        if key != "__unknown__" and len(values) >= 3
    }
    tier_medians = {
        key: float(median(values))
        for key, values in by_tier.items()
        if len(values) >= 6
    }
    return league_medians, tier_medians


def build_money_radar(state: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """Rank unusual Matchbook total-goals money across the entire football board."""
    rows = _raw_rows(state)
    league_medians, tier_medians = _baselines(rows)
    candidates: list[dict[str, Any]] = []

    for raw in rows:
        event = dict(raw["event"])
        market = dict(raw["market"])
        tier = str(raw["tier"])
        volume = float(raw["volume"])
        league_key = str(raw["league_key"])

        fixed = _env(
            "MATCHBOOK_RADAR_TOP_VOLUME_GBP" if tier == "top" else "MATCHBOOK_RADAR_NON_TOP_VOLUME_GBP",
            25000.0 if tier == "top" else 5000.0,
        )
        extreme = _env(
            "MATCHBOOK_RADAR_TOP_EXTREME_GBP" if tier == "top" else "MATCHBOOK_RADAR_NON_TOP_EXTREME_GBP",
            75000.0 if tier == "top" else 10000.0,
        )
        baseline = league_medians.get(league_key)
        baseline_scope = "league"
        if baseline is None:
            baseline = tier_medians.get(tier)
            baseline_scope = "tier"
        if baseline is None or baseline <= 0.0:
            baseline = fixed
            baseline_scope = "fixed"

        baseline_multiple = max(1.5, _env("MATCHBOOK_RADAR_BASELINE_MULTIPLE", 3.0))
        threshold = (
            fixed
            if baseline_scope == "fixed"
            else max(fixed, baseline * baseline_multiple)
        )
        volume_multiple = volume / max(1.0, baseline)

        flow = _flow_snapshot(market)
        long_accumulation = bool(
            flow["window"] in {"120s", "300s"}
            and flow["volume_delta"] >= _env("MATCHBOOK_RADAR_LONG_MIN_DELTA_GBP", 1000.0)
            and flow["relative_pct"] >= _env("MATCHBOOK_RADAR_LONG_MIN_RELATIVE_PCT", 12.0)
            and abs(flow["fair_delta_pp"]) >= _env("MATCHBOOK_RADAR_MIN_DIRECTION_PP", 0.55)
            and volume >= fixed * _env("MATCHBOOK_RADAR_LONG_MIN_FIXED_FRACTION", 0.70)
        )
        absolute_anomaly = volume >= threshold
        extreme_anomaly = volume >= extreme
        if not (absolute_anomaly or extreme_anomaly or long_accumulation):
            continue

        if extreme_anomaly:
            level = "EXTREME_VOLUME"
        elif long_accumulation:
            level = "ACCUMULATION"
        else:
            level = "BIG_VOLUME"

        over_money, under_money, over_share, under_share = _selection_money(market)
        score = min(
            99.0,
            58.0
            + min(18.0, volume_multiple * 4.0)
            + min(10.0, abs(float(flow["fair_delta_pp"])) * 2.0)
            + min(8.0, float(flow["relative_pct"]) * 0.20)
            + (4.0 if bool(event.get("in_running")) else 0.0)
            + (4.0 if flow["direction"] != "WAIT" else 0.0),
        )
        if extreme_anomaly:
            score = max(score, 90.0)

        candidates.append(
            {
                "event_id": event.get("event_id"),
                "name": event.get("name"),
                "home": event.get("home"),
                "away": event.get("away"),
                "country": event.get("country"),
                "league": event.get("league"),
                "start": event.get("start"),
                "in_running": bool(event.get("in_running")),
                "line": _num(market.get("line")),
                "market_volume": volume,
                "over_matched": over_money,
                "under_matched": under_money,
                "over_share_pct": over_share,
                "under_share_pct": under_share,
                "tier": tier,
                "baseline_scope": baseline_scope,
                "baseline_volume": baseline,
                "threshold_volume": threshold,
                "volume_multiple": volume_multiple,
                "level": level,
                "score": round(score, 1),
                **flow,
            }
        )

    best_by_event: dict[str, dict[str, Any]] = {}
    for row in candidates:
        key = str(row.get("event_id") or row.get("name") or "")
        previous = best_by_event.get(key)
        if previous is None or (float(row["score"]), float(row["market_volume"])) > (
            float(previous["score"]),
            float(previous["market_volume"]),
        ):
            best_by_event[key] = row

    ranked = sorted(
        best_by_event.values(),
        key=lambda row: (float(row["score"]), float(row["market_volume"])),
        reverse=True,
    )
    return ranked[: max(1, int(limit))]


__all__ = ["build_money_radar"]
