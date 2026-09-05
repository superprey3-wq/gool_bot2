from gool_bot2.goal_state_engine import build_goal_state_experts


def _strong_live_record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "no-pre-live",
            "home": "Home",
            "away": "Away",
            "league": "Lower League",
            "minute": 58,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
            "is_halftime": False,
        },
        "prematch_context": {},
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": [12, 10],
                    "shots_on_target": [5, 4],
                    "shots_inside_box": [7, 6],
                    "big_chances": [2, 2],
                    "dangerous_attacks": [46, 42],
                    "corners": [5, 4],
                },
                "meta": {},
            }
        },
        "live_momentum": {
            "home_shots_last_5m": 3,
            "away_shots_last_5m": 2,
            "home_sot_last_5m": 2,
            "away_sot_last_5m": 1,
            "home_big_last_5m": 1,
            "away_big_last_5m": 1,
            "home_danger_last_5m": 8,
            "away_danger_last_5m": 7,
            "home_shots_last_10m": 5,
            "away_shots_last_10m": 4,
            "home_sot_last_10m": 3,
            "away_sot_last_10m": 2,
        },
        "cards": {},
    }


def test_strong_live_another_goal_can_pass_without_optional_prematch_history():
    record = _strong_live_record()
    experts = build_goal_state_experts(
        record,
        model_result={
            "trained_probability": {"another_goal": 0.80},
            "another_goal_live": {"combined_pressure": 1.30, "passed": True},
        },
        data_quality=0.70,
    )

    assert experts["another_goal"]["state"] == "PASS"
    assert experts["another_goal"]["passed"] is True
    home = experts["another_goal"]["diagnostics"]["home"]
    away = experts["another_goal"]["diagnostics"]["away"]
    assert home["raw"]["prematch_available"] is False
    assert away["raw"]["prematch_available"] is False
