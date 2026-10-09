#!/usr/bin/env python3
"""Out-of-sample basketball H2H historical quarter backtest. Does not place bets."""
from __future__ import annotations
import argparse
import json
import os
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_multisport_steam import parse_flashscore_events

DAY = "2026-10-08"
TZ = ZoneInfo("Europe/Moscow")
ROOT = Path("artifacts/basket_all_h2h_20261008")
ROOT.mkdir(parents=True, exist_ok=True)

def write(name, val):
    (ROOT / name).write_text(json.dumps(val, indent=2, ensure_ascii=False), encoding="utf-8")

def quarters(parts):
    if len(parts) < 4:
        return None
    try:
        vals = [sum(map(int, x)) for x in parts[:4]]
        return vals if all(x >= 0 for x in vals) else None
    except (TypeError, ValueError, IndexError):
        return None

def norm(value):
    return "".join(c for c in str(value or "").casefold() if c.isalnum())

def same_name(a,b):
    a,b=norm(a),norm(b)
    return bool(a and b and (a==b or (min(len(a),len(b))>=6 and (a in b or b in a))))

def exact_pair(item, home, away):
    h,a=item.get("home",""),item.get("away","")
    return (same_name(h,home) and same_name(a,away)) or (same_name(a,home) and same_name(h,away))

def discover():
    fs=FlashscoreProvider()
    rows={}
    sources=[]
    for offset in [-2,-1,0,1]:
        for code in ("3", "0"):
            path=f"f_3_{offset}_{code}_en_1"
            body=fs._feed(path,timeout=14,max_hosts=2)
            seen=parse_flashscore_events(body) if body else []
            sources.append({"path":path,"bytes":len(body),"matches":len(seen)})
            for m in seen:
                eid=m.get("flashscore_event_id")
                if eid and (eid not in rows or len(m.get("score_parts") or [])>len(rows[eid].get("score_parts") or [])):
                    rows[eid]=m
    selected=[]
    statuses=Counter()
    for row in rows.values():
        ts=int(row.get("start_ts") or 0)
        if not ts or datetime.fromtimestamp(ts,TZ).date().isoformat()!=DAY:
            continue
        statuses[str(row.get("coarse_status") or "")]+=1
        # Finished coarse status 3, and four valid quarters: exclude live, future,
        # postponed, non-standard halves and incomplete scoreboard data.
        if str(row.get("coarse_status") or "") != "3":
            continue
        if quarters(row.get("score_parts") or []) is None:
            continue
        selected.append(row)
    selected.sort(key=lambda x:(x.get("start_ts") or 0,x.get("flashscore_event_id") or ""))
    manifest={"date":DAY,"timezone":"Europe/Moscow","source":sources,"on_date_statuses":dict(statuses),"all_retrieved_events":len(rows),"eligible_finished_with_quarters":len(selected),"events":selected}
    write("manifest.json",manifest)
    print("DISCOVERY "+json.dumps({k:v for k,v in manifest.items() if k!="events"},ensure_ascii=False),flush=True)
    print("DISCOVERY_SAMPLES "+json.dumps([{"id":x["flashscore_event_id"],"game":x["home"]+" -- "+x["away"],"score":x["score"]} for x in selected[:12]],ensure_ascii=False),flush=True)

def segment_total(fs, eid):
    score=fs.fetch_segment_scores(eid,"basketball")
    return quarters([score.get(f"QUARTER_{i}") for i in range(1,5)])

