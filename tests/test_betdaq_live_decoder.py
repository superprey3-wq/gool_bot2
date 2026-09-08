from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def test_runner_line_accepts_live_betdaq_parentheses() -> None:
    from gool_bot2.betdaq_production import runner_line

    assert runner_line("Over (0.5)") == ("over", 0.5)
    assert runner_line("Under (2.5)") == ("under", 2.5)
    assert runner_line("Over 3.5") == ("over", 3.5)
    assert runner_line("Home Team Total Goals Over (0.5)") == (None, None)


def test_event_hierarchy_is_persistent_not_fetch_only() -> None:
    from gool_bot2 import betdaq_exchange
    from gool_bot2.betdaq_production import event_hierarchy_fields

    fields = event_hierarchy_fields()
    assert fields[2] == betdaq_exchange.SOCCER_ID
    assert fields[5] is False
    assert fields[11] is True


def test_market_information_subscription_uses_real_total_types() -> None:
    from gool_bot2.betdaq_production import GOOL_MARKET_TYPES, market_information_fields

    fields = market_information_fields(15139301, 1001)
    requested = {int(value) for value in str(fields[4]).split("~")}

    assert requested == set(GOOL_MARKET_TYPES)
    assert {3, 4, 17, 27, 40, 46} <= requested
    assert 13 in requested  # backwards compatibility only; 13 is Unspecified
    assert fields[7] is False  # SubscribeMarketInformation.fetchOnly is deprecated/must be false
    assert fields[8] is True


def test_live_half_total_market_is_recognized() -> None:
    from gool_bot2 import betdaq_exchange
    from gool_bot2.betdaq_production import install_live_decoder

    install_live_decoder()
    selections = {327054935: "Over (0.5)", 327054936: "Under (0.5)"}
    assert betdaq_exchange._goal_total_market("Half-time Totals Over/Under (0.5)", selections) == ("1H", 0.5)
    assert betdaq_exchange._goal_total_market("Home Team Total Goals Over/Under (0.5)", selections) is None


def test_live_aapi_price_ladder_decodes_actual_payload_shape() -> None:
    from gool_bot2 import betdaq_exchange
    from gool_bot2.betdaq_production import install_live_decoder

    install_live_decoder()
    attrs = {
        "1V1-1": "327054936",
        "1V1-2V1-1": "3.6",
        "1V1-2V1-2": "7.69",
        "1V1-3V1-1": "5",
        "1V1-3V1-2": "20.00",
        "1V2-1": "327054935",
        "1V2-2V1-1": "1.25",
        "1V2-2V1-2": "80.00",
        "1V2-3V1-1": "1.39",
        "1V2-3V1-2": "20.00",
    }
    labels = {327054935: "Over (0.5)", 327054936: "Under (0.5)"}
    runners = betdaq_exchange._decode_price_ladder(attrs, labels)
    by_label = {row["label"]: row for row in runners}

    assert by_label["Over (0.5)"]["best_back"]["odds"] == 1.25
    assert by_label["Over (0.5)"]["best_lay"]["odds"] == 1.39
    assert by_label["Under (0.5)"]["best_back"]["odds"] == 3.6
    assert by_label["Under (0.5)"]["best_lay"]["odds"] == 5.0


def test_production_catalog_keeps_current_half_total_market_type(tmp_path: Path) -> None:
    from gool_bot2.betdaq_production import ProductionBetdaqExchangeCollector

    event = 15139301
    market = 52961327
    over = 327054935
    under = 327054936
    root = f"AAPI/1/E/E_1/E/E_100003/E/E_1186459/E/E_4012456/E/E_{event}/M/E_{market}"
    collector = ProductionBetdaqExchangeCollector(tmp_path / "betdaq.json")
    collector._tracked_events = {event}
    collector._topics = {
        f"{root}/MEI": {"2": "40"},
        f"{root}/MEI/MEL/en": {"1": "Half-time Totals Over/Under (0.5)"},
        f"{root}/S/E_{over}/SEI/SEL/en": {"1": "Over (0.5)"},
        f"{root}/S/E_{under}/SEI/SEL/en": {"1": "Under (0.5)"},
    }

    catalog = collector._catalog()
    assert market in catalog
    assert catalog[market]["kind"] == "total"
    assert catalog[market]["period"] == "1H"
    assert catalog[market]["line"] == 0.5


