from __future__ import annotations

"""Shadow-only football model challengers.

These models never select or deliver a bet. They provide independent,
transparent probability estimates that can be logged beside the production
GOOL Brain and evaluated chronologically before any production influence.
"""

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChallengerForecast:
    name: str
    home_lambda: float
    away_lambda: float
    home: float
    draw: float
    away: float

    @property
    def total_lambda(self) -> float:
        return self.home_lambda + self.away_lambda


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _poisson_1x2(home_lambda: float, away_lambda: float, max_goals: int = 10) -> tuple[float, float, float]:
    hp = [math.exp(-home_lambda) * home_lambda ** k / math.factorial(k) for k in range(max_goals + 1)]
    ap = [math.exp(-away_lambda) * away_lambda ** k / math.factorial(k) for k in range(max_goals + 1)]
    h = d = a = 0.0
    for hg, ph in enumerate(hp):
        for ag, pa in enumerate(ap):
            p = ph * pa
            if hg > ag:
                h += p
            elif hg == ag:
                d += p
            else:
                a += p
    mass = h + d + a
    return (h / mass, d / mass, a / mass) if mass else (1 / 3, 1 / 3, 1 / 3)


def poisson_profile_challenger(profile: dict[str, Any]) -> ChallengerForecast | None:
    """Independent Poisson baseline using GOOL's already-collected history.

    It deliberately consumes only pre-kickoff profile values, making it safe for
    chronological shadow evaluation and avoiding leakage from bookmaker odds.
    """
    full = profile.get("full_match") or {}
    hl = _num(full.get("home_expected_goals"))
    al = _num(full.get("away_expected_goals"))
    if hl is None or al is None or hl <= 0 or al <= 0:
        first = profile.get("first_half") or {}
        second = profile.get("second_half") or {}
        h1, a1 = _num(first.get("home_expected_goals")), _num(first.get("away_expected_goals"))
        h2, a2 = _num(second.get("home_expected_goals")), _num(second.get("away_expected_goals"))
        if None in (h1, a1, h2, a2):
            return None
        hl, al = float(h1 + h2), float(a1 + a2)
    # Keep sparse/noisy histories from creating absurd tails.
    hl = max(0.15, min(4.5, float(hl)))
    al = max(0.15, min(4.5, float(al)))
    h, d, a = _poisson_1x2(hl, al)
    return ChallengerForecast("poisson_profile_v1", hl, al, h, d, a)


def total_probability(forecast: ChallengerForecast, line: float, over: bool = True) -> float:
    lam = forecast.total_lambda
    threshold = int(math.floor(float(line))) + 1
    p_over = 1.0 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(threshold))
    return p_over if over else 1.0 - p_over


def challenger_market_candidates(*, event_id: str, home: str, away: str, profile: dict[str, Any],
                                 market: dict[str, Any], data_quality: float = 1.0) -> list[dict[str, Any]]:
    """Price independent challenger probabilities against the same bookmaker snapshot."""
    forecast = poisson_profile_challenger(profile)
    if forecast is None:
        return []
    out: list[dict[str, Any]] = []
    x = market.get("match_1x2") or {}
    probs = {"home": forecast.home, "draw": forecast.draw, "away": forecast.away}
    for selection, probability in probs.items():
        try:
            odds = float(x[selection])
        except (KeyError, TypeError, ValueError):
            continue
        if odds <= 1:
            continue
        out.append({"event_id": event_id, "home": home, "away": away, "market": "match_1x2",
                    "selection": selection, "odds": odds, "probability": probability,
                    "market_probability": 1.0 / odds, "quality": data_quality, "model": forecast.name})
    for row in market.get("match_totals") or []:
        try:
            line, over, under = float(row["line"]), float(row["over"]), float(row["under"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(over, under) <= 1:
            continue
        raw_o, raw_u = 1.0 / over, 1.0 / under
        margin = raw_o + raw_u
        fair_o, fair_u = raw_o / margin, raw_u / margin
        po = total_probability(forecast, line, True)
        for selection, odds, probability, fair in (
            (f"over {line:g}", over, po, fair_o), (f"under {line:g}", under, 1.0-po, fair_u)
        ):
            out.append({"event_id": event_id, "home": home, "away": away, "market": "match_total",
                        "selection": selection, "odds": odds, "probability": probability,
                        "market_probability": fair, "quality": data_quality, "model": forecast.name})
    return out


def rank_challenger(candidates: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    rows = []
    for row in candidates:
        p, odds, market_p, q = float(row["probability"]), float(row["odds"]), float(row["market_probability"]), float(row["quality"])
        edge, ev = p-market_p, p*odds-1.0
        if 1.50 <= odds <= 3.25 and q >= .55 and p >= .60 and edge >= .025 and ev >= .01:
            rows.append({**row, "edge": edge, "expected_value": ev})
    rows.sort(key=lambda r:(r["expected_value"],r["edge"],r["probability"],r["quality"]),reverse=True)
    out=[]; seen=set()
    for row in rows:
        if row["event_id"] in seen: continue
        seen.add(row["event_id"]); out.append(row)
        if len(out)>=limit: break
    return out


def build_challenger_double(shortlist: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible=[r for r in shortlist if r["probability"] >= .64 and r["quality"] >= .60 and r["edge"] >= .04]
    best=None
    for i,a in enumerate(eligible):
        for b in eligible[i+1:]:
            if a["event_id"] == b["event_id"]: continue
            odds=a["odds"]*b["odds"]; probability=a["probability"]*b["probability"]
            if not 1.70 <= odds <= 4.00 or probability < .42: continue
            row={"legs":[a,b],"combined_odds":odds,"combined_probability":probability,
                 "expected_value":probability*odds-1.0}
            if best is None or (row["expected_value"],probability) > (best["expected_value"],best["combined_probability"]):
                best=row
    return best
