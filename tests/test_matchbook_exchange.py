from __future__ import annotations

from types import SimpleNamespace

from gool_bot2.matchbook_exchange import decode_event, matchbook_context
from gool_bot2.multi_exchange_confirmation import apply_matchbook_confirmation


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


def test_matchbook_never_manufactures_bet_from_wait() -> None:
    decision = SimpleNamespace(status="WAIT", winner=None)
    record = {"matchbook_exchange": {"available": True}}
    assert apply_matchbook_confirmation(decision, record) is decision
    assert decision.status == "WAIT"


def test_strong_support_requires_xbet_confluence_for_rating_bonus() -> None:
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
    assert row.rating == 79.0
    assert "matchbook_xbet_confluence" in row.reason_tags


def test_exchange_opposition_makes_entry_stricter() -> None:
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
    assert row.rating == 77.0
    assert "matchbook_strong_opposition" in row.reason_tags
