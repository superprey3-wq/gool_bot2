from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _root(event: int, market: int) -> str:
    return f"AAPI/1/E/E_1/E/E_100003/E/E_1186459/E/E_4012456/E/E_{event}/M/E_{market}"


def test_selection_matched_subscription_is_live_and_lightweight() -> None:
    from gool_bot2.betdaq_selection_matched import selection_matched_fields

    fields = selection_matched_fields([11, 22, 33], 50001)
    assert fields[0] == 50001
    assert fields[1] == "11~22~33"
    assert fields[2] is False
    assert fields[4] is False


def test_zero_sized_old_price_is_not_best_quote(tmp_path: Path) -> None:
    from gool_bot2 import betdaq_exchange
    from gool_bot2.betdaq_selection_matched import SelectionMatchedBetdaqCollector

    SelectionMatchedBetdaqCollector(tmp_path / "state.json")
    attrs = {
        "1V1-1": "101",
        "1V1-2V1-1": "2.00",
        "1V1-2V1-2": "100.00",
        "1V1-3V1-1": "1.50",
        "1V1-3V1-2": "0",
        "1V1-3V2-1": "2.04",
        "1V1-3V2-2": "80.00",
    }
    runner = betdaq_exchange._decode_price_ladder(attrs, {101: "Home"})[0]
    assert runner["best_back"]["odds"] == 2.0
    assert runner["best_lay"]["odds"] == 2.04
    assert all(row["available"] > 0 for row in runner["prices"])


def test_market_matched_does_not_read_selection_child_topic(tmp_path: Path) -> None:
    from gool_bot2.betdaq_selection_matched import SelectionMatchedBetdaqCollector

    event = 100
    market = 200
    root = _root(event, market)
    collector = SelectionMatchedBetdaqCollector(tmp_path / "state.json")
    collector._topics = {
        f"{root}/MMA/GBP": {"1": "54800", "2": "60000"},
        f"{root}/MMA/GBP/SMA/101": {"1": "1234", "2": "2345"},
    }
    attrs = collector._market_topic_attrs(market, "/MMA/GBP")
    assert attrs["1"] == "54800"
    assert collector._selection_matched(market)[101]["for"] == 1234.0


def test_decode_market_attaches_real_selection_matched_and_depth(tmp_path: Path) -> None:
    from gool_bot2.betdaq_selection_matched import SelectionMatchedBetdaqCollector

    event = 100
    market = 200
    root = _root(event, market)
    collector = SelectionMatchedBetdaqCollector(tmp_path / "state.json")
    collector._topics = {
        f"{root}/MDP/1": {
            "1V1-1": "101",
            "1V1-2V1-1": "2.00",
            "1V1-2V1-2": "100.00",
            "1V1-3V1-1": "2.04",
            "1V1-3V1-2": "80.00",
        },
        f"{root}/MMA/GBP": {"1": "54800", "2": "60000"},
        f"{root}/MMA/GBP/SMA/101": {"1": "1234", "2": "2345"},
    }
    meta = {
        "id": market,
        "event_id": event,
        "kind": "match_odds",
        "name": "Match Odds",
        "selections": {101: "Home"},
    }
    decoded = collector._decode_market(meta)
    runner = decoded["runners"][0]
    assert decoded["matched_gbp"] == 54800.0
    assert runner["matched_for_gbp"] == 1234.0
    assert runner["matched_against_gbp"] == 2345.0
    assert runner["back_depth_gbp"] == 100.0
    assert runner["lay_depth_gbp"] == 80.0


def test_catalog_prefers_canonical_selection_language(tmp_path: Path) -> None:
    from gool_bot2.betdaq_selection_matched import SelectionMatchedBetdaqCollector

    event = 100
    market = 200
    root = _root(event, market)
    collector = SelectionMatchedBetdaqCollector(tmp_path / "state.json")
    collector._tracked_events = {event}
    collector._topics = {
        f"{root}/MEI": {"2": "3"},
        f"{root}/MEI/MEL/en": {"1": "Match Odds"},
        f"{root}/S/E_101/SEI/SEL/en": {"1": "Wrong duplicate"},
        f"{root}/S/E_101/SL/en": {"1": "Home"},
        f"{root}/S/E_102/SEI/SEL/en": {"1": "Wrong duplicate"},
        f"{root}/S/E_102/SL/en": {"1": "Draw"},
        f"{root}/S/E_103/SEI/SEL/en": {"1": "Away"},
        f"{root}/S/E_103/SL/en": {"1": "Away"},
    }
    labels = collector._catalog()[market]["selections"]
    assert labels == {101: "Home", 102: "Draw", 103: "Away"}


def test_invalid_duplicate_1x2_labels_disable_direction(tmp_path: Path) -> None:
    from gool_bot2.betdaq_selection_matched import SelectionMatchedBetdaqCollector

    collector = SelectionMatchedBetdaqCollector(tmp_path / "state.json")
    collector._markets = {}
    event_rows = [
        {
            "event_id": 100,
            "name": "Home v Away",
            "home": "Home",
            "away": "Away",
            "start": datetime.now(timezone.utc),
        }
    ]
    # Post-process the state directly to exercise the validation path without a socket.
    original = SelectionMatchedBetdaqCollector.__mro__[1]._build_state
    base_state = original(collector, event_rows)
    base_state["events"][0]["match_odds"] = {
        "runners": [
            {"label": "Home"},
            {"label": "Home"},
            {"label": "Away"},
        ],
        "flow": {"ready": True, "level": "FLOW"},
    }
    base_state["events"][0]["runners"] = base_state["events"][0]["match_odds"]["runners"]

    # Mirror only the validation section through a tiny synthetic parent result.
    class SyntheticCollector(SelectionMatchedBetdaqCollector):
        def __init__(self):
            pass

    synthetic = SyntheticCollector()
    parent = SelectionMatchedBetdaqCollector.__mro__[1]
    saved = parent._build_state
    try:
        parent._build_state = lambda self, rows: base_state
        checked = SelectionMatchedBetdaqCollector._build_state(synthetic, event_rows)
    finally:
        parent._build_state = saved

    event = checked["events"][0]
    assert event["match_odds"]["labels_valid"] is False
    assert event["flow"]["level"] == "INVALID_RUNNERS"
