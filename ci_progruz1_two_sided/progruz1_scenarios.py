"""PROGRUZ 1: three goal-pressure scenarios based on actual LIVE evidence.

TWO_SIDED now requires verified fresh pressure from BOTH teams.
The old GOALLESS_PRESSURE is retired.
TRAILING_CHASE needs verified team-specific shot momentum; QUALITY_SURGE
needs verified 8-10 minute xG improvement, not cumulative second-half xG.
All thresholds are experimental hypotheses, not validated profitable signals.
"""
from __future__ import annotations

import math


APPROVED_SCENARIOS = frozenset({"TWO_SIDED", "TRAILING_CHASE", "QUALITY_SURGE"})


def _verified_pair(value, *, integer=False):
    """Reject missing, corrected or fabricated counter snapshots."""
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    if integer:
        if not all(type(v) is int and v >= 0 for v in value):
            return None
        return tuple(value)
    try:
        pair = tuple(float(v) for v in value)
    except (ValueError, TypeError, OverflowError):
        return None
    return pair if all(math.isfinite(v) and v >= 0 for v in pair) else None


def _two_sided_freshness_guard(*, minute, second_half, total,
                               shots10, sot10, shots5, sot5, xg10,
                               opening_shots, opening_sot):
    """Evidence-based verification of *both* teams' recent attacks.

    Unknown shot history is never interpreted as quiet play. Historical
    FULL-MATCH shots and aggregate recent SOT alone cannot verify a live
    two-sided pattern. Thresholds are conservative prospective hypotheses,
    not a calibrated expected-value model.
    """
    if shots10 is None or sot10 is None:
        # In minutes 1-10 / 45-55 an 8-10m observation may not yet
        # exist. Require a REAL shorter same-half delta for each club;
        # never manufacture a ten-minute history at 1'.
        opening=(not second_half and minute<=10
                 or second_half and minute<=55)
        if opening and opening_shots is not None and opening_sot is not None:
            if (min(opening_shots)>=1 and min(opening_sot)>=1
                    and sum(opening_shots)>=4):
                return None
            return "TWO_SIDED_OPENING_ONE_SIDE_UNCONFIRMED"
        return "TWO_SIDED_RECENT_TEAM_STATS_UNVERIFIED"
    if min(shots10) < 2 or min(sot10) < 1 or sum(shots10) < 5:
        return "TWO_SIDED_NOT_BOTH_TEAMS_ATTACKING_10M"
    # At 65' or later and after 30' of a goalless first half,
    # pressure from 8-10 minutes ago is not enough: the game must
    # still be active for BOTH clubs over the last observed ~5 min.
    time_sensitive=(second_half and minute>=65
                    or not second_half and minute>=30 and total==0)
    if time_sensitive:
        if shots5 is None or sot5 is None:
            return "TWO_SIDED_RECENT_5M_TEAMS_UNVERIFIED"
        if min(shots5)<1:
            return "TWO_SIDED_RECENT_5M_ONE_TEAM_QUIET"
        if sum(sot5)<1:
            return "TWO_SIDED_RECENT_5M_NO_SOT"
    if second_half and minute >= 73:
        # Late goals need stronger *current* activity on each side;
        # accumulated 70-minute domination does not provide it.
        if (sum(shots10) < 6 or sum(sot10) < 3 or
                (shots5 is not None and sum(shots5)<3) or
                (sot5 is not None and sum(sot5)<2)):
            return "TWO_SIDED_LATE_ATTACK_PRESSURE_WEAK"
        if total == 0:
            if xg10 is not None and sum(xg10) < 0.50:
                return "TWO_SIDED_LATE_00_CHANCE_QUALITY_WEAK"
            if xg10 is None and sum(sot10) < 4:
                return "TWO_SIDED_LATE_00_NO_XG_NEEDS_4_SOT"
    if not second_half and minute >= 30 and total == 0:
        # 34' / 0:0 leaves little 1H time. Require a real stronger pulse
        # rather than the match's whole 1H shot tally.
        if sum(shots10) < 6 or sum(sot10) < 3:
            return "TWO_SIDED_LATE_1H_ATTACK_PRESSURE_WEAK"
        if xg10 is not None and sum(xg10) < 0.30:
            return "TWO_SIDED_LATE_1H_CHANCE_QUALITY_WEAK"
    return None


