from gool_bot2.providers.common import ProviderMatch, pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider
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


def test_consensus_keeps_provider_disagreement_visible():
    rows = [
        ProviderMatch("flashscore", "1", "A", "B", stats={"xg": (1.2, 0.4)}),
        ProviderMatch("fotmob", "2", "A", "B", stats={"xg": (0.8, 0.5)}),
        ProviderMatch("365scores", "3", "A", "B", stats={"xg": (1.0, 0.3)}),
    ]
    assert FootballDataFusion._consensus(rows, "xg") == (1.0, 0.4)
    assert FootballDataFusion._spread(rows, "xg") == (0.4, 0.2)
