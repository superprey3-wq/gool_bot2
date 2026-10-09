#!/usr/bin/env python3
"""GOOL 2026-10-09 remaining-basketball historical quarter screening.
Read-only one-time scouting (NO Telegram, NO production-model changes).
Only games not yet started at initial discovery timestamp, Moscow calendar day.
"""
from __future__ import annotations
import argparse, json, math, os, statistics, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_multisport_steam import (
    parse_flashscore_events, MultiSportSteamWorker, SPORTS,
    map_xbet_to_flashscore
)
from gool_bot2.xbet_multisport_markets import market_lanes

ROOT=Path("artifacts/basketball_today_stable_20261009")
ROOT.mkdir(parents=True,exist_ok=True)
MST=ZoneInfo("Europe/Moscow")
DAY="2026-10-09"
EXCLUDE=("esports","e-sports","ebasketball","cyber","nba2k","3x3","2x2","virtual")
def dump(name,obj): (ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
def load(name):return json.loads((ROOT/name).read_text(encoding="utf-8"))
def sname(fs, a, b):return fs._same_team(str(a or ""),str(b or ""))
def pair_match(fs,row,a,b):
    return (sname(fs,row.get("home"),a) and sname(fs,row.get("away"),b)) or (sname(fs,row.get("away"),a) and sname(fs,row.get("home"),b))
def short(x):return round(float(x),3)
def qscore(fs,event_id):
    d=fs.fetch_segment_scores(str(event_id),"basketball")
    pts=[]
    for i in range(1,5):
        r=d.get(f"QUARTER_{i}")
        if not isinstance(r,(list,tuple)) or len(r)!=2:return None
        try: v=list(map(int,r))
        except (ValueError,TypeError):return None
        if min(v)<0:return None
        pts.append(v)
    return pts
def discover():
    fs=FlashscoreProvider()
    rows={}
    sources=[]
    for path in ("f_3_-1_3_en_1","f_3_0_3_en_1","f_3_0_0_en_1","f_3_1_3_en_1"):
        body=fs._feed(path,timeout=12,max_hosts=2)
        parsed=parse_flashscore_events(body) if body else []
        sources.append({"path":path,"matches":len(parsed),"bytes":len(body)})
        for r in parsed:
            eid=str(r.get("flashscore_event_id") or "")
            if eid:
                rows[eid]=r
    captured=int(time.time())
    end=int(datetime(2026,10,10,tzinfo=MST).timestamp())
    selected=[]
    statuses=Counter()
    for r in rows.values():
        ts=int(r.get("start_ts") or 0)
        if not ts or datetime.fromtimestamp(ts,MST).date().isoformat()!=DAY:continue
        statuses[str(r.get("coarse_status") or "")]+=1
        if str(r.get("coarse_status") or "")!="1":continue
        if not (captured+600 < ts < end):continue
        if any(x in str(r.get("league") or "").casefold() for x in EXCLUDE):continue
        selected.append(r)
    selected.sort(key=lambda r:(r.get("start_ts") or 0,r.get("home") or ""))
    result={"date":DAY,"capture_unix":captured,"capture_msk":datetime.fromtimestamp(captured,MST).isoformat(),"end_msk":datetime.fromtimestamp(end,MST).isoformat(),
        "feeds":sources,"date_statuses":dict(statuses),"unique_scanned":len(rows),"remaining_upcoming":len(selected),"fixtures":selected}
    dump("manifest.json",result)
    print("DISCOVERY "+json.dumps({k:v for k,v in result.items() if k!="fixtures"},ensure_ascii=False),flush=True)
    for r in selected:
        print("FIXTURE "+json.dumps({"event_id":r["flashscore_event_id"],"start_msk":datetime.fromtimestamp(r["start_ts"],MST).strftime("%H:%M"),"match":r["home"]+" — "+r["away"],"league":r["league"]},ensure_ascii=False),flush=True)
def side_history(fs,rows,team,other,kickoff,event_id):
    seen=set();prior=[]
    for m in sorted(rows,key=lambda v:int(v.get("timestamp") or 0),reverse=True):
        eid=str(m.get("event_id") or "")
        ts=int(m.get("timestamp") or 0)
        if not eid or eid==event_id or eid in seen or not (ts>0 and ts<kickoff):continue
        if not (sname(fs,m.get("home"),team) or sname(fs,m.get("away"),team)):continue
        if pair_match(fs,m,team,other):continue
        seen.add(eid)
        prior.append(m)
        if len(prior)>=10:break
    return prior
def evaluate(r):
    fs=FlashscoreProvider()
    eid=str(r["flashscore_event_id"]);home=str(r["home"]);away=str(r["away"])
    result={"event_id":eid,"home":home,"away":away,"league":r.get("league"),"start_ts":r["start_ts"],
        "start_msk":datetime.fromtimestamp(int(r["start_ts"]),MST).strftime("%d.%m %H:%M"),
        "status":"WAIT"}
    try:
        ctx=fs.fetch_match_history(eid,home,away,limit=40)
        combined=[]
        for key in ("home_recent","away_recent","home_at_home","away_away","h2h"):
            combined += [x for x in ctx.get(key,[]) if isinstance(x,dict)]
        ha=side_history(fs,combined,home,away,int(r["start_ts"]),eid)
        aa=side_history(fs,combined,away,home,int(r["start_ts"]),eid)
        result["history_feed_present"]=bool(ctx.get("feed_present"))
        result["prior_home_n"]=len(ha);result["prior_away_n"]=len(aa)
        if len(ha)<10 or len(aa)<10:
            result["status"]="WAIT_HISTORY_INSUFFICIENT";return result
        allgames={m["event_id"]:m for m in ha+aa}
        with ThreadPoolExecutor(max_workers=7) as pool:
            seg=dict(zip(allgames,pool.map(lambda x:qscore(fs,x),allgames)))
        def profile(rows,team):
            data=[]
            for m in rows:
                q=seg.get(m["event_id"])
                if not q:continue
                direct=sname(fs,m.get("home"),team)
                reverse=sname(fs,m.get("away"),team)
                if direct==reverse:continue
                data.append({"event_id":m["event_id"],"date":datetime.fromtimestamp(int(m["timestamp"]),MST).date().isoformat(),
                    "scored":[p[0 if direct else 1] for p in q],
                    "conceded":[p[1 if direct else 0] for p in q],
                    "totals":[sum(p) for p in q]})
            return data
        h=profile(ha,home);a=profile(aa,away)
        result["complete_home_n"]=len(h);result["complete_away_n"]=len(a)
        if len(h)<10 or len(a)<10:
            result["status"]="WAIT_QUARTER_DATA";return result
        result["history_home_ids"]=[x["event_id"] for x in h]
        result["history_away_ids"]=[x["event_id"] for x in a]
        predictions=[]
        candidates=[]
        for q in range(4):
            hs=statistics.mean(x["scored"][q] for x in h)
            hc=statistics.mean(x["conceded"][q] for x in h)
            aws=statistics.mean(x["scored"][q] for x in a)
            ac=statistics.mean(x["conceded"][q] for x in a)
            projh=(hs+ac)/2;proja=(aws+hc)/2
            predicted=projh+proja
            htot=sorted(x["totals"][q] for x in h)
            atot=sorted(x["totals"][q] for x in a)
            # The most demanding synthetic line supported by >=9/10 in BOTH
            # samples; purely historical, no proof that this line is on offer.
            max_over=min(htot[1],atot[1])-0.5
            min_under=max(htot[8],atot[8])+0.5
            predictions.append({"quarter":q+1,"predicted":short(predicted),
                "home_expected":short(projh),"away_expected":short(proja),
                "home_scored_mean":short(hs),"away_scored_mean":short(aws),
                "home_conceded_mean":short(hc),"away_conceded_mean":short(ac),
                "over_nine_max_line":max_over,
                "under_nine_min_line":min_under,
                "home_totals":htot,"away_totals":atot})
            # Synthetic thresholds from history. These are NOT an actual pick.
            for direction,line in (("OVER",max_over),("UNDER",min_under)):
                hit=lambda z: z>line if direction=="OVER" else z<line
                hn=sum(hit(v) for v in htot)
                an=sum(hit(v) for v in atot)
                candidates.append({"quarter":q+1,"direction":direction,"synthetic_line":line,
                    "home_hits":hn,"away_hits":an,"historical_mean":short(predicted),
                    "edge_vs_synthetic_line":round((predicted-line) if direction=="OVER" else (line-predicted),2)})
        # Optional head-to-head historical coefficient, restricted to
        # verified pre-kickoff event IDs and 5 H2H for this same pairing.
        h2h=[]
        for x in sorted(ctx.get("h2h") or [],key=lambda v:int(v.get("timestamp") or 0),reverse=True):
            if (str(x.get("event_id"))!=eid and int(x.get("timestamp") or 0)<int(r["start_ts"])
                and pair_match(fs,x,home,away)):
                h2h.append(x)
            if len(h2h)>=5:break
        result["h2h_n"]=len(h2h)
        if len(h2h)==5:
            hh=[]
            for x in h2h:
                segs=seg.get(x["event_id"]) or qscore(fs,x["event_id"])
                if not segs:break
                direct=sname(fs,x.get("home"),home)
                reverse=sname(fs,x.get("away"),home)
                if direct==reverse:break
                hh.append(([p[0 if direct else 1] for p in segs],[p[1 if direct else 0] for p in segs]))
            if len(hh)==5:
                for q,pred in enumerate(predictions):
                    coefh=statistics.mean(x[0][q] for x in hh)/max(1,statistics.mean(x["scored"][q] for x in h))
                    coefa=statistics.mean(x[1][q] for x in hh)/max(1,statistics.mean(x["scored"][q] for x in a))
                    adjh=1+0.5*(min(1.25,max(.75,coefh))-1)
                    adja=1+0.5*(min(1.25,max(.75,coefa))-1)
                    pred["coefficient_home"]=short(coefh)
                    pred["coefficient_away"]=short(coefa)
                    pred["adjusted_expected"]=short(pred["home_expected"]*adjh+pred["away_expected"]*adja)
                result["h2h_coefficient_available"]=True
        result["status"]="HISTORY_READY"
        result["prediction"]=predictions
        result["synthetic_watch_thresholds"]=candidates
        return result
    except Exception as exc:
        result["status"]="ERROR"
        result["error"]=type(exc).__name__+":"+str(exc)
        return result

def shard(index,total,workers):
    manifest=load("manifest.json")
    targets=[r for i,r in enumerate(manifest["fixtures"]) if i%total==index]
    results=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks=[pool.submit(evaluate,r) for r in targets]
        for task in as_completed(tasks):
            r=task.result()
            results.append(r)
            print("HISTORY "+json.dumps({"event_id":r["event_id"],"game":r["home"]+" — "+r["away"],"status":r["status"],
                "home":r.get("complete_home_n"),"away":r.get("complete_away_n"),"h2h":r.get("h2h_n")},ensure_ascii=False),flush=True)
    dump(f"shard_{index}.json",results)
    print("SHARD_DONE "+json.dumps({"index":index,"count":len(results),"states":dict(Counter(x["status"] for x in results))},ensure_ascii=False),flush=True)

def count_hits(p, direction, line, *, adjust_for_opponent=False):
    """Check the SAME bookmaker quarter line on both teams' independent last 10 games.

    First compute the per-team opponent coefficient from the last five H2Hs.
    Its bounded half-strength impact moves the projected match-quarter mean.
    Apply that SAME translation (delta points) to the 10 historical quarter
    totals from EACH team's past games, preserving the variation. In other
    words the threshold test is run on opponent-adjusted historical evidence,
    not the raw games. Without five valid H2H games this mode is not eligible.
    """
    delta=(float(p["adjusted_expected"])-float(p["predicted"])) if adjust_for_opponent else 0.0
    h=p["home_totals"];a=p["away_totals"]
    predicate=(lambda v:v+delta>line) if direction=="OVER" else (lambda v:v+delta<line)
    return sum(predicate(x) for x in h),sum(predicate(x) for x in a)

def price(history,manifest):
    output={"status":"NOT_CHECKED","xbet_index":0,"mapped":0,"priced_fixtures":0,"diagnostic":{},"prices":[]}
    eligible=[x for x in history if x["status"]=="HISTORY_READY"
              and x.get("h2h_coefficient_available") is True
              and int(x["start_ts"])>int(time.time())+120]
    if not eligible:return output
    try:
        os.environ["GOOL_MULTISPORT_PREMATCH_INDEX_COUNT"]="1000"
        os.environ["GOOL_MULTISPORT_CURRENT_LINEFEED_FALLBACK"]="1"
        worker=MultiSportSteamWorker(ROOT/"readonly_worker")
        cfg=SPORTS["basketball"]
        events=worker._xbet_prematch_index(cfg)
        output["xbet_index"]=len(events)
        output["diagnostic"]=worker._prematch_index_diag.get(cfg.key,{})
        fs_lookup={x["flashscore_event_id"]:x for x in manifest["fixtures"]}
        fs_targets=[fs_lookup[x["event_id"]] for x in eligible if x["event_id"] in fs_lookup]
        mappings=map_xbet_to_flashscore(events,fs_targets)
        output["mapped"]=len(mappings)
        history_by_id={x["event_id"]:x for x in eligible}
        for item,fs,reversed_,quality in mappings:
            if float(fs.get("start_ts") or 0)<=time.time()+120:continue
            row=history_by_id.get(str(fs["flashscore_event_id"]))
            if not row:continue
            eid=str(item["I"])
            game=worker._prematch_game(eid,cfg) or item
            decoded,metadata=worker._market_tree(game,cfg,prematch=True,
                wanted_scopes={"QUARTER_1","QUARTER_2","QUARTER_3","QUARTER_4"})
            lanes=[v for v in market_lanes(decoded)
                   if v.get("market_family")=="match_total" and str(v.get("scope") or "").startswith("QUARTER_")]
            accepted=[]
            for lane in lanes:
                scope=str(lane.get("scope") or "")
                try:q=int(scope.split("_")[-1])
                except (ValueError,IndexError):continue
                if q not in (1,2,3,4):continue
                line=float(lane["line"])
                pred=row["prediction"][q-1]
                for direction in ("OVER","UNDER"):
                    odd=float(lane["over"] if direction=="OVER" else lane["under"])
                    if not 1.30<=odd<=4.0:continue
                    raw_a,raw_b=count_hits(pred,direction,line,adjust_for_opponent=False)
                    hits_a,hits_b=count_hits(pred,direction,line,adjust_for_opponent=True)
                    gap=(float(pred["adjusted_expected"])-line)*(1 if direction=="OVER" else -1)
                    raw_gap=(float(pred["predicted"])-line)*(1 if direction=="OVER" else -1)
                    # Comparable original/adjusted screen against EXACT same quote.
                    old_8=min(raw_a,raw_b)>=8 and raw_gap>0
                    new_8=min(hits_a,hits_b)>=8 and gap>0
                    if not (old_8 or new_8):continue
                    accepted.append({"quarter":q,"direction":direction,"line":line,"odd":odd,
                        "home_hits":hits_a,"away_hits":hits_b,"strong_9_each":min(hits_a,hits_b)>=9 and gap>0,
                        "raw_home_hits":raw_a,"raw_away_hits":raw_b,
                        "raw_strong_9_each":min(raw_a,raw_b)>=9 and raw_gap>0,
                        "old_8":old_8,"new_8":new_8,
                        "opponent_adjustment_points":round(float(pred["adjusted_expected"])-float(pred["predicted"]),3),
                        "coefficient_home":pred["coefficient_home"],
                        "coefficient_away":pred["coefficient_away"],
                        "unadjusted_forecast":pred["predicted"],
                        "forecast":pred["adjusted_expected"],
                        "raw_forecast_edge":round(raw_gap,2),
                        "forecast_edge":round(gap,2),"fs_match":row["home"]+" — "+row["away"],
                        "event_id":row["event_id"],"xbet_event_id":eid,
                        "start_msk":row["start_msk"],
                        "mapped_quality":round(float(quality),3),
                        "quote_snapshot_utc":datetime.now(timezone.utc).isoformat()})
            output["prices"].append({"event_id":row["event_id"],"match":row["home"]+" — "+row["away"],
                "start_msk":row["start_msk"],"market_scopes":metadata.get("coverage"),"total_lanes":len(lanes),
                "eligible_lanes":accepted})
        output["priced_fixtures"]=len(output["prices"])
        output["status"]="OK" if events else "NO_XBET_INDEX"
    except Exception as exc:
        output["status"]="ERROR"
        output["error"]=type(exc).__name__+":"+str(exc)
    return output

def report():
    manifest=load("manifest.json")
    # Reuse the previous day's SAME frozen dataset, so both screen versions
    # are compared without rerunning or changing any historical features.
    if (ROOT/"all_fixtures.json").exists():
        targets=load("all_fixtures.json")
    else:
        targets=[]
        for path in sorted(ROOT.glob("shard_*.json")):
            targets+=json.loads(path.read_text(encoding="utf-8"))
    if len(targets)!=len(manifest["fixtures"]) or len({x["event_id"] for x in targets})!=len(targets):
        raise SystemExit("INCOMPLETE_DISCOVERY_COVERAGE")
    eligible=[r for r in targets if r["status"]=="HISTORY_READY"]
    price_data=price(eligible,manifest)
    priced=[lane for item in price_data["prices"] for lane in item["eligible_lanes"]]
    strict=[x for x in priced if x["strong_9_each"]]
    old_strict=[x for x in priced if x["raw_strong_9_each"]]
    moderate=[x for x in priced if x["new_8"] and not x["strong_9_each"]]
    newly_qualified=[x for x in strict if not x["raw_strong_9_each"]]
    no_longer_qualified=[x for x in old_strict if not x["strong_9_each"]]
    def rank(x):
        return (min(x["home_hits"],x["away_hits"]),x["home_hits"]+x["away_hits"],
                x["forecast_edge"],x["odd"])
    selected={}
    for p in strict:
        key=p["event_id"]
        if key not in selected or rank(p)>rank(selected[key]):
            selected[key]=p
    if not strict:
        # Explicitly no real book-confirmed 9/10 signal, do not invent.
        print("NO_BOOK_CONFIRMED_9_OF_10",flush=True)
    synthetic=[]
    for r in eligible:
        for c in r["synthetic_watch_thresholds"]:
            if min(c["home_hits"],c["away_hits"])>=9:
                synthetic.append({"event_id":r["event_id"],"game":r["home"]+" — "+r["away"],
                    "start_msk":r["start_msk"],**c})
    result={"date_msk":DAY,"captured_msk":manifest["capture_msk"],
        "run_msk":datetime.now(MST).isoformat(),"fixtures_remaining_at_capture":len(manifest["fixtures"]),
        "all_fixtures_analyzed":len(targets),"status_counts":dict(Counter(x["status"] for x in targets)),
        "history_10_teams_ready":len(eligible),"h2h_adjustment_ready":sum(x.get("h2h_coefficient_available") is True for x in eligible),
        "synthetic_9of10_thresholds":len(synthetic),"synthetic_examples":synthetic[:30],
        "xbet":price_data,"book_confirmed_9of10_bets":sorted(selected.values(),key=lambda x:x["start_msk"]),
        "book_confirmed_8of10_not_9":len(moderate),
        "original_raw_9of10_same_games_and_quotes":len(old_strict),
        "new_opponent_adjusted_9of10":len(strict),
        "newly_promoted_by_opponent_coefficient":newly_qualified,
        "demoted_by_opponent_coefficient":no_longer_qualified,
        "compare_note":"Both model versions checked on the same currently published bookmaker lines and same frozen October 9 history. 10-game quarter totals translated by the quarter-specific half-strength capped H2H correction (in points). 5 verified H2H games required; no fallback to unadjusted model on missing H2H.",

        "warning":"Only bookmaker-verified prices can be actual bets; historical thresholds have NO verified bookmaker quote. LIVE quarter price may differ after match starts. No odds guarantee or ROI claim."}
    dump("full_report.json",result)
    dump("all_fixtures.json",sorted(targets,key=lambda x:x["start_ts"]))
    lean={k:v for k,v in result.items() if k not in ("xbet","synthetic_examples")}
    print("TODAY_FINAL "+json.dumps(lean,ensure_ascii=False),flush=True)
    print("XBET_STATUS "+json.dumps({"status":price_data["status"],"events":price_data["xbet_index"],"mapped":price_data["mapped"],"priced":price_data["priced_fixtures"],"diag":price_data["diagnostic"],"error":price_data.get("error")},ensure_ascii=False),flush=True)
    print("CORRECTION_COMPARE "+json.dumps({"old_raw_9of10":len(old_strict),"corrected_9of10":len(strict),
        "promoted":len(newly_qualified),"demoted":len(no_longer_qualified),"corrected_8only":len(moderate)},ensure_ascii=False),flush=True)
    for v in newly_qualified:
        print("COEFFICIENT_PROMOTED "+json.dumps(v,ensure_ascii=False),flush=True)
    for v in no_longer_qualified:
        print("COEFFICIENT_DEMOTED "+json.dumps(v,ensure_ascii=False),flush=True)
    for v in moderate:
        print("CORRECTION_BORDERLINE "+json.dumps(v,ensure_ascii=False),flush=True)
    for v in sorted(selected.values(),key=lambda x:x["start_msk"]):
        print("TODAY_REAL_PRICE_PICK "+json.dumps(v,ensure_ascii=False),flush=True)
    for r in sorted(eligible,key=lambda x:x["start_ts"])[:40]:
        print("TODAY_HISTORY "+json.dumps({"start_msk":r["start_msk"],"game":r["home"]+" — "+r["away"],
            "forecast":[p.get("adjusted_expected",p["predicted"]) for p in r["prediction"]],
            "thresholds":[{"quarter":c["quarter"],"dir":c["direction"],"line":c["synthetic_line"],
                "home_hits":c["home_hits"],"away_hits":c["away_hits"]} for c in r["synthetic_watch_thresholds"] if min(c["home_hits"],c["away_hits"])>=9]},ensure_ascii=False),flush=True)
    md=["# Today's remaining basketball games — historical selective quarter scouting",
        f"Captured MSK: {result['captured_msk']}",
        f"Upcoming games: {len(targets)}; history ready: {len(eligible)}; H2H correction available: {result['h2h_adjustment_ready']}",
        f"1xBet: {price_data['status']} | index={price_data['xbet_index']} mapped={price_data['mapped']} priced={price_data['priced_fixtures']}",
        f"**Book-confirmed 9/10 candidate matches (corrected): {len(selected)}**",
        f"Unadjusted same-market 9/10 candidates: {len(old_strict)}; adjusted: {len(strict)}",
        f"Newly promoted: {len(newly_qualified)}; removed: {len(no_longer_qualified)}",
        "",
        "| Time MSK | Match | Quarter | Pick | Price | History team A / B |",
        "|---|---|---:|---|---:|---|"]
    for x in sorted(selected.values(),key=lambda x:x["start_msk"]):
        md.append(f"| {x['start_msk']} | {x['fs_match']} | {x['quarter']} | {x['direction']} {x['line']:g} | {x['odd']:.2f} | {x['home_hits']}/10, {x['away_hits']}/10 |")
    md.extend(["","### Borderline adjusted 8/10 quotes (NOT strong signals)"])
    for x in moderate[:20]:
        md.append(f"- {x['start_msk']} | {x['fs_match']} | Q{x['quarter']} {x['direction']} {x['line']:g} @{x['odd']:.2f} | adjusted {x['home_hits']}/10, {x['away_hits']}/10 vs raw {x['raw_home_hits']}/10, {x['raw_away_hits']}/10; delta {x['opponent_adjustment_points']:+.2f} pts")
    md.extend(["","Synthetic historical thresholds are NOT bookmaker selections and cannot be placed without fresh price verification.","See full_report.json and all_fixtures.json for all diagnostics and historical averages."])
    (ROOT/"report.md").write_text("\n".join(md),encoding="utf-8")

if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("command",choices=["discover","shard","report"])
    a.add_argument("--index",type=int,default=0)
    a.add_argument("--total",type=int,default=12)
    a.add_argument("--workers",type=int,default=2)
    z=a.parse_args()
    if z.command=="discover":discover()
    elif z.command=="shard":shard(z.index,z.total,z.workers)
    else:report()
