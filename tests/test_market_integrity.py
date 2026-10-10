from __future__ import annotations

from gool_bot2.market_integrity import (
    basketball_points_market_anomaly,
    bookmaker_kickoff_compatible,
)
from gool_bot2.xbet_multisport_steam import map_xbet_to_flashscore


def test_basketball_misdecoded_three_point_line_is_not_team_points():
    bad = {"scope": "QUARTER_4", "market_family": "home_total", "line": 2.5}
    good = {"scope": "QUARTER_4", "market_family": "home_total", "line": 21.5}
    assert basketball_points_market_anomaly(bad) == "implausibly_low_quarter_points"
    assert basketball_points_market_anomaly(good) is None
    assert basketball_points_market_anomaly(
        {"scope": "QUARTER_4", "market_family": "match_total", "line": 42.5}
    ) is None


def test_kickoff_guard_rejects_cross_day_matches_even_with_exact_team_names():
    first = {"O1": "Saitama", "O2": "Niigata", "I": 759974398, "S": 1791648000}
    today = {"home": "Saitama", "away": "Niigata", "start_ts": 1791648000}
    tomorrow = {"home": "Saitama", "away": "Niigata", "start_ts": 1791734400}
    assert bookmaker_kickoff_compatible(first, today)
    assert not bookmaker_kickoff_compatible(first, tomorrow)
    mapping = map_xbet_to_flashscore([first], [tomorrow], min_score=.7, min_side=.5)
    assert mapping == []
    matching = map_xbet_to_flashscore([first], [today, tomorrow], min_score=.7, min_side=.5)
    assert len(matching) == 1
    assert matching[0][1] is today


def test_millisecond_kickoff_supported_and_missing_legacy_timestamps_preserved():
    bookmaker = {"S": 1791648000000}
    event = {"start_ts": 1791648000}
    assert bookmaker_kickoff_compatible(bookmaker, event)
    assert bookmaker_kickoff_compatible({}, event)


def test_historical_v3_blocks_suspect_points_market(monkeypatch):
    from gool_bot2 import xbet_multisport_steam as steam
    def no_historical_fetch(*args, **kwargs):
        raise AssertionError("suspect market must not reach the history scorer")
    monkeypatch.setattr(steam, "best_market", no_historical_fetch)
    assert steam.basketball_historical_market_signal(
        {"status": "READY"},
        {
            "scope": "QUARTER_4",
            "market_family": "home_total",
            "line": 2.5,
            "over": 2.43,
            "under": 1.6,
        },
        phase="PREMATCH",
    ) is None
