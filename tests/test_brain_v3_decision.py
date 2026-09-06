from __future__ import annotations

from gool_bot2.brain_v3_decision import BET, WATCH, evaluate_brain_v3


def _record(
    minute: int,
    *,
    score=(1, 0),
    state="AWAY_SIEGE",
    home_pressure=0.35,
    away_pressure=0.82,
    home_trend="STEADY",
    away_trend="RISING",
    xg=(0.8, 1.6),
    w5_xg=(0.05, 0.35),
    w10_xg=(0.10, 0.70),
    epoch_age=15,
    epoch_samples=12,
    prematch=0.75,
) -> dict:
    period = "1H" if minute <= 45 else "2H"
    return {
        "match": {
            "flashscore_event_id": "brain-v3-test",
            "home": "Home",
            "away": "Away",
            "league": "Test League",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": [8, 15],
                    "shots_on_target": [3, 7],
                    "xg": list(xg),
                    "big_chances": [1, 3],
                    "dangerous_attacks": [30, 55],
                    "corners": [3, 6],
                }
            }
        },
        "brain_v3_memory": {
            "score_epoch": {
                "score": list(score),
                "period": period,
                "start_minute": minute - epoch_age,
                "age_minutes": epoch_age,
                "samples": epoch_samples,
            },
            "windows": {
                "5m": {
                    "home_xg": w5_xg[0], "away_xg": w5_xg[1],
                    "home_shots": 1, "away_shots": 5,
                    "home_sot": 0, "away_sot": 3,
                    "home_big": 0, "away_big": 1,
                    "home_danger": 4, "away_danger": 20,
                },
                "10m": {
                    "home_xg": w10_xg[0], "away_xg": w10_xg[1],
                    "home_shots": 2, "away_shots": 10,
                    "home_sot": 1, "away_sot": 5,
                    "home_big": 0, "away_big": 1,
                    "home_danger": 8, "away_danger": 36,
                },
            },
            "pressure": {
                "state": state,
                "home": home_pressure,
                "away": away_pressure,
                "home_trend": {"state": home_trend},
                "away_trend": {"state": away_trend},
            },
        },
        "prematch_goal_profile": {
            "active": {
                "available": True,
                "one_more_probability": prematch,
                "pair_sample": 8,
                "h2h_sample": 3,
            }
        },
    }


def test_v3_bets_when_losing_team_sustains_real_late_pressure() -> None:
    out = evaluate_brain_v3(_record(70), data_quality=0.80)
    assert out["status"] == BET
    assert out["probability"] >= out["bet_min"]
    assert out["recent_quality"] is True
    assert out["sustained_pressure"] is True
    assert out["prematch"]["adjustment_pp"] > 0


def test_v3_waits_when_match_is_calm_even_with_good_prematch() -> None:
    record = _record(
        70,
        state="CALM",
        home_pressure=0.18,
        away_pressure=0.22,
        xg=(0.35, 0.45),
        w5_xg=(0.02, 0.03),
        w10_xg=(0.05, 0.06),
        prematch=0.90,
    )
    record["brain_v3_memory"]["windows"]["5m"].update({
        "home_shots": 1, "away_shots": 1,
        "home_sot": 0, "away_sot": 0,
        "home_big": 0, "away_big": 0,
    })
    out = evaluate_brain_v3(record, data_quality=0.80)
    assert out["status"] == WATCH
    assert "brain_v3_pressure_not_confirmed" in out["blocks"]
    assert out["prematch"]["adjustment_pp"] > 0


def test_v3_observes_again_after_goal_before_reentering() -> None:
    out = evaluate_brain_v3(
        _record(71, score=(2, 0), epoch_age=1, epoch_samples=2),
        data_quality=0.85,
    )
    assert out["status"] == WATCH
    assert "brain_v3_post_goal_observe" in out["blocks"]


def test_v3_first_half_uses_same_state_machine() -> None:
    record = _record(
        28,
        score=(1, 0),
        state="HOME_SIEGE",
        home_pressure=0.84,
        away_pressure=0.30,
        home_trend="RISING",
        away_trend="STEADY",
        xg=(1.55, 0.35),
        w5_xg=(0.36, 0.04),
        w10_xg=(0.70, 0.08),
        epoch_age=10,
        epoch_samples=9,
        prematch=0.72,
    )
    out = evaluate_brain_v3(record, data_quality=0.82)
    assert out["strategy"] == "goal_before_ht"
    assert out["status"] == BET
    assert out["probability"] >= 0.70
