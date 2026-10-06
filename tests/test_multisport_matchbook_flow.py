from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2 import matchbook_auth, matchbook_pagination
from gool_bot2.matchbook_exchange import decode_event
from gool_bot2.multisport_matchbook_flow import matchbook_money_flow


def _market(name: str, line: float, *, volume: float = 900.0):
    return {
        "id": f"m-{name}-{line}",
        "name": name,
        "status": "open",
        "in-running-flag": True,
        "volume": volume,
        "runners": [
            {
                "name": f"OVER {line}",
                "volume": volume * 0.55,
                "prices": [
                    {"side": "back", "odds": 1.80, "available-amount": 300},
                    {"side": "lay", "odds": 1.84, "available-amount": 250},
                ],
            },
            {
                "name": f"UNDER {line}",
                "volume": volume * 0.45,
                "prices": [
                    {"side": "back", "odds": 2.05, "available-amount": 250},
                    {"side": "lay", "odds": 2.10, "available-amount": 220},
                ],
            },
        ],
    }


def _event(*, sport_key: str, sport_id: int, market_name: str, line: float):
    return {
        "id": 123,
        "name": "Alpha vs Beta",
        "_gool_sport_key": sport_key,
        "_gool_sport_id": sport_id,
        "status": "open",
        "in-running-flag": True,
        "allow-live-betting": True,
        "event-participants": [
            {"participant-name": "Alpha"},
            {"participant-name": "Beta"},
        ],
        "markets": [_market(market_name, line)],
    }


def _flow_state(*, sport: str, period: str, line: float, pp: float, delta: float):
    return {
        "captured_at": "2026-10-07T00:00:30+00:00",
        "events": [
            {
                "event_id": "mb1",
                "name": "Alpha vs Beta",
                "home": "Alpha",
                "away": "Beta",
                "sport_key": sport,
                "in_running": True,
                "totals": {
                    f"{period}:{line:g}": {
                        "id": "market1",
                        "name": "Total",
                        "period": period,
                        "line": line,
                        "volume": 2500.0,
                        "fair_over": 0.61,
                        "flow": {
                            "window_ready_30s": True,
                            "window_ready_60s": True,
                            "volume_delta_30s": delta,
                            "volume_delta_60s": delta + 20.0,
                            "fair_over_delta_pp_30s": pp,
                            "fair_over_delta_pp_60s": pp,
                            "transient_liquidity_spike": False,
                            "liquidity_pull": False,
                            "level": "STRONG_SUPPORT" if pp > 0 else "STRONG_OPPOSITION",
                        },
                    }
                },
            }
        ],
    }


def test_matchbook_sport_lookup_resolves_football_basketball_and_hockey(monkeypatch):
    matchbook_pagination._SPORT_CACHE.update({"ts": 0.0, "mapping": {}})

    def fake_request(url: str, user_agent: str, *, retry_auth: bool = True):
        assert "lookups/sports" in url
        return {
            "sports": [
                {"id": 15, "name": "Soccer"},
                {"id": 4, "name": "Basketball"},
                {"id": 8, "name": "Ice Hockey"},
                {"id": 99, "name": "Tennis"},
            ]
        }

    monkeypatch.setattr(matchbook_auth, "_request_json", fake_request)
    mapping = matchbook_pagination.resolve_sport_ids(force=True)
    assert mapping == {15: "football", 4: "basketball", 8: "hockey"}
    params = matchbook_pagination._sport_filter_params()
    assert params == {"sport-ids": "4,8,15"}


