#!/usr/bin/env python3
"""Read-only opponent-coefficient backtest: 10 general games vs 5 H2H, 2026-10-08.

All inputs were collected as pre-target matches in previous two successful runs.
Never use the evaluated game's score to build a forecast or pick a comparison line.
"""
from __future__ import annotations
import argparse, json, math, statistics
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from gool_bot2.providers.flashscore import FlashscoreProvider

ROOT=Path("artifacts/opponent_coefficient_20261008")
ROOT.mkdir(parents=True,exist_ok=True)
MODELS=("h2h5","form10","raw_coefficient","bounded_half_coefficient")

def save(name, value):
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")

def read(name):
    return json.loads((ROOT/name).read_text(encoding="utf-8"))

def baselines():
    h2h=read("h2h/all_matches.json")
    form=read("form/all_form_games.json")
    byh={r["event_id"]:r for r in h2h}
    byf={r["id"]:r for r in form}
    if len(byh)!=81 or len(byf)!=81 or set(byh)!=set(byf):
        raise ValueError(f"Original artifact coverage mismatch h2h={len(byh)} teamform={len(byf)}")
    return [(byh[k],byf[k]) for k in sorted(byh)]

def same(fs, left, right):
    return fs._same_team(left,right)

def verify_history(fs, row, home, away):
    eid=str(row["event_id"])
    segments=fs.fetch_segment_scores(eid,"basketball")
    expected=list(row.get("quarter_totals") or [])
    if len(expected)!=4:
        print(f"COEFF_DIAG {eid} history_missing_expected",flush=True)
        return None
    team_a,team_b=[],[]
    h,a=str(row.get("home") or ""),str(row.get("away") or "")
    direct=same(fs,h,home) and same(fs,a,away)
    reversed_=same(fs,h,away) and same(fs,a,home)
    if direct==reversed_:
        print(f"COEFF_DIAG {eid} name_mismatch h={h!r} a={a!r} target_h={home!r} target_a={away!r} direct={direct} reverse={reversed_}",flush=True)
        return None
    for q in range(1,5):
        pts=segments.get(f"QUARTER_{q}")
        if not isinstance(pts,(list,tuple)) or len(pts)!=2:
            print(f"COEFF_DIAG {eid} no_quarter={q} got={pts} keys={list(segments)}",flush=True)
            return None
        x,y=int(pts[0]),int(pts[1])
        if min(x,y)<0 or x+y!=int(expected[q-1]):
            print(f"COEFF_DIAG {eid} quarter_score_mismatch q={q} fetched={[x,y]} expected={expected[q-1]}",flush=True)
            return None
        team_a.append(x if direct else y)
        team_b.append(y if direct else x)
    return {"event_id":eid,"a":team_a,"b":team_b}

