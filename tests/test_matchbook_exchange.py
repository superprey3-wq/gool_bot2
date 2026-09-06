from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gool_bot2.matchbook_exchange import MatchbookExchangeCollector, decode_event, matchbook_context
from gool_bot2.multi_exchange_confirmation import apply_matchbook_confirmation
from gool_bot2.multi_money_flow import evaluate_money_flow


def _event() -> dict:
    return {
        "id": 101,
        "name": "Alpha FC vs Beta United",
        "status": "open",
        "in-running-flag": True,
        "allow-live-betting": True,
        "volume": 1200.0,
        "event-participants": [
            {"participant-name": "Alpha FC"},
            {"participant-name": "Beta United"},
        ],
        "markets": [
            {
                "id": 201,
                "name": "Match Odds",
                "status": "open",
                "in-running-flag": True,
                "volume": 700.0,
                "runners": [
                    {"name": "Alpha FC", "volume": 300.0, "prices": [{"side": "back", "odds": 2.0, "available-amount": 100}, {"side": "lay", "odds": 2.04, "available-amount": 80}]},
                    {"name": "Beta United", "volume": 200.0, "prices": [{"side": "back", "odds": 4.0, "available-amount": 50}, {"side": "lay", "odds": 4.2, "available-amount": 40}]},
                    {"name": "Draw", "volume": 200.0, "prices": [{"side": "back", "odds": 3.2, "available-amount": 60}, {"side": "lay", "odds": 3.3, "available-amount": 60}]},
                ],
            },
            {
                "id": 202,
                "name": "Total",
                "status": "open",
                "in-running-flag": True,
                "volume": 240.0,
                "runners": [
                    {"name": "OVER 2.5", "volume": 140.0, "prices": [{"side": "back", "odds": 1.80, "available-amount": 100}, {"side": "lay", "odds": 1.84, "available-amount": 120}]},
                    {"name": "UNDER 2.5", "volume": 100.0, "prices": [{"side": "back", "odds": 2.12, "available-amount": 90}, {"side": "lay", "odds": 2.18, "available-amount": 110}]},
                ],
            },
            {
                "id": 203,
                "name": "1st Half Total",
                "status": "open",
                "in-running-flag": True,
                "volume": 90.0,
                "runners": [
                    {"name": "OVER 1.5", "volume": 55.0, "prices": [{"side": "back", "odds": 2.00, "available-amount": 60}, {"side": "lay", "odds": 2.08, "available-amount": 60}]},
                    {"name": "UNDER 1.5", "volume": 35.0, "prices": [{"side": "back", "odds": 1.80, "available-amount": 70}, {"side": "lay", "odds": 1.86, "available-amount": 70}]},
                ],
            },
        ],
    }


def _book_market(fair: float, volume: float, back: float, lay: float) -> dict:
    return {
        "fair_over": fair,
        "volume": volume,
        "over": {
            "best_back": {"odds": 1.80, "available": back},
            "best_lay": {"odds": 1.82, "available": lay},
            "prices": [
                {"side": "back", "odds": 1.80, "available": back},
                {"side": "lay", "odds": 1.82, "available": lay},
            ],
        },
    }


def _money_flow_record(flow: dict) -> dict:
    return {
        "match": {
            "flashscore_event_id": "m1",
            "home": "Alpha",
            "away": "Beta",
            "minute": 60,
            "home_score": 1,
            "away_score": 1,
        },
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "101"},
            "systems": {
                "money_flow": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 2.5,
                    "market_status": "open",
                    "market_id": "202",
                    "market_name": "Total",
                    "volume": 1000.0,
                    "fair_over": 0.65,
                    "over": {
                        "best_back": {"odds": 1.70},
                        "best_lay": {"odds": 1.72},
                    },
                    "flow": flow,
                }
            },
        },
    }


def test_decode_matchbook_totals_and_orderbook() -> None:
    event = decode_event(_event())
    assert event is not None
    assert event["home"] == "Alpha FC"
    assert "FT:2.5" in event["totals"]
    assert "1H:1.5" in event["totals"]
    total = event["totals"]["FT:2.5"]
    assert total["volume"] == 240.0
    assert total["over"]["best_back"]["odds"] == 1.80
    assert total["over"]["best_lay"]["odds"] == 1.84
    assert 0.50 < total["fair_over"] < 0.60


def test_matchbook_context_selects_exact_next_goal_line() -> None:
    event = decode_event(_event())
    assert event is not None
    event["totals"]["FT:2.5"]["flow"] = {
        "level": "SUPPORT",
        "direction_pp": 1.7,
        "activity_volume": 35.0,
    }
    state = {"captured_at": "2026-09-05T18:00:00+00:00", "events": [event]}
    record = {
        "match": {
            "home": "Alpha",
            "away": "Beta United",
            "minute": 61,
            "home_score": 1,
            "away_score": 1,
        }
    }
    ctx = matchbook_context(record, state)
    assert ctx["available"] is True
    active = ctx["systems"]["another_goal"]
    assert active["line"] == 2.5
    assert active["liquid"] is True
    assert active["level"] == "SUPPORT"
    assert active["support"] is True


