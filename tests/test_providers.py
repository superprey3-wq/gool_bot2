from gool_bot2.match_context import provider_pair
from gool_bot2.providers.common import ProviderMatch, pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.flashscore_incident_guard import goals_from_incidents, parse_summary_incidents
from gool_bot2.providers.flashscore_stats_guard import parse_match_stats_body
from gool_bot2.providers.fusion import FootballDataFusion
from gool_bot2.providers.secondary_live_guard import (
    parse_365_goal_timeline,
    parse_365_stats_payload,
    parse_fotmob_goal_timeline,
)


def test_pair_score_matches_team_names():
    assert pair_score("Manchester United FC", "Arsenal", "Manchester United", "Arsenal FC") > 0.9


def test_flashscore_master_parser_extracts_live_match():
    body = "ZA÷Test League~AA÷ABCDEFGH¬AB÷2¬AC÷38¬AE÷Home FC¬AF÷Away FC¬AG÷1¬AH÷0"
    rows = FlashscoreProvider().parse_master_live(body)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider_match_id == "ABCDEFGH"
    assert row.home == "Home FC"
    assert row.away == "Away FC"
    assert row.home_score == 1
    assert row.away_score == 0
    assert row.minute == 45
    assert row.is_halftime is True


def test_flashscore_stats_keep_full_match_totals_over_second_half():
    body = (
        "SE÷Match~"
        "SD÷432¬SH÷0.68¬SI÷1.02~"
        "SD÷34¬SH÷6¬SI÷9~"
        "SD÷13¬SH÷2¬SI÷4~"
        "SD÷459¬SH÷2¬SI÷2~"
        "SD÷471¬SH÷10¬SI÷19~"
        "SE÷2nd Half~"
        "SD÷432¬SH÷0.04¬SI÷0.06~"
        "SD÷34¬SH÷0¬SI÷1~"
        "SD÷13¬SH÷0¬SI÷0~"
        "SD÷459¬SH÷0¬SI÷0~"
        "SD÷471¬SH÷1¬SI÷2"
    )
    stats = parse_match_stats_body(body)
    assert stats["xg"] == (0.68, 1.02)
    assert stats["shots"] == (6.0, 9.0)
    assert stats["shots_on_target"] == (2.0, 4.0)
    assert stats["big_chances"] == (2.0, 2.0)
    assert stats["touches_box"] == (10.0, 19.0)


def test_flashscore_match_section_wins_even_if_it_comes_after_period():
    body = (
        "SE÷1st Half~SD÷34¬SH÷2¬SI÷3~"
        "SE÷Match~SD÷34¬SH÷6¬SI÷9~"
        "SE÷2nd Half~SD÷34¬SH÷4¬SI÷6"
    )
    assert parse_match_stats_body(body)["shots"] == (6.0, 9.0)


def test_three_provider_consensus_rejects_one_bad_low_outlier():
    record = {
        "providers": {
            "flashscore": {"stats": {"shots": (1, 0), "xg": (0.10, 0.00)}},
            "fotmob": {"stats": {"shots": (6, 9), "xg": (0.68, 1.02)}},
            "365scores": {"stats": {"shots": (6, 8), "xg": (0.65, 0.98)}},
        }
    }
    assert provider_pair(record, "shots") == (6.0, 8.0)
    assert provider_pair(record, "xg") == (0.65, 0.98)


def test_empty_secondary_snapshots_do_not_erase_flashscore_activity():
    record = {
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": (6, 9),
                    "shots_on_target": (2, 4),
                    "big_chances": (2, 2),
                    "touches_box": (10, 19),
                    "xg": (0.68, 1.02),
                }
            },
            "fotmob": {"stats": {"shots": (0, 0), "shots_on_target": (0, 0), "big_chances": (0, 0)}},
            "365scores": {"stats": {"shots": (0, 0), "shots_on_target": (0, 0), "big_chances": (0, 0)}},
        }
    }
    assert provider_pair(record, "shots") == (6.0, 9.0)
    assert provider_pair(record, "shots_on_target") == (2.0, 4.0)
    assert provider_pair(record, "big_chances") == (2.0, 2.0)
    assert provider_pair(record, "xg") == (0.68, 1.02)


