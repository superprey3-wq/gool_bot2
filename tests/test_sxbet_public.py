from __future__ import annotations

from copy import deepcopy


def _market(hash_: str, outcome: str) -> dict:
    return {
        "marketHash": hash_,
        "outcomeOneName": outcome,
        "outcomeTwoName": "No",
        "teamOneName": "Home",
        "teamTwoName": "Away",
        "sportId": 5,
        "type": 1,
        "sportXeventId": "E1",
        "gameTime": 1780000000,
    }


def _order(hash_: str, maker_probability: float, maker_usdc: int) -> dict:
    return {
        "marketHash": hash_,
        "percentageOdds": str(int(maker_probability * 10**20)),
        "totalBetSize": str(maker_usdc * 1_000_000),
        "fillAmount": "0",
        "isMakerBettingOutcomeOne": False,
    }


def test_sxbet_builds_real_executable_1x2_depth() -> None:
    from gool_bot2.sxbet_public import build_live_1x2_board

    markets = [_market("h1", "Home"), _market("h2", "Tie"), _market("h3", "Away")]
    orders = [
        _order("h1", 0.60, 600),
        _order("h2", 0.70, 700),
        _order("h3", 0.80, 800),
    ]

    board = build_live_1x2_board(markets, orders)
    assert len(board) == 1
    event = board[0]

    assert event["outcomes"]["P1"]["probability"] == 0.40
    assert event["outcomes"]["P1"]["decimal_odd"] == 2.50
    assert event["outcomes"]["P1"]["available_usdc"] == 400.0
    assert event["outcomes"]["X"]["available_usdc"] == 300.0
    assert event["outcomes"]["P2"]["available_usdc"] == 200.0
    assert event["liquidity_usdc"] == 900.0


def test_sxbet_direction_needs_price_and_depth_confirmation() -> None:
    from gool_bot2 import sxbet_public

    old = {
        "outcomes": {
            "P1": {"probability": 0.40, "decimal_odd": 2.50, "available_usdc": 400.0},
            "X": {"probability": 0.30, "decimal_odd": 3.3333, "available_usdc": 300.0},
            "P2": {"probability": 0.20, "decimal_odd": 5.00, "available_usdc": 200.0},
        }
    }

    price_only = deepcopy(old)
    price_only["outcomes"]["P1"].update({"probability": 0.42, "decimal_odd": 1 / 0.42})
    assert sxbet_public._flow(price_only, old)["level"] == "NEUTRAL"

    confirmed = deepcopy(price_only)
    confirmed["outcomes"]["P1"]["available_usdc"] = 500.0
    flow = sxbet_public._flow(confirmed, old)
    assert flow["level"] == "STRONG_LIQUIDITY_PUSH"
    assert flow["outcome"] == "P1"
    assert flow["implied_delta_pp"] == 2.0
    assert flow["liquidity_delta_usdc"] == 100.0


def test_sxbet_state_is_anonymous_orderbook_not_fake_matched(monkeypatch, tmp_path) -> None:
    from gool_bot2 import sxbet_public

    markets = [_market("h1", "Home"), _market("h2", "Tie"), _market("h3", "Away")]
    orders = [
        _order("h1", 0.60, 600),
        _order("h2", 0.70, 700),
        _order("h3", 0.80, 800),
    ]
    monkeypatch.setenv("SXBET_BOARD_SNAPSHOT_PATH", str(tmp_path / "sx.json"))
    monkeypatch.setattr(sxbet_public, "fetch_live_soccer_markets", lambda **_: markets)
    monkeypatch.setattr(sxbet_public, "fetch_orders", lambda hashes, **_: orders)

    state = sxbet_public.fetch_sxbet_state(timeout=0.1)

    assert state["available"] is True
    assert state["source"] == "sxbet_public_rest"
    assert state["currency"] == "USDC"
    assert state["semantics"] == "executable_orderbook_liquidity_not_matched_volume"
    assert state["events"][0]["flow"]["level"] == "WARMING"
    assert "matched" not in state["events"][0]
