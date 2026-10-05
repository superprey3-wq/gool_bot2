from gool_bot2.basketball_signal_card import render_basketball_live_card, render_basketball_prematch_card
from gool_bot2.hockey_signal_card import render_hockey_live_card, render_hockey_prematch_card
from gool_bot2.xbet_multisport_steam import SPORTS


def _signal(line=5.5):
    return {
        "direction": "over",
        "line": line,
        "odd": 1.86,
        "strength": 82,
        "metric_delta": 1.2,
        "probability_delta_pp": 3.4,
        "line_delta": 0.5,
        "moves": 4,
        "start": {"line": line - 0.5, "over": 1.94},
        "selection": f"ТБ {line:g}",
    }


def test_hockey_cards_render_as_png():
    live = {
        "home": "SKA", "away": "CSKA", "league": "KHL",
        "score": [2, 1], "match_score": [2, 1], "period": "2nd period",
        "clock_seconds": 455, "scope": "PERIOD_2", "market_family": "match_total",
    }
    pre = {
        "home": "SKA", "away": "CSKA", "league": "KHL",
        "start_ts": 1893456000, "scope": "PERIOD_3", "market_family": "match_total",
    }
    for png in (
        render_hockey_live_card(live, _signal(1.5), SPORTS["hockey"]),
        render_hockey_prematch_card(pre, _signal(1.5), SPORTS["hockey"]),
    ):
        assert png.startswith(b"\x89PNG")
        assert len(png) > 5000


def test_basketball_cards_render_as_png():
    live = {
        "home": "Denver", "away": "Utah", "league": "NBA",
        "score": [73, 71], "match_score": [73, 71], "period": "3rd quarter",
        "clock_seconds": 370, "scope": "QUARTER_3", "market_family": "match_total",
    }
    pre = {
        "home": "Denver", "away": "Utah", "league": "NBA",
        "start_ts": 1893456000, "scope": "FIRST_HALF", "market_family": "match_total",
    }
    for png in (
        render_basketball_live_card(live, _signal(53.5), SPORTS["basketball"]),
        render_basketball_prematch_card(pre, _signal(111.5), SPORTS["basketball"]),
    ):
        assert png.startswith(b"\x89PNG")
        assert len(png) > 5000


def test_prematch_cards_render_long_team_names_without_layout_crash():
    basket = {
        "home": "Sacramento Kings",
        "away": "Los Angeles Lakers",
        "league": "USA: NBA - Pre-season",
        "start_ts": 1791252000,
        "scope": "FIRST_HALF",
        "market_family": "match_total",
    }
    hockey = {
        "home": "MHC Spartak MAH",
        "away": "Mikhaylov Academy U20",
        "league": "RUSSIA: MHL",
        "start_ts": 1791300600,
        "scope": "PERIOD_1",
        "market_family": "away_total",
    }
    basket_png = render_basketball_prematch_card(
        basket,
        {**_signal(118.5), "selection": "1-я половина: ТМ 118.5", "direction": "under"},
        SPORTS["basketball"],
    )
    hockey_png = render_hockey_prematch_card(
        hockey,
        {**_signal(0.5), "selection": "1-й период: ИТБ2 0.5", "direction": "over"},
        SPORTS["hockey"],
    )
    assert basket_png.startswith(b"\x89PNG")
    assert hockey_png.startswith(b"\x89PNG")
    assert len(basket_png) > 5000
    assert len(hockey_png) > 5000
