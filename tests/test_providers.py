from gool_bot2.match_context import provider_pair
from gool_bot2.providers.common import ProviderMatch, pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.flashscore_stats_guard import parse_match_stats_body
from gool_bot2.providers.fusion import FootballDataFusion


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
