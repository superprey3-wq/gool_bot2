from __future__ import annotations

from copy import deepcopy


def _total_market(hash_: str, *, line: float = 2.5, reversed_: bool = False) -> dict:
    one = f"Under {line}" if reversed_ else f"Over {line}"
    two = f"Over {line}" if reversed_ else f"Under {line}"
    return {
        "marketHash": hash_,
        "outcomeOneName": one,
        "outcomeTwoName": two,
        "teamOneName": "Home",
        "teamTwoName": "Away",
        "sportId": 5,
        "type": 2,
        "sportXeventId": "E1",
        "gameTime": 1780000000,
        "line": line,
        "mainLine": True,
    }


def _order(hash_: str, maker_probability: float, maker_usdc: int, *, maker_one: bool) -> dict:
    return {
        "marketHash": hash_,
        "percentageOdds": str(int(maker_probability * 10**20)),
        "totalBetSize": str(maker_usdc * 1_000_000),
        "fillAmount": "0",
        "isMakerBettingOutcomeOne": maker_one,
    }


def test_sxbet_total_builds_tb_tm_from_type_2_main_line() -> None:
    from gool_bot2.sxbet_totals import build_live_total_board

    market = _total_market("t1")
    orders = [
        _order("t1", 0.45, 450, maker_one=False),
        _order("t1", 0.55, 550, maker_one=True),
    ]

    board = build_live_total_board([market], orders)
    assert len(board) == 1
    row = board[0]
    assert row["line"] == 2.5
    assert row["outcomes"]["TB"]["probability"] == 0.55
    assert row["outcomes"]["TM"]["probability"] == 0.45
    assert row["outcomes"]["TB"]["decimal_odd"] == 1 / 0.55
    assert row["outcomes"]["TM"]["decimal_odd"] == 1 / 0.45
    assert row["liquidity_usdc"] > 0


def test_sxbet_total_handles_reversed_over_under_outcomes() -> None:
    from gool_bot2.sxbet_totals import build_live_total_board

    market = _total_market("t1", reversed_=True)
    orders = [
        _order("t1", 0.45, 450, maker_one=False),
        _order("t1", 0.55, 550, maker_one=True),
    ]
    row = build_live_total_board([market], orders)[0]
    assert row["outcomes"]["TB"]["probability"] == 0.45
    assert row["outcomes"]["TM"]["probability"] == 0.55


def test_sxbet_total_direction_needs_price_and_depth_confirmation() -> None:
    from gool_bot2 import sxbet_totals

    old = {
        "outcomes": {
            "TB": {"probability": 0.50, "decimal_odd": 2.0, "available_usdc": 300.0},
            "TM": {"probability": 0.50, "decimal_odd": 2.0, "available_usdc": 300.0},
        }
    }
    price_only = deepcopy(old)
    price_only["outcomes"]["TB"].update({"probability": 0.52, "decimal_odd": 1 / 0.52})
    assert sxbet_totals._flow(price_only, old)["level"] == "NEUTRAL"

    confirmed = deepcopy(price_only)
    confirmed["outcomes"]["TB"]["available_usdc"] = 420.0
    flow = sxbet_totals._flow(confirmed, old)
    assert flow["level"] == "STRONG_LIQUIDITY_PUSH"
    assert flow["outcome"] == "TB"
    assert flow["implied_delta_pp"] == 2.0
    assert flow["liquidity_delta_usdc"] == 120.0


def test_sxbet_totals_state_requests_only_live_main_totals(monkeypatch, tmp_path) -> None:
    from gool_bot2 import sxbet_totals

    markets = [_total_market("t1")]
    orders = [
        _order("t1", 0.45, 450, maker_one=False),
        _order("t1", 0.55, 550, maker_one=True),
    ]
    monkeypatch.setenv("SXBET_TOTALS_SNAPSHOT_PATH", str(tmp_path / "totals.json"))
    monkeypatch.setattr(sxbet_totals, "fetch_live_total_markets", lambda **_: markets)
    monkeypatch.setattr(sxbet_totals.sxbet_public, "fetch_orders", lambda hashes, **_: orders)

    state = sxbet_totals.fetch_sxbet_totals_state(timeout=0.1)
    assert state["available"] is True
    assert state["market_type"] == 2
    assert state["semantics"] == "executable_orderbook_liquidity_not_matched_volume"
    assert state["events"][0]["line"] == 2.5
    assert state["events"][0]["flow"]["level"] == "WARMING"
