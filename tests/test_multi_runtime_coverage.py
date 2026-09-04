from gool_bot2.multi_runtime import _ensure_any_goal_coverage_proxy


def test_team_confidence_creates_broad_any_goal_confidence_proxy():
    experts = {
        "away_goal": {
            "probability": 0.847,
            "metric": "confidence",
            "source": "shadow:away_goal",
            "passed": True,
            "blocks": [],
        }
    }

    _ensure_any_goal_coverage_proxy(experts)

    proxy = experts["another_goal"]
    assert proxy["probability"] == 0.847
    assert proxy["metric"] == "confidence"
    assert proxy["passed"] is True
    assert proxy["coverage_proxy"] is True
    assert proxy["proxy_from"] == "away_goal"


def test_existing_calibrated_another_goal_is_never_replaced():
    original = {
        "probability": 0.61,
        "metric": "probability",
        "source": "model:another_goal",
        "passed": True,
        "blocks": [],
    }
    experts = {
        "another_goal": dict(original),
        "away_goal": {
            "probability": 0.90,
            "metric": "confidence",
            "source": "shadow:away_goal",
            "passed": True,
            "blocks": [],
        },
    }

    _ensure_any_goal_coverage_proxy(experts)

    assert experts["another_goal"] == original


def test_failed_team_signal_does_not_create_broad_proxy():
    experts = {
        "away_goal": {
            "probability": 0.90,
            "metric": "confidence",
            "source": "shadow:away_goal",
            "passed": False,
            "blocks": ["side_no_recent_threat"],
        }
    }

    _ensure_any_goal_coverage_proxy(experts)

    assert "another_goal" not in experts
