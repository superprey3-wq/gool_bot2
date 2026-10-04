from gool_bot2.xbet_multisport_markets import (
    decode_core_markets,
    market_lanes,
    scope_from_subgame,
)


def test_legacy_ge_without_parent_group_decodes_totals_and_team_totals():
    game = {
        "GE": [{"E": [[
            {"G": 4, "T": 9, "P": 160.5, "C": 1.83},
            {"G": 4, "T": 10, "P": 160.5, "C": 1.87},
            {"G": 5, "T": 11, "P": 82.5, "C": 1.80},
            {"G": 5, "T": 12, "P": 82.5, "C": 1.90},
            {"G": 6, "T": 13, "P": 78.5, "C": 1.84},
            {"G": 6, "T": 14, "P": 78.5, "C": 1.86},
        ]]}],
    }
    decoded = decode_core_markets(game, "basketball")
    assert decoded["match_total"][0]["line"] == 160.5
    assert decoded["home_total"][0]["line"] == 82.5
    assert decoded["away_total"][0]["line"] == 78.5


def test_new_builder_ae_parent_group_is_inherited_by_selections():
    game = {
        "AE": [
            {"G": 17, "ME": [{"T": 9, "P": 5.5, "C": 1.85}, {"T": 10, "P": 5.5, "C": 1.91}]},
            {"G": 15, "ME": [{"T": 11, "P": 2.5, "C": 1.80}, {"T": 12, "P": 2.5, "C": 1.95}]},
            {"G": 62, "ME": [{"T": 13, "P": 2.5, "C": 1.88}, {"T": 14, "P": 2.5, "C": 1.86}]},
        ]
    }
    decoded = decode_core_markets(game, "hockey")
    assert decoded["match_total"][0]["line"] == 5.5
    assert decoded["home_total"][0]["line"] == 2.5
    assert decoded["away_total"][0]["line"] == 2.5


def test_period_and_quarter_scope_mapping():
    assert scope_from_subgame({"PN": "1st period"}, "hockey") == "PERIOD_1"
    assert scope_from_subgame({"PN": "3rd period"}, "hockey") == "PERIOD_3"
    assert scope_from_subgame({"PN": "4th quarter"}, "basketball") == "QUARTER_4"
    assert scope_from_subgame({"PN": "2 Half"}, "basketball") == "SECOND_HALF"


def test_every_scope_becomes_independent_total_lanes():
    root = decode_core_markets({"GE": [{"E": [[
        {"G": 4, "T": 9, "P": 160.5, "C": 1.83},
        {"G": 4, "T": 10, "P": 160.5, "C": 1.87},
    ]]}]}, "basketball", scope="FULL_MATCH")
    q1 = decode_core_markets({"GE": [{"E": [[
        {"G": 4, "T": 9, "P": 39.5, "C": 1.85},
        {"G": 4, "T": 10, "P": 39.5, "C": 1.85},
    ]]}]}, "basketball", scope="QUARTER_1")
    lanes = market_lanes({"FULL_MATCH": root, "QUARTER_1": q1})
    assert {(row["scope"], row["market_family"]) for row in lanes} == {
        ("FULL_MATCH", "match_total"),
        ("QUARTER_1", "match_total"),
    }