def classify_scenario(*, minute: int, score: tuple[int, int],
                      second_half: bool, shots: tuple[float, float],
                      sot: tuple[float, float], recent_shots: int,
                      recent_sot: int, odd: float, xg_available: bool,
                      corner_pressure: dict | None = None,
                      avg_total: float | None = None,
                      recent_10m_shots_by_team: tuple[int, int] | None = None,
                      recent_10m_age: int = 0,
                      recent_10m_sot_by_team: tuple[int, int] | None = None,
                      recent_5m_shots_by_team: tuple[int, int] | None = None,
                      recent_5m_sot_by_team: tuple[int, int] | None = None,
                      recent_5m_age: int = 0,
                      opening_shots_by_team: tuple[int, int] | None = None,
                      opening_sot_by_team: tuple[int, int] | None = None,
                      opening_sample_age: int = 0,
                      recent_10m_xg_by_team: tuple[float, float] | None = None,
                      recent_10m_xg_age: int = 0,
                      first_half_score: tuple[int, int] | None = None,
                      first_half_sot_total: int | None = None,
                      first_half_xg_total: float | None = None,
                      second_half_sot_by_team: tuple[int, int] | None = None,
                      second_half_xg_delta: tuple[float, float] | None = None,
                      prematch_odds: dict | None = None,
                      first_goal_side: str | None = None,
                      red_cards: tuple[int, int] | None = None) -> dict:
    """Classify verified pre-signal Flashscore samples into three scenarios.

    Team-specific 8-10 minute changes are required for the new scenarios.
    Missing xG, missing reds and absent team snapshots never become zeros.
    The scorer/market matching and base signal quality rules remain upstream.
    """
    minute = int(minute)
    home, away = int(score[0]), int(score[1])
    gap = abs(home - away)
    total = home + away
    shots = tuple(float(v) for v in shots)
    sot = tuple(float(v) for v in sot)
    recent_shots = int(recent_shots)
    recent_sot = int(recent_sot)
    risk = []
    notes = []

    # Retain the existing narrow, verified state cautions.
    if second_half and minute >= 60 and gap >= 2:
        risk.append("LATE_COMFORTABLE_LEAD")
    if not second_half and minute >= 28 and total == 0 and recent_sot <= 1:
        risk.append("LATE_1H_GOALLESS_WEAK_SOT")
    if second_half and minute >= 68 and total == 0 and recent_sot <= 1:
        risk.append("LATE_2H_GOALLESS_WEAK_SOT")

    # 0:0: do not block every low-total team or every weak snapshot.
    # Wait for the next verified burst only when BOTH risks coincide.
    if total == 0 and avg_total is not None:
        try:
            if float(avg_total) < 2.5 and recent_shots < 4:
                risk.append("GOALLESS_LOW_FORM_WEAK_PULSE_WAIT")
        except (TypeError, ValueError, OverflowError):
            pass

    # One-goal lead from 65' with >=4 shots separating the teams:
    # verify whether the TRAILING side still attacks. A low observed
    # 8-10m pulse means wait, not exclude the fixture permanently.
    trailing = 0 if home < away else 1 if away < home else None
    if (gap == 1 and minute >= 65 and
            abs(shots[0] - shots[1]) >= 4 and trailing is not None):
        verified = (isinstance(recent_10m_shots_by_team, (tuple, list))
                    and len(recent_10m_shots_by_team) == 2
                    and isinstance(recent_10m_age, int)
                    and 480 <= recent_10m_age <= 600
                    and all(type(v) is int and v >= 0
                            for v in recent_10m_shots_by_team))
        if verified:
            if recent_10m_shots_by_team[trailing] < 2:
                risk.append("TRAILING_TEAM_10M_SHOTS_WEAK_WAIT")
            else:
                notes.append("TRAILING_TEAM_10M_ACTIVE")
        else:
            # Not enough valid snapshots -> do not invent a quiet team.
            notes.append("TRAILING_TEAM_10M_UNKNOWN")

    shots10 = (_verified_pair(recent_10m_shots_by_team, integer=True)
               if type(recent_10m_age) is int
               and 480 <= recent_10m_age <= 600 else None)
    sot10 = (_verified_pair(recent_10m_sot_by_team, integer=True)
             if shots10 is not None else None)
    opening_shots = (_verified_pair(opening_shots_by_team, integer=True)
                     if type(opening_sample_age) is int
                     and 30<=opening_sample_age<=600 else None)
    opening_sot = (_verified_pair(opening_sot_by_team, integer=True)
                   if opening_shots is not None else None)
    if opening_shots is not None and opening_sot is not None and any(
            opening_sot[i]>opening_shots[i] for i in (0,1)):
        opening_shots,opening_sot=None,None
    shots5 = (_verified_pair(recent_5m_shots_by_team, integer=True)
              if type(recent_5m_age) is int
              and 240 <= recent_5m_age <= 360 else None)
    sot5 = (_verified_pair(recent_5m_sot_by_team, integer=True)
            if shots5 is not None else None)
    if shots5 is not None and sot5 is not None and any(
            sot5[i]>shots5[i] for i in (0, 1)):
        shots5,sot5=None,None
    xg10 = (_verified_pair(recent_10m_xg_by_team)
            if type(recent_10m_xg_age) is int
            and 480 <= recent_10m_xg_age <= 600 else None)
    if shots10 is not None and sot10 is not None and any(
            sot10[i] > shots10[i] for i in (0, 1)):
        shots10, sot10 = None, None
    cards = _verified_pair(red_cards, integer=True)

    # Do not rename the failed 2H_DOMINATION logic: this demands xG
    # growth within a *real 8-10 minute window* and contemporary team shots.
    quality = False
    chasing = False
    if second_half and 52 <= minute <= 72 and total >= 1:
        if shots10 is not None and sot10 is not None and xg10 is not None:
            quality = (recent_shots >= 4 and recent_sot >= 2 and
                       any(xg10[i] >= 0.45
                           and shots10[i] >= 3 and sot10[i] >= 1
                           and (cards is None or cards[i] == 0)
                           for i in (0, 1)))
        if (gap == 1 and trailing is not None
                and shots10 is not None and sot10 is not None
                and (cards is None or cards[trailing] == 0)):
            chasing = (shots[trailing] >= 6 and sot[trailing] >= 2
                       and shots10[trailing] >= 3
                       and sot10[trailing] >= 1
                       and recent_shots >= 4 and recent_sot >= 2)

    old_two_sided_candidate = (
        min(shots) >= 4 and min(sot) >= 1 and
        recent_shots >= 4 and recent_sot >= 2)
    # Only tighten TWO_SIDED here. Independent verified QUALITY_SURGE
    # and TRAILING_CHASE retain their own existing requirements.
    if old_two_sided_candidate and not quality and not chasing:
        two_sided_problem = _two_sided_freshness_guard(
            minute=minute,second_half=second_half,total=total,
            shots10=shots10,sot10=sot10,
            shots5=shots5,sot5=sot5,xg10=xg10,
            opening_shots=opening_shots,opening_sot=opening_sot)
        if two_sided_problem:
            risk.append(two_sided_problem)
        else:
            if shots10 is not None and sot10 is not None:
                notes.append(
                    f"TWO_SIDED_10M_ATTACKS_{int(shots10[0])}_"
                    f"{int(shots10[1])}_SOT_{int(sot10[0])}_{int(sot10[1])}")
            else:
                notes.append("TWO_SIDED_EARLY_SAME_HALF_TEAM_PULSE")
            if shots5 is not None and sot5 is not None:
                notes.append(
                    f"TWO_SIDED_5M_ATTACKS_{int(shots5[0])}_"
                    f"{int(shots5[1])}_SOT_{int(sot5[0])}_{int(sot5[1])}")

    if risk:
        key, title = "STATE_RISK", "Ожидание подтверждения атак"
    elif quality:
        key, title = "QUALITY_SURGE", "Всплеск опасных моментов"
    elif chasing:
        key, title = "TRAILING_CHASE", "Давление отстающей команды"
    elif old_two_sided_candidate:
        key, title = "TWO_SIDED", "Обоюдные атаки"
    else:
        key, title = "NO_CLEAR_SCENARIO", "Нет подтверждённого сценария"
        risk.append("NO_APPROVED_SCENARIO")

    if float(odd) >= 1.80:
        notes.append("ODDS_1_80_PLUS")
    if not xg_available:
        notes.append("XG_MISSING")
    return {
        "id": key, "label": title,
        "risk_flags": risk,
        "notes": notes,
        "corner_evidence": None,
        "shadow_decision": "RISK" if risk else "RETAIN",
        "version": 8,
    }
