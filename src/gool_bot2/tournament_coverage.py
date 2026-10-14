"""Verified bookmaker-only reserve legs for *complete* tournament coupons.

These are NOT model-confirmed value bets. Their estimated p is the bookmaker
de-vig proxy, NOT the model probability. They must be explicitly flagged as
COVERAGE_ONLY and never fed to the standalone or value-delivery paths.
"""
from __future__ import annotations

import math
from typing import Any

from .v4_prematch_engine import PrematchPick, devig_three_way, devig_two_way


def _odd(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) and x >= 1.0 else None
    except (TypeError, ValueError):
        return None


def bookmaker_coverage_picks(
    quote: dict[str, Any] | None,
    *,
    quote_source: str,
    event_id: str,
    home: str,
    away: str,
    league: str,
    kickoff_ts: float,
    min_odd: float = 1.30,
) -> list[PrematchPick]:
    """One selection may be chosen later; list ALL legitimate 1.30+ options.

    All quote families must have BOTH sides (or 3 in 1X2) to estimate fair
    probability after bookmaker margin. Never use fabricated bookmaker odds,
    incomplete Asian settlement lines, or a quote from an unknown source.
    """
    if quote_source not in {"1xbet", "flashscore"} or not isinstance(quote, dict):
        return []
    try:
        kick = float(kickoff_ts)
        if not math.isfinite(kick) or kick <= 0:
            return []
    except (TypeError, ValueError):
        return []

    def credible_margin(odds: list[float]) -> bool:
        # Real bookmaker alternatives should belong to one coherent two-
        # or three-sided market. Arbitrage-like (<100%) or extreme (>125%)
        # paired overround often means stale or mismapped selections.
        vig = sum(1.0 / value for value in odds)
        return 0.995 <= vig <= 1.25

    out: list[PrematchPick] = []
    seen: set[tuple[str, str]] = set()

    def append(market: str, selection: str, odd: float, fair: float) -> None:
        if (not math.isfinite(fair) or not 0 < fair < 1
                or odd < max(1.30, min_odd) or not math.isfinite(odd)):
            return
        key = market, selection
        if key in seen:
            return
        seen.add(key)
        # Reserved sides use bookmaker-fair as an explicit market estimate,
        # never claiming an independent model-predicted positive edge.
        out.append(PrematchPick(
            str(event_id), str(home), str(away), market, selection,
            odd, fair, fair, .60, str(league), kick,
        ))

    for market, family in (("match_total", "match_totals"),
                           ("home_total", "home_totals"),
                           ("away_total", "away_totals")):
        family_rows = [row for row in quote.get(family) or [] if isinstance(row, dict)]
        # For a single bookmaker full-time quote, more goals must never make
        # OVER cheaper or UNDER dearer. If its totals ladder is incoherent,
        # exclude the entire family rather than publishing a swapped line.
        ladder = []
        for item in family_rows:
            try:
                line = float(item["line"])
            except (TypeError, ValueError, KeyError):
                continue
            o, u = _odd(item.get("over")), _odd(item.get("under"))
            if o is not None and u is not None and o > 1 and u > 1:
                ladder.append((line, o, u))
        ladder.sort()
        if any(
            high[1] + .01 < low[1] or high[2] > low[2] + .01
            for low, high in zip(ladder, ladder[1:]) if high[0] > low[0]
        ):
            continue
        for row in family_rows:
            if not isinstance(row, dict):
                continue
            try:
                line = float(row["line"])
            except (TypeError, KeyError, ValueError):
                continue
            # No whole/quarter Asian totals without push/half settlement.
            if not math.isfinite(line) or line < .5 or abs((line % 1.0) - .5) > 1e-8:
                continue
            over, under = _odd(row.get("over")), _odd(row.get("under"))
            if over is None or under is None or min(over, under) <= 1:
                continue
            if not credible_margin([over, under]):
                continue
            fo, fu = devig_two_way(over, under)
            append(market, f"over {line:g}", over, fo)
            append(market, f"under {line:g}", under, fu)

    three = quote.get("match_1x2") or {}
    if isinstance(three, dict):
        odds = [_odd(three.get(k)) for k in ("home", "draw", "away")]
        if all(x is not None and x > 1 for x in odds) and credible_margin(odds):
            fairs = devig_three_way(*odds)
            for sel, odd, fair in zip(("home", "draw", "away"), odds, fairs):
                append("match_1x2", sel, odd, fair)

    btts = quote.get("btts") or {}
    if isinstance(btts, dict):
        yes, no = _odd(btts.get("yes")), _odd(btts.get("no"))
        if yes is not None and no is not None and min(yes, no) > 1 and credible_margin([yes, no]):
            fy, fn = devig_two_way(yes, no)
            append("btts", "yes", yes, fy)
            append("btts", "no", no, fn)

    # A model-rejected reserve should favour the highest implied chance of
    # success, NOT the largest accumulator headline price.
    out.sort(key=lambda p: (-p.model_probability, p.odds, p.market, p.selection))
    return out
