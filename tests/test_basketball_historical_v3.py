from __future__ import annotations

from gool_bot2.basketball_historical_v3 import (
    build_profile, best_market, opponent_weight, screen_market,
)
from gool_bot2.xbet_multisport_steam import basketball_historical_market_signal


class FakeFlashscore:
    @staticmethod
    def _same_team(a, b):
        return a.casefold() == b.casefold()

    def __init__(self, h2h=True, late=False):
        self.h2h = h2h
        self.late = late
        self.history = {}
        for i in range(10):
            self.history[f"h{i}"] = {
                "event_id": f"h{i}", "home": "A", "away": f"other_h{i}",
                "timestamp": 990-i,
            }
            self.history[f"a{i}"] = {
                "event_id": f"a{i}", "home": f"other_a{i}", "away": "B",
                "timestamp": 980-i,
            }
        for i in range(5):
            self.history[f"pair{i}"] = {
                "event_id": f"pair{i}", "home": "A" if i%2 else "B",
                "away": "B" if i%2 else "A", "timestamp": 960-i,
            }

    def fetch_match_history(self, event_id, home, away, limit=40):
        return {
            "feed_present": True,
            "home_recent": list(self.history[f"h{i}"] for i in range(10)),
            "away_recent": list(self.history[f"a{i}"] for i in range(10)),
            "h2h": [self.history[f"pair{i}"] for i in range(5)] if self.h2h else [],
        }

    def fetch_segment_scores(self, event_id, sport):
        assert sport == "basketball"
        if event_id.startswith("h"):
            return {f"QUARTER_{q}": [20, 20] for q in range(1,5)}
        if event_id.startswith("a"):
            return {f"QUARTER_{q}": [20, 20] for q in range(1,5)}
        if event_id.startswith("pair"):
            m=self.history[event_id]
            v=[30,20] if m["home"]=="A" else [20,30]
            return {f"QUARTER_{q}":v for q in range(1,5)}
        raise KeyError(event_id)


def test_historical_profile_strict_and_opponent_adjustment():
    fs=FakeFlashscore()
    p=build_profile(fs,"target","A","B",1000)
    assert p["status"]=="READY"
    assert p["opponent_coefficient_available"]
    assert p["home_games"]==10
    assert p["h2h_games"]==5
    assert p["quarters"][0]["home_coeff"]==1.5
    assert p["quarters"][0]["home_delta"]==2.5
    assert p["quarters"][0]["away_delta"]==0.
    assert p["quarters"][0]["home_mu"]==22.5
    assert p["quarters"][0]["away_mu"]==20.


def test_quarter_signal_requires_eight_of_ten_both():
    p=build_profile(FakeFlashscore(),"target","A","B",1000)
    quote={"scope":"QUARTER_2","market_family":"match_total","line":38.5,"over":1.70,"under":2.2}
    s=screen_market(p,quote,"over")
    assert s["tier"]=="PASS_8"
    assert s["home_hits"]==s["away_hits"]==10
    out=best_market(p,quote,phase="LIVE")
    assert out and out["direction"]=="over"
    signal=basketball_historical_market_signal(p,quote,phase="LIVE")
    assert signal and signal["brain_mode"]=="basketball_historical_v3"
    assert signal["historical_home_hits"]==10


def test_seven_of_ten_stays_watch():
    p=build_profile(FakeFlashscore(),"target","A","B",1000)
    for i, row in enumerate(p["home_history"]):
        if i>=7:
            row["total"][0]=0
    quote={"scope":"QUARTER_1","market_family":"match_total","line":39.5,"over":1.70,"under":3.2}
    q=screen_market(p,quote,"over")
    assert q["home_hits"]==7
    assert q["tier"]=="WATCH_7"
    assert best_market(p,quote) is None


def test_no_future_matches_or_borrowed_h2h_and_no_impossible_markets():
    fs=FakeFlashscore(h2h=False)
    fs.history["h0"]["timestamp"]=1001
    p=build_profile(fs,"target","A","B",1000)
    assert p["status"]=="WAIT_HISTORY"
    fs=FakeFlashscore(h2h=False)
    p=build_profile(fs,"target","A","B",1000)
    assert p["status"]=="READY"
    assert not p["opponent_coefficient_available"]
    quote={"scope":"FULL_MATCH","market_family":"match_total","line":159.5,"over":1.7,"under":2.1}
    assert screen_market(p,quote,"over") is None
    quote["scope"]="FIRST_HALF"
    assert best_market(p,quote,phase="LIVE") is None


