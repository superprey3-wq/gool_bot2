from gool_bot2.prematch_full_market_runtime import select_production_full_market_pick
from gool_bot2.v4_prematch_settlement import settle_prematch_pick


def _analysis(row):
    return {"candidates": [row]}


def _row(**overrides):
    row = {
        "scope": "FULL_TIME",
        "market_type": "DOUBLE_CHANCE",
        "selection": "HOME_OR_DRAW",
        "odds": 1.45,
        "honest_probability": 0.73,
        "market_probability": 0.68,
        "probability_range_low": 0.62,
        "probability_range_high": 0.84,
        "expected_value": 0.04,
        "quality": 0.82,
        "status": "BET",
        "confidence_score": 76.0,
        "confidence_grade": "HIGH",
        "bookmaker": "TestBook",
    }
    row.update(overrides)
    return row


def test_full_market_candidate_converts_to_production_pick():
    picked = select_production_full_market_pick(
        _analysis(_row()),
        event_id="m1", home="A", away="B", league="L", kickoff_ts=1.0,
    )
    assert picked is not None
    pick, meta = picked
    assert pick.market == "double_chance"
    assert pick.selection == "home or draw"
    assert meta["full_market_confidence"] == 76.0


def test_quarter_asian_handicap_stays_research_only():
    picked = select_production_full_market_pick(
        _analysis(_row(
            market_type="ASIAN_HANDICAP",
            selection="HOME -0.25",
        )),
        event_id="m1", home="A", away="B", league="L", kickoff_ts=1.0,
    )
    assert picked is None


def test_new_full_time_settlement_families():
    assert settle_prematch_pick({"market": "double_chance", "market_family": "double_chance", "selection": "home or draw"}, 1, 1) == "won"
    assert settle_prematch_pick({"market": "double_chance", "market_family": "double_chance", "selection": "home or away"}, 1, 1) == "lost"
    assert settle_prematch_pick({"market": "draw_no_bet", "market_family": "draw_no_bet", "selection": "away"}, 1, 1) == "push"
    assert settle_prematch_pick({"market": "draw_no_bet", "market_family": "draw_no_bet", "selection": "away"}, 0, 2) == "won"
    assert settle_prematch_pick({"market": "asian_handicap", "market_family": "asian_handicap", "selection": "away +1.5"}, 2, 1) == "won"
    assert settle_prematch_pick({"market": "asian_handicap", "market_family": "asian_handicap", "selection": "home -1"}, 2, 1) == "push"
    assert settle_prematch_pick({"market": "european_handicap", "market_family": "european_handicap", "selection": "away +2"}, 2, 1) == "won"
    assert settle_prematch_pick({"market": "european_handicap", "market_family": "european_handicap", "selection": "draw (home -1)"}, 2, 1) == "won"
