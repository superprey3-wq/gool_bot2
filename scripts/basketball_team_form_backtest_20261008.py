#!/usr/bin/env python3
"""Independent team-form basketball backtest for 2026-10-08 Moscow date.
Read-only; team A and team B last 5/10 OTHER-OPPONENT games, pre-target only.
Compare against frozen five-H2H baseline on identical fixture subsets.
Synthetic totals/odds are NOT real bookmaker offers.
"""
from __future__ import annotations
import argparse
import json
import math
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gool_bot2.providers.flashscore import FlashscoreProvider

ROOT=Path("artifacts/basket_form_20261008")
ROOT.mkdir(parents=True,exist_ok=True)
BASE=ROOT/"baseline"
DATE="2026-10-08"

def write(name, obj):
    (ROOT/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding="utf-8")

def matches(fs, label, target):
    # Use production's Flashscore team matching to handle shortened team names.
    return fs._same_team(str(label or ""),str(target or ""))

def is_direct(fs, item, home, away):
    h,a=item.get("home",""),item.get("away","")
    return ((matches(fs,h,home) and matches(fs,a,away)) or
            (matches(fs,a,home) and matches(fs,h,away)))

def quarter_points(fs, game, team):
    seg=fs.fetch_segment_scores(str(game["event_id"]),"basketball")
    scored=[]; allowed=[]
    for idx in range(1,5):
        pair=seg.get(f"QUARTER_{idx}")
        if not isinstance(pair,(list,tuple)) or len(pair)!=2:return None
        try:h,a=[int(x) for x in pair]
        except (TypeError,ValueError):return None
        if min(h,a)<0:return None
        if matches(fs,game.get("home"),team):
            scored.append(h);allowed.append(a)
        elif matches(fs,game.get("away"),team):
            scored.append(a);allowed.append(h)
        else:return None
    return {"scored":scored,"conceded":allowed,"combined":[a+b for a,b in zip(scored,allowed)]}

def side_games(fs, rows, team, opponent, ts, event_id):
    arr=[]
    seen=set()
    for m in sorted(rows,key=lambda x:int(x.get("timestamp") or 0),reverse=True):
        event=str(m.get("event_id") or "")
        date=int(m.get("timestamp") or 0)
        if not event or event in seen or event==event_id or date>=ts or date<=0:continue
        if not (matches(fs,m.get("home"),team) or matches(fs,m.get("away"),team)):continue
        if is_direct(fs,m,team,opponent):continue
        seen.add(event);arr.append(dict(m))
        if len(arr)>=10:break
    return arr

def synth(vals, target_mean, actual, count):
    import math
    out=[]
    for q in range(4):
        v=sorted(r[q] for r in vals)
        n=len(v)
        # For 10/20 source game totals: nearest historical 20th/80th percentiles.
        oi=max(0,min(n-1,math.ceil(n*0.20)-1))
        ui=max(0,min(n-1,math.ceil(n*0.80)-1))
        over=float(v[oi]) - 0.5
        under=float(v[ui]) + 0.5
        pred=float(target_mean[q])
        side,line=("OVER",over) if abs(pred-over)<=abs(under-pred) else ("UNDER",under)
        win=actual[q]>line if side=="OVER" else actual[q]<line
        out.append({"quarter":q+1,"direction":side,"line":line,"actual":actual[q],"won":bool(win),"proxy_history_size":n,"p80_hits":int(sum((x>line if side=="OVER" else x<line) for x in v))})
    return out

