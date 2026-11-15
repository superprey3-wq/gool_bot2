"""PROGRUZ 1: optional 3-brain LIVE decision gate.

Brain A: existing verified Flashscore + paired Fonbet LIVE market selection.
Brain B: independent real-data game-state scenarios.
Brain C: Poisson remaining-goal estimate (uncalibrated hypothesis).

JOINT is an experimental veto of Brain A, NOT a trained classifier nor
evidence of profitable betting. SHADOW measures decisions without veto;
OFF uses the legacy selector. No extra network requests, no auto bets.
"""
from __future__ import annotations

import math

from progruz1_scenarios import APPROVED_SCENARIOS


def _prob(value):
    try:
        v=float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return v if math.isfinite(v) and 0 < v < 1 else None


def joint_decision(*, scenario, model, odd, mode="JOINT"):
    """Return a documented approval/rejection for the old valid candidate.

    Compare the independent, UNCALIBRATED goal estimate to a de-vigged
    market baseline only to flag severe disagreement, not as an edge.
    Hypotheses may fail on future data; control mode is always reversible.
    """
    mode=str(mode).upper()
    if mode not in {"JOINT","SHADOW","OFF"}:
        raise ValueError("GOOL_NEXT_GOAL_DECISION_MODE must be JOINT, SHADOW or OFF")
    try:
        odd=float(odd)
    except (ValueError, TypeError):
        return {"approved":False,"reason":"JOINT_ODD_INVALID","mode":mode,
                "p_model":None,"p_market":None,"p_break_even":None,
                "threshold":None}
    break_even=1/odd if math.isfinite(odd) and odd>1 else None
    p_model=_prob((model or {}).get("estimated_goal_probability")
                  if isinstance(model,dict) else None)
    p_market=_prob((model or {}).get("market_fair_over_probability")
                   if isinstance(model,dict) else None)
    risks=list(scenario.get("risk_flags",[])) if isinstance(scenario,dict) else []
    result={"approved":True,"reason":"JOINT_AGREE","mode":mode,
            "scenario_id":str((scenario or {}).get("id") or "")
                if isinstance(scenario,dict) else "",
            "risk_flags":risks,
            "p_model":p_model,"p_market":p_market,
            "p_break_even":break_even,"threshold":None,
            "not_calibrated":True}
    if mode=="OFF":
        result.update(reason="JOINT_OFF")
        return result
    # An uncalibrated Poisson estimate CANNOT be required to beat the
    # bookmaker. That old requirement virtually silenced the goal scanner:
    # a 1.50 quote demanded model P >67.7%, regardless of sample quality.
    # A live model is used as a negative-risk cross-check, not a fabricated
    # proof of positive EV. Missing optional research data stays visible
    # in the audit but does not kill an otherwise verified goal alert.
    if scenario is None or not isinstance(scenario,dict):
        result.update(approved=False,reason="JOINT_SCENARIO_UNAVAILABLE")
    elif risks:
        result.update(approved=False,reason=(
            "JOINT_NO_APPROVED_SCENARIO"
            if "NO_APPROVED_SCENARIO" in risks
            else "JOINT_SCENARIO_RISK"))
    elif str(scenario.get("id") or "") not in APPROVED_SCENARIOS:
        result.update(approved=False,reason="JOINT_SCENARIO_REMOVED")
    elif (p_model is None or break_even is None or p_market is None
          or model.get("status")!="UNCALIBRATED_SHADOW"):
        result.update(reason="JOINT_MODEL_UNAVAILABLE_PASS")
    else:
        # Only a *large* model-versus-market discrepancy triggers a veto.
        # The stronger requirement for odds >=1.80 reflects 07.11 risk
        # observations; it is a research hypothesis, not trained ROI.
        ratio=0.75 if odd>=1.80 else 0.65
        min_gap=0.12 if odd>=1.80 else 0.15
        threshold=p_market*ratio
        result["threshold"]=round(threshold,5)
        result["model_market_gap"]=round(p_market-p_model,5)
        if p_model < threshold and p_market-p_model>=min_gap:
            result.update(approved=False,
                          reason="JOINT_MODEL_STRONG_CONTRADICTION")
        elif p_model < p_market:
            result.update(reason="JOINT_MODEL_SOFT_DISAGREEMENT_PASS")
    if mode=="SHADOW":
        result["would_approve"]=result["approved"]
        result["would_reason"]=result["reason"]
        result.update(approved=True,reason="JOINT_SHADOW_ONLY")
    return result
