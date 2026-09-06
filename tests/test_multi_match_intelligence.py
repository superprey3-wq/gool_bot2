from __future__ import annotations

from gool_bot2 import multi_match_intelligence as intelligence
from gool_bot2.multi_match_intelligence import (
    calibration_snapshot,
    chance_quality_context,
    enforce_match_suitability,
    kickoff_market_context,
    minute_hazard,
    score_epoch_context,
)
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _record(minute=50, score=(0, 0), stats=None):
    stats = stats or {}
    return {
        "match": {
            "flashscore_event_id": "m1",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "home": "A",
            "away": "B",
        },
        "providers": {
            "flashscore": {
                "stats": stats,
                "meta": {"goal_timeline": []},
            }
        },
        "prematch_goal_profile": {
            "active": {"pair_sample": 5},
        },
    }


def _market(score=(0, 0), age_stamp="2099-01-01T00:00:00+00:00"):
    return {
        "score_home": score[0],
        "score_away": score[1],
        "captured_at": age_stamp,
        "markets": {
            "match_1x2": {
                "home": 2.4,
                "draw": 3.2,
                "away": 3.1,
                "fair": {"home": 0.40, "draw": 0.30, "away": 0.30},
            },
            "match_total": [
                {"line": 2.5, "over": 1.90, "under": 1.90},
            ],
        },
    }


def test_minute_hazard_separates_first_and_second_half_buckets():
    assert minute_hazard(5)["factor"] < 1.0
    assert minute_hazard(27)["factor"] > 1.0
    assert minute_hazard(50)["period"] == "2H"
    assert minute_hazard(70)["factor"] > minute_hazard(50)["factor"]


def test_score_epoch_resets_after_score_change_and_excludes_goal_snapshot(monkeypatch):
    intelligence._EPOCH_BASELINES.clear()
    values = {
        "xg": (0.7, 0.2),
        "xgot": (0.8, 0.1),
        "shots": (7.0, 3.0),
        "shots_on_target": (3.0, 1.0),
        "shots_inside_box": (4.0, 1.0),
        "big_chances": (1.0, 0.0),
        "high_xg_shots": (1.0, 0.0),
        "dangerous_attacks": (25.0, 12.0),
        "corners": (4.0, 1.0),
        "red_cards": (0.0, 0.0),
    }
    monkeypatch.setattr(intelligence, "_pair", lambda record, key: values.get(key, (None, None)))

    record = _record(minute=50, score=(0, 0))
    first = score_epoch_context(record)
    assert first["period"] == "2H"
    assert first["total"]["shots"] == 0.0

    # The scoring snapshot becomes the new baseline, so the goal-producing
    # shot/xG cannot count again toward the next goal.
    values["xg"] = (1.0, 0.2)
    values["shots"] = (8.0, 3.0)
    record["match"]["minute"] = 53
    record["match"]["home_score"] = 1
    after_goal = score_epoch_context(record)
    assert after_goal["identity"] == "2H:1:0"
    assert after_goal["total"]["shots"] == 0.0
    assert after_goal["total"]["xg"] == 0.0

    # Only new football after 1:0 is accumulated.
    values["xg"] = (1.25, 0.25)
    values["shots"] = (10.0, 4.0)
    record["match"]["minute"] = 56
    later = score_epoch_context(record)
    assert later["total"]["shots"] == 3.0
    assert round(later["total"]["xg"], 2) == 0.30


def test_score_epoch_resets_at_halftime(monkeypatch):
    intelligence._EPOCH_BASELINES.clear()
    values = {
        "xg": (0.9, 0.5),
        "xgot": (0.8, 0.4),
        "shots": (9.0, 6.0),
        "shots_on_target": (4.0, 2.0),
        "shots_inside_box": (5.0, 3.0),
        "big_chances": (2.0, 1.0),
        "high_xg_shots": (2.0, 1.0),
        "dangerous_attacks": (30.0, 22.0),
        "corners": (4.0, 3.0),
        "red_cards": (0.0, 0.0),
    }
    monkeypatch.setattr(intelligence, "_pair", lambda record, key: values.get(key, (None, None)))
    record = _record(minute=44, score=(1, 0))
    score_epoch_context(record)
    record["match"]["minute"] = 46
    second = score_epoch_context(record)
    assert second["period"] == "2H"
    assert second["total"]["shots"] == 0.0


def test_chance_quality_prefers_fewer_high_quality_chances():
    strong = {
        "available": True,
        "home": {
            "shots": 3,
            "xg": 0.75,
            "xgot": 0.90,
            "shots_on_target": 2,
            "shots_inside_box": 3,
            "big_chances": 1,
        },
        "away": {},
    }
    weak = {
        "available": True,
        "home": {
            "shots": 7,
            "xg": 0.20,
            "xgot": 0.10,
            "shots_on_target": 1,
            "shots_inside_box": 2,
            "big_chances": 0,
        },
        "away": {},
    }
    assert chance_quality_context(strong)["score"] > chance_quality_context(weak)["score"]


def test_late_first_market_observation_is_not_called_kickoff(monkeypatch):
    intelligence._KICKOFF_CACHE = {}
    monkeypatch.setattr(intelligence, "_save_kickoff_cache", lambda cache: None)
    record = _record(minute=18)
    context = kickoff_market_context(record, _market())
    assert context["quality"] == "late_first_observation"
    assert context["usable_as_kickoff_prior"] is False


def test_near_kickoff_market_is_usable_prior(monkeypatch):
    intelligence._KICKOFF_CACHE = {}
    monkeypatch.setattr(intelligence, "_save_kickoff_cache", lambda cache: None)
    record = _record(minute=3)
    context = kickoff_market_context(record, _market())
    assert context["usable_as_kickoff_prior"] is True
    assert round(context["main_total"]["fair_over"], 3) == 0.5


def _decision() -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:1.5",
        family="match_total",
        strategy="another_goal",
        label="ТБ 1.5",
        odd=1.8,
        model_probability=0.78,
        market_probability=0.60,
        correlation_key="goal",
        source="gool",
        rating=80.0,
    )
    return RouterDecision(
        status="BET",
        minute=60,
        score=(1, 0),
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="BET",
    )


def test_match_suitability_is_diagnostic_not_a_main_brain_veto():
    decision = _decision()
    record = {"match_intelligence": {"suitability": {"score": 0.2, "minimum": 0.58, "hard_blocks": ["score_desync"]}}}
    out = enforce_match_suitability(decision, record)
    assert out is decision
    assert out.status == "BET"
    assert out.winner is not None


def test_calibration_snapshot_records_fair_edge_bucket():
    entry = {
        "strategy": "another_goal",
        "model_probability": 0.78,
        "market_probability": 0.62,
        "odd": 1.55,
    }
    snap = calibration_snapshot(entry, {"match_intelligence": {"kickoff_market": {"quality": "near_kickoff"}}})
    assert snap["edge_pp"] == 16.0
    assert snap["probability_bucket"] == "75-80%"
    assert snap["market_raw_implied_probability"] > snap["market_fair_probability"]
