#!/usr/bin/env python3
"""Select ONLY stable basketball quarters, verified with 10 preceding games for EACH team.
Historic synthetic lines from the earlier frozen test; no market prices or future leakage.
This is research, not a production signal sender.
"""
from __future__ import annotations
import argparse, json, statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from gool_bot2.providers.flashscore import FlashscoreProvider

OUT=Path("artifacts/selective_quarter_20261008")
OUT.mkdir(parents=True,exist_ok=True)

def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
def read(name):
    return json.loads((OUT/name).read_text(encoding="utf-8"))
def get_part(fs,id):
    d=fs.fetch_segment_scores(str(id),"basketball")
    arr=[]
    for i in range(1,5):
        pair=d.get(f"QUARTER_{i}")
        if not pair or len(pair)!=2:return None
        try:
            x,y=int(pair[0]),int(pair[1])
        except (ValueError,TypeError):return None
        if min(x,y)<0:return None
        arr.append(x+y)
    return arr

def evaluate(g):
    result={"id":g["id"],"home":g["home"],"away":g["away"],"league":g.get("league"),
        "actual":g["actual"],"start_ts":g["start_ts"]}
    model=g.get("models",{}).get("10",{})
    if model.get("status")!="evaluated":
        result["status"]="missing_10_games_per_team"
        return result
    hi=list(model.get("home_source_ids") or [])
    ai=list(model.get("away_source_ids") or [])
    if len(hi)!=10 or len(ai)!=10:
        result["status"]="incomplete_source_ids"
        return result
    fs=FlashscoreProvider()
    ids=list(dict.fromkeys(hi+ai))
    with ThreadPoolExecutor(max_workers=6) as pool:
        profiles=dict(zip(ids,pool.map(lambda x:get_part(fs,x),ids)))
    h=[profiles[i] for i in hi]
    a=[profiles[i] for i in ai]
    result["source_home_ids"]=hi
    result["source_away_ids"]=ai
    result["complete_home"]=sum(x is not None for x in h)
    result["complete_away"]=sum(x is not None for x in a)
    if not all(h) or not all(a):
        result["status"]="historical_quarter_unavailable"
        return result
    bets=model.get("synthetic_bets") or []
    predicted=model.get("predicted_quarters") or []
    if len(bets)!=4 or len(predicted)!=4:
        result["status"]="missing_frozen_bets"
        return result
    result["status"]="evaluated"
    candidates=[]
    for q in range(4):
        bet=bets[q]
        line=float(bet["line"])
        direction=str(bet["direction"]).upper()
        if direction not in ("OVER","UNDER"):
            result["status"]="invalid_direction";return result
        predicate=(lambda total:total>line) if direction=="OVER" else (lambda total:total<line)
        home_hits=sum(predicate(x[q]) for x in h)
        away_hits=sum(predicate(x[q]) for x in a)
        actual=int(result["actual"][q])
        historical_mixed=(home_hits+away_hits)/20
        rec={"quarter":q+1,"line":line,"direction":direction,
             "general10_mean":float(predicted[q]),"distance_from_mean":round(abs(float(predicted[q])-line),3),
             "home_hits":home_hits,"away_hits":away_hits,
             "home_hit_rate":home_hits/10,"away_hit_rate":away_hits/10,
             "combined_hits":home_hits+away_hits,
             "history_consensus":round(historical_mixed,3),
             "actual":actual,"won":bool(predicate(actual))}
        candidates.append(rec)
    result["candidates"]=candidates
    return result

def test_index(i,total,workers):
    raw=read("baseline/all_form_games.json")
    raw.sort(key=lambda x:(x["start_ts"],x["id"]))
    batch=[row for idx,row in enumerate(raw) if idx%total==i]
    out=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(evaluate,r) for r in batch]
        for f in as_completed(futures):
            r=f.result()
            out.append(r)
            print("SELECTIVE_MATCH "+json.dumps({"match":r["home"]+" — "+r["away"],
                "status":r["status"],"stable8":[c["quarter"] for c in r.get("candidates",[]) if c["home_hits"]>=8 and c["away_hits"]>=8],
                "stable9":[c["quarter"] for c in r.get("candidates",[]) if c["home_hits"]>=9 and c["away_hits"]>=9]},ensure_ascii=False),flush=True)
    save(f"shard_{i}.json",out)
    print("SHARD_DONE "+json.dumps({"shard":i,"count":len(out),"statuses":dict(Counter(r["status"] for r in out))}),flush=True)

