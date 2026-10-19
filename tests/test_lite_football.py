"""Offline contract tests for the small model-free football worker."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from gool_bot2.lite_football import LiteFootballWorker, _over, _paired_trend_price


def _game(kickoff: float, *, live: bool = False):
    return SimpleNamespace(
        provider_match_id="ABCDEFGH", home="Alpha FC", away="Beta FC",
        league="Test League", meta={"scheduled_start_ts": kickoff},
        minute=24 if live else 0, is_halftime=False,
        home_score=0, away_score=0,
    )


class FakeProvider:
    def __init__(self, kickoff: float, live: bool = False):
        self.fixture = _game(kickoff, live=live)
        self.live = live

    def scheduled_matches_for_day(self, day: int):
        assert day == 0
        return [] if self.live else [self.fixture]

    def fetch_match_history(self, event_id: str, home: str, away: str, limit=10):
        assert event_id == "ABCDEFGH"
        assert limit == 10
        # Both teams average 2.0 goals for and 2.0 against: total lambda 4.0.
        a = [{"event_id": str(i), "timestamp": 1700000000 + i * 86400,
              "home": "Alpha FC", "away": "Other FC", "home_score": 2,
              "away_score": 2} for i in range(10)]
        b = [{"event_id": str(i+20), "timestamp": 1700000000 + i * 86400,
              "home": "Other FC", "away": "Beta FC", "home_score": 2,
              "away_score": 2} for i in range(10)]
        return {"home_recent": a, "away_recent": b, "h2h": []}

    def live_matches(self):
        return [self.fixture] if self.live else []

    def fetch_stats(self, event_id: str):
        assert event_id == "ABCDEFGH"
        return {"shots": (12, 11), "shots_on_target": (5, 4),
                "big_chances": (2, 2), "corners": (4, 4)}


def _quote(event_id: str, timeout: int):
    assert event_id == "ABCDEFGH"
    return {"settings": {"bookmakers": [{"bookmaker": {"id": 1, "name": "TestBook"}}]},
            "odds": [
                {"bettingScope": "FULL_TIME", "bettingType": "OVER_UNDER", "bookmakerId": 1,
                 "odds": [{"selection": "OVER", "value": 1.45, "handicap": {"value": 2.5}},
                          {"selection": "UNDER", "value": 3.0, "handicap": {"value": 2.5}}]},
            ]}


def test_scenarios_without_quote_never_make_a_bet(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    now = time.time()
    worker = LiteFootballWorker(tmp_path, provider=FakeProvider(now + 3600),
                                odds_fetch=lambda *_args, **_kwargs: {})
    result = worker.scan_prematch(now=now)
    assert result["analysed"] == 1
    assert result["scenario_matches"] >= 1
    assert result["priced"] == 0
    assert result["prematch_candidates"] == []


def test_real_paired_quote_is_required_for_prematch_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    now = time.time()
    worker = LiteFootballWorker(tmp_path, provider=FakeProvider(now + 3600),
                                odds_fetch=_quote)
    result = worker.scan_prematch(now=now)
    assert result["priced"] == 1
    assert len(result["prematch_candidates"]) == 1
    item = result["prematch_candidates"][0]
    assert item["market"] == "FT_OVER_2.5"
    assert item["odd"] == 1.45
    assert item["bookmaker"] == "TestBook"
    assert item["model_probability"] > item["market_probability"]
    assert item["parlay_safe"]


def test_live_analysis_cannot_send_without_verified_price(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    now = time.time()
    worker = LiteFootballWorker(tmp_path, provider=FakeProvider(now + 1800, live=True),
                                odds_fetch=_quote)
    result = worker.scan_live(now=now)
    assert result["analysed"] == 1
    assert result["errors"] == 0
    assert result["decisions"]
    assert all(not x["publishable"] and not x["priced"] for x in result["decisions"])


def test_poisson_probability_is_monotone():
    assert 0 < _over(1.5, 3) < _over(3.5, 3) < 1


def test_odds_cannot_mix_sides_across_bookmakers():
    market = {
        "settings": {"bookmakers": [
            {"bookmaker": {"id": 1, "name": "Book A"}},
            {"bookmaker": {"id": 2, "name": "Book B"}},
        ]},
        "odds": [
            {"bookmakerId": 1, "bettingScope": "FULL_TIME",
             "bettingType": "OVER_UNDER", "odds": [
                 {"selection": "OVER", "value": 1.9, "handicap": {"value": 2.5}}
             ]},
            {"bookmakerId": 2, "bettingScope": "FULL_TIME",
             "bettingType": "OVER_UNDER", "odds": [
                 {"selection": "UNDER", "value": 1.9, "handicap": {"value": 2.5}}
             ]},
        ],
    }
    assert _paired_trend_price(market, "FT_OVER_2.5") is None
    market["odds"][0]["odds"].append(
        {"selection": "UNDER", "value": 2.0, "handicap": {"value": 2.5}}
    )
    priced = _paired_trend_price(market, "FT_OVER_2.5")
    assert priced["bookmaker"] == "Book A"
    assert priced["best_odds"] == 1.9
