"""LIVE snapshots accumulate BEFORE odds exist, not just after pricing."""
import sqlite3
from types import SimpleNamespace

from gool_goal_live import GoalPressure
from progruz1_scenario_evidence import ScenarioEvidence
from progruz1_flashscore_preobserve import observe_live_batch


EVENT="AbCd1234"
KICK=2_000_000_000


def fixture(minute=62):
    return SimpleNamespace(
        provider_match_id=EVENT,
        home="Home",away="Away",minute=minute,
        home_score=1,away_score=1,
        meta={"scheduled_start_ts":KICK,"status_code":"13"})


class RealSnapshots:
    def __init__(self):
        self.snapshots={
            KICK+3600:((4,3),(1,1),(.4,.2)),
            KICK+3900:((6,5),(2,2),(.6,.45)),
            KICK+4140:((9,7),(4,3),(1.1,.75)),
        }
        self.calls=[]

    def context(self,**kw):
        self.calls.append(kw)
        shots,sot,xg=self.snapshots[kw["now"]]
        return {"ok":True,"event_id":kw["fixture_id"],
                "score":kw["score"],
                "stats":{"shots":shots,"shots_on_target":sot,
                         "xg":xg}}


def test_preobserver_warms_unpriced_match_and_reuses_fs_identity():
    db=sqlite3.connect(":memory:")
    gp=GoalPressure(db)
    xg=ScenarioEvidence(db)
    fs=RealSnapshots()
    rows=[
        ("fonbet:100",KICK,fixture(),{}),
        ("FS:"+EVENT,KICK,fixture(),{}),
        ("fonbet:200",KICK,fixture(),{})]
    observations=[]
    for t,row in zip(sorted(fs.snapshots),rows):
        ready,counters=observe_live_batch(
            [row],now=t,flashscore=fs,
            goal_pressure=gp,scenario_evidence=xg)
        assert counters["observed"]==1
        assert counters["failures"]=={}
        observations.append(ready[row[0]])
    assert len(fs.calls)==3
    assert observations[1][1]["recent_5m_shots_by_team"]==(2,2)
    final=observations[-1]
    assert final[1]["recent_10m_shots_by_team"]==(5,4)
    assert final[1]["recent_5m_shots_by_team"]==(3,2)
    assert final[2]["recent_10m_xg_by_team"] is not None
    assert round(final[2]["recent_10m_xg_by_team"][0],2)==.7
    assert db.execute(
        "SELECT COUNT(DISTINCT root_id) FROM goal_live_pressure"
    ).fetchone()[0]==1
    assert db.execute(
        "SELECT DISTINCT root_id FROM goal_live_pressure"
    ).fetchone()[0]=="FS:"+EVENT
    # A bookmaker market may appear at the third snapshot. There is
    # already real 5m+10m evidence instead of a brand-new zero baseline.
    assert final[1]["sample_age"]==540


def test_preobserver_rejects_wrong_score_and_ambiguous_stats():
    class WrongContext:
        def context(self,**kwargs):
            return {"ok":True,"event_id":EVENT,"score":(0,0),
                    "stats":{"shots":(8,5),"shots_on_target":(3,2)}}
    db=sqlite3.connect(":memory:")
    ready,counts=observe_live_batch(
        [("FS:"+EVENT,KICK,fixture(),{})],
        now=KICK+3600,flashscore=WrongContext(),
        goal_pressure=GoalPressure(db),
        scenario_evidence=ScenarioEvidence(db))
    assert ready=={}
    assert counts["observed"]==0
    assert counts["failures"]["FS_PREOBSERVE_CONTEXT_UNVERIFIED"]==1
    assert db.execute(
        "SELECT COUNT(*) FROM goal_live_pressure").fetchone()[0]==0


def test_preobserver_missing_live_stats_stays_unknown():
    class MissingStats:
        def context(self,**kwargs):
            return {"ok":True,"event_id":EVENT,"score":(1,1),
                    "stats":{}}
    db=sqlite3.connect(":memory:")
    ready,counts=observe_live_batch(
        [("FS:"+EVENT,KICK,fixture(),{})],
        now=KICK+3600,flashscore=MissingStats(),
        goal_pressure=GoalPressure(db),
        scenario_evidence=ScenarioEvidence(db))
    assert ready=={}
    assert counts["failures"]["FS_PREOBSERVE_SHOTS_UNVERIFIED"]==1
