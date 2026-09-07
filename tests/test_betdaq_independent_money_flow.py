from __future__ import annotations

from pathlib import Path


def _record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "fs-1",
            "home": "Home",
            "away": "Away",
            "minute": 67,
            "home_score": 1,
            "away_score": 1,
            "is_finished": False,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": [{"minute": 51, "score": [1, 1]}]}}},
        "matchbook_exchange": {"sentinel": "keep-me"},
    }


def _betdaq_exchange() -> dict:
    flow = {
        "window_ready_30s": True,
        "volume_delta_30s": 400.0,
        "fair_over_delta_pp_30s": 3.0,
        "window_ready_60s": True,
        "volume_delta_60s": 700.0,
        "fair_over_delta_pp_60s": 4.0,
        "orderbook_ready": False,
    }
    context = {
        "available": True,
        "liquid": True,
        "period": "FT",
        "line": 2.5,
        "market_id": "bd-25",
        "market_name": "Totals Over/Under (2.5)",
        "market_status": "open",
        "volume": 2000.0,
        "fair_over": 0.62,
        "over": {
            "best_back": {"odds": 1.70, "available": 500.0},
            "best_lay": {"odds": 1.76, "available": 500.0},
        },
        "under": {
            "best_back": {"odds": 2.30, "available": 500.0},
            "best_lay": {"odds": 2.38, "available": 500.0},
        },
        "flow": flow,
    }
    return {
        "available": True,
        "source": "betdaq",
        "event": {"id": "betdaq-event-1"},
        "systems": {"money_flow": context},
    }


def test_betdaq_flow_uses_own_exchange_without_mutating_matchbook(monkeypatch):
    from gool_bot2.multi_betdaq_money_flow import evaluate_betdaq_money_flow

    monkeypatch.setenv("BETDAQ_FLOW_BET_MIN_MARKET_VOLUME", "500")
    monkeypatch.setenv("BETDAQ_FLOW_BET_MIN_DELTA_30S", "250")
    monkeypatch.setenv("BETDAQ_FLOW_BET_MIN_RELATIVE_PCT", "8")
    monkeypatch.setenv("BETDAQ_FLOW_BET_MIN_FAIR_PP", "2")

    record = _record()
    original_matchbook = dict(record["matchbook_exchange"])
    result = evaluate_betdaq_money_flow(record, _betdaq_exchange())

    assert result["eligible"] is True
    assert result["strategy"] == "betdaq_money_flow"
    assert result["betdaq_event_id"] == "betdaq-event-1"
    assert result["window"] == "30s"
    assert record["matchbook_exchange"] == original_matchbook


def test_betdaq_has_separate_journal_path(monkeypatch, tmp_path: Path):
    from gool_bot2.multi_betdaq_money_flow import betdaq_money_flow_journal_path

    target = tmp_path / "betdaq-flow.json"
    monkeypatch.setenv("GOOL_BETDAQ_MONEY_FLOW_JOURNAL_PATH", str(target))
    assert betdaq_money_flow_journal_path() == target


def test_betdaq_runtime_does_not_replace_matchbook_context():
    from gool_bot2 import matchbook_exchange

    assert matchbook_exchange.matchbook_context.__module__.endswith("matchbook_exchange")
    assert matchbook_exchange.matchbook_context.__name__ == "matchbook_context"


def test_monkey_start_launches_both_exchange_workers():
    source = Path("monkey_start.py").read_text(encoding="utf-8")
    assert '"matchbook": [sys.executable, "-m", "gool_bot2.matchbook_market_worker"' in source
    assert '"betdaq": [sys.executable, "-m", "gool_bot2.betdaq_market_worker"' in source
    assert "matchbook=separate_money_flow betdaq=separate_money_flow" in source