def evaluate(item, fs):
    event_id=item["flashscore_event_id"]
    actual=quarters(item.get("score_parts") or [])
    rec={
        "event_id":event_id, "home":item.get("home"),"away":item.get("away"),
        "league":item.get("league"),"start_ts":item.get("start_ts"),
        "actual":actual,"actual_regulation_total":sum(actual),
        "final_score":item.get("score"),
        "ot_points":sum(map(int,item.get("score") or [0,0]))-sum(actual),
    }
    try:
        history=fs.fetch_match_history(event_id,rec["home"],rec["away"],limit=36)
        rec["h2h_feed_present"]=bool(history.get("feed_present"))
        matches=history.get("h2h") or []
        seen=set()
        past=[]
        for x in sorted(matches,key=lambda x:int(x.get("timestamp") or 0),reverse=True):
            mid=str(x.get("event_id") or "")
            date=int(x.get("timestamp") or 0)
            if not mid or mid==event_id or mid in seen or not (0<date<int(item.get("start_ts") or 0)):
                continue
            if not exact_pair(x,rec["home"],rec["away"]):
                continue
            seen.add(mid)
            past.append(x)
            if len(past)>=10: break
        rec["h2h_identified"]=len(past)
        # Only the five immediately preceding meetings. Never skip a past game
        # with missing periods and silently use an older substitute.
        five=past[:5]
        hist=[]
        for match in five:
            q=segment_total(fs,match["event_id"])
            hist.append({"event_id":match["event_id"],"ts":match.get("timestamp"),"date":datetime.fromtimestamp(int(match["timestamp"]),TZ).date().isoformat(),"quarter_totals":q,"total":sum(q) if q else None})
        rec["historical_matches"]=hist
        complete=[m["quarter_totals"] for m in hist if m.get("quarter_totals") is not None]
        rec["h2h_complete"]=len(complete)
        if len(five)<5 or len(complete)<5:
            rec["status"]="insufficient_h2h" if len(five)<5 else "quarter_data_missing"
            return rec
        means=[round(statistics.mean(row[k] for row in complete),2) for k in range(4)]
        rec["historical_mean_quarters"]=means
        rec["historical_mean_total"]=round(sum(means),2)
        errors=[round(x-y,2) for x,y in zip(actual,means)]
        rec["errors"]=errors
        rec["mae"]=round(statistics.mean(map(abs,errors)),2)
        rec["bias"]=round(statistics.mean(errors),2)
        rec["status"]="evaluated"
        # Exactly the same frozen hypothetical 4/5 rule as the five-game demo:
        # establish candidate line on historical samples ONLY (before target).
        bets=[]
        for q in range(4):
            vals=sorted(match[q] for match in complete)
            over,under=vals[1]-0.5,vals[3]+0.5
            mean=means[q]
            side,line=("OVER",over) if mean-over<=under-mean else ("UNDER",under)
            hit=(actual[q]>line) if side=="OVER" else (actual[q]<line)
            bets.append({"quarter":q+1,"side":side,"line":line,"won":bool(hit),"actual":actual[q],"distance_from_mean":round(abs(line-mean),2)})
        rec["synthetic_bets"]=bets
        rec["synthetic_wins"]=sum(x["won"] for x in bets)
        return rec
    except Exception as exc:
        rec["status"]="error"
        rec["error"]=str(type(exc).__name__)+":"+str(exc)
        return rec

def shard(idx,n,workers):
    manifest=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
    batch=[m for i,m in enumerate(manifest["events"]) if i%n==idx]
    fs=FlashscoreProvider()
    results=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(evaluate,row,fs) for row in batch]
        for fut in as_completed(futures):
            val=fut.result()
            results.append(val)
            print("MATCH "+json.dumps({"event_id":val["event_id"],"status":val["status"],"h2h":val.get("h2h_identified"),"h2h_complete":val.get("h2h_complete"),"win":val.get("synthetic_wins"),"game":val["home"]+" — "+val["away"]},ensure_ascii=False),flush=True)
    write(f"shard_{idx}.json",results)
    print(f"SHARD_DONE {idx}/{n} count={len(results)} status={dict(Counter(r['status'] for r in results))}",flush=True)