def test_production_catalog_keeps_legacy_unspecified_total_when_runners_validate(tmp_path: Path) -> None:
    from gool_bot2.betdaq_production import ProductionBetdaqExchangeCollector

    event = 15139301
    market = 52961328
    root = f"AAPI/1/E/E_1/E/E_100003/E/E_1186459/E/E_4012456/E/E_{event}/M/E_{market}"
    collector = ProductionBetdaqExchangeCollector(tmp_path / "betdaq.json")
    collector._tracked_events = {event}
    collector._topics = {
        f"{root}/MEI": {"2": "13"},
        f"{root}/MEI/MEL/en": {"1": "Totals Over/Under (2.5)"},
        f"{root}/S/E_1/SEI/SEL/en": {"1": "Over (2.5)"},
        f"{root}/S/E_2/SEI/SEL/en": {"1": "Under (2.5)"},
    }

    catalog = collector._catalog()
    assert catalog[market]["kind"] == "total"
    assert catalog[market]["period"] == "FT"
    assert catalog[market]["line"] == 2.5


def test_match_odds_does_not_depend_on_exact_english_market_name(tmp_path: Path) -> None:
    from gool_bot2.betdaq_production import ProductionBetdaqExchangeCollector

    event = 15139301
    market = 52961329
    root = f"AAPI/1/E/E_1/E/E_100003/E/E_1186459/E/E_4012456/E/E_{event}/M/E_{market}"
    collector = ProductionBetdaqExchangeCollector(tmp_path / "betdaq.json")
    collector._tracked_events = {event}
    collector._topics = {
        f"{root}/MEI": {"2": "3"},
        f"{root}/MEI/MEL/en": {"1": "90 Minutes Match Odds"},
    }

    catalog = collector._catalog()
    assert catalog[market]["kind"] == "match_odds"
    assert catalog[market]["name"] == "90 Minutes Match Odds"


def test_betdaq_flow_helper_has_own_calibration_and_windows(monkeypatch, tmp_path: Path) -> None:
    from gool_bot2.betdaq_production import BetdaqFlowHelper

    monkeypatch.setenv("MATCHBOOK_FLOW_WOM_MIN", "0.91")
    monkeypatch.setenv("BETDAQ_FLOW_WOM_MIN", "0.50")
    helper = BetdaqFlowHelper(tmp_path / "betdaq-flow.json")

    def market(volume: float, fair: float) -> dict:
        over = {
            "best_back": {"odds": 1.80, "available": 400.0},
            "best_lay": {"odds": 1.82, "available": 100.0},
            "prices": [
                {"side": "back", "odds": 1.80, "available": 400.0},
                {"side": "lay", "odds": 1.82, "available": 100.0},
            ],
        }
        return {"volume": volume, "fair_over": fair, "over": over, "under": {}}

    first = helper._flow("bd-env-test", "FT:2.5", market(1000.0, 0.50), 1000.0)
    second = helper._flow("bd-env-test", "FT:2.5", market(1400.0, 0.54), 1031.0)

    assert first["window_ready_30s"] is False
    assert second["window_ready_30s"] is True
    assert second["volume_delta_30s"] == 400.0
    assert second["fair_over_delta_pp_30s"] == 4.0
    assert second["back_wom"] > 0.5
    assert os.environ["MATCHBOOK_FLOW_WOM_MIN"] == "0.91"


def test_betdaq_context_targets_next_half_goal_line() -> None:
    from gool_bot2.betdaq_exchange import betdaq_context

    now = datetime.now(timezone.utc).isoformat()
    total_market = {
        "id": "52960000",
        "name": "Totals Over/Under (2.5)",
        "status": "open",
        "volume": 2000.0,
        "fair_over": 0.58,
        "over": {"best_back": {"odds": 1.8}, "best_lay": {"odds": 1.82}},
        "under": {"best_back": {"odds": 2.1}, "best_lay": {"odds": 2.12}},
        "flow": {"window_ready_30s": True},
    }
    state = {
        "captured_at": now,
        "available": True,
        "events": [
            {
                "event_id": "99",
                "name": "Home v Away",
                "home": "Home",
                "away": "Away",
                "totals": {"FT:2.5": total_market},
            }
        ],
    }
    record = {
        "match": {
            "home": "Home",
            "away": "Away",
            "minute": 67,
            "home_score": 1,
            "away_score": 1,
            "is_finished": False,
        }
    }

    context = betdaq_context(record, state)
    flow = context["systems"]["money_flow"]
    assert context["available"] is True
    assert flow["available"] is True
    assert flow["period"] == "FT"
    assert flow["line"] == 2.5