def summary():
    origin=read("baseline/all_form_games.json")
    allrows=[]
    for p in sorted(OUT.glob("shard_*.json")):
        allrows+=json.loads(p.read_text(encoding="utf-8"))
    if len(allrows)!=len(origin) or len({x["id"] for x in allrows})!=len(origin):
        raise SystemExit("SHARD_COVERAGE_ERROR: not all fixtures evaluated once")
    valid=[r for r in allrows if r["status"]=="evaluated"]
    def ranking(item):
        return (min(item["home_hits"],item["away_hits"]),item["combined_hits"],
                -item["distance_from_mean"],-item["quarter"])
    def calc(threshold,limit):
        # Strict one-pick-per-match policy: choose best pre-target quarter candidate.
        chosen=[]
        allcandidate=[]
        for r in valid:
            q=[dict(c,match=r["home"]+" — "+r["away"],id=r["id"]) for c in r["candidates"]
                  if min(c["home_hits"],c["away_hits"])>=threshold
                  and (limit is None or c["distance_from_mean"]<=limit)]
            allcandidate.extend(q)
            if q:chosen.append(max(q,key=ranking))
        def parts(lst):
            under=[x for x in lst if x["direction"]=="UNDER"]
            over=[x for x in lst if x["direction"]=="OVER"]
            return {"signals":len(lst),"wins":sum(x["won"] for x in lst),
                "win_rate":round(sum(x["won"] for x in lst)/len(lst),4) if lst else None,
                "over":{"signals":len(over),"wins":sum(x["won"] for x in over)},
                "under":{"signals":len(under),"wins":sum(x["won"] for x in under)},
                "by_quarter":[{"quarter":i+1,"signals":sum(x["quarter"]==i+1 for x in lst),
                               "wins":sum(x["quarter"]==i+1 and x["won"] for x in lst)} for i in range(4)]}
        return {"per_quarter_candidates":parts(allcandidate),
                "one_per_match":parts(chosen),"no_signal_matches":len(valid)-len(chosen),
                "selected":sorted(chosen,key=lambda x:x["id"])}
    variants={
        "at_least_8_each":calc(8,None),
        "at_least_9_each":calc(9,None),
        "8_each_and_distance_max8":calc(8,8),
        "9_each_and_distance_max8":calc(9,8),
        "8_each_and_distance_max5":calc(8,5)
    }
    frozen=[b for r in valid for b in next((src["models"]["10"].get("synthetic_bets",[]) for src in origin if src["id"]==r["id"]),[])]
    result={"date":"2026-10-08","source_matches":len(origin),
        "complete_history_matches":len(valid),
        "status_counts":dict(Counter(x["status"] for x in allrows)),
        "unfiltered_same_matches":{"signals":len(frozen),"wins":sum(x["won"] for x in frozen),
           "win_rate":round(sum(x["won"] for x in frozen)/len(frozen),4) if frozen else None},
        "variants":variants,
        "method":"Use historical line chosen by last10-team-form script, then check that both teams' separate 10 previous non-H2H games crossed exact line at least 8/10 or 9/10. Filter synthetic line gap to mean in some variants. Only strongest quarter per match chosen.",
        "limitations":"Historical synthetic lines only, no real odds; high historical hit counts with ten games can overfit. Do not interpret as realizable betting profits."}
    save("summary.json",result)
    save("all_matches.json",sorted(allrows,key=lambda x:x["id"]))
    print("SELECTIVE_FINAL "+json.dumps({k:v for k,v in result.items() if k!="variants"},ensure_ascii=False),flush=True)
    for key,v in variants.items():
        print("VARIANT "+json.dumps({"name":key,"all":v["per_quarter_candidates"],"one_per_match":v["one_per_match"],"wait":v["no_signal_matches"]},ensure_ascii=False),flush=True)
    lines=["# Stable-quarter-only test — October 8",
          f"Full 10+10 prior history: {len(valid)} / {len(origin)} matches",
          "Unfiltered historical picks: "+str(result["unfiltered_same_matches"]),
          "",
          "| Gate | One/match picks | Won | Rate | WAIT |",
          "|---|---:|---:|---:|---:|"]
    for k,v in variants.items():
        p=v["one_per_match"]
        lines.append(f"| {k} | {p['signals']} | {p['wins']} | {p['win_rate']} | {v['no_signal_matches']} |")
    lines.append("\nThis is NOT a sportsbook availability, pricing, or ROI test.")
    (OUT/"report.md").write_text("\n".join(lines),encoding="utf-8")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=["shard","summary"])
    p.add_argument("--index",type=int,default=0)
    p.add_argument("--total",type=int,default=12)
    p.add_argument("--workers",type=int,default=2)
    a=p.parse_args()
    if a.mode=="shard": test_index(a.index,a.total,a.workers)
    else:summary()
