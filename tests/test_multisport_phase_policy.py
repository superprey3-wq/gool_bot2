from gool_bot2.xbet_multisport_markets import lane_phase_policy, live_scopes_from_period


def lane(scope, family="match_total"):
    return {"scope": scope, "market_family": family}


def test_hockey_prematch_all_periods_allowed_but_live_only_current_period():
    assert lane_phase_policy("hockey", "PREMATCH", lane("PERIOD_1"))[0]
    assert lane_phase_policy("hockey", "PREMATCH", lane("PERIOD_2"))[0]
    assert lane_phase_policy("hockey", "PREMATCH", lane("PERIOD_3"))[0]
    assert lane_phase_policy("hockey", "LIVE", lane("FULL_MATCH"), "2nd period")[0]
    assert lane_phase_policy("hockey", "LIVE", lane("PERIOD_2"), "2nd period")[0]
    ok, reason = lane_phase_policy("hockey", "LIVE", lane("PERIOD_3"), "2nd period")
    assert not ok
    assert reason == "live_future_or_finished_segment"


def test_basket_live_current_quarter_and_half_only():
    scopes = live_scopes_from_period("basketball", "3rd quarter")
    assert scopes == {"FULL_MATCH", "QUARTER_3", "SECOND_HALF"}
    assert lane_phase_policy("basketball", "LIVE", lane("QUARTER_3"), "3rd quarter")[0]
    assert lane_phase_policy("basketball", "LIVE", lane("SECOND_HALF"), "3rd quarter")[0]
    assert not lane_phase_policy("basketball", "LIVE", lane("QUARTER_4"), "3rd quarter")[0]
    assert not lane_phase_policy("basketball", "LIVE", lane("FIRST_HALF"), "3rd quarter")[0]


def test_team_totals_follow_same_phase_scope_policy():
    assert lane_phase_policy("hockey", "LIVE", lane("PERIOD_1", "home_total"), "1st period")[0]
    assert not lane_phase_policy("hockey", "LIVE", lane("PERIOD_2", "away_total"), "1st period")[0]
    assert lane_phase_policy("basketball", "PREMATCH", lane("QUARTER_4", "away_total"))[0]
