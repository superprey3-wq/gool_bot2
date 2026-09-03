from gool_bot2 import storage_market_shadow_worker as merged


def test_equivalent_side_for_one_sided_score():
    assert merged._equivalent_side(1, 0) == "away"
    assert merged._equivalent_side(2, 0) == "away"
    assert merged._equivalent_side(0, 1) == "home"
    assert merged._equivalent_side(0, 0) is None
    assert merged._equivalent_side(1, 1) is None


def test_correlated_confirmation_can_promote_market_override():
    primary = {"head": "team_to_score", "override": False, "level": "NEUTRAL", "targets": []}
    secondary = {
        "head": "both_teams_to_score",
        "override": True,
        "level": "STRONG_STEAM",
        "score_pp": 4.3,
        "strongest_delta_pp": 7.3,
        "strongest_one_way_moves": 3,
        "targets": [{"market": "btts_yes", "label": "ОЗ — Да", "old_odd": 2.11, "new_odd": 2.02}],
    }
    info = merged._copy_correlated(primary, secondary, label="ОЗ — Да")
    assert info["cross_market"] is True
    assert info["override"] is True
    assert info["correlated_confirmation"]["strongest_delta_pp"] == 7.3


def test_only_one_open_correlated_exposure_per_match():
    rows = [{"match_id": "abc", "head": "both_teams_to_score", "result": "pending"}]
    assert merged._already_recorded(rows, "abc", "team_to_score") is True
    assert merged._already_recorded(rows, "xyz", "team_to_score") is False
