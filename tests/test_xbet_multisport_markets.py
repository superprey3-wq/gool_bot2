from gool_bot2.xbet_multisport_markets import (
    decode_core_markets,
    market_lanes,
    prematch_parlay_market_lanes,
    scope_from_subgame,
    period_scores,
    lane_phase_policy,
    policy_text_ru,
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



def test_period_scores_reads_real_sc_ps_shape_and_builds_halves():
    game = {
        "SC": {
            "PS": [
                {"Key": 1, "Value": {"S1": 19, "S2": 20, "NF": "1st quarter"}},
                {"Key": 2, "Value": {"S1": 24, "S2": 18, "NF": "2nd quarter"}},
                {"Key": 3, "Value": {"S1": 21, "S2": 22, "NF": "3rd quarter"}},
                {"Key": 4, "Value": {"S1": 20, "S2": 25, "NF": "4th quarter"}},
            ]
        }
    }
    scores = period_scores(game, "basketball")
    assert scores["QUARTER_1"] == (19, 20)
    assert scores["QUARTER_4"] == (20, 25)
    assert scores["FIRST_HALF"] == (43, 38)
    assert scores["SECOND_HALF"] == (41, 47)


def test_parlay_market_lanes_keep_all_total_lines_not_only_balanced_one():
    decoded = decode_core_markets({
        "GE": [{"E": [[
            {"G": 4, "T": 9, "P": 150.5, "C": 1.50},
            {"G": 4, "T": 10, "P": 150.5, "C": 2.55},
            {"G": 4, "T": 9, "P": 160.5, "C": 1.85},
            {"G": 4, "T": 10, "P": 160.5, "C": 1.85},
            {"G": 4, "T": 9, "P": 170.5, "C": 2.45},
            {"G": 4, "T": 10, "P": 170.5, "C": 1.52},
        ]]}],
    }, "basketball", scope="FULL_MATCH")

    regular = market_lanes({"FULL_MATCH": decoded})
    parlay = prematch_parlay_market_lanes({"FULL_MATCH": decoded}, "basketball")

    assert len([x for x in regular if x["market_family"] == "match_total"]) == 1
    assert {
        x["line"] for x in parlay if x["market_family"] == "match_total"
    } == {150.5, 160.5, 170.5}


def test_basketball_live_policy_allows_only_current_quarter_total():
    ok, reason = lane_phase_policy(
        "basketball",
        "LIVE",
        {"scope": "QUARTER_2", "market_family": "match_total"},
        period="2nd quarter",
    )
    assert ok is True
    assert reason == "basketball_live_current_quarter_total"

    assert lane_phase_policy(
        "basketball",
        "LIVE",
        {"scope": "FULL_MATCH", "market_family": "match_total"},
        period="2nd quarter",
    )[0] is False
    assert lane_phase_policy(
        "basketball",
        "LIVE",
        {"scope": "FULL_MATCH", "market_family": "away_total"},
        period="2nd quarter",
    )[0] is False
    assert lane_phase_policy(
        "basketball",
        "LIVE",
        {"scope": "QUARTER_1", "market_family": "match_total"},
        period="2nd quarter",
    )[0] is False


def test_basketball_policy_text_matches_two_quarter_live_rule():
    _, live = policy_text_ru("basketball")
    assert "текущей четверти" in live
    assert "2 четверти за матч" in live
    assert "ИТБ/ИТМ всего матча" not in live
