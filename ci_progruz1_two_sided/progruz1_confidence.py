"""Auditable 0-100 next-half-goal *situation strength* (NOT probability).

Independent Flashscore evidence only: clock, full live shots/SoT/xG, 5-min
momentum, and two-team historical goal form. No bookmaker-derived likelihood,
unverified model posterior or odds are used to manufacture confidence.
"""
from __future__ import annotations

import math


def _bounded(v, minimum=0.0, maximum=1.0):
    try:
        n=float(v)
        if not math.isfinite(n):
            return 0.0
        return min(maximum,max(minimum,n))
    except (TypeError,ValueError,OverflowError):
        return 0.0


def confidence_score(*,minute,shots,sot,xg,recent_shots,recent_sot,
                     history,score,period_score,second_half_started=False):
    """Return stable score and explainable factor components.

    The scale ranks comparable situations; it is NOT P(goal) and must not be
    called an 80% hit rate. Zero missing xG gets zero xG points, never an
    imputed positive value. More time or confirmed attacks cannot lower
    the base component scores.
    """
    minute=int(minute)
    left=max(0,(90-minute if second_half_started or minute>=46
                else 45-minute))
    shots_total=sum(shots)
    sot_total=sum(sot)
    xg_total=(sum(xg) if isinstance(xg,(tuple,list)) and len(xg)==2
              else None)
    avg=_bounded((history or {}).get("avg_total"),0,10)
    # Complete maximum 100 pts, 6 independent components:
    # opportunity/time 20; shots 18; SoT 16; xG 14; recent pressure 16;
    # verified two-team goal form 10; both teams attack 6.
    parts={
        "time":round(20*_bounded(left/22),1),
        "shots":round(18*_bounded(shots_total/12),1),
        "sot":round(16*_bounded(sot_total/5),1),
        "xg":round(14*_bounded(
            (xg_total/1.7) if xg_total is not None else 0),1),
        "recent":round(
            8*_bounded(recent_shots/5)+
            8*_bounded(recent_sot/3),1),
        "teams":round(10*_bounded((avg-1.7)/1.7),1),
        "two_sided":round(
            6 if (min(shots)>=3 and min(sot)>=1)
            else 3 if min(shots)>=2 else 0,1),
    }
    # Small, transparent context caution: not all shot activity predicts
    # near-term scoring when neither side has scored in a fading 1H.
    adjustment=0
    if not second_half_started and minute<=45 and minute>=28 and sum(period_score)==0:
        adjustment-=4
    if minute>=65 and abs(score[0]-score[1])>=2:
        adjustment-=5
    score_pts=max(0,min(100,round(sum(parts.values())+adjustment)))
    label=("высокая" if score_pts>=82 else
           "хорошая" if score_pts>=68 else
           "умеренная" if score_pts>=55 else "низкая")
    return {
        "score":score_pts,"label":label,
        "factors":parts,"adjustment":adjustment,
        "xg_available":xg_total is not None,
        "minutes_remaining_regular":left,
        "meaning":"рейтинг ситуации, НЕ вероятность гола",
    }


def minimum_confidence(odd):
    # Require stronger confirmed context to justify a higher-variance price,
    # without pretending to compute bookmaker edge or fair probability.
    return 74 if float(odd)>=2.50 else 68
