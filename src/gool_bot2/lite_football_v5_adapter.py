"""Adapter from Flashscore *paired real bookmaker quotes* to original GOOL Football V5.

Business rules come exclusively from football_v5_shadow.forecast_from_history
and football_v5_production.production_v5_picks. This adapter only normalizes
verified book prices. No guessed bookmaker or fabricated market prices.
"""
from __future__ import annotations

import math
from typing import Any


def _decimal(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) and 1.01 < n < 30.0 else None
    except (ValueError, TypeError):
        return None


def _same_line(row: dict[str, Any]) -> float | None:
    try:
        handicap = row.get("handicap") or {}
        return float(handicap.get("value"))
    except (ValueError, TypeError, AttributeError):
        return None


def normalized_full_market(raw: dict[str, Any]) -> dict[str, Any]:
    """Produce fully paired candidate rows for V5 full-analysis input.

    Side prices are always from the same bookmaker, scope, handicap and market.
    For 1X2, all three outcomes must exist at one bookmaker.
    """
    names: dict[str, str] = {}
    for row in (raw.get("settings") or {}).get("bookmakers") or []:
        if not isinstance(row, dict):
            continue
        book = row.get("bookmaker") or {}
        if isinstance(book, dict) and book.get("id") is not None:
            names[str(book["id"])] = str(book.get("name") or book["id"])

    candidates: list[dict[str, Any]] = []
    for market in raw.get("odds") or []:
        if not isinstance(market, dict) or str(market.get("bettingScope") or "") != "FULL_TIME":
            continue
        typ = str(market.get("bettingType") or "")
        book = names.get(str(market.get("bookmakerId")), str(market.get("bookmakerId") or ""))
        rows = market.get("odds") or []
        if typ == "OVER_UNDER":
            lines: dict[float, dict[str, float]] = {}
            for item in rows:
                if not isinstance(item, dict):
                    continue
                line = _same_line(item)
                if line is None or line < 0 or abs(2*line-round(2*line)) > 1e-7 or round(2*line) % 2 == 0:
                    continue
                direction = str(item.get("selection") or "").upper()
                value = _decimal(item.get("value"))
                if value and direction in {"OVER", "UNDER"}:
                    lines.setdefault(line, {})[direction] = value
            for line, pair in lines.items():
                if set(pair) != {"OVER", "UNDER"}:
                    continue
                over, under = pair["OVER"], pair["UNDER"]
                denom = 1/over + 1/under
                for direction, odd in pair.items():
                    fair = (1/odd)/denom
                    candidates.append({
                        "scope": "FULL_TIME", "market_type": "OVER_UNDER",
                        "selection": f"{direction}_{line:g}",
                        "odds": odd, "market_probability": fair,
                        "bookmaker": book,
                    })
        elif typ == "BOTH_TEAMS_TO_SCORE":
            sides: dict[str, float] = {}
            for item in rows:
                if not isinstance(item, dict):
                    continue
                value = _decimal(item.get("value"))
                flag = item.get("bothTeamsToScore")
                side = "YES" if flag is True else "NO" if flag is False else str(item.get("selection") or "").upper()
                if value and side in {"YES", "NO"}:
                    sides[side] = value
            if set(sides) != {"YES", "NO"}:
                continue
            denom = sum(1/x for x in sides.values())
            for side, odd in sides.items():
                candidates.append({
                    "scope": "FULL_TIME", "market_type": "BOTH_TEAMS_TO_SCORE",
                    "selection": side, "odds": odd,
                    "market_probability": (1/odd)/denom, "bookmaker": book,
                })
        elif typ == "HOME_DRAW_AWAY":
            sides: dict[str, float] = {}
            for item in rows:
                if not isinstance(item, dict):
                    continue
                side = str(item.get("selection") or "").upper().strip()
                side = {"1":"HOME", "X":"DRAW", "2":"AWAY"}.get(side, side)
                value = _decimal(item.get("value"))
                if value and side in {"HOME", "DRAW", "AWAY"}:
                    sides[side] = value
            if set(sides) != {"HOME", "DRAW", "AWAY"}:
                continue
            denom = sum(1/x for x in sides.values())
            for side, odd in sides.items():
                candidates.append({
                    "scope":"FULL_TIME", "market_type":"HOME_DRAW_AWAY",
                    "selection":side, "odds":odd,
                    "market_probability":(1/odd)/denom, "bookmaker":book,
                })
    return {"candidates": candidates, "bookmakers": names}


def original_v5_picks(
    *, history: dict[str, Any], raw_odds: dict[str, Any],
    event_id: str, home: str, away: str, league: str, kickoff: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute the unchanged original Football V5 forecaster and production gates."""
    from .football_v5_shadow import forecast_from_history
    from .football_v5_production import production_v5_picks
    from .football_prematch_localization import football_selection_ru

    forecast = forecast_from_history(
        home=home, away=away, kickoff=kickoff, context=history,
    )
    if forecast.get("status") != "READY":
        return [], {"status": forecast.get("status"), "forecast": forecast, "model": "FOOTBALL_V5"}
    analysis = normalized_full_market(raw_odds)
    picks, info = production_v5_picks(
        forecast, event_id=event_id, home=home, away=away, league=league,
        kickoff_ts=kickoff, xbet=None, full_analysis=analysis,
    )
    out: list[dict[str, Any]] = []
    for pick in picks:
        bookmaker = next((
            str(row.get("bookmaker") or "") for row in analysis["candidates"]
            if abs(float(row["odds"]) - pick.odds) < 1e-7
            and pick.market == {
                "OVER_UNDER":"match_total", "BOTH_TEAMS_TO_SCORE":"btts",
                "HOME_DRAW_AWAY":"match_1x2",
            }.get(row["market_type"])
            and pick.selection == str(row["selection"]).replace("_", " ").lower()
        ), "")
        if not bookmaker:
            continue
        out.append({
            "sport":"football", "phase":"PREMATCH", "event_id":event_id,
            "home":home, "away":away, "league":league,
            "start_ts":kickoff,
            "market":pick.market, "market_family":pick.market,
            "original_selection":pick.selection,
            "selection":football_selection_ru(pick.market, pick.selection),
            "odd":round(pick.odds, 3), "bookmaker":bookmaker,
            "model_probability":round(pick.model_probability, 4),
            "market_probability":round(pick.market_probability, 4),
            "edge":round(pick.edge, 4),
            "expected_value":round(pick.expected_value, 4),
            "data_quality":round(pick.data_quality, 4),
            "strength":round(pick.model_probability*100, 2),
            "brain":"FOOTBALL_V5",
        })
    return out, {
        "status":forecast.get("status"), "forecast":forecast,
        "model":"FOOTBALL_V5", "priced_candidates":len(out),
        "market_rows":len(analysis["candidates"]), "engine":info,
    }
