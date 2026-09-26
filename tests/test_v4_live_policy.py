from gool_bot2.v4_live_policy import LiveV4Input, decide_live_v4


def test_live_v4_only_exposes_two_public_markets():
    row = LiveV4Input("btts", 55, 0.9, 0.9)
    assert decide_live_v4(row).reason == "unsupported_market"


def test_normal_signal_is_not_killed_by_nonperfect_secondary_features():
    row = LiveV4Input(
        "another_goal", 65, 0.72, 0.75,
        pressure=0.45, trend=0.35, expected_remaining=0.85,
    )
    decision = decide_live_v4(row)
    assert decision.allowed
    assert decision.tier in {"NORMAL", "STRONG"}


def test_bad_score_sync_is_hard_block():
    row = LiveV4Input("another_goal", 70, 0.95, 0.95, score_synced=False)
    decision = decide_live_v4(row)
    assert not decision.allowed
    assert decision.reason == "score_not_synced"


def test_late_second_half_remains_possible_for_strong_match():
    row = LiveV4Input(
        "another_goal", 84, 0.86, 0.9,
        pressure=0.85, trend=0.8, expected_remaining=1.0,
    )
    decision = decide_live_v4(row)
    assert decision.allowed
