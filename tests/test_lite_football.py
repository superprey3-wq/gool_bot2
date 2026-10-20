"""Offline contract tests for the small model-free football worker."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from gool_bot2.lite_football import LiteFootballWorker


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
                 "odds": [{"selection": "OVER", "value": 2.80, "handicap": {"value": 2.5}},
                          {"selection": "UNDER", "value": 1.60, "handicap": {"value": 2.5}}]},
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
    assert item["market"] == "match_total"
    assert item["original_selection"] == "over 2.5"
    assert item["odd"] == 2.80
    assert item["bookmaker"] == "TestBook"
    assert item["model_probability"] > item["market_probability"]
    assert not item.get("parlay_safe", False)
    assert item["brain"] == "FOOTBALL_V5"


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


def test_live_tracks_cohort_for_five_scans_before_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "0")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
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
    for i in range(10):
        result = worker.scan_live(now=now + i * 151)
        assert result["errors"] == 0
        assert result["analysed"] <= 4
        assert result["filters"]["brain_bet"] == 0
    assert len(set(seen)) == 12
    assert all(seen[4*i:4*i+4] == seen[:4] for i in range(5))
    assert all(seen[4*i:4*i+4] == seen[20:24] for i in range(5,10))
    assert set(seen[:4]).isdisjoint(set(seen[20:24]))


def test_fotmob_verification_uses_true_provider_match_score_not_echoed_names(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "0")
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


def test_diagnostic_second_source_probe_when_primary_lacks_xg(tmp_path, monkeypatch):
    """A strict preliminary V4 NO_BET must not disable all source verification."""
    import time
    from types import SimpleNamespace
    from collections import deque
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "1")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
    now = time.time()
    mid = "ABCDEFGH"

    class FS:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id=mid, home="Alpha FC",
                                    away="Beta FC", league="Test", minute=27,
                                    is_halftime=False, home_score=0, away_score=0)]
        def fetch_stats(self, event_id):
            return {"shots": (5, 4), "shots_on_target": (1, 1),
                    "big_chances": (0, 0)}
    class Independent:
        def __init__(self):
            self.calls = 0
        def enrich(self, home, away):
            self.calls += 1
            return SimpleNamespace(stats={"shots": (5, 4), "xg": (.3, .25)},
                                   meta={"match_score": .95})

    worker = LiteFootballWorker(tmp_path, provider=FS())
    worker._live[mid] = deque([
        {"ts": now - 330, "minute": 21, "score": [0,0],
         "stats": {"shots": (4,3), "shots_on_target": (1,0),
                   "big_chances": (0,0)}}], maxlen=12)
    independent = Independent()
    worker._scores365_provider = independent
    result = worker.scan_live(now=now)
    assert result["errors"] == 0
    assert result["filters"]["secondary_diagnostic"] == 1
    assert result["filters"]["scores365_checked"] == 1
    assert result["filters"]["scores365_confirmed"] == 1
    assert independent.calls == 1
    assert result["filters"]["no_xg"] == 0
    # Preserve unchanged GOOL V4 decision, even if it says NO_BET.
    assert result["decisions"][0]["decision"] == result["decisions"][0]["base_decision"]
    assert result["decisions"][0]["brain"] == "ORIGINAL_LIVE_V4"


def test_original_secondary_collector_runs_without_preliminary_bet_and_caches(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "1")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
    mid = "ABCDEFGH"
    now = time.time()
    class FS:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id=mid, home="Alpha FC",
                                    away="Beta FC", league="Test", minute=27,
                                    is_halftime=False, home_score=0, away_score=0)]
        def fetch_stats(self, event_id):
            return {"shots": (1, 1), "shots_on_target": (0, 0)}
    class Secondary:
        def __init__(self):
            self.calls = 0
        def enrich(self, home, away):
            self.calls += 1
            return SimpleNamespace(
                stats={"shots": (1, 1), "xg": (0.3, 0.22)},
                meta={"match_score": 0.94})
    secondary = Secondary()
    worker = LiteFootballWorker(tmp_path, provider=FS())
    worker._scores365_provider = secondary
    first = worker.scan_live(now=now)
    assert first["filters"]["scores365_checked"] == 1
    assert first["filters"]["scores365_confirmed"] == 1
    assert first["filters"]["no_xg"] == 0
    second = worker.scan_live(now=now + 151)
    assert second["filters"]["scores365_checked"] == 0
    assert second["filters"]["secondary_cache_used"] == 1
    assert second["filters"]["no_xg"] == 0
    assert secondary.calls == 1
    assert first["filters"]["brain_bet"] == 0  # no fake betting signals


def test_365scores_confirmation_without_xg_not_reported_as_xg(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "1")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
    now = time.time()
    class FS:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id="ABCDEFGH", home="Alpha FC",
                                    away="Beta FC", league="Test", minute=22,
                                    is_halftime=False, home_score=0, away_score=0)]
        def fetch_stats(self, eid):
            return {"shots": (5,3), "shots_on_target": (1,1), "corners": (2,2)}
    class Scores:
        def enrich(self, home, away):
            return SimpleNamespace(
                stats={"shots":(5,3), "shots_on_target":(1,1)},
                meta={"match_score": .96})
    worker = LiteFootballWorker(tmp_path, provider=FS())
    worker._scores365_provider = Scores()
    result = worker.scan_live(now=now)
    assert result["filters"]["scores365_confirmed"] == 1
    assert result["filters"]["scores365_without_xg"] == 1
    assert result["filters"]["scores365_with_xg"] == 0
    assert result["filters"]["no_xg"] == 1
    assert result["filters"]["brain_bet"] == 0


def test_stale_long_live_gap_resets_original_momentum(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from collections import deque
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "0")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
    now = time.time()
    match = SimpleNamespace(provider_match_id="ABCDEFGH", home="Alpha FC",
                            away="Beta FC", league="Test", minute=48,
                            is_halftime=False, home_score=0, away_score=0)
    class FS:
        def live_matches(self):
            return [match]
        def fetch_stats(self, eid):
            return {"shots": (14,9), "shots_on_target": (5,3)}
    worker = LiteFootballWorker(tmp_path, provider=FS())
    worker._live["ABCDEFGH"] = deque([
        {"ts": now-1000, "minute": 25, "score": [0,0],
         "stats": {"shots": (2,2), "shots_on_target": (1,0)}},
    ], maxlen=12)
    worker._momentum_tracker._history["ABCDEFGH"].append({
        "minute":25,"score":(0,0),"shots":(2.,2.),
        "sot":(1.,0.),"xg":None,"big":None,"danger":None})
    result = worker.scan_live(now=now)
    assert result["errors"] == 0
    assert result["filters"]["momentum_reset"] == 1
    assert result["filters"]["no_momentum"] >= 1
    assert len(worker._momentum_tracker._history["ABCDEFGH"]) == 1