def test_flow_waits_for_real_30_and_60_second_windows() -> None:
    collector = MatchbookExchangeCollector(Path("unused.json"))
    base = {"fair_over": 0.50, "volume": 100.0}
    first = collector._flow("1", "FT:2.5", base, 100.0)
    assert first["window_ready_15s"] is False
    assert first["window_ready_30s"] is False
    assert first["window_ready_60s"] is False

    second = collector._flow("1", "FT:2.5", {"fair_over": 0.54, "volume": 150.0}, 116.0)
    assert second["window_ready_15s"] is True
    assert second["window_ready_30s"] is False
    assert second["window_ready_60s"] is False
    assert second["level"] == "NEUTRAL"

    third = collector._flow("1", "FT:2.5", {"fair_over": 0.55, "volume": 190.0}, 131.0)
    assert third["window_ready_30s"] is True
    assert third["window_ready_60s"] is False
    assert third["volume_delta_30s"] == 90.0
    assert third["fair_over_delta_pp_30s"] == 5.0
    assert third["level"] == "STRONG_SUPPORT"

    fourth = collector._flow("1", "FT:2.5", {"fair_over": 0.56, "volume": 230.0}, 161.0)
    assert fourth["window_ready_60s"] is True


def test_orderbook_confirmation_requires_persistent_pressure() -> None:
    collector = MatchbookExchangeCollector(Path("unused.json"))
    collector._flow("2", "FT:2.5", _book_market(0.50, 500.0, 100.0, 140.0), 100.0)
    second = collector._flow("2", "FT:2.5", _book_market(0.53, 700.0, 260.0, 160.0), 116.0)
    assert second["back_wom"] > 0.54
    assert second["orderbook_support_streak"] == 1

    third = collector._flow("2", "FT:2.5", _book_market(0.56, 1000.0, 360.0, 150.0), 131.0)
    assert third["window_ready_30s"] is True
    assert third["back_wom"] > 0.65
    assert third["orderflow_imbalance"] > 0.0
    assert third["orderbook_support_streak"] >= 2
    assert third["orderbook_confirmation_count"] >= 2
    assert third["orderbook_confirmed"] is True


def test_single_large_unmatched_spike_is_not_confirmation() -> None:
    collector = MatchbookExchangeCollector(Path("unused.json"))
    collector._flow("3", "FT:2.5", _book_market(0.50, 500.0, 100.0, 100.0), 100.0)
    spike = collector._flow("3", "FT:2.5", _book_market(0.51, 510.0, 800.0, 100.0), 116.0)
    assert spike["transient_liquidity_spike"] is True
    assert spike["orderbook_confirmed"] is False

    persisted = collector._flow("3", "FT:2.5", _book_market(0.54, 520.0, 800.0, 100.0), 131.0)
    assert persisted["transient_liquidity_spike"] is False
    assert persisted["orderbook_support_streak"] >= 2
    assert persisted["orderbook_confirmed"] is True


def test_money_flow_blocks_unconfirmed_orderbook() -> None:
    flow = {
        "window_ready_30s": True,
        "volume_delta_30s": 400.0,
        "fair_over_delta_pp_30s": 3.0,
        "orderbook_ready": True,
        "orderbook_confirmed": False,
        "transient_liquidity_spike": True,
        "liquidity_pull": False,
    }
    result = evaluate_money_flow(_money_flow_record(flow))
    assert result["eligible"] is False
    assert result["reason"] == "money_flow_transient_liquidity_spike"


def test_money_flow_keeps_orderbook_metrics_on_confirmed_signal() -> None:
    flow = {
        "window_ready_30s": True,
        "window_ready_60s": False,
        "volume_delta_30s": 400.0,
        "fair_over_delta_pp_30s": 3.0,
        "orderbook_ready": True,
        "orderbook_confirmed": True,
        "orderbook_confirmation_count": 3,
        "back_wom": 0.68,
        "book_imbalance": 0.36,
        "orderflow_imbalance": 0.42,
        "orderbook_support_streak": 3,
        "back_depth_weighted": 520.0,
        "lay_depth_weighted": 245.0,
    }
    result = evaluate_money_flow(_money_flow_record(flow))
    assert result["eligible"] is True
    assert result["orderbook_confirmed"] is True
    assert result["back_wom"] == 0.68
    assert result["orderflow_imbalance"] == 0.42
    assert result["orderbook_support_streak"] == 3


def test_matchbook_never_manufactures_bet_from_wait() -> None:
    decision = SimpleNamespace(status="WAIT", winner=None)
    record = {"matchbook_exchange": {"available": True}}
    assert apply_matchbook_confirmation(decision, record) is decision
    assert decision.status == "WAIT"


def test_matchbook_support_does_not_modify_main_brain_rating() -> None:
    row = SimpleNamespace(
        strategy="another_goal",
        rating=78.0,
        reason_tags=[],
        market_level="STRONG_STEAM",
        market_pressure_pp=6.5,
    )
    decision = SimpleNamespace(status="BET", winner=row)
    record = {
        "matchbook_exchange": {
            "available": True,
            "systems": {
                "another_goal": {
                    "available": True,
                    "level": "STRONG_SUPPORT",
                    "volume": 450.0,
                    "fair_over": 0.68,
                    "flow": {"direction_pp": 3.5, "activity_volume": 120.0},
                }
            },
        }
    }
    apply_matchbook_confirmation(decision, record)
    assert row.rating == 78.0
    assert row.reason_tags == []


def test_matchbook_opposition_does_not_veto_main_brain() -> None:
    row = SimpleNamespace(
        strategy="another_goal",
        rating=79.0,
        reason_tags=[],
        market_level="NEUTRAL",
        market_pressure_pp=0.0,
    )
    decision = SimpleNamespace(status="BET", winner=row)
    record = {
        "matchbook_exchange": {
            "available": True,
            "systems": {
                "another_goal": {
                    "available": True,
                    "level": "STRONG_OPPOSITION",
                    "volume": 800.0,
                    "fair_over": 0.42,
                    "flow": {"direction_pp": -4.1, "activity_volume": 180.0},
                }
            },
        }
    }
    apply_matchbook_confirmation(decision, record)
    assert row.rating == 79.0
    assert row.reason_tags == []
