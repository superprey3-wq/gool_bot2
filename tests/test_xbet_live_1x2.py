from gool_bot2.xbet_market_pressure import XBetMarketCollector, decode_markets, live_1x2_context


def _game() -> dict:
    return {
        "GE": [{
            "E": [
                [{"T": 1, "C": 2.50, "G": 1}],
                [{"T": 2, "C": 3.20, "G": 1}],
                [{"T": 3, "C": 3.10, "G": 1}],
            ]
        }]
    }


def test_decode_live_1x2_and_remove_margin() -> None:
    markets = decode_markets(_game())
    one = markets["match_1x2"]

    assert one["home"] == 2.50
    assert one["draw"] == 3.20
    assert one["away"] == 3.10
    assert one["overround"] > 0
    assert abs(sum(one["fair"].values()) - 1.0) < 1e-5


def test_1x2_is_included_in_score_epoch_pressure() -> None:
    markets = decode_markets(_game())
    flat = XBetMarketCollector._flat(markets)

    assert set(flat) >= {
        "match_1x2:home",
        "match_1x2:draw",
        "match_1x2:away",
    }
    assert abs(sum(flat[key]["prob"] for key in (
        "match_1x2:home", "match_1x2:draw", "match_1x2:away"
    )) - 1.0) < 1e-5


def test_live_1x2_context_exposes_odds_fair_and_movement() -> None:
    markets = decode_markets(_game())
    row = {
        "captured_at": "2026-09-05T16:00:00+00:00",
        "markets": markets,
        "pressure": {
            "match_1x2:home": {"prob_delta_pp": -1.2},
            "match_1x2:draw": {"prob_delta_pp": 3.4},
            "match_1x2:away": {"prob_delta_pp": -2.2},
        },
    }
    ctx = live_1x2_context(row)

    assert ctx["available"] is True
    assert ctx["odds"] == {"home": 2.5, "draw": 3.2, "away": 3.1}
    assert abs(sum(ctx["fair"].values()) - 1.0) < 1e-5
    assert ctx["delta_pp"]["draw"] == 3.4
