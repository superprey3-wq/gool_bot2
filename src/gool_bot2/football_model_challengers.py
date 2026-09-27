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


def choose_challenger_delivery(shortlist: list[dict[str, Any]], max_legs: int = 4) -> dict[str, Any] | None:
    """Choose SINGLE or 2..N leg ACCA without forcing extra legs.

    Shadow-only. Confidence gates get stricter as legs are added so a strong
    single is preferred to a weaker accumulator assembled just for higher odds.
    """
    if not shortlist:
        return None
    strong = [r for r in shortlist if r["quality"] >= .70 and r["edge"] >= .04 and r["expected_value"] >= .04]
    singles = [r for r in strong if 1.70 <= r["odds"] <= 3.25 and r["probability"] >= .62]
    best_single = max(singles, key=lambda r:(r["probability"], r["expected_value"], r["edge"]), default=None)

    import itertools
    tickets = []
    for n in range(2, min(max_legs, len(strong)) + 1):
        min_leg_p = .67 if n == 2 else (.70 if n == 3 else .72)
        min_combined_p = .44 if n == 2 else (.32 if n == 3 else .24)
        for legs in itertools.combinations(strong, n):
            if len({r["event_id"] for r in legs}) != n or any(r["probability"] < min_leg_p for r in legs):
                continue
            odds = math.prod(r["odds"] for r in legs)
            probability = math.prod(r["probability"] for r in legs)
            if odds > 8.0 or probability < min_combined_p:
                continue
            tickets.append({"type": f"ACCA_{n}", "legs": list(legs), "combined_odds": odds,
                            "combined_probability": probability, "expected_value": probability * odds - 1.0})

    # Confidence first, then value. Do not prefer more legs merely for a bigger price.
    best_acca = max(tickets, key=lambda t:(t["combined_probability"], t["expected_value"]), default=None)
    if best_single is None and best_acca is None:
        return None
    if best_acca is None:
        return {"type":"SINGLE","legs":[best_single],"combined_odds":best_single["odds"],
                "combined_probability":best_single["probability"],"expected_value":best_single["expected_value"]}
    if best_single is None:
        return best_acca
    # A multi must retain substantial confidence to displace a very strong single.
    if best_acca["combined_probability"] >= .48 and best_acca["expected_value"] >= best_single["expected_value"] * 1.15:
        return best_acca
    return {"type":"SINGLE","legs":[best_single],"combined_odds":best_single["odds"],
            "combined_probability":best_single["probability"],"expected_value":best_single["expected_value"]}


def dixon_coles_profile_challenger(profile: dict[str, Any], rho: float = -0.08) -> ChallengerForecast | None:
    """Low-score corrected Poisson challenger.

    rho is a configurable shadow hyperparameter, not presented as fitted until
    chronological outcome data is available for estimation.
    """
    base = poisson_profile_challenger(profile)
    if base is None:
        return None
    hl, al = base.home_lambda, base.away_lambda
    max_goals = 10
    hp = [math.exp(-hl) * hl ** k / math.factorial(k) for k in range(max_goals + 1)]
    ap = [math.exp(-al) * al ** k / math.factorial(k) for k in range(max_goals + 1)]
    h = d = a = 0.0
    for hg, ph in enumerate(hp):
        for ag, pa in enumerate(ap):
            tau = 1.0
            if hg == 0 and ag == 0: tau = 1.0 - hl * al * rho
            elif hg == 0 and ag == 1: tau = 1.0 + hl * rho
            elif hg == 1 and ag == 0: tau = 1.0 + al * rho
            elif hg == 1 and ag == 1: tau = 1.0 - rho
            p = max(0.0, ph * pa * tau)
            if hg > ag: h += p
            elif hg == ag: d += p
            else: a += p
    mass = h+d+a
    if mass <= 0: return None
    return ChallengerForecast("dixon_coles_profile_v1", hl, al, h/mass, d/mass, a/mass)


def model_market_candidates(forecast: ChallengerForecast, *, event_id: str, home: str, away: str,
                            market: dict[str, Any], data_quality: float = 1.0) -> list[dict[str, Any]]:
    out=[]
    x=market.get("match_1x2") or {}
    for selection,p in {"home":forecast.home,"draw":forecast.draw,"away":forecast.away}.items():
        try: odds=float(x[selection])
        except (KeyError,TypeError,ValueError): continue
        if odds>1:
            out.append({"event_id":event_id,"home":home,"away":away,"market":"match_1x2","selection":selection,
                        "odds":odds,"probability":p,"market_probability":1/odds,"quality":data_quality,"model":forecast.name})
    for row in market.get("match_totals") or []:
        try: line,over,under=float(row["line"]),float(row["over"]),float(row["under"])
        except (KeyError,TypeError,ValueError): continue
        if min(over,under)<=1: continue
        ro,ru=1/over,1/under; z=ro+ru
        po=total_probability(forecast,line,True)
        for selection,odds,p,fair in ((f"over {line:g}",over,po,ro/z),(f"under {line:g}",under,1-po,ru/z)):
            out.append({"event_id":event_id,"home":home,"away":away,"market":"match_total","selection":selection,
                        "odds":odds,"probability":p,"market_probability":fair,"quality":data_quality,"model":forecast.name})
    return out


def consensus_candidates(model_rows: list[list[dict[str, Any]]], min_models: int = 2) -> list[dict[str, Any]]:
    """Aggregate only selections independently supported by >= min_models."""
    grouped={}
    for rows in model_rows:
        seen=set()
        for r in rows:
            key=(str(r["event_id"]),r["market"],r["selection"])
            if key in seen: continue
            seen.add(key); grouped.setdefault(key,[]).append(r)
    out=[]
    for rows in grouped.values():
        if len(rows)<min_models: continue
        p=sum(float(r["probability"]) for r in rows)/len(rows)
        base=rows[0]; market_p=float(base["market_probability"]); odds=float(base["odds"])
        out.append({**base,"probability":p,"edge":p-market_p,"expected_value":p*odds-1,
                    "model":"consensus","model_count":len(rows),
                    "models":[r["model"] for r in rows]})
    return out
