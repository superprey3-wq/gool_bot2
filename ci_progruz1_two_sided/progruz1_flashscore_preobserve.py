"""Price-independent LIVE Flashscore pressure collector for PROGRUZ 1.

Observe every *selected* fixture in the scoring window before any
bookmaker price is evaluated, so a late-opening price does not reset the
5/10-minute shot and xG history. No fabricated data or stale quotes.
"""
from __future__ import annotations

from gool_flashscore_goal_brain import _score, _pair, _second_half_started


def observe_live_batch(batch_rows, *, now, flashscore,
                       goal_pressure, scenario_evidence):
    """Return (prepared_by_root, counters).

    batch_rows: [(bookmaker_root, bookmaker_kickoff, fs_fixture, book)].
    prepared[root]: (live_context, measured_pressure, scenario_evidence, stats)

    All snapshots are independently obtained from the verified fixture ID.
    Caller reuses prepared context for priced fixtures; unpriced fixtures
    still accumulate a real 5/10-minute baseline for future LIVE scans.
    """
    prepared={}
    failures={}
    checked=0

    def fail(reason):
        reason=str(reason)[:80]
        failures[reason]=failures.get(reason,0)+1

    for root,kick,fs,book in batch_rows:
        checked+=1
        fs_id=str(getattr(fs,"provider_match_id",None) or "")
        score=_score((fs.home_score,fs.away_score))
        if not fs_id or score is None:
            fail("FS_PREOBSERVE_ID_OR_SCORE_MISSING")
            continue
        try:
            # The real adapter scouts only LIVE shots/SoT/xG. Test/fake
            # adapters can keep supporting the original context API.
            if callable(getattr(flashscore,"scout_live",None)):
                ctx=flashscore.scout_live(fixture=fs,now=now)
            else:
                ctx=flashscore.context(
                    root=root,home=str(fs.home),away=str(fs.away),
                    kickoff=kick,now=now,score=score,fixture_id=fs_id)
        except Exception as exc:
            fail("FS_PREOBSERVE_CONTEXT_"+type(exc).__name__)
            continue
        if (not ctx.get("ok") or
                str(ctx.get("event_id") or "")!=fs_id or
                _score(ctx.get("score"))!=score):
            fail(ctx.get("reason") or "FS_PREOBSERVE_CONTEXT_UNVERIFIED")
            continue
        stats=ctx.get("stats") or {}
        shots=_pair(stats.get("shots"))
        sot=_pair(stats.get("shots_on_target"))
        if (shots is None or sot is None
                or any(int(v)!=v for v in shots+sot)
                or any(sot[i]>shots[i] for i in (0,1))):
            fail("FS_PREOBSERVE_SHOTS_UNVERIFIED")
            continue

        minute=int(fs.minute)
        status_code=(getattr(fs,"meta",None) or {}).get("status_code")
        second=_second_half_started(minute,status_code)
        phase="2-й тайм" if second else "1-й тайм"
        stable_root="FS:"+fs_id
        stable_kickoff=int((getattr(fs,"meta",None) or {}).get(
            "scheduled_start_ts") or kick)
        observed=goal_pressure.observe(
            root=stable_root,kickoff=stable_kickoff,
            now=now,minute=minute,phase=phase,score=score,
            shots=tuple(int(v) for v in shots),
            on=tuple(int(v) for v in sot),
            early=(minute<=50 if second else minute<=5))
        if not isinstance(observed,dict):
            fail("FS_PREOBSERVE_MOMENTUM_UNVERIFIED")
            continue
        evidence=scenario_evidence.observe(
            root=stable_root,kickoff=stable_kickoff,
            now=now,phase=phase,xg=stats.get("xg"),pressure=observed)
        prepared[root]=(ctx,observed,evidence,stats)
    return prepared,{
        "checked":checked,
        "observed":len(prepared),
        "failures":failures,
    }
