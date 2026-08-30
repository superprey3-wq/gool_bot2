from gool_bot2.archive_flashscore import parse_finished_season_feed


def test_parse_finished_season_feed_ignores_live_matches():
    body = (
        "ZA÷Premier League~"
        "AA÷ABCD1234¬AB÷3¬AE÷Home¬AF÷Away¬AD÷1788091200¬AG÷2¬AH÷1~"
        "AA÷LIVE1234¬AB÷2¬AE÷Live Home¬AF÷Live Away¬AD÷1788094800¬AG÷0¬AH÷0"
    )
    rows = parse_finished_season_feed(body)
    assert len(rows) == 1
    assert rows[0].match_id == "ABCD1234"
    assert rows[0].league == "Premier League"
    assert rows[0].home == "Home"
    assert rows[0].away == "Away"
    assert (rows[0].home_score, rows[0].away_score) == (2, 1)
