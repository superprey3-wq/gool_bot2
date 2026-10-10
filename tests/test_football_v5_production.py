from __future__ import annotations

from pathlib import Path

import pytest

from gool_bot2.football_v5_production import (
    _p_over, price_1xbet, price_flashscore_full_market, production_v5_picks,
)
from gool_bot2.football_v5_shadow import score_grid
from gool_bot2.v4_prematch_engine import signal_tier, PrematchPick


def model():
    return {
        "status":"READY","home_lambda":2.3,"away_lambda":1.0,
        "quality":0.9,"opponent_strength_coverage":0.0,
        "probabilities":score_grid(2.3,1.0),
    }


def params():
    return dict(event_id="game01",home="Alpha",away="Beta",league="Test",kickoff_ts=1791702000.0)


def test_real_1xbet_v5_candidates_are_deliverable():
    quote={"match_totals":[{"line":1.5,"over":1.60,"under":2.40},
                            {"line":2.0,"over":1.60,"under":2.40}],
           "home_totals":[{"line":0.5,"over":1.65,"under":2.10}],
           "away_totals":[],
           "match_1x2":{"home":2.25,"draw":3.1,"away":3.5},
           "btts":{"yes":1.88,"no":1.88}}
    picks,meta=production_v5_picks(model(),xbet=quote,full_analysis=None,**params())
    assert picks
    assert meta["model"]=="football_v5"
    assert all(p.odds>=1.4 and p.edge>=0.09 and p.expected_value>=0.05 for p in picks)
    assert all(" 2 " not in f" {p.selection} " for p in picks if p.market=="match_total")
    assert all(p.event_id=="game01" for p in picks)


def test_flashscore_catalog_uses_its_own_real_quote_not_old_model_probability():
    raw={"candidates":[
        {"status":"SKIP","scope":"FULL_TIME","market_type":"OVER_UNDER",
         "selection":"OVER_1.5","odds":1.85,"market_probability":0.52,
         "honest_probability":0.01},
        {"status":"SKIP","scope":"FULL_TIME","market_type":"OVER_UNDER",
         "selection":"UNDER_1.5","odds":2.7,"market_probability":0.48,
         "honest_probability":0.99},
        {"status":"BET","scope":"FIRST_HALF","market_type":"OVER_UNDER",
         "selection":"OVER_0.5","odds":1.85,"market_probability":0.52,
         "honest_probability":0.99}
    ]}
    picks=price_flashscore_full_market(model(),raw,**params())
    assert any(p.market=="match_total" and p.selection=="over 1.5" for p in picks)
    assert not any(p.market.startswith("1H") for p in picks)
    assert all(p.model_probability>0.2 for p in picks)


def test_no_v4_fallback_for_unavailable_history_or_book():
    no_history={**model(),"status":"WAIT_HISTORY"}
    picks,meta=production_v5_picks(no_history,xbet={"match_totals":[{"line":0.5,"over":2.0,"under":2.0}]},full_analysis=None,**params())
    assert picks==[] and meta["reject"]=="WAIT_HISTORY"
    picks,meta=production_v5_picks(model(),xbet=None,full_analysis=None,**params())
    assert picks==[] and meta["reject"]=="NO_POSITIVE_VALUE_AT_REAL_ODDS"


def test_v5_rejects_asian_whole_and_quarter_totals():
    for line in (0.0,1.0,2.0,2.25,2.75,-0.5):
        assert _p_over(3.2,line) is None
    assert _p_over(3.2,2.5) is not None


def test_v5_minimum_odd_and_existing_delivery(monkeypatch):
    monkeypatch.setenv("GOOL_FOOTBALL_V5_ACTIVE","1")
    low=PrematchPick("e","A","B","match_total","over 0.5",1.40,0.91,0.70,0.9)
    assert signal_tier(low) is not None
    monkeypatch.setenv("GOOL_FOOTBALL_V5_ACTIVE","0")
    assert signal_tier(low) is None


def test_v5_script_is_primary_selector():
    script=(Path(__file__).resolve().parents[1]/"scripts/gool_flashscore_today.py").read_text("utf-8")
    assert "if V5_ACTIVE:" in script
    assert "production_v5_picks(" in script
    assert "return picks,info,r,None,value_diag" in script
