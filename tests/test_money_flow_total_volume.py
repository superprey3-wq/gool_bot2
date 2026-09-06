from __future__ import annotations

from gool_bot2 import multi_money_flow
from gool_bot2.multi_money_flow_total_volume import enhance_money_flow_result


def _record(
    *,
    league: str = "Czech Republic: CFL",
    volume: float = 8500.0,
    delta_30: float = 80.0,
    fair_pp_30: float = 1.1,
    delta_60: float = 120.0,
    fair_pp_60: float = 1.3,
):
    return {
        "match": {
            "flashscore_event_id": "cz123",
            "home": "Prague B",
            "away": "Brno B",
            "league": league,
            "minute": 58,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": []}}},
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "mb-cz"},
            "systems": {
                "another_goal": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 2.5,
                    "market_id": "m-cz",
                    "market_name": "Total Goals 2.5",
                    "market_status": "open",
                    "volume": volume,
                    "fair_over": 0.61,
                    "over": {
                        "best_back": {"odds": 1.72, "available": 180.0},
                        "best_lay": {"odds": 1.74, "available": 160.0},
                    },
                    "under": {
                        "best_back": {"odds": 2.34, "available": 150.0},
                        "best_lay": {"odds": 2.38, "available": 150.0},
                    },
                    "flow": {
                        "window_ready_15s": True,
                        "window_ready_30s": True,
                        "window_ready_60s": True,
                        "volume_delta_15s": 35.0,
                        "volume_delta_30s": delta_30,
                        "volume_delta_60s": delta_60,
                        "fair_over_delta_pp_15s": 0.8,
                        "fair_over_delta_pp_30s": fair_pp_30,
                        "fair_over_delta_pp_60s": fair_pp_60,
                        "orderbook_ready": False,
                        "orderbook_confirmed": False,
                        "back_wom": 0.58,
                        "orderflow_imbalance": 0.12,
                        "orderbook_support_streak": 2,
                    },
                }
            },
        },
    }


def test_non_top_huge_total_volume_can_signal_without_fast_burst(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_TOTAL_VOLUME_GBP", "5000")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_TOTAL_MIN_FAIR_PP", "0.75")
    record = _record(volume=8500.0, delta_30=80.0, fair_pp_30=1.1)

    base = multi_money_flow.evaluate_money_flow(record)
    assert base["eligible"] is False
    assert base["reason"] == "money_flow_threshold_not_reached"

    info = enhance_money_flow_result(record, base)
    assert info["eligible"] is True
    assert info["signal_basis"] == "non_top_total_volume"
    assert info["level"] == "NON_TOP_BIG_VOLUME"
    assert info["market_volume"] == 8500.0
    assert info["volume_multiple"] == 1.7
    assert info["fair_delta_pp"] >= 1.1


def test_top_league_large_total_volume_is_not_special_signal(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_TOTAL_VOLUME_GBP", "5000")
    record = _record(league="England: Premier League", volume=8500.0)
    base = multi_money_flow.evaluate_money_flow(record)
    info = enhance_money_flow_result(record, base)

    assert info["eligible"] is False
    assert info["league_tier"] == "top"


def test_total_volume_without_over_direction_stays_wait(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_TOTAL_VOLUME_GBP", "5000")
    record = _record(volume=12000.0, fair_pp_30=0.2, fair_pp_60=0.3)
    record["matchbook_exchange"]["systems"]["another_goal"]["flow"]["fair_over_delta_pp_15s"] = 0.1
    base = multi_money_flow.evaluate_money_flow(record)
    info = enhance_money_flow_result(record, base)

    assert info["eligible"] is False
    assert info["reason"] == "money_flow_total_volume_wait_direction"


def test_existing_fast_flow_gets_non_top_total_volume_bonus(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_TOTAL_VOLUME_GBP", "5000")
    record = _record(volume=9000.0, delta_30=900.0, fair_pp_30=4.0, delta_60=1100.0, fair_pp_60=4.2)
    base = multi_money_flow.evaluate_money_flow(record)
    assert base["eligible"] is True

    info = enhance_money_flow_result(record, base)
    assert info["eligible"] is True
    assert info["total_volume_anomaly"] is True
    assert info["total_volume_bonus"] > 0
    assert info["score"] >= base["score"]
