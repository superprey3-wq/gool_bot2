from __future__ import annotations

from typing import Any


def _num(v: Any) -> float | None:
    try:
        if v in (None, "", "-"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_live_threat(record: dict[str, Any]) -> dict[str, Any]:
    momentum = record.get("live_momentum") or {}
    minutes = _num(momentum.get("minutes_in_epoch")) or 0.0
    if minutes < 3.0:
        return {"available": False, "state": "NO_DATA", "modifier": 0.0, "reason": "short_epoch"}

    xg5 = _num(momentum.get("xg_total_last_5m"))
    sot5 = _num(momentum.get("sot_total_last_5m"))
    shots5 = _num(momentum.get("shots_total_last_5m"))
    big5 = _num(momentum.get("big_total_last_5m"))
    xg10 = _num(momentum.get("xg_total_last_10m"))
    sot10 = _num(momentum.get("sot_total_last_10m"))
    shots10 = _num(momentum.get("shots_total_last_10m"))

    values = (xg5, sot5, shots5, big5, xg10, sot10, shots10)
    if all(v is None for v in values):
        return {"available": False, "state": "NO_DATA", "modifier": 0.0, "reason": "momentum_missing"}

    score = 0.0
    score += min(0.38, max(0.0, xg5 or 0.0) / 0.55 * 0.38)
    score += min(0.22, max(0.0, sot5 or 0.0) / 3.0 * 0.22)
    score += min(0.15, max(0.0, shots5 or 0.0) / 6.0 * 0.15)
    score += min(0.15, max(0.0, big5 or 0.0) / 2.0 * 0.15)

    # Reward acceleration: most of the 10m pressure happened in the latest 5m.
    acceleration = 0.0
    if xg5 is not None and xg10 is not None and xg10 > 0 and xg5 >= max(0.14, 0.62 * xg10):
        acceleration += 0.05
    if sot5 is not None and sot10 is not None and sot10 > 0 and sot5 >= max(1.0, 0.60 * sot10):
        acceleration += 0.03
    if shots5 is not None and shots10 is not None and shots10 > 0 and shots5 >= max(2.0, 0.60 * shots10):
        acceleration += 0.02
    score = min(1.0, score + acceleration)

    if score >= 0.72:
        state, modifier = "SURGE", 6.0
    elif score >= 0.52:
        state, modifier = "BUILDING", 3.5
    elif score >= 0.30:
        state, modifier = "WARM", 1.5
    else:
        state, modifier = "QUIET", -3.0

    return {
        "available": True,
        "state": state,
        "score": round(score, 4),
        "modifier": modifier,
        "minutes_in_epoch": round(minutes, 2),
        "xg5": xg5,
        "sot5": sot5,
        "shots5": shots5,
        "big5": big5,
        "acceleration": round(acceleration, 3),
    }


def apply_threat_modifier(decision: Any, record: dict[str, Any]) -> Any:
    """Modify only an already selected GOOL BET; never manufacture a signal."""
    if getattr(decision, "status", "") != "BET" or getattr(decision, "winner", None) is None:
        return decision

    threat = evaluate_live_threat(record)
    record["threat_sequence"] = threat
    if not threat.get("available"):
        return decision

    winner = decision.winner
    before = float(getattr(winner, "rating", 0.0) or 0.0)
    modifier = float(threat.get("modifier") or 0.0)
    after = max(0.0, min(100.0, before + modifier))
    winner.rating = round(after, 1)

    state = str(threat.get("state") or "NO_DATA")
    winner.reason_tags.append(f"threat_{state.lower()}")
    decision.reason = f"{decision.reason} · Threat {state} {modifier:+.1f}"

    # QUIET is only a soft veto for borderline selections.
    if state == "QUIET" and after < 62.0:
        winner.blocks.append("threat_sequence_borderline_veto")
        winner.eligible = False
        decision.rejected.insert(0, winner)
        decision.winner = None
        decision.status = "WAIT"
        decision.reason = f"Threat QUIET ослабил пограничный LIVE-сигнал: {before:.1f}→{after:.1f}."
    return decision


__all__ = ["evaluate_live_threat", "apply_threat_modifier"]
