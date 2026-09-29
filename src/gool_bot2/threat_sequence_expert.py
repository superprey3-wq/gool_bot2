from __future__ import annotations

from typing import Any


def _num(v: Any) -> float | None:
    try:
        if v in (None, "", "-"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _last_valid(rows: list[dict[str, Any]], key: str) -> float | None:
    for row in reversed(rows):
        v = _num(row.get(key))
        if v is not None:
            return v
    return None


def _delta(rows: list[dict[str, Any]], key: str) -> float | None:
    vals=[_num(r.get(key)) for r in rows]
    vals=[v for v in vals if v is not None]
    if len(vals)<2:
        return None
    return max(0.0, vals[-1]-vals[0])


def evaluate_threat_sequence(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Score short-horizon goal threat from sequential cumulative LIVE snapshots.

    Diagnostic only. Uses changes, not raw cumulative totals, and keeps market
    confirmation separate from football evidence.
    """
    if len(history) < 3:
        return {"available": False, "state": "NO_DATA", "score": 0.0, "reason": "need_3_snapshots"}

    rows=history[-8:]
    score_epoch=tuple(rows[-1].get("score") or ())
    rows=[r for r in rows if tuple(r.get("score") or ())==score_epoch]
    if len(rows)<3:
        return {"available": False, "state": "RESET", "score": 0.0, "reason": "score_epoch_changed"}

    xg=_delta(rows,"xg_total")
    sot=_delta(rows,"sot_total")
    shots=_delta(rows,"shots_total")
    danger=_delta(rows,"danger_total")
    corners=_delta(rows,"corners_total")
    prob0=_num(rows[0].get("over_prob"))
    prob1=_last_valid(rows,"over_prob")
    market_pp=None if prob0 is None or prob1 is None else (prob1-prob0)*100.0

    # Acceleration: second half of the sequence versus first half.
    mid=max(1,len(rows)//2)
    a=rows[:mid+1]
    b=rows[mid:]
    xg_a=_delta(a,"xg_total") or 0.0
    xg_b=_delta(b,"xg_total") or 0.0
    sot_a=_delta(a,"sot_total") or 0.0
    sot_b=_delta(b,"sot_total") or 0.0
    danger_a=_delta(a,"danger_total") or 0.0
    danger_b=_delta(b,"danger_total") or 0.0

    football=0.0
    football += min(0.34, (xg or 0.0)/0.70*0.34)
    football += min(0.22, (sot or 0.0)/4.0*0.22)
    football += min(0.14, (shots or 0.0)/8.0*0.14)
    football += min(0.14, (danger or 0.0)/18.0*0.14)
    football += min(0.06, (corners or 0.0)/4.0*0.06)

    acceleration=0.0
    if xg_b > xg_a + 0.08: acceleration += 0.04
    if sot_b > sot_a: acceleration += 0.025
    if danger_b > danger_a + 2: acceleration += 0.025

    market=0.0
    if market_pp is not None:
        market=min(0.10,max(-0.05,market_pp/6.0*0.10))

    total=max(0.0,min(1.0,football+acceleration+market))
    if total>=0.72:
        state="SURGE"
    elif total>=0.52:
        state="BUILDING"
    elif total>=0.32:
        state="WARM"
    else:
        state="QUIET"

    divergence="NONE"
    if football>=0.50 and (market_pp is not None and market_pp<=0.0):
        divergence="FOOTBALL_AHEAD_OF_MARKET"
    elif football<0.25 and (market_pp is not None and market_pp>=1.5):
        divergence="MARKET_AHEAD_OF_FOOTBALL"

    return {
        "available": True,
        "state": state,
        "score": round(total,4),
        "football_score": round(football+acceleration,4),
        "market_delta_pp": None if market_pp is None else round(market_pp,3),
        "divergence": divergence,
        "deltas": {
            "xg": None if xg is None else round(xg,3),
            "sot": None if sot is None else round(sot,3),
            "shots": None if shots is None else round(shots,3),
            "dangerous_attacks": None if danger is None else round(danger,3),
            "corners": None if corners is None else round(corners,3),
        },
        "acceleration": {
            "xg_first": round(xg_a,3),"xg_second": round(xg_b,3),
            "sot_first": round(sot_a,3),"sot_second": round(sot_b,3),
            "danger_first": round(danger_a,3),"danger_second": round(danger_b,3),
        },
        "score_epoch": list(score_epoch),
        "points": len(rows),
    }


__all__=["evaluate_threat_sequence"]
