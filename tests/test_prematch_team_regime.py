from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.prematch_team_regime import apply_team_regime
from gool_bot2.v4_prematch_engine import PrematchPick


def _row(day, home, away, hs, aws):
    return {
        "home": home,
        "away": away,
        "home_score": hs,
        "away_score": aws,
        "timestamp": f"2026-09-{day:02d}T12:00:00Z",
    }


def test_full_match_profile_keeps_team_distribution_not_only_average():
    home_rows = [
        _row(20, "Home", "X1", 4, 0),
        _row(19, "Home", "X2", 0, 0),
        _row(18, "X3", "Home", 1, 3),
        _row(17, "Home", "X4", 1, 0),
        _row(16, "X5", "Home", 0, 4),
    ]
    away_rows = [
        _row(20, "Away", "Y1", 1, 3),
        _row(19, "Y2", "Away", 4, 0),
        _row(18, "Away", "Y3", 1, 1),
        _row(17, "Y4", "Away", 3, 0),
        _row(16, "Away", "Y5", 2, 2),
    ]
    profile = build_prematch_goal_profile({
        "match": {"home": "Home", "away": "Away"},
        "prematch_context": {"home_recent": home_rows, "away_recent": away_rows},
    })
    home = profile["full_match"]["home"]
    away = profile["full_match"]["away"]

    assert home["matches"] == 5
    assert home["scored_ge"]["4"] == 3 / 5
    assert home["recent5_over"]["2.5"] == 3 / 5
    assert away["conceded_ge"]["3"] >= 2 / 5
    assert len(home["goal_sequence"]) == 5


def test_team_total_under_is_penalized_when_attack_and_defence_have_explosion_risk():
    pick = PrematchPick(
        "m1", "Home", "Away", "home_total", "under 3.5",
        1.65, .70, .60, .90,
    )
    profile = {
        "full_match": {
            "available": True,
            "home": {
                "scored_ge": {"4": .40},
                "conceded_ge": {"4": .10},
                "recent5_scored_ge": {"4": .40},
                "recent5_conceded_ge": {"4": .00},
            },
            "away": {
                "scored_ge": {"4": .10},
                "conceded_ge": {"4": .40},
                "recent5_scored_ge": {"4": .00},
                "recent5_conceded_ge": {"4": .40},
            },
        }
    }

    adjusted, meta = apply_team_regime(pick, profile)

    assert adjusted.model_probability < pick.model_probability
    assert meta["adjustment_pp"] < 0
    assert "team_explosion_risk_4plus" in meta["tags"]


def test_match_under_is_penalized_when_recent_under_streak_conflicts_with_baseline():
    pick = PrematchPick(
        "m2", "Home", "Away", "match_total", "under 2.5",
        1.70, .69, .59, .90,
    )
    profile = {
        "full_match": {
            "available": True,
            "home": {
                "over": {"2.5": .60},
                "recent5_over": {"2.5": .20},
                "scored_ge": {"3": .20},
                "conceded_ge": {"3": .20},
            },
            "away": {
                "over": {"2.5": .50},
                "recent5_over": {"2.5": .20},
                "scored_ge": {"3": .20},
                "conceded_ge": {"3": .20},
            },
        }
    }

    adjusted, meta = apply_team_regime(pick, profile)

    assert adjusted.model_probability < pick.model_probability
    assert "recent_under_streak_vs_normal_baseline" in meta["tags"]
    assert meta["adjustment_pp"] >= -5.0
