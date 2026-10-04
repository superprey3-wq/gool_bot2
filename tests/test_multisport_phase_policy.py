from gool_bot2.xbet_multisport_markets import lane_phase_policy, live_scopes_from_period


def lane(scope, family="match_total"):
    return {"scope": scope, "market_family": family}


def test_hockey_live_only_current_period_total():
    for scope in ("FULL_MATCH", "PERIOD_1", "PERIOD_2", "PERIOD_3"):
        assert lane_phase_policy("hockey", "PREMATCH", lane(scope))[0]

    assert live_scopes_from_period("hockey", "2nd period") == {"PERIOD_2"}
    assert lane_phase_policy("hockey", "LIVE", lane("PERIOD_2"), "2nd period")[0]
    assert not lane_phase_policy("hockey", "LIVE", lane("FULL_MATCH"), "2nd period")[0]
    assert not lane_phase_policy("hockey", "LIVE", lane("PERIOD_1"), "2nd period")[0]
    assert not lane_phase_policy("hockey", "LIVE", lane("PERIOD_3"), "2nd period")[0]
    assert not lane_phase_policy("hockey", "LIVE", lane("PERIOD_2", "home_total"), "2nd period")[0]


def test_basket_live_only_current_quarter_total():
    assert live_scopes_from_period("basketball", "3rd quarter") == {"QUARTER_3"}
    assert lane_phase_policy("basketball", "LIVE", lane("QUARTER_3"), "3rd quarter")[0]
    assert not lane_phase_policy("basketball", "LIVE", lane("FULL_MATCH"), "3rd quarter")[0]
    assert not lane_phase_policy("basketball", "LIVE", lane("SECOND_HALF"), "3rd quarter")[0]
    assert not lane_phase_policy("basketball", "LIVE", lane("QUARTER_4"), "3rd quarter")[0]
    assert not lane_phase_policy("basketball", "LIVE", lane("QUARTER_3", "away_total"), "3rd quarter")[0]


def test_prematch_all_core_families_are_signal_eligible():
    for sport in ("hockey", "basketball"):
        for family in ("match_total", "home_total", "away_total", "handicap", "moneyline"):
            assert lane_phase_policy(sport, "PREMATCH", lane("FULL_MATCH", family))[0]


def test_live_scope_parser_understands_numeric_segments():
    assert live_scopes_from_period("hockey", "2") == {"PERIOD_2"}
    assert live_scopes_from_period("basketball", "4") == {"QUARTER_4"}