def evaluate(src):
    fs=FlashscoreProvider()
    result={
      "id":src["event_id"],"home":src["home"],"away":src["away"],
      "league":src.get("league"),"start_ts":src["start_ts"],
      "actual":src["actual"],"baseline_status":src.get("status"),
      "baseline_h2h_quarters":src.get("historical_mean_quarters"),
      "baseline_h2h_mae":src.get("mae"),
      "baseline_h2h_bets":src.get("synthetic_bets"),
    }
    try:
        context=fs.fetch_match_history(result["id"],result["home"],result["away"],limit=38)
        historical=[]
        for key in ("home_recent","away_recent","home_at_home","away_away","h2h"):
            historical.extend(x for x in context.get(key,[]) if isinstance(x,dict))
        result["history_feed_present"]=bool(context.get("feed_present"))
        result["history_raw_entries"]=len(historical)
        home=side_games(fs,historical,result["home"],result["away"],int(result["start_ts"]),result["id"])
        away=side_games(fs,historical,result["away"],result["home"],int(result["start_ts"]),result["id"])
        result["home_prior_other_games"]=len(home)
        result["away_prior_other_games"]=len(away)
        with ThreadPoolExecutor(max_workers=6) as pool:
            all_req=[("home",x) for x in home]+[("away",x) for x in away]
            futures={pool.submit(quarter_points,fs,g,result["home"] if s=="home" else result["away"]):(s,g) for s,g in all_req}
            profiles={"home":{},"away":{}}
            for fut in as_completed(futures):
                side,game=futures[fut]
                try:points=fut.result()
                except Exception:points=None
                profiles[side][str(game["event_id"])]=points
        def retain(rows,side):
            out=[]
            for g in rows:
                score=profiles[side].get(str(g["event_id"]))
                if score:out.append({"event_id":g["event_id"],"timestamp":g["timestamp"],**score})
            return out
        h=retain(home,"home")
        a=retain(away,"away")
        result["home_complete_recent"]=len(h)
        result["away_complete_recent"]=len(a)
        result["models"]={}
        for sample in (5,10):
            name=str(sample)
            if len(h)<sample or len(a)<sample:
                result["models"][name]={"status":"insufficient_team_form"}
                continue
            hist_h=h[:sample];hist_a=a[:sample]
            def mean(x,field,idx):return statistics.mean(y[field][idx] for y in x)
            exp_h=[];exp_a=[]
            for k in range(4):
                # Independent attack/defense, symmetric and pre-game.
                exp_h.append((mean(hist_h,"scored",k)+mean(hist_a,"conceded",k))/2)
                exp_a.append((mean(hist_a,"scored",k)+mean(hist_h,"conceded",k))/2)
            pred=[round(x+y,3) for x,y in zip(exp_h,exp_a)]
            actual=list(src["actual"])
            errors=[round(actual[q]-pred[q],3) for q in range(4)]
            combined=[row["combined"] for row in hist_h+hist_a]
            bets=synth(combined,pred,actual,sample)
            result["models"][name]={
                "status":"evaluated",
                "mean_home_scored":[round(mean(hist_h,"scored",k),3) for k in range(4)],
                "mean_home_conceded":[round(mean(hist_h,"conceded",k),3) for k in range(4)],
                "mean_away_scored":[round(mean(hist_a,"scored",k),3) for k in range(4)],
                "mean_away_conceded":[round(mean(hist_a,"conceded",k),3) for k in range(4)],
                "projected_home":[round(x,3) for x in exp_h],
                "projected_away":[round(x,3) for x in exp_a],
                "predicted_quarters":pred,
                "predicted_total":round(sum(pred),3),
                "errors":errors,
                "mae":round(statistics.mean(abs(x) for x in errors),4),
                "bias":round(statistics.mean(errors),4),
                "synthetic_bets":bets,
                "synthetic_wins":sum(b["won"] for b in bets),
                "home_source_ids":[x["event_id"] for x in hist_h],
                "away_source_ids":[x["event_id"] for x in hist_a],
                "last5_source_total_ranges":{
                     "home":[sum(x["combined"]) for x in hist_h[:5]],
                     "away":[sum(x["combined"]) for x in hist_a[:5]],
                },
            }
        return result
    except Exception as err:
        result["error"]=type(err).__name__+":"+str(err)
        return result

def source_rows():
    return json.loads((BASE/"all_matches.json").read_text(encoding="utf-8"))

def shard(idx,total,workers):
    matches=sorted(source_rows(),key=lambda x:(x["start_ts"],x["event_id"]))
    group=[x for j,x in enumerate(matches) if j%total==idx]
    result=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(evaluate,x):x for x in group}
        for future in as_completed(futures):
            x=future.result()
            result.append(x)
            print("FORM_MATCH "+json.dumps({
                "match":x["home"]+" — "+x["away"],"f5":x.get("models",{}).get("5",{}).get("status"),
                "f10":x.get("models",{}).get("10",{}).get("status"),
                "h":x.get("home_complete_recent"),"a":x.get("away_complete_recent"),"error":x.get("error"),
            },ensure_ascii=False),flush=True)
    write(f"shard_{idx}.json",result)
    print("SHARD_DONE "+json.dumps({"shard":idx,"total":len(result),"f5":sum(x.get("models",{}).get("5",{}).get("status")=="evaluated" for x in result),"f10":sum(x.get("models",{}).get("10",{}).get("status")=="evaluated" for x in result)},ensure_ascii=False),flush=True)

