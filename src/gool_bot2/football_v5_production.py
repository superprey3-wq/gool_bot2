"""Production GOOL Football V5 PREMATCH pricing and filtering.

Every V5 wager comes from a V5 score-grid probability AND a real bookmaker
quote. GOOL V4 cannot silently replace an unpriced or rejected V5 pick.

V5 historical probabilities are uncalibrated: treat the edge as provisional,
shrink to the fair bookmaker prior in the existing delivery ranker, and retain
a conservative minimum model disagreement. No simulated/invented odds.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

from .v4_prematch_engine import PrematchPick, devig_two_way, devig_three_way


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _p_over(lam: float, line: float) -> float | None:
    if not (math.isfinite(line) and line >= 0.5 and abs(line * 2 - round(line * 2)) < 1e-8 and int(round(line * 2)) % 2 == 1):
        # Whole/quarter lines are not binary bets (push/half settlement).
        return None
    threshold = int(math.floor(line)) + 1
    cdf = sum(math.exp(-lam) * lam**k / math.factorial(k) for k in range(threshold))
    return min(1.0, max(0.0, 1.0 - cdf))


def _price_ok(odd: float) -> bool:
    return math.isfinite(odd) and odd >= _f("GOOL_FOOTBALL_V5_MIN_ODD", 1.4)


def _gate(p: PrematchPick) -> bool:
    return (
        _price_ok(p.odds)
        and p.data_quality >= _f("GOOL_FOOTBALL_V5_MIN_QUALITY", 0.55)
        and p.model_probability >= _f("GOOL_FOOTBALL_V5_MIN_PROB", 0.56)
        and p.edge >= _f("GOOL_FOOTBALL_V5_MIN_RAW_EDGE", 0.09)
        and p.expected_value >= _f("GOOL_FOOTBALL_V5_MIN_RAW_EV", 0.05)
    )


def _from_prob(
    event_id: str, home: str, away: str, league: str, kickoff_ts: float,
    market: str, selection: str, odd: float, prob: float, fair: float, quality: float,
) -> PrematchPick | None:
    if not all(math.isfinite(float(x)) for x in (odd, prob, fair, quality)):
        return None
    if not (0.0 <= prob <= 1.0 and 0.0 < fair < 1.0):
        return None
    pick = PrematchPick(
        str(event_id), str(home), str(away), str(market), str(selection),
        float(odd), float(prob), float(fair), float(quality),
        str(league or ""), float(kickoff_ts),
    )
    return pick if _gate(pick) else None


def price_1xbet(
    forecast: dict[str, Any], quote: dict[str, Any], *,
    event_id: str, home: str, away: str, league: str, kickoff_ts: float,
) -> list[PrematchPick]:
    if forecast.get("status") != "READY" or not isinstance(quote, dict):
        return []
    if kickoff_ts <= 0:
        return []
    hl, al = float(forecast["home_lambda"]), float(forecast["away_lambda"])
    quality = float(forecast.get("quality") or 0)
    predictions = forecast["probabilities"]
    out: list[PrematchPick] = []
    def add(market: str, label: str, odd: float, prob: float, fair: float):
        pick = _from_prob(event_id, home, away, league, kickoff_ts,
                          market, label, odd, prob, fair, quality)
        if pick is not None:
            out.append(pick)

    def binary_rows(rows: list[dict], lam: float, market_name: str, prefix: str = ""):
        for row in rows:
            try:
                line, o, u = float(row["line"]), float(row["over"]), float(row["under"])
            except (KeyError, ValueError, TypeError):
                continue
            if min(o, u) <= 1:
                continue
            p_over = _p_over(lam, line)
            if p_over is None:
                continue
            fo, fu = devig_two_way(o, u)
            if prefix:
                add(market_name, f"ИТБ{prefix} {line:g}", o, p_over, fo)
                add(market_name, f"ИТМ{prefix} {line:g}", u, 1-p_over, fu)
            else:
                add(market_name, f"over {line:g}", o, p_over, fo)
                add(market_name, f"under {line:g}", u, 1-p_over, fu)

    binary_rows(quote.get("match_totals") or [], hl+al, "match_total")
    binary_rows(quote.get("home_totals") or [], hl, "home_total", "1")
    binary_rows(quote.get("away_totals") or [], al, "away_total", "2")

    one_x_two = quote.get("match_1x2") or {}
    try:
        odds = [float(one_x_two[k]) for k in ("home", "draw", "away")]
        if min(odds) > 1:
            fair = devig_three_way(*odds)
            for key, odd, base in zip(("home","draw","away"),odds,fair):
                add("match_1x2",key,odd,float(predictions[f"{key}_win"] if key != "draw" else predictions["draw"]),base)
    except (KeyError, TypeError, ValueError):
        pass

    btts = quote.get("btts") or {}
    try:
        yes, no = float(btts["yes"]), float(btts["no"])
        if min(yes, no) > 1:
            fy, fn = devig_two_way(yes, no)
            p = float(predictions["btts_yes"])
            add("btts","yes",yes,p,fy)
            add("btts","no",no,1-p,fn)
    except (KeyError,TypeError,ValueError):
        pass
    return _unique_sorted(out)


def price_flashscore_full_market(
    forecast: dict[str, Any], analysis: dict[str, Any], *,
    event_id: str, home: str, away: str, league: str, kickoff_ts: float,
) -> list[PrematchPick]:
    """Decode real full-market odds directly, regardless of the old model's BET/WATCH.

    Only half-goal FT totals, team totals, BTTS and 1X2 are journal-safe here.
    We never re-use V4 model odds/probability, only its decoded bookmaker quote.
    """
    if forecast.get("status") != "READY" or not isinstance(analysis, dict):
        return []
    from .prematch_full_market_runtime import _market_and_selection
    hl, al = float(forecast["home_lambda"]), float(forecast["away_lambda"])
    probs = forecast["probabilities"]
    q = float(forecast.get("quality") or 0.0)
    out: list[PrematchPick] = []
    for row in analysis.get("candidates") or []:
        if not isinstance(row, dict) or str(row.get("scope") or "").upper() != "FULL_TIME":
            continue
        converted = _market_and_selection(row)
        if not converted:
            continue
        market, selection = converted
        try:
            odds = float(row["odds"])
            fair = float(row["market_probability"])
        except (TypeError,ValueError,KeyError):
            continue
        if not (_price_ok(odds) and 0 < fair < 1):
            continue
        p = None
        if market in ("match_total","home_total","away_total"):
            words = selection.casefold().strip()
            match = re.match(r"^(over|under)\s+(\d+(?:\.\d+)?)$", words)
            if not match:
                continue
            lam = hl+al if market == "match_total" else hl if market == "home_total" else al
            po = _p_over(lam, float(match.group(2)))
            if po is None:
                continue
            p = po if match.group(1) == "over" else 1-po
        elif market == "btts":
            if selection in ("yes","no"):
                p = float(probs["btts_yes"])
                if selection == "no":
                    p = 1-p
        elif market == "match_1x2":
            if selection in ("home","draw","away"):
                p = float(probs["draw"] if selection == "draw" else probs[f"{selection}_win"])
        if p is None:
            continue
        pick = _from_prob(event_id, home, away, league, kickoff_ts, market, selection, odds, p, fair, q)
        if pick is not None:
            out.append(pick)
    return _unique_sorted(out)


def _unique_sorted(picks: list[PrematchPick]) -> list[PrematchPick]:
    out = {}
    for pick in sorted(picks, key=lambda p:(p.edge,p.expected_value,p.model_probability),reverse=True):
        # Same market+selection across books: use quote with the higher EV.
        key = pick.market, pick.selection
        out.setdefault(key,pick)
    return list(out.values())


def production_v5_picks(
    forecast: dict[str,Any], *, event_id: str, home: str, away: str,
    league: str, kickoff_ts: float,
    xbet: dict[str,Any] | None, full_analysis: dict[str,Any] | None,
) -> tuple[list[PrematchPick], dict[str,Any]]:
    if forecast.get("status") != "READY":
        return [], {"model":"football_v5", "reject":str(forecast.get("status") or "NO_DATA")}
    if forecast.get("opponent_strength_coverage",0.0) < _f("GOOL_FOOTBALL_V5_MIN_OPPONENT_COVERAGE", 0.0):
        return [], {"model":"football_v5","reject":"OPPONENT_COVERAGE"}
    candidates: list[PrematchPick] = []
    if xbet:
        candidates.extend(price_1xbet(forecast,xbet,event_id=event_id,home=home,away=away,league=league,kickoff_ts=kickoff_ts))
    if full_analysis:
        candidates.extend(price_flashscore_full_market(forecast,full_analysis,event_id=event_id,home=home,away=away,league=league,kickoff_ts=kickoff_ts))
    candidates = _unique_sorted(candidates)
    if not candidates:
        return [], {"model":"football_v5","reject":"NO_POSITIVE_VALUE_AT_REAL_ODDS"}
    return candidates, {
        "model": "football_v5",
        "brain_version": "V5",
        "v5_home_lambda": forecast["home_lambda"],
        "v5_away_lambda": forecast["away_lambda"],
        "v5_opponent_strength_coverage": forecast.get("opponent_strength_coverage", 0.0),
        "v5_uncalibrated": True,
        "bookmaker": "1xBet / Flashscore odds",
    }