def forecast(a,b):
    result={"id":a["event_id"],"match":f"{a['home']} — {a['away']}",
        "league":a.get("league"),"actual":a.get("actual"),
        "start_ts":a.get("start_ts")}
    general=b.get("models",{}).get("10",{})
    if a.get("status")!="evaluated" or general.get("status")!="evaluated":
        result["status"]="baseline_not_comparable"
        return result
    fs=FlashscoreProvider()
    rows=list(a.get("historical_matches") or [])
    if len(rows)!=5 or any(int(r.get("timestamp") or r.get("ts") or 0)>=int(a["start_ts"]) for r in rows):
        result["status"]="bad_historical_time_window"
        return result
    try:
        # Original audit saved quarter totals and IDs, not home/away orientation.
        # Recover it from the original Flashscore H2H event list; never guess.
        meta=fs.fetch_match_history(a["event_id"],a["home"],a["away"],limit=40)
        history_by_id={
            str(m.get("event_id") or ""):m
            for m in (meta.get("h2h") or [])
            if m.get("event_id") and m.get("home") and m.get("away")
        }
        if any(str(m["event_id"]) not in history_by_id for m in rows):
            result["status"]="h2h_orientation_metadata_missing"
            return result
        oriented=[
            {**m,"home":history_by_id[str(m["event_id"])]["home"],
                "away":history_by_id[str(m["event_id"])]["away"]}
            for m in rows
        ]
        # Fetch team-specific quarter scores and verify frozen historical totals.
        rec=[verify_history(fs,r,a["home"],a["away"]) for r in oriented]
        if any(x is None for x in rec):
            result["status"]="h2h_segment_unavailable_or_team_mismatch"
            return result
        pred_h2h=list(a["historical_mean_quarters"])
        base_h=list(general["projected_home"])
        base_a=list(general["projected_away"])
        team_h=list(general["mean_home_scored"])
        team_a=list(general["mean_away_scored"])
        actual=list(a["actual"])
        raw,tempered=[],[]
        ratios_h,ratios_a=[],[]
        mean_h2h_h,mean_h2h_a=[],[]
        for q in range(4):
            ha=statistics.mean(r["a"][q] for r in rec)
            hb=statistics.mean(r["b"][q] for r in rec)
            if abs(ha+hb-pred_h2h[q])>0.12:
                result["status"]="h2h_baseline_mismatch"
                result["mismatch_quarter"]=q+1
                return result
            ga,gb=float(team_h[q]),float(team_a[q])
            if ga<1 or gb<1:
                result["status"]="zero_general_team_scoring"
                return result
            ka,kb=ha/ga,hb/gb
            ratios_h.append(round(ka,4));ratios_a.append(round(kb,4))
            mean_h2h_h.append(round(ha,3));mean_h2h_a.append(round(hb,3))
            raw.append(round(float(base_h[q])*ka+float(base_a[q])*kb,3))
            shrink_a=1.0+0.5*(max(0.75,min(1.25,ka))-1.0)
            shrink_b=1.0+0.5*(max(0.75,min(1.25,kb))-1.0)
            tempered.append(round(float(base_h[q])*shrink_a+float(base_a[q])*shrink_b,3))
        result.update({
            "status":"evaluated","h2h_ids":[x["event_id"] for x in rec],
            "mean_a_h2h":mean_h2h_h,"mean_b_h2h":mean_h2h_a,
            "mean_a_general10":team_h,"mean_b_general10":team_a,
            "coeff_a":ratios_h,"coeff_b":ratios_a,
            "forecast":{"h2h5":pred_h2h,
                "form10":list(general["predicted_quarters"]),
                "raw_coefficient":raw,"bounded_half_coefficient":tempered},
            "actual":actual,
        })
        # One fixed, entirely historical quarter line for ALL models.
        # An average between raw H2H and general-game forecast is neutral anchor.
        # This is NOT an actual sportsbook quote. It avoids using different
        # artificially safe lines to inflate win percentage for each model.
        lines=[math.floor((pred_h2h[q]+float(general["predicted_quarters"][q]))/2.0)+0.5 for q in range(4)]
        result["common_hypothetical_lines"]=lines
        for model,pred in result["forecast"].items():
            errs=[round(actual[q]-pred[q],3) for q in range(4)]
            calls=[]
            for q in range(4):
                side="OVER" if pred[q]>lines[q] else "UNDER"
                margin=abs(pred[q]-lines[q])
                hit=actual[q]>lines[q] if side=="OVER" else actual[q]<lines[q]
                calls.append({"quarter":q+1,"direction":side,"line":lines[q],"edge_points":round(margin,3),
                    "won":bool(hit),"strong":margin>=2.0})
            result.setdefault("metrics",{})[model]={"errors":errs,
                "mae":round(statistics.mean(abs(x) for x in errs),4),
                "total_forecast":round(sum(pred),3),
                "total_error":round(sum(actual)-sum(pred),3),
                "calls":calls}
        return result
    except Exception as exc:
        result["status"]="exception"
        result["error"]=f"{type(exc).__name__}:{exc}"
        return result

def shard(idx,total,workers):
    eligible=baselines()
    group=[pair for i,pair in enumerate(eligible) if i%total==idx]
    out=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(forecast,*pair) for pair in group]
        for future in as_completed(futures):
            r=future.result()
            out.append(r)
            print("MATCH "+json.dumps({"id":r["id"],"game":r["match"],"status":r["status"],
                "coeff_a":r.get("coeff_a"),"coeff_b":r.get("coeff_b")},ensure_ascii=False),flush=True)
    save(f"shard_{idx}.json",out)
    print(f"SHARD_DONE {idx} {len(out)} statuses={dict(Counter(r['status'] for r in out))}",flush=True)

