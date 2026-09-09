from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gool_bot2.betdaq_stream_priority_fix import prioritize_fresh_stream_market_ids


def _event(event_id: int, start: datetime) -> dict:
    return {
        "event_id": event_id,
        "name": f"Home {event_id} v Away {event_id}",
        "home": f"Home {event_id}",
        "away": f"Away {event_id}",
        "start": start,
    }


def _match_odds(event_id: int, market_id: int) -> dict:
    return {
        "event_id": event_id,
        "id": market_id,
        "name": "Match Odds",
        "kind": "match_odds",
    }


def test_stale_discovery_event_does_not_consume_stream_capacity():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    events = [
        _event(1, now - timedelta(hours=5)),
        _event(2, now + timedelta(minutes=30)),
    ]
    markets = {
        100: _match_odds(1, 100),
        200: _match_odds(2, 200),
    }

    selected = prioritize_fresh_stream_market_ids(events, markets, 1, now=now)

    assert selected == [200]


def test_live_event_inside_four_hour_window_is_kept():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    events = [
        _event(1, now - timedelta(hours=3, minutes=59)),
        _event(2, now + timedelta(minutes=30)),
    ]
    markets = {
        100: _match_odds(1, 100),
        200: _match_odds(2, 200),
    }

    selected = prioritize_fresh_stream_market_ids(events, markets, 1, now=now)

    assert selected == [100]