def summary():
    manifest=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
    rows=[]
    for path in sorted(ROOT.glob("shard_*.json")): rows.extend(json.loads(path.read_text(encoding="utf-8")))
    ids=[r["event_id"] for r in rows]
    valid=[r for r in rows if r.get("status")=="evaluated"]
    bets=[b for r in valid for b in r["synthetic_bets"]]
    allerr=[e for r in valid for e in r["errors"]]
    result={
        "day":DAY,"timezone":"Europe/Moscow","eligible_finished_with_quarters":manifest["eligible_finished_with_quarters"],
        "processed":len(rows),"unique_processed":len(set(ids)),
        "status_counts":dict(Counter(r["status"] for r in rows)),
        "strict_five_h2h_evaluated":len(valid),
        "quarters_evaluated":len(allerr),
        "mae_points_per_quarter":round(statistics.mean(map(abs,allerr)),3) if allerr else None,
        "signed_bias_points_per_quarter":round(statistics.mean(allerr),3) if allerr else None,
        "synthetic_bets":len(bets),"synthetic_wins":sum(b["won"] for b in bets),
        "synthetic_win_rate":round(sum(b["won"] for b in bets)/len(bets),4) if bets else None,
        "over":sum(b["side"]=="OVER" for b in bets),"under":sum(b["side"]=="UNDER" for b in bets),
        "over_wins":sum(b["won"] for b in bets if b["side"]=="OVER"),
        "under_wins":sum(b["won"] for b in bets if b["side"]=="UNDER"),
        "quarter_results":[{"quarter":i+1,"bets":sum(b["quarter"]==i+1 for b in bets),"wins":sum(b["quarter"]==i+1 and b["won"] for b in bets)} for i in range(4)],
        "warning":"Synthetic lines chosen from historical distribution; no bookmaker offered odds or availability checked. Results do NOT measure profitability."
    }
    write("summary.json",result)
    write("all_matches.json",sorted(rows,key=lambda x:x.get("start_ts") or 0))
    print("FINAL_SUMMARY "+json.dumps(result,ensure_ascii=False),flush=True)
    for r in valid[:100]:
        print("RESULT "+json.dumps({"game":r["home"]+" — "+r["away"],"league":r["league"],"forecast":r["historical_mean_quarters"],"actual":r["actual"],"wins":r["synthetic_wins"],"picks":[b["side"][0]+str(b["line"])+("W" if b["won"] else "L") for b in r["synthetic_bets"]]},ensure_ascii=False),flush=True)
    if len(rows)!=manifest["eligible_finished_with_quarters"] or len(set(ids))!=len(rows):
        raise SystemExit("INCOMPLETE_SHARD_COVERAGE")
    md=[
        f"# Historical basketball H2H backtest — {DAY} MSK",
        f"Finished with four quarter scores: {result['eligible_finished_with_quarters']}",
        f"Processed: {result['processed']} | Strict five H2H complete: {len(valid)}",
        f"Status counts: {result['status_counts']}",
        f"Mean absolute error / quarter: {result['mae_points_per_quarter']}",
        f"Average signed forecast error (actual - prediction): {result['signed_bias_points_per_quarter']}",
        f"Synthetic 4/5 historical-threshold accuracy: **{result['synthetic_wins']}/{len(bets)}** ({result['synthetic_win_rate']})",
        f"OVER wins: {result['over_wins']}/{result['over']}; UNDER wins: {result['under_wins']}/{result['under']}",
        "**Warning:** No real bookmaker odds or historical quote availability; synthetic accuracy is not betting profitability.",
    ]
    Path("artifacts/basket_all_h2h_20261008/report.md").write_text("\n\n".join(md),encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as out:out.write("\n\n".join(md)+"\n")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("mode",choices=["discover","shard","summary"])
    ap.add_argument("--index",type=int,default=0)
    ap.add_argument("--total",type=int,default=12)
    ap.add_argument("--workers",type=int,default=3)
    args=ap.parse_args()
    if args.mode=="discover": discover()
    elif args.mode=="shard":shard(args.index,args.total,args.workers)
    else:summary()