def summary():
    baselines_rows=baselines()
    out=[]
    for path in sorted(ROOT.glob("shard_*.json")):
        out+=json.loads(path.read_text(encoding="utf-8"))
    ids=[r["id"] for r in out]
    if len(ids)!=len(baselines_rows) or len(set(ids))!=len(ids):
        raise SystemExit(f"SHARD_COVERAGE_INCOMPLETE read={len(ids)} unique={len(set(ids))} required={len(baselines_rows)}")
    evaluated=[x for x in out if x["status"]=="evaluated"]
    summaries={}
    for model in MODELS:
        rows=[x["metrics"][model] for x in evaluated]
        errors=[err for x in rows for err in x["errors"]]
        calls=[c for x in rows for c in x["calls"]]
        strong=[c for c in calls if c["strong"]]
        summaries[model]={
            "matches":len(rows),"quarters":len(errors),
            "mae_quarter":round(statistics.mean(abs(x) for x in errors),3) if errors else None,
            "rmse_quarter":round(math.sqrt(statistics.mean(x*x for x in errors)),3) if errors else None,
            "mean_error_actual_minus_forecast":round(statistics.mean(errors),3) if errors else None,
            "mae_full_match":round(statistics.mean(abs(x["total_error"]) for x in rows),3) if rows else None,
            "within_5_points":sum(abs(x)<=5 for x in errors),
            "common_line_all_picks":len(calls),"common_line_all_wins":sum(c["won"] for c in calls),
            "common_line_all_rate":round(sum(c["won"] for c in calls)/len(calls),4) if calls else None,
            "common_line_strong_picks":len(strong),"common_line_strong_wins":sum(c["won"] for c in strong),
            "common_line_strong_rate":round(sum(c["won"] for c in strong)/len(strong),4) if strong else None,
            "common_line_over":sum(c["direction"]=="OVER" for c in calls),
            "common_line_under":sum(c["direction"]=="UNDER" for c in calls),
            "common_line_over_wins":sum(c["direction"]=="OVER" and c["won"] for c in calls),
            "common_line_under_wins":sum(c["direction"]=="UNDER" and c["won"] for c in calls),
            "by_quarter":[{"quarter":q+1,"mae":round(statistics.mean(abs(x["errors"][q]) for x in rows),3) if rows else None,
                "wins":sum(x["calls"][q]["won"] for x in rows)} for q in range(4)],
        }
    report={"date":"2026-10-08","original_games":len(baselines_rows),
        "included_games":len(evaluated),"included_quarters":4*len(evaluated),
        "status_counts":dict(Counter(x["status"] for x in out)),
        "models":summaries,
        "description":"Each team's q-specific correction k=mean(team q H2H scored in last 5)/mean(team q scored across last 10 non-H2H games). Apply separately to general attack-defense projection for A and B. Partial: k=1+0.5*(clamp(k,0.75,1.25)-1).",
        "limitations":"Common lines are synthetic historical anchors, not betting-market lines. Compare MAE/RMSE as primary measures; no real odds/ROI.",
    }
    save("all_matches.json",sorted(out,key=lambda x:x.get("start_ts") or 0))
    save("summary.json",report)
    print("OPPONENT_COEFFICIENT_FINAL "+json.dumps(report,ensure_ascii=False),flush=True)
    for r in evaluated[:12]:
        print("EXAMPLE "+json.dumps({"match":r["match"],"k_a":r["coeff_a"],"k_b":r["coeff_b"],
            "actual":r["actual"],"form10":r["forecast"]["form10"],
            "raw":r["forecast"]["raw_coefficient"],
            "bounded":r["forecast"]["bounded_half_coefficient"]},ensure_ascii=False),flush=True)
    lines=["# Opponent-adjusted basketball model — Oct 8, 2026",
        "Source: 81 finished games, two frozen historical artifact datasets",
        f"Successfully compared: {len(evaluated)} games / {4*len(evaluated)} quarters",
        f"Other statuses: {report['status_counts']}",
        "Model | MAE | RMSE | Common-line wins/total | Common-line strong wins/total",
        "---|---:|---:|---:|---:"]
    for key,val in summaries.items():
        lines.append(f"{key} | {val['mae_quarter']} | {val['rmse_quarter']} | {val['common_line_all_wins']}/{val['common_line_all_picks']} | {val['common_line_strong_wins']}/{val['common_line_strong_picks']}")
    lines.append("**Not real bookmaker prices; no ROI.**")
    (ROOT/"report.md").write_text("\n".join(lines),encoding="utf-8")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=["shard","summary"])
    p.add_argument("--index",type=int,default=0)
    p.add_argument("--total",type=int,default=12)
    p.add_argument("--workers",type=int,default=3)
    args=p.parse_args()
    if args.mode=="shard": shard(args.index,args.total,args.workers)
    else: summary()