def test_two_provider_consensus_is_their_midpoint():
    record = {
        "providers": {
            "flashscore": {"stats": {"shots": (6, 9)}},
            "fotmob": {"stats": {"shots": (8, 7)}},
        }
    }
    assert provider_pair(record, "shots") == (7.0, 8.0)


def test_consensus_keeps_provider_disagreement_visible():
    rows = [
        ProviderMatch("flashscore", "1", "A", "B", stats={"xg": (1.2, 0.4)}),
        ProviderMatch("fotmob", "2", "A", "B", stats={"xg": (0.8, 0.5)}),
        ProviderMatch("365scores", "3", "A", "B", stats={"xg": (1.0, 0.3)}),
    ]
    assert FootballDataFusion._consensus(rows, "xg") == (1.0, 0.4)
    assert FootballDataFusion._spread(rows, "xg") == (0.4, 0.2)


def test_flashscore_goal_timeline_ignores_non_goals_and_keeps_added_time(monkeypatch):
    provider = FlashscoreProvider()
    # The 35' yellow-card row deliberately carries the *current* 2:0 score.
    # A legacy fallback is only allowed for chunks explicitly labelled goal.
    body = (
        "III÷card¬IA÷2¬IB÷35¬INX÷2¬IOX÷0"
        "~III÷goal1¬IA÷1¬IB÷26¬INX÷1¬IOX÷0"
        "~III÷goal2¬IA÷1¬IB÷45+2¬INX÷2¬IOX÷0"
    )
    monkeypatch.setattr(provider, "_feed", lambda _path: body)

    rows = provider.fetch_goal_timeline("ABCDEFGH")

    assert [row["minute"] for row in rows] == [26, 47]
    assert [row["score"] for row in rows] == [[1, 0], [2, 0]]
    assert rows[1]["display_minute"] == "45+2"
    assert rows[1]["period"] == "1H"
    assert all(row["event_type"] == "goal" for row in rows)


def test_flashscore_real_ia_ie_grammar_from_leones_atletico():
    body = (
        "III÷penalty¬IA÷2¬IB÷3'¬IE÷5¬IK÷Penalty Awarded"
        "¬IE÷10¬INX÷0¬IOX÷1¬IK÷Penalty¬IF÷Onate A."
        "~III÷goal¬IA÷2¬IB÷11'¬IE÷3¬INX÷0¬IOX÷2¬IK÷Goal¬IF÷Onate A."
        "~III÷red¬IA÷1¬IB÷78'¬IE÷2¬IK÷Red Card"
    )
    incidents = parse_summary_incidents(body)
    goals = goals_from_incidents(incidents)

    assert [row["minute"] for row in goals] == [3, 11]
    assert [row["side"] for row in goals] == ["away", "away"]
    assert [row["score"] for row in goals] == [[0, 1], [0, 2]]
    assert goals[0]["goal_kind"] == "penalty"
    red = [row for row in incidents if row["event_type"] == "red_card"]
    assert len(red) == 1
    assert red[0]["minute"] == 78
    assert red[0]["side"] == "home"


def test_flashscore_red_card_stat_id_22_is_cumulative():
    body = (
        "SE÷Match~SD÷23¬SG÷Yellow cards¬SH÷4¬SI÷1~SD÷22¬SG÷Red cards¬SH÷1¬SI÷0~"
        "SE÷2nd Half~SD÷23¬SG÷Yellow cards¬SH÷2¬SI÷1~SD÷22¬SG÷Red cards¬SH÷1¬SI÷0"
    )
    stats = parse_match_stats_body(body)
    assert stats["yellow_cards"] == (4.0, 1.0)
    assert stats["red_cards"] == (1.0, 0.0)


