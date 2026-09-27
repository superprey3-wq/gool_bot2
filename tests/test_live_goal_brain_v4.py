from gool_bot2.live_goal_brain_v4 import evaluate_live_goals


def rec(minute=20, score=(0, 0), stats=None, halftime=False):
    stats = stats or {}
    return {
        "match": {
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": halftime,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {"stats": stats},
            "fotmob": {"stats": stats},
        },
    }


def test_high_real_xg_pressure_can_bet():
    r = rec(stats={"xg": [1.4, 0.8], "shots": [10, 7], "shots_on_target": [5, 3], "big_chances": [3, 1], "touches_box": [20, 12], "corners": [4, 3]})
    r["live_momentum"] = {"minutes_in_epoch": 5, "xg_total_last_5m": 0.25, "shots_total_last_5m": 4, "sot_total_last_5m": 2}
    ds = evaluate_live_goals(r)
    assert any(d.decision == "BET" for d in ds)


def test_low_activity_is_no_bet():
    r = rec(minute=35, stats={"xg": [0.05, 0.04], "shots": [1, 1], "shots_on_target": [0, 0], "big_chances": [0, 0]})
    assert all(d.decision == "NO_BET" for d in evaluate_live_goals(r))


def test_no_evidence_returns_no_decisions():
    assert evaluate_live_goals(rec(stats={})) == []


def test_after_42_only_another_goal():
    ds = evaluate_live_goals(rec(minute=55, stats={"xg": [0.8, 0.7], "shots": [7, 6], "shots_on_target": [3, 2]}))
    assert ds and {d.market for d in ds} == {"ANOTHER_GOAL"}


def test_halftime_returns_no_decisions():
    assert evaluate_live_goals(rec(minute=45, halftime=True, stats={"xg": [1, 1], "shots": [8, 8]})) == []


def test_red_card_reduces_confidence():
    base = {"xg": [1.0, 0.8], "shots": [8, 7], "shots_on_target": [4, 3], "big_chances": [2, 1], "red_cards": [0, 0]}
    red = dict(base, red_cards=[1, 0])
    a = evaluate_live_goals(rec(stats=base))[0]
    b = evaluate_live_goals(rec(stats=red))[0]
    assert b.confidence < a.confidence
