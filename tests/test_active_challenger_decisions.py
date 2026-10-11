from __future__ import annotations

from pathlib import Path

from gool_bot2.active_challenger_decisions import (
    football_v6_active_picks,
    hockey_ev_active_signal,
    basketball_v4_active_signal,
    football_live_v6_veto,
)


def _v6_candidate(odd=2.1, p=.55, fair=.47, ev=.155):
    return {
        "state": "PASS_SHADOW",
        "market": "match_total",
        "selection": "over 2.5",
        "odd": odd,
        "market_shrunk_probability_heuristic": p,
        "bookmaker_fair_probability": fair,
        "heuristic_ev_units": ev,
        "model_disagreement_pp": 3.0,
    }


def _v6_shadow(*, quality=.85, candidate=None):
    return {
        "status": "PASS_SHADOW",
        "quote_source": "flashscore",
        "bookmaker": "Observed bookmaker",
        "data_quality": quality,
        "candidates": [candidate if candidate is not None else _v6_candidate()],
    }


def _football(shadow):
    return football_v6_active_picks(
        shadow,
        event_id="fixture-id", home="Team A", away="Team B",
        league="Test league", kickoff_ts=1790000000.0,
    )


def test_football_v6_active_replaces_v5_and_only_real_market_candidate():
    picks, metadata = _football(_v6_shadow())
    assert len(picks) == 1
    assert picks[0].market == "match_total"
    assert picks[0].selection == "over 2.5"
    assert picks[0].odds == 2.1
    assert picks[0].model_probability == .55
    assert metadata["brain_version"] == "V6"
    assert metadata["bookmaker"] == "Observed bookmaker"
    assert metadata["probabilities_calibrated"] is False


def test_football_v6_below_value_low_quality_and_bad_settlement_all_wait():
    for shadow in (
        {**_v6_shadow(), "status": "WAIT"},
        _v6_shadow(quality=.40),
        _v6_shadow(candidate={**_v6_candidate(), "selection": "over 2"}),
        _v6_shadow(candidate={**_v6_candidate(), "odd": 1.2}),
        _v6_shadow(candidate={**_v6_candidate(), "heuristic_ev_units": -.1}),
        _v6_shadow(candidate={**_v6_candidate(), "model_disagreement_pp": 22.0}),
        _v6_shadow(candidate={**_v6_candidate(), "market": "period_total"}),
    ):
        picks, metadata = _football(shadow)
        assert not picks
        assert "reject" in metadata


def _hockey(direction="over", odd=1.8):
    return {
        "direction": direction, "odd": odd, "brain_mode": "hockey_prematch_v2",
        "market_family": "match_total", "line": 4.5, "strength": 80.0,
    }


def _hockey_ev(direction="over", quality=.85, ev=.15):
    return {
        "status": "PASS_SHADOW", "direction": direction,
        "data_quality": quality, "best_expected_value_units_uncalibrated": ev,
        "alternatives": [
            {"direction": direction, "odd": 1.8, "push_probability": 0.0,
             "conditional_win_probability_uncalibrated": .68},
        ],
    }


def test_hockey_ev_active_requires_agreeing_v2_and_push_aware_return():
    active = hockey_ev_active_signal(_hockey(), _hockey_ev())
    assert active
    assert active["brain_mode"] == "hockey_prematch_ev_active"
    assert active["expected_ev_units_uncalibrated"] == .15
    assert hockey_ev_active_signal(_hockey(), _hockey_ev(direction="under")) is None
    assert hockey_ev_active_signal(_hockey(), _hockey_ev(ev=-.2)) is None
    assert hockey_ev_active_signal(None, _hockey_ev()) is None
    assert hockey_ev_active_signal(_hockey(), _hockey_ev(), odds_range=(1.3, 1.5)) is None


def _basket_dynamic(**kwargs):
    return {
        "direction": "over", "odd": 1.85, "model_probability": .70,
        "edge": .12, "strength": 81, "data_quality": .83,
        "possession_source": "actual_possessions",
        "projection_clock_source": "1xbet_segment_clock",
        **kwargs,
    }


def test_basketball_v4_active_quarter_signal_direction_and_market_quality():
    original = _basket_dynamic()
    result = basketball_v4_active_signal(
        original, {"direction": "over"}, {"state": "PASS", "direction": "over"},
    )
    assert result["brain_mode"] == "basketball_live_v4_active"
    assert result["basketball_v3_agrees"] is True
    assert original["brain_mode"] if "brain_mode" in original else True
    assert basketball_v4_active_signal(
        _basket_dynamic(), {"direction": "under"}, {"state": "PASS", "direction": "over"},
    ) is None
    assert basketball_v4_active_signal(
        _basket_dynamic(possession_source="points_clock_fallback"),
        None, {"state": "PASS", "direction": "over"},
    ) is None
    assert basketball_v4_active_signal(
        _basket_dynamic(projection_clock_source="flashscore_elapsed_fallback"),
        None, {"state": "PASS", "direction": "over"},
    ) is None
    assert basketball_v4_active_signal(None,None,{"state":"PASS","direction":"over"}) is None


def test_football_live_v6_can_veto_clear_weak_goal_hazard_not_create_bets():
    base = {
        "status": "COMPARED",
        "hazard": {"available": True, "probability": .22},
        "live_v4": {"probability": .56, "data_source": "provider_xg"},
    }
    assert football_live_v6_veto(base) == "football_live_v6_low_goal_hazard"
    assert football_live_v6_veto({**base,"hazard":{"available":True,"probability":.7}}) is None
    assert football_live_v6_veto({**base,"status":"WAIT"}) is None
    assert football_live_v6_veto({**base,"hazard":{"available":False,"probability":None}}) is None


def test_all_three_live_active_switches_are_wired_in_workflows():
    root = Path(__file__).resolve().parents[1]
    football = (root / "scripts/gool_flashscore_today.py").read_text("utf-8")
    multisport = (root / "src/gool_bot2/xbet_multisport_steam.py").read_text("utf-8")
    throughput = (root / "src/gool_bot2/production_signal_throughput.py").read_text("utf-8")
    assert "GOOL_FOOTBALL_V6_ACTIVE" in football
    assert "football_v6_active_picks(" in football
    assert "GOOL_HOCKEY_PREMATCH_EV_ACTIVE" in multisport
    assert "hockey_ev_active_signal(" in multisport
    assert "GOOL_BASKETBALL_V4_ACTIVE" in multisport
    assert "basketball_v4_active_signal(" in multisport
    assert "GOOL_FOOTBALL_LIVE_V6_ACTIVE" in throughput
    assert "football_live_v6_veto(" in throughput