def summarize():
    baseline=source_rows()
    allrows=[]
    for path in sorted(ROOT.glob("shard_*.json")): allrows.extend(json.loads(path.read_text(encoding="utf-8")))
    ids=[x["id"] for x in allrows]
    if len(ids)!=len(baseline) or len(set(ids))!=len(ids):
        raise SystemExit(f"INCOMPLETE_RESULTS baseline={len(baseline)} form={len(ids)} unique={len(set(ids))}")
    byid={x["event_id"]:x for x in baseline}
    outcomes={}
    def collect(q):
        abs_err=[abs(e) for r in q for e in r["errors"]]
        errs=[e for r in q for e in r["errors"]]
        bets=[b for r in q for b in r.get("synthetic_bets",[])]
        def pair(side):
            selected=[b for b in bets if b["direction"]==side]
            return {"bets":len(selected),"wins":sum(x["won"] for x in selected),"rate":round(sum(x["won"] for x in selected)/len(selected),4) if selected else None}
        return {
            "matches":len(q),"quarters":len(errs),
            "mae":round(statistics.mean(abs_err),3) if abs_err else None,
            "signed_bias":round(statistics.mean(errs),3) if errs else None,
            "rmse":round(math.sqrt(statistics.mean(x*x for x in errs)),3) if errs else None,
            "synthetic_wins":sum(b["won"] for b in bets),"synthetic_bets":len(bets),
            "synthetic_rate":round(sum(b["won"] for b in bets)/len(bets),4) if bets else None,
            "over":pair("OVER"),"under":pair("UNDER"),
            "by_quarter":[{"quarter":i+1,"bets":sum(b["quarter"]==i+1 for b in bets),"wins":sum(b["quarter"]==i+1 and b["won"] for b in bets)} for i in range(4)],
        }
    for size in ("5","10"):
        chosen=[r for r in allrows if r.get("models",{}).get(size,{}).get("status")=="evaluated"]
        form=[r["models"][size] for r in chosen]
        common=[r for r in chosen if byid[r["id"]].get("status")=="evaluated"]
        h2h=collect([{"errors":byid[r["id"]]["errors"],"synthetic_bets":[{"quarter":b["quarter"],"direction":b["side"],"won":b["won"]} for b in byid[r["id"]].get("synthetic_bets",[])]} for r in common])
        outcomes[size]={"team_form_all_eligible":collect(form),"common_with_h2h_matches":len(common),"on_same_matches":{"team_form":collect([r["models"][size] for r in common]),"head_to_head":h2h}}
    summary={
        "date":DATE,"source_baseline_matches":len(baseline),
        "processed":len(allrows),"model_5":outcomes["5"],"model_10":outcomes["10"],
        "missing_data":{"under_five_each":sum(r.get("models",{}).get("5",{}).get("status")!="evaluated" for r in allrows),"under_ten_each":sum(r.get("models",{}).get("10",{}).get("status")!="evaluated" for r in allrows),"exceptions":sum(bool(r.get("error")) for r in allrows)},
        "method":"Independent non-H2H recent team matches scored/conceded. Project home=(home scored+away conceded)/2, away=(away scored+home conceded)/2, separately per quarter. Synthetic 80% historical thresholds from both teams' own game totals, direction chosen by proximity to expected mean.",
        "limitations":"Only matches with verified historical periods. Same-fixture comparison has different proxy betting lines; MAE is more comparable. No verified bookmaker odds or ROI.",
    }
    write("summary.json",summary)
    write("all_form_games.json",sorted(allrows,key=lambda x:x.get("start_ts") or 0))
    print("FORM_FINAL_SUMMARY "+json.dumps(summary,ensure_ascii=False),flush=True)
    md=[f"# Team-form basketball October 8 {DATE}",
        f"Processed: {len(allrows)} of {len(baseline)}",
        f"Team 5: {json.dumps(outcomes['5'],ensure_ascii=False)}",
        f"Team 10: {json.dumps(outcomes['10'],ensure_ascii=False)}",
        "No real bookmaker prices were used. See summary.json and all_form_games.json."]
    (ROOT/"report.md").write_text("\n\n".join(md),encoding="utf-8")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["shard","summary"])
    parser.add_argument("--index",type=int,default=0)
    parser.add_argument("--total",type=int,default=12)
    parser.add_argument("--workers",type=int,default=3)
    args=parser.parse_args()
    if args.mode=="shard":shard(args.index,args.total,args.workers)
    else:summarize()