def test_fotmob_lower_coverage_still_provides_goal_timeline():
    detail = {
        "content": {
            "matchFacts": {
                "events": {
                    "events": [
                        {"type": "Goal", "time": 11, "isHome": False, "newScore": [0, 2], "player": {"name": "A"}},
                        {"type": "Goal", "time": 3, "isHome": False, "newScore": [0, 1], "goalDescription": "Penalty", "player": {"name": "A"}},
                    ]
                }
            },
            "momentum": False,
            "lineup": None,
            "stats": None,
            "shotmap": {"shots": []},
        }
    }
    goals = parse_fotmob_goal_timeline(detail)
    assert [row["minute"] for row in goals] == [3, 11]
    assert [row["score"] for row in goals] == [[0, 1], [0, 2]]
    assert goals[0]["goal_kind"] == "penalty"


def test_365_stats_endpoint_maps_real_cumulative_counters():
    payload = {
        "ttl": 10,
        "lastUpdateId": 5746578588,
        "statistics": [
            {"name": "Possession", "competitorId": 9829, "value": "67%"},
            {"name": "Total Shots", "competitorId": 9829, "value": "16"},
            {"name": "Shots On Target", "competitorId": 9829, "value": "7"},
            {"name": "Corners", "competitorId": 9829, "value": "11"},
            {"name": "Red Cards", "competitorId": 9829, "value": "1"},
            {"name": "Yellow Cards", "competitorId": 9829, "value": "4"},
            {"name": "Attacks", "competitorId": 9829, "value": "99"},
            {"name": "Possession", "competitorId": 10362, "value": "33%"},
            {"name": "Total Shots", "competitorId": 10362, "value": "6"},
            {"name": "Shots On Target", "competitorId": 10362, "value": "4"},
            {"name": "Corners", "competitorId": 10362, "value": "2"},
            {"name": "Red Cards", "competitorId": 10362, "value": "0"},
            {"name": "Yellow Cards", "competitorId": 10362, "value": "1"},
            {"name": "Attacks", "competitorId": 10362, "value": "57"},
        ],
    }
    stats = parse_365_stats_payload(payload, 9829, 10362)
    assert stats["possession"] == (67.0, 33.0)
    assert stats["shots"] == (16.0, 6.0)
    assert stats["shots_on_target"] == (7.0, 4.0)
    assert stats["corners"] == (11.0, 2.0)
    assert stats["red_cards"] == (1.0, 0.0)
    assert stats["yellow_cards"] == (4.0, 1.0)
    assert stats["attacks"] == (99.0, 57.0)


def test_365_goal_timeline_reconstructs_score_from_event_side():
    game = {
        "events": [
            {"competitorId": 10362, "gameTime": 2.0, "order": 2, "gameTimeDisplay": "2'", "eventType": {"id": 1, "name": "Goal", "subTypeName": "Penalty"}},
            {"competitorId": 10362, "gameTime": 11.0, "order": 3, "gameTimeDisplay": "11'", "eventType": {"id": 1, "name": "Goal", "subTypeName": "Field Goal"}},
        ]
    }
    goals = parse_365_goal_timeline(game, 9829, 10362)
    assert [row["score"] for row in goals] == [[0, 1], [0, 2]]
    assert goals[0]["goal_kind"] == "penalty"


def test_fusion_uses_secondary_exact_timeline_when_flashscore_is_incomplete():
    providers = [
        ProviderMatch("flashscore", "fs", "A", "B", meta={"provider_goal_timeline": []}),
        ProviderMatch("fotmob", "fm", "A", "B", meta={"provider_goal_timeline": [
            {"minute": 3, "score": [0, 1]},
            {"minute": 11, "score": [0, 2]},
        ]}),
        ProviderMatch("365scores", "sc", "A", "B", meta={"provider_goal_timeline": [
            {"minute": 2, "score": [0, 1]},
        ]}),
    ]
    timeline, source, audit = FootballDataFusion._select_goal_timeline(providers, (0, 2))
    assert source == "fotmob"
    assert [row["score"] for row in timeline] == [[0, 1], [0, 2]]
    assert audit["flashscore"]["exact_score_match"] is False
    assert audit["fotmob"]["exact_score_match"] is True
