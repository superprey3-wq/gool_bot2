from __future__ import annotations

import json
import math
from pathlib import Path

from gool_bot2.football_v5_shadow import (
    _observations, append_first_snapshots, forecast_from_history,
    price_shadow, score_grid,
)


def history(*, weaker=False):
    home, away = [], []
    for i in range(10):
        # Timestamp order is 990, 989, ..., 981, all before kickoff=1000
        home.append({
            "event_id": f"h{i}", "timestamp": 990 - i,
            "home": "Alpha", "away": f"OpponentH{i}",
            "home_score": 2 if not weaker else 0, "away_score": 1,
        })
        away.append({
            "event_id": f"a{i}", "timestamp": 980 - i,
            "home": f"OpponentA{i}", "away": "Beta",
            "home_score": 1, "away_score": 1 if not weaker else 3,
        })
    return {"home_recent":home,"away_recent":away,"h2h":[]}


def test_shadow_has_pre_kickoff_predictions_without_odds():
    r = forecast_from_history(home="Alpha",away="Beta",kickoff=1000,context=history())
    assert r["status"]=="READY"
    assert r["home_games"]==10 and r["away_games"]==10
    assert r["opponent_strength_coverage"]==0
    assert r["home_lambda"]>0 and r["away_lambda"]>0
    assert r["warning"]=="shadow_only_uncalibrated_no_betting"
    p=r["probabilities"]
    assert abs(p["home_win"]+p["draw"]+p["away_win"]-1)<0.00001


def test_opponent_strength_only_if_opponent_has_multiple_other_observations():
    ctx=history()
    # Three OTHER historical matches for the same opponent; don't count
    # the target match itself as evidence about that opponent's defence.
    ctx["away_recent"] += [
        {"event_id":f"support{i}", "timestamp":900+i,
         "home":"OpponentH0", "away":f"Extra{i}",
         "home_score":0,"away_score":3}
        for i in range(3)
    ]
    r=forecast_from_history(home="Alpha",away="Beta",kickoff=1000,context=ctx)
    assert r["status"]=="READY"
    assert r["opponent_strength_coverage"]>0
    assert r["home_attack_index"]>0


def test_no_future_score_leakage_or_target_event_in_history():
    ctx=history()
    ctx["home_recent"].insert(0,{
        "event_id":"future", "timestamp":1001,
        "home":"Alpha","away":"Future", "home_score":100,"away_score":0,
    })
    ctx["home_recent"].insert(0,{
        "event_id":"unknown", "timestamp":0,
        "home":"Alpha","away":"Unknown", "home_score":100,"away_score":0,
    })
    clean=forecast_from_history(home="Alpha",away="Beta",kickoff=1000,context=history())
    with_future=forecast_from_history(home="Alpha",away="Beta",kickoff=1000,context=ctx)
    assert clean["home_lambda"]==with_future["home_lambda"]


def test_insufficient_history_not_published():
    ctx=history()
    ctx["away_recent"]=ctx["away_recent"][:5]
    f=forecast_from_history(home="Alpha",away="Beta",kickoff=1000,context=ctx)
    assert f["status"]=="WAIT_HISTORY"
    assert price_shadow(f,{"match_totals":[]})==[]


def test_price_shadow_actual_bookmaker_line_and_no_integer_push():
    r=forecast_from_history(home="Alpha",away="Beta",kickoff=1000,context=history(weaker=True))
    quote={"match_totals":[
        {"line":0.5,"over":1.60,"under":2.40},
        {"line":1.0,"over":1.60,"under":2.40},
    ]}
    picks=price_shadow(r,quote)
    assert all(p["line"]==0.5 for p in picks)
    assert all(p["odd"]>=1.4 for p in picks)
    assert all(p["ev_uncalibrated"]>=0.035 for p in picks)


def test_v5_first_snapshot_is_immutable(tmp_path):
    path=tmp_path/"audit.jsonl"
    a={"event_id":"X1","v5":{"home_lambda":1.0}}
    b={"event_id":"X1","v5":{"home_lambda":8.0}}
    assert append_first_snapshots(path,[a])==1
    assert append_first_snapshots(path,[b])==0
    rows=[json.loads(x) for x in path.read_text("utf-8").splitlines()]
    assert len(rows)==1 and rows[0]["v5"]["home_lambda"]==1.0


def test_score_grid_known_zero_limit():
    p=score_grid(0.001,0.001)
    assert 0 <= p["btts_yes"] < 0.001
    assert abs(p["home_win"]+p["draw"]+p["away_win"]-1)<1e-5