def test_matchbook_sport_lookup_falls_back_to_soccer_tag(monkeypatch):
    matchbook_pagination._SPORT_CACHE.update({"ts": 0.0, "mapping": {}})

    def fail(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(matchbook_auth, "_request_json", fail)
    assert matchbook_pagination._sport_filter_params() == {"tag-url-names": "soccer"}


def test_decode_basketball_quarter_and_hockey_period_markets():
    basket = decode_event(_event(sport_key="basketball", sport_id=4, market_name="3rd Quarter Total", line=44.5))
    assert basket is not None
    assert basket["sport_key"] == "basketball"
    assert basket["sport_id"] == 4
    assert "QUARTER_3:44.5" in basket["totals"]

    hockey = decode_event(_event(sport_key="hockey", sport_id=8, market_name="2nd Period Total", line=1.5))
    assert hockey is not None
    assert hockey["sport_key"] == "hockey"
    assert "PERIOD_2:1.5" in hockey["totals"]


def test_real_matched_money_confirms_same_direction_basketball_total():
    state = _flow_state(sport="basketball", period="QUARTER_3", line=44.5, pp=3.4, delta=180.0)
    result = matchbook_money_flow(
        sport="basketball",
        home="Alpha",
        away="Beta",
        scope="QUARTER_3",
        line=45.5,
        direction="over",
        state=state,
        now=datetime(2026, 10, 7, 0, 1, 0, tzinfo=timezone.utc),
    )
    assert result["available"] is True
    assert result["confirmed"] is True
    assert result["strong"] is True
    assert result["agrees"] is True
    assert result["direction"] == "over"
    assert result["matched_volume_delta"] == 200.0
    assert result["line_gap"] == 1.0
    assert result["source"] == "matchbook_matched_volume"


def test_real_matched_money_can_confirm_under_as_fair_over_falls():
    state = _flow_state(sport="hockey", period="PERIOD_2", line=1.5, pp=-3.2, delta=100.0)
    result = matchbook_money_flow(
        sport="hockey",
        home="Alpha",
        away="Beta",
        scope="PERIOD_2",
        line=1.5,
        direction="under",
        state=state,
        now=datetime(2026, 10, 7, 0, 1, 0, tzinfo=timezone.utc),
    )
    assert result["confirmed"] is True
    assert result["strong"] is True
    assert result["agrees"] is True
    assert result["direction"] == "under"


def test_opposite_real_money_is_exposed_as_disagreement():
    state = _flow_state(sport="basketball", period="FT", line=180.5, pp=-3.8, delta=200.0)
    result = matchbook_money_flow(
        sport="basketball",
        home="Alpha",
        away="Beta",
        scope="FULL_MATCH",
        line=180.5,
        direction="over",
        state=state,
        now=datetime(2026, 10, 7, 0, 1, 0, tzinfo=timezone.utc),
    )
    assert result["confirmed"] is True
    assert result["strong"] is True
    assert result["agrees"] is False
    assert result["direction"] == "under"


def test_multisport_money_flow_rejects_wrong_sport_and_stale_state():
    wrong = _flow_state(sport="football", period="FT", line=180.5, pp=4.0, delta=200.0)
    result = matchbook_money_flow(
        sport="basketball",
        home="Alpha",
        away="Beta",
        scope="FULL_MATCH",
        line=180.5,
        direction="over",
        state=wrong,
        now=datetime(2026, 10, 7, 0, 1, 0, tzinfo=timezone.utc),
    )
    assert result["available"] is False
    assert result["reason"] == "matchbook_event_unmatched"

    stale = _flow_state(sport="basketball", period="FT", line=180.5, pp=4.0, delta=200.0)
    result = matchbook_money_flow(
        sport="basketball",
        home="Alpha",
        away="Beta",
        scope="FULL_MATCH",
        line=180.5,
        direction="over",
        state=stale,
        now=datetime(2026, 10, 7, 0, 5, 0, tzinfo=timezone.utc),
    )
    assert result["available"] is False
    assert result["reason"] == "matchbook_state_stale"


def test_money_flow_ignores_player_prop_even_when_its_line_is_closer():
    state = _flow_state(sport="basketball", period="QUARTER_3", line=45.5, pp=3.2, delta=120.0)
    event = state["events"][0]
    total = event["totals"].pop("QUARTER_3:45.5")
    total["line"] = 44.5
    total["name"] = "3rd Quarter Total"
    event["totals"]["QUARTER_3:44.5"] = total
    event["totals"]["QUARTER_3:45.5"] = {
        **total,
        "id": "player-prop",
        "name": "Player Points Total",
        "line": 45.5,
    }

    result = matchbook_money_flow(
        sport="basketball",
        home="Alpha",
        away="Beta",
        scope="QUARTER_3",
        line=45.5,
        direction="over",
        state=state,
        now=datetime(2026, 10, 7, 0, 1, 0, tzinfo=timezone.utc),
    )
    assert result["available"] is True
    assert result["market_id"] == "market1"
    assert result["market_name"] == "3rd Quarter Total"
    assert result["line_gap"] == 1.0
