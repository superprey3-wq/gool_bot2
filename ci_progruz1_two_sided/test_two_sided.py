"""CI-only copies of PROGRUZ1 scenario regressions; no user bot changes."""
import pytest
from progruz1_scenarios import classify_scenario


def scenario(*,minute=60,score=(1,1),shots=(9,8),sot=(4,4),
             recent_shots=5,recent_sot=3,**extra):
    return classify_scenario(
        minute=minute,score=score,second_half=minute>=46,
        shots=shots,sot=sot,recent_shots=recent_shots,
        recent_sot=recent_sot,odd=1.72,xg_available=False,
        avg_total=3.0,**extra)


@pytest.mark.parametrize("minute,score,shots,sot",[
    (65,(0,0),(11,4),(6,1)),
    (79,(0,0),(11,5),(7,2)),
    (77,(1,2),(5,12),(3,6)),
    (34,(0,0),(7,4),(1,4)),
])
def test_four_reported_losses_cannot_be_approved_from_cumulative_counts(
        minute,score,shots,sot):
    r=scenario(minute=minute,score=score,shots=shots,sot=sot,
               recent_shots=4,recent_sot=2)
    assert r["id"]=="STATE_RISK"
    assert "TWO_SIDED_RECENT_TEAM_STATS_UNVERIFIED" in r["risk_flags"]


def test_real_both_team_pressure_still_passes():
    r=scenario(
        minute=65,
        recent_10m_shots_by_team=(4,3),
        recent_10m_sot_by_team=(2,1),recent_10m_age=540,
        recent_5m_shots_by_team=(2,2),
        recent_5m_sot_by_team=(1,1),recent_5m_age=300)
    assert r["id"]=="TWO_SIDED"


def test_last_five_minutes_other_team_quiet_blocks():
    r=scenario(
        minute=65,
        recent_10m_shots_by_team=(4,3),
        recent_10m_sot_by_team=(2,1),recent_10m_age=540,
        recent_5m_shots_by_team=(3,0),
        recent_5m_sot_by_team=(2,0),recent_5m_age=300)
    assert r["id"]=="STATE_RISK"
    assert "TWO_SIDED_RECENT_5M_ONE_TEAM_QUIET" in r["risk_flags"]


def test_late_nil_nil_is_stricter():
    base={
        "minute":79,"score":(0,0),"shots":(11,8),"sot":(6,4),
        "recent_10m_shots_by_team":(4,3),
        "recent_10m_age":540,
        "recent_5m_shots_by_team":(2,1),
        "recent_5m_sot_by_team":(1,1),
        "recent_5m_age":300}
    weak=scenario(**base,recent_10m_sot_by_team=(2,1))
    assert "TWO_SIDED_LATE_00_NO_XG_NEEDS_4_SOT" in weak["risk_flags"]
    strong=scenario(**base,recent_10m_sot_by_team=(2,2))
    assert strong["id"]=="TWO_SIDED"


def test_early_opening_half_can_pass_with_real_shorter_team_delta():
    r=scenario(minute=5,score=(0,0),shots=(4,4),sot=(2,2),
        opening_shots_by_team=(2,2),
        opening_sot_by_team=(1,1),opening_sample_age=60)
    assert r["id"]=="TWO_SIDED"
    unknown=scenario(minute=5,score=(0,0),shots=(4,4),sot=(2,2))
    assert unknown["id"]=="STATE_RISK"


def test_quality_surge_is_not_silenced():
    r=scenario(minute=60,score=(1,1),shots=(12,3),sot=(4,0),
        recent_10m_shots_by_team=(3,1),
        recent_10m_sot_by_team=(1,0),recent_10m_age=540,
        recent_10m_xg_by_team=(0.52,0.05),recent_10m_xg_age=540)
    assert r["id"]=="QUALITY_SURGE"


def test_trailing_chase_is_not_silenced():
    r=scenario(minute=61,score=(0,1),shots=(12,3),sot=(4,0),
        recent_10m_shots_by_team=(4,0),
        recent_10m_sot_by_team=(2,0),recent_10m_age=540)
    assert r["id"]=="TRAILING_CHASE"
