"""Offline contract tests for the small model-free football worker."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from gool_bot2.lite_football import LiteFootballWorker, _over, _paired_trend_price, _lite_live_decision


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


def test_prematch_rotates_through_matches_instead_of_first_five(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("GOOL_LITE_FOOTBALL_PREMATCH_MAX", "5")
    now = time.time()
    games = [
        SimpleNamespace(provider_match_id=f"A{i:07d}", home=f"Home{i}", away=f"Away{i}",
                        league="Test", meta={"scheduled_start_ts": now + 3200})
        for i in range(16)
    ]
    class Provider:
        def scheduled_matches_for_day(self, day):
            return games
    worker = LiteFootballWorker(tmp_path, provider=Provider())
    seen = []
    def no_history(game):
        seen.append(game.provider_match_id)
        return {"status":"WAIT_HISTORY", "quality":0}, {}
    monkeypatch.setattr(worker, "_forecast", no_history)
    for i in range(4):
        result = worker.scan_prematch(now=now + 601 * i)
        assert result["selected"] <= 5
    assert len(set(seen)) == 16
    assert result["filters"]["no_history"] >= 1


def test_live_rotates_in_three_scan_windows_and_preserves_momentum(tmp_path):
    from types import SimpleNamespace
    now = time.time()
    fixtures = [
        SimpleNamespace(provider_match_id=f"L{i:07d}", home=f"H{i}", away=f"A{i}",
                        league="Test", meta={}, minute=25, is_halftime=False,
                        home_score=0, away_score=0)
        for i in range(12)
    ]
    seen = []
    class Provider:
        def live_matches(self):
            return fixtures
        def fetch_stats(self, event_id):
            seen.append(event_id)
            return {"shots": (4, 3), "shots_on_target": (1, 2), "corners": (2, 1)}
    worker = LiteFootballWorker(tmp_path, provider=Provider())
    for i in range(9):
        result = worker.scan_live(now=now + i * 151)
        assert result["errors"] == 0
        assert result["analysed"] <= 4
        assert result["filters"]["brain_bet"] == 0
    assert len(set(seen)) == 12
    assert seen[:4] == seen[4:8] == seen[8:12]
    assert set(seen[:4]).isdisjoint(set(seen[12:16]))


def test_single_source_live_can_pass_only_with_real_xg_and_strong_fresh_momentum():
    from types import SimpleNamespace
    decision = SimpleNamespace(data_source="provider_xg", probability=.82, confidence=.68)
    reasons = {"momentum_ready": "1", "sources": "1", "recent_signals": "3",
               "red_cards": "0", "threshold": ".65"}
    ok, required, reason = _lite_live_decision(decision, reasons)
    assert ok and required == .62 and reason == "VERIFIED_MOMENTUM"
    proxy = SimpleNamespace(data_source="attack_proxy", probability=.89, confidence=.73)
    ok, _, reason = _lite_live_decision(proxy, reasons)
    assert not ok and reason == "SINGLE_SOURCE_INSUFFICIENT"
    reasons["momentum_ready"] = "0"
    assert not _lite_live_decision(decision, reasons)[0]


def test_multisource_live_checks_activity_confidence_and_probability():
    from types import SimpleNamespace
    decision = SimpleNamespace(data_source="attack_proxy", probability=.78, confidence=.73)
    reasons = {"momentum_ready": "1", "sources": "2", "recent_signals": "3",
               "red_cards": "0", "threshold": ".65"}
    assert _lite_live_decision(decision, reasons)[0]
    reasons["recent_signals"] = "2"
    assert not _lite_live_decision(decision, reasons)[0]
    reasons["recent_signals"] = "3"
    reasons["red_cards"] = "1"
    assert not _lite_live_decision(decision, reasons)[0]


def test_fotmob_verification_uses_true_provider_match_score_not_echoed_names(tmp_path):
    """FotMob.enrich echoes requested names: old code accepted weak mappings."""
    from collections import deque
    from types import SimpleNamespace
    now = time.time()
    mid = "ABCDEFGH"
    class Provider:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id=mid, home="Alpha FC",
                                    away="Beta FC", league="Test", minute=25,
                                    is_halftime=False, home_score=0, away_score=0)]
        def fetch_stats(self, event_id):
            return {"shots": (12, 11), "shots_on_target": (6, 5),
                    "big_chances": (1, 1), "corners": (6, 6)}
    class FotMob:
        def __init__(self, match_score):
            self.match_score = match_score
            self.called = 0
        def enrich(self, home, away):
            self.called += 1
            return SimpleNamespace(
                home=home, away=away,
                stats={"shots": (12, 11), "shots_on_target": (6, 5),
                       "big_chances": (1, 1), "xg": (1.6, 1.4)},
                meta={"match_score": self.match_score, "has_shotmap": True})
    for mapped_score, expected in ((.75, 0), (.96, 1)):
        worker = LiteFootballWorker(tmp_path / str(mapped_score), provider=Provider())
        worker._live[mid] = deque([{"ts": now - 350, "minute": 18,
                                    "score": [0,0],
                                    "stats": {"shots": (5, 4), "shots_on_target": (2, 1),
                                              "big_chances": (0, 0), "corners": (1, 1)}}], maxlen=12)
        worker._fotmob_provider = FotMob(mapped_score)
        result = worker.scan_live(now=now)
        assert result["errors"] == 0
        assert result["filters"]["fotmob_checked"] == 1
        assert result["filters"]["fotmob_confirmed"] == expected
        assert result["filters"]["fotmob_unmapped"] == 1-expected
        assert worker._fotmob_provider.called == 1
