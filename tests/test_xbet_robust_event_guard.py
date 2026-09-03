from __future__ import annotations

from types import SimpleNamespace

from gool_bot2.xbet_robust_event_guard import apply_event_guard


def _markets(over: float = 1.90, under: float = 1.90):
    return {
        "match_total": [{"line": 0.5, "over": over, "under": under}],
        "first_half_total": [],
        "home_total": [{"line": 0.5, "over": 1.80, "under": 2.00}],
        "away_total": [{"line": 0.5, "over": 1.85, "under": 1.95}],
        "btts": {"yes": 2.10, "no": 1.65},
    }


def _state(over: float = 1.90, under: float = 1.90, score=(0, 0)):
    return {
        "matches": {
            "m1": {
                "flashscore_event_id": "m1",
                "xbet_event_id": "x1",
                "score_home": score[0],
                "score_away": score[1],
                "markets": _markets(over, under),
                "pressure": {"match_total:0.5": {"prob_delta_pp": 8.0}},
                "line_move": True,
            }
        }
    }


def _obs(score=(0, 0), red=(0, 0), timeline=None):
    return {
        "m1": {
            "xbet_score": score,
            "red_cards": red,
            "timeline_score": score if timeline is None else timeline,
        }
    }


def _collector():
    return SimpleNamespace(snapshots={"m1": [{"old": True}]})


def test_red_card_blocks_markets_and_clears_fake_steam():
    collector = _collector()
    baseline = _state()
    assert apply_event_guard(collector, baseline, _obs(red=(0, 0)), now=100.0) is False

    after_red = _state(over=1.45, under=2.80)
    assert apply_event_guard(collector, after_red, _obs(red=(1, 0)), now=112.0) is True
    row = after_red["matches"]["m1"]
    assert row["repricing_guard"] is True
    assert row["repricing_guard_reason"].startswith("EVENT_REPRICE_")
    assert row["markets"] == {}
    assert row["pressure"] == {}
    assert collector.snapshots["m1"] == []


def test_penalty_like_one_cycle_odds_shock_is_not_treated_as_steam(monkeypatch):
    monkeypatch.setenv("XBET_ODDS_SHOCK_GUARD_PP", "12")
    collector = _collector()
    baseline = _state(over=2.00, under=1.80)
    assert apply_event_guard(collector, baseline, _obs(), now=200.0) is False

    # A penalty/VAR suspension can reopen with a large one-cycle reprice even
    # before the master score changes. This must never become MARKET_OVERRIDE.
    shock = _state(over=1.25, under=3.80)
    assert apply_event_guard(collector, shock, _obs(), now=212.0) is True
    row = shock["matches"]["m1"]
    assert row["repricing_guard_reason"] == "EVENT_REPRICE_ODDS_SHOCK"
    assert row["markets"] == {}
    assert row["pressure"] == {}


def test_normal_moderate_market_move_remains_actionable(monkeypatch):
    monkeypatch.setenv("XBET_ODDS_SHOCK_GUARD_PP", "12")
    collector = _collector()
    baseline = _state(over=1.90, under=1.90)
    assert apply_event_guard(collector, baseline, _obs(), now=300.0) is False

    normal = _state(over=1.82, under=1.98)
    assert apply_event_guard(collector, normal, _obs(), now=312.0) is False
    row = normal["matches"]["m1"]
    assert row["repricing_guard"] is False
    assert row["markets"]["match_total"][0]["over"] == 1.82


def test_score_epoch_change_blocks_post_goal_repricing_even_when_scores_are_synced():
    collector = _collector()
    baseline = _state(score=(0, 0))
    assert apply_event_guard(collector, baseline, _obs(score=(0, 0)), now=400.0) is False

    scored = _state(score=(1, 0))
    assert apply_event_guard(collector, scored, _obs(score=(1, 0), timeline=(1, 0)), now=412.0) is True
    row = scored["matches"]["m1"]
    assert row["repricing_guard"] is True
    assert row["repricing_guard_reason"] == "POST_GOAL_REPRICE"
    assert row["markets"] == {}


def test_score_desync_is_always_hard_block():
    collector = _collector()
    state = _state(score=(0, 0))
    assert apply_event_guard(collector, state, _obs(score=(1, 0), timeline=(0, 0)), now=500.0) is True
    row = state["matches"]["m1"]
    assert row["repricing_guard_reason"] == "SCORE_DESYNC"
    assert row["markets"] == {}
