from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gool_bot2.betdaq_selection_matched import (
    SelectionMatchedBetdaqCollector,
    prioritize_stream_market_ids,
)


def _event(event_id: int, start: datetime) -> dict:
    return {
        "event_id": event_id,
        "name": f"Home {event_id} v Away {event_id}",
        "home": f"Home {event_id}",
        "away": f"Away {event_id}",
        "start": start,
    }


def _market(event_id: int, market_id: int, kind: str, *, line: float | None = None, period: str = "FT") -> dict:
    row = {
        "event_id": event_id,
        "id": market_id,
        "name": kind,
        "kind": kind,
    }
    if kind == "total":
        row.update({"line": float(line or 0.0), "period": period})
    return row


def test_stream_plan_prioritizes_full_live_event_before_prematch():
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    events = [
        _event(1, now - timedelta(hours=1)),
        _event(2, now + timedelta(hours=2)),
    ]
    markets = {
        100: _market(1, 100, "match_odds"),
        101: _market(1, 101, "total", line=2.5),
        102: _market(1, 102, "total", line=3.5),
        103: _market(1, 103, "total", line=1.5, period="1H"),
        200: _market(2, 200, "match_odds"),
        201: _market(2, 201, "total", line=2.5),
        202: _market(2, 202, "total", line=3.5),
    }

    selected = prioritize_stream_market_ids(events, markets, 5, now=now)

    assert selected == [100, 101, 102, 103, 200]


def test_stream_plan_keeps_all_match_odds_when_board_fits_default_capacity():
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    events = []
    markets = {}
    match_ids = set()
    for event_id in range(1, 181):
        events.append(_event(event_id, now + timedelta(minutes=event_id)))
        match_id = 10000 + event_id
        total_id = 20000 + event_id
        match_ids.add(match_id)
        markets[match_id] = _market(event_id, match_id, "match_odds")
        markets[total_id] = _market(event_id, total_id, "total", line=2.5)

    selected = prioritize_stream_market_ids(events, markets, 450, now=now)

    assert match_ids <= set(selected)
    assert len(selected) == 360


def test_stream_plan_never_exceeds_limit():
    now = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    events = [_event(1, now - timedelta(minutes=30))]
    markets = {100: _market(1, 100, "match_odds")}
    for index in range(1, 20):
        markets[100 + index] = _market(1, 100 + index, "total", line=0.5 + index)

    selected = prioritize_stream_market_ids(events, markets, 7, now=now)

    assert len(selected) == 7
    assert selected[0] == 100


def test_collector_records_official_aapi_subscription_capacity_response(tmp_path):
    collector = SelectionMatchedBetdaqCollector(tmp_path / "state.json")
    # Official BETDAQ sample response for SubscribeDetailedMarketPrices(10):
    # correlation=122, RC000, subscriptionId=1, availableMarketsCount=499.
    message = "AAPI/6/D\x0210\x02F\x010\x02122\x011\x020\x012\x021\x014\x02499\x01"

    collector._apply_message(message)

    assert collector._subscription_last[10]["correlation_id"] == "122"
    assert collector._subscription_last[10]["return_code"] == "RC000"
    assert collector._subscription_last[10]["available_markets"] == 499
