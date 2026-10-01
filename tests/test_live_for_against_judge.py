from gool_bot2.live_for_against_judge import evaluate_argument_judge


def _record(minute=30, score=(0, 0), state="HOME_PRESSURE", epoch_samples=6, xg5=0.35, sot5=2, shots5=5, big5=1):
    return {
        "match": {
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {"stats": {"xg": (0.8, 0.4)}},
            "fotmob": {"stats": {"xg": (0.75, 0.45)}},
        },
        "brain_v3_memory": {
            "windows": {
                "5m": {
                    "home_xg": xg5, "away_xg": 0.0,
                    "home_sot": sot5, "away_sot": 0,
                    "home_shots": shots5, "away_shots": 0,
                    "home_big": big5, "away_big": 0,
                },
                "10m": {
                    "home_xg": 0.55, "away_xg": 0.05,
                    "home_sot": 3, "away_sot": 0,
                },
            },
            "pressure": {
                "state": state,
                "home_trend": {"state": "RISING"},
                "away_trend": {"state": "STEADY"},
            },
            "score_epoch": {"age_minutes": 8, "samples": epoch_samples, "score": list(score)},
        },
    }


def test_strong_live_pressure_can_bet():
    d = evaluate_argument_judge(_record())
    assert d is not None
    assert d.market == "GOAL_BEFORE_HT"
    assert d.decision == "BET"
    assert d.for_score > d.against_score


def test_missing_memory_blocks():
    r = _record()
    r["brain_v3_memory"] = {"windows": {}, "pressure": {"state": "NO_DATA"}, "score_epoch": {"samples": 1, "age_minutes": 1, "score": [0, 0]}}
    d = evaluate_argument_judge(r)
    assert d is not None
    assert d.decision == "NO_BET"
    assert d.against_score >= 35


def test_post_goal_reset_blocks():
    d = evaluate_argument_judge(_record(minute=60, score=(1, 0), epoch_samples=2))
    assert d is not None
    assert d.market == "ANOTHER_GOAL"
    assert d.decision != "BET"


def test_outside_window_returns_none():
    assert evaluate_argument_judge(_record(minute=44)) is None


def test_acute_five_minute_surge_can_bet_even_if_pressure_state_lags():
    # Pressure-state can lag a fast attacking burst. Two SOT + three shots in the
    # last 5m across multiple providers is enough for the surge path.
    d = evaluate_argument_judge(
        _record(state="CALM", epoch_samples=4, xg5=0.06, sot5=2, shots5=3, big5=0)
    )
    assert d is not None
    assert d.decision == "BET"
    assert "acute_threat(+14)" in d.reasons_for