def test_shrinkage_does_not_overreact_to_five_head_to_head_matches():
    assert opponent_weight(1.5)==1.125
    assert opponent_weight(.5)==.875
    assert opponent_weight(1.0)==1.0


def test_basketball_historical_singles_min_140_without_max():
    p = build_profile(FakeFlashscore(), "target", "A", "B", 1000)
    from gool_bot2.xbet_multisport_markets import market_lanes, prematch_market_lanes
    for odd in (1.001, 1.01, 1.29, 1.39, 1.40, 1.41, 4.5, 25.0, 101.0):
        lane = {
            "scope": "QUARTER_2", "market_family": "match_total",
            "line": 38.5, "over": odd, "under": 2.0,
        }
        # Price decoding does not drop valid markets. Signal selection does.
        assert screen_market(p, lane, "over")["tier"] == "PASS_8"
        assert best_market(p, lane, phase="LIVE")["odd"] == odd
        for phase in ("LIVE", "PREMATCH"):
            signal = basketball_historical_market_signal(p, lane, phase=phase)
            if odd < 1.40:
                assert signal is None
            else:
                assert signal is not None and signal["odd"] == odd
        decoded = {"QUARTER_2": {"match_total": [
            {"line": 38.5, "over": odd, "under": 2.0},
        ]}}
        assert len(market_lanes(decoded)) == 1
        assert any(v["market_family"] == "match_total" for v in prematch_market_lanes(decoded, "basketball"))


def test_invalid_odds_are_not_treated_as_available_prices():
    p = build_profile(FakeFlashscore(), "target", "A", "B", 1000)
    from gool_bot2.xbet_multisport_markets import market_lanes
    for bad in (0, 0.9, 1.0, float("inf"), float("-inf"), float("nan")):
        lane = {
            "scope": "QUARTER_2", "market_family": "match_total",
            "line": 38.5, "over": bad, "under": 2.0,
        }
        assert screen_market(p, lane, "over") is None
        assert basketball_historical_market_signal(p, lane, phase="PREMATCH") is None
        decoded = {"QUARTER_2": {"match_total": [{"line": 38.5, "over": bad, "under": 2.0}]}}
        assert market_lanes(decoded) == []


def test_parlay_range_stays_explicit_and_independent_of_singles():
    p = build_profile(FakeFlashscore(), "target", "A", "B", 1000)
    lane = {
        "scope": "QUARTER_2", "market_family": "match_total",
        "line": 38.5, "over": 25.0, "under": 2.0,
    }
    assert basketball_historical_market_signal(p, lane, phase="PREMATCH") is not None
    assert basketball_historical_market_signal(
        p, lane, phase="PREMATCH", odds_range=(1.30, 1.50),
    ) is None



def test_all_basketball_singles_have_final_140_delivery_gate(capsys):
    """Final publication gate catches low-odd handicap/choice signals too."""
    from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS

    class DuplicateStub:
        def __init__(self):
            self.lookups = 0

        def _already_seen(self, *args, **kwargs):
            self.lookups += 1
            return True

    stub = DuplicateStub()
    row = {
        "event_id": "womens_fixture", "phase": "PREMATCH",
        "market_family": "handicap", "scope": "FULL_MATCH",
        "home": "Vienna Timberwolves W", "away": "UBSC-DBBC Graz W",
    }
    for odd in (1.01, 1.29, 1.39):
        result = MultiSportSteamWorker._record_signal(
            stub, row, {"odd": odd, "direction": "away"}, SPORTS["basketball"],
        )
        assert result == (False, 0)
        assert stub.lookups == 0
        assert "GOOL_BASKETBALL_SINGLE_ODD_REJECT" in capsys.readouterr().out
    for odd in (1.40, 1.41, 9.99):
        result = MultiSportSteamWorker._record_signal(
            stub, row, {"odd": odd, "direction": "away"}, SPORTS["basketball"],
        )
        assert result == (False, 0)  # stopped only by synthetic duplicate guard
        assert stub.lookups > 0
        assert "GOOL_BASKETBALL_SINGLE_ODD_REJECT" not in capsys.readouterr().out
