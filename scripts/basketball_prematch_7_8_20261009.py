#!/usr/bin/env python3
"""Read-only PREMATCH historical matchup screen (tiers 7/10 and 8/10).

Frozen Oct 9 Flashscore fixture/history snapshot, fetched before kickoff.
No live pace; no Telegram; no production changes.
Markets: regulation-quarter totals, first-half totals, match totals and individual
totals. Full-game totals may include overtime depending on bookmaker; flagged.
"""
from __future__ import annotations
import argparse, json, os, statistics, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_multisport_steam import (
    MultiSportSteamWorker, SPORTS, map_xbet_to_flashscore
)
from gool_bot2.xbet_multisport_markets import market_lanes

ROOT=Path("artifacts/basketball_prematch_7_8_20261009")
ROOT.mkdir(parents=True,exist_ok=True)
DATE="2026-10-09"
MST=ZoneInfo("Europe/Moscow")

def save(name,value):
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
def load(name):
    return json.loads((ROOT/name).read_text(encoding="utf-8"))
def same(fs,a,b):
    return fs._same_team(str(a or ""),str(b or ""))
def is_pair(fs,game,home,away):
    return (same(fs,game.get("home"),home) and same(fs,game.get("away"),away)) or (same(fs,game.get("home"),away) and same(fs,game.get("away"),home))
def game_quarters(fs,eid):
    data=fs.fetch_segment_scores(str(eid),"basketball")
    result=[]
    for i in range(1,5):
        v=data.get(f"QUARTER_{i}")
        if not isinstance(v,(list,tuple)) or len(v)!=2:
            return None
        try:
            a,b=map(int,v)
        except (ValueError,TypeError):
            return None
        if min(a,b)<0:return None
        result.append((a,b))
    return result
def adjusted_multiplier(coeff):
    return 1+0.5*(max(0.75,min(1.25,float(coeff)))-1)
def raw_samples(row,history_index,team):
    # Retain only the actual team order from the original Flashscore H2H history.
    fs=FlashscoreProvider()
    selected=[]
    for eid in row:
        m=history_index.get(str(eid))
        if not m:
            selected.append({"event_id":eid,"orientation":"unknown"})
            continue
        home=same(fs,m.get("home"),team)
        away=same(fs,m.get("away"),team)
        selected.append({"event_id":eid,"orientation":"home" if home and not away else ("away" if away and not home else "unknown")})
    return selected

def eval_fixture(r):
    fs=FlashscoreProvider()
    result={
        "event_id":r["event_id"],"home":r["home"],"away":r["away"],
        "league":r.get("league"),"start_ts":r["start_ts"],"start_msk":r.get("start_msk"),
        "status":"WAIT"
    }
    if r.get("status")!="HISTORY_READY":
        result["status"]=r.get("status","WAIT_HISTORY")
        return result
    hids=list(r.get("history_home_ids") or [])
    aids=list(r.get("history_away_ids") or [])
    if len(hids)!=10 or len(aids)!=10:
        result["status"]="WAIT_SOURCE_IDS"
        return result
    result["has_h2h_coefficient"]=bool(r.get("h2h_coefficient_available"))
    # Require actual H2H sample for strict opponent-adjusted mode;
    # unadjusted fixtures are still documented but not primary selections.
    predictions=list(r.get("prediction") or [])
    if len(predictions)!=4:
        result["status"]="WAIT_PREDICTION"
        return result
    eventids=list(dict.fromkeys(hids+aids))
    try:
        ctx=fs.fetch_match_history(r["event_id"],r["home"],r["away"],limit=48)
        history={}
        for name in ("home_recent","away_recent","home_at_home","away_away","h2h"):
            for x in ctx.get(name) or []:
                if isinstance(x,dict) and x.get("event_id"):
                    history[str(x["event_id"])]=x
        orientations_h=raw_samples(hids,history,r["home"])
        orientations_a=raw_samples(aids,history,r["away"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            scores=dict(zip(eventids,pool.map(lambda x:game_quarters(fs,x),eventids)))
        def team_records(games):
            res=[]
            for x in games:
                parts=scores.get(x["event_id"])
                if not parts:return []
                if x["orientation"]=="home":
                    score=[a for a,b in parts]; conceded=[b for a,b in parts]
                elif x["orientation"]=="away":
                    score=[b for a,b in parts]; conceded=[a for a,b in parts]
                else:
                    score=None;conceded=None
                res.append({"id":x["event_id"],"totals":[a+b for a,b in parts],
                    "scored":score,"conceded":conceded})
            return res
        h=team_records(orientations_h)
        a=team_records(orientations_a)
        if len(h)!=10 or len(a)!=10:
            result["status"]="WAIT_SEGMENT_QUARTERS"
            return result
        result["individual_stats_available"]=all(v["scored"] is not None for v in h+a)
        def sum_scope(values,indices):
            return sum(values[q] for q in indices)
        scopes={
            "QUARTER_1":[0],"QUARTER_2":[1],
            "QUARTER_3":[2],"QUARTER_4":[3],
            "FIRST_HALF":[0,1],"SECOND_HALF":[2,3],
            "FULL_MATCH":[0,1,2,3],
        }
        def changes(q):
            p=predictions[q]
            if not result["has_h2h_coefficient"]:
                return (0.,0.)
            kh=adjusted_multiplier(p.get("coefficient_home",1))
            ka=adjusted_multiplier(p.get("coefficient_away",1))
            return float(p["home_expected"])*(kh-1),float(p["away_expected"])*(ka-1)
        means={}
        for scope,indices in scopes.items():
            dh=sum(changes(q)[0] for q in indices)
            da=sum(changes(q)[1] for q in indices)
            mu_home=sum(float(predictions[q]["home_expected"]) for q in indices)+dh
            mu_away=sum(float(predictions[q]["away_expected"]) for q in indices)+da
            def build(group,field):
                if any(z[field] is None for z in group):return None
                return [sum_scope(z[field],indices) for z in group]
            means[scope]={
                "expected_home":round(mu_home,3),"expected_away":round(mu_away,3),
                "expected_total":round(mu_home+mu_away,3),
                "home_delta":round(dh,4),"away_delta":round(da,4),
                # IMPORTANT: independent 10-game per-team historical evidence.
                # For team individual total pair HOME scored with AWAY conceded.
                # For AWAY individual total pair AWAY scored with HOME conceded.
                "match_total_h":build(h,"totals"),"match_total_a":build(a,"totals"),
                "home_scored":build(h,"scored"),"home_allowed":build(a,"conceded"),
                "away_scored":build(a,"scored"),"away_allowed":build(h,"conceded"),
            }
        result["scopes"]=means
        result["status"]="HISTORY_READY"
        return result
    except Exception as ex:
        result["status"]="ERROR"
        result["error"]=f"{type(ex).__name__}:{ex}"
        return result

def shard(index,total,workers):
    rows=load("baseline/all_fixtures.json")
    selected=[r for j,r in enumerate(rows) if j%total==index]
    out=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks=[pool.submit(eval_fixture,r) for r in selected]
        for t in as_completed(tasks):
            v=t.result()
            out.append(v)
            print("PREMATCH_HISTORY "+json.dumps({"id":v["event_id"],"game":v["home"]+" — "+v["away"],
                "status":v["status"],"individual_stats":v.get("individual_stats_available"),
                "coefficient":v.get("has_h2h_coefficient")},ensure_ascii=False),flush=True)
    save(f"shard_{index}.json",out)
    print("SHARD_DONE "+json.dumps({"shard":index,"count":len(out),
        "status_counts":dict(Counter(x["status"] for x in out))},ensure_ascii=False),flush=True)

def check(stats,family,direction,line):
    base={
        "match_total":("match_total_h","match_total_a","expected_total","home_delta","away_delta"),
        "home_total":("home_scored","home_allowed","expected_home","home_delta"),
        "away_total":("away_scored","away_allowed","expected_away","away_delta"),
    }.get(family)
    if not base:return None
    left=stats.get(base[0]);right=stats.get(base[1])
    if left is None or right is None or len(left)!=10 or len(right)!=10:return None
    offset=sum(float(stats.get(k) or 0.0) for k in base[3:])
    mu=float(stats[base[2]])
    predicate=(lambda x:x>line) if direction=="OVER" else (lambda x:x<line)
    raw_hits=[sum(predicate(x) for x in series) for series in (left,right)]
    corrected_hits=[sum(predicate(x+offset) for x in series) for series in (left,right)]
    margin=(mu-line)*(1 if direction=="OVER" else -1)
    return {"raw_hits":raw_hits,"corrected_hits":corrected_hits,
        "historical_mean":round(mu,2),"correction_points":round(offset,3),
        "margin_points":round(margin,3)}

def prematch_price(rows):
    output={"status":"NOT_CHECKED","xbet_index":0,"mapped":0,"inspected":0,
        "book_market_lanes":0,"priced_rows":[],"diag":{}}
    upcoming=[x for x in rows if x.get("status")=="HISTORY_READY"
              and int(x.get("start_ts") or 0)>int(time.time())+180]
    if not upcoming:return output
    try:
        os.environ["GOOL_MULTISPORT_PREMATCH_INDEX_COUNT"]="1000"
        os.environ["GOOL_MULTISPORT_CURRENT_LINEFEED_FALLBACK"]="1"
        worker=MultiSportSteamWorker(ROOT/"readonly_worker")
        cfg=SPORTS["basketball"]
        xevents=worker._xbet_prematch_index(cfg)
        output["xbet_index"]=len(xevents)
        output["diag"]=worker._prematch_index_diag.get("basketball") or {}
        source=load("baseline/manifest.json")
        byfs={x["flashscore_event_id"]:x for x in source["fixtures"]}
        candidates=[byfs[x["event_id"]] for x in upcoming if x["event_id"] in byfs]
        mapped=map_xbet_to_flashscore(xevents,candidates)
        output["mapped"]=len(mapped)
        byid={x["event_id"]:x for x in upcoming}
        for xb,fs,reverse,quality in mapped:
            if int(fs.get("start_ts") or 0)<=int(time.time())+180:continue
            row=byid.get(str(fs.get("flashscore_event_id") or ""))
            if not row:continue
            game=worker._prematch_game(str(xb["I"]),cfg) or xb
            decoded,meta=worker._market_tree(game,cfg,prematch=True,
                wanted_scopes={"QUARTER_1","QUARTER_2","QUARTER_3","QUARTER_4",
                    "FIRST_HALF","SECOND_HALF"})
            lanes=[l for l in market_lanes(decoded) if l.get("market_family") in
                ("match_total","home_total","away_total")]
            eligible=[]
            seen=set()
            for lane in lanes:
                scope=str(lane.get("scope"))
                family=str(lane.get("market_family"))
                stats=row["scopes"].get(scope)
                if not stats:continue
                line=float(lane["line"])
                if line<=0:continue
                for direction in ("OVER","UNDER"):
                    odd=float(lane["over"] if direction=="OVER" else lane["under"])
                    if not (1.45<=odd<=3.00):continue
                    code=(scope,family,direction,round(line,3))
                    if code in seen:continue
                    seen.add(code)
                    vals=check(stats,family,direction,line)
                    if not vals:continue
                    count_a,count_b=vals["corrected_hits"]
                    min_hits=min(count_a,count_b)
                    if min_hits<7:continue
                    # Enforce a positive expected margin, avoiding "pass 8/10"
                    # picks resulting only from far-away synthetic low/hi lines.
                    floor=4.0 if scope=="FULL_MATCH" else (2.5 if "HALF" in scope else 1.5)
                    if family!="match_total":floor*=0.70
                    if vals["margin_points"]<floor:continue
                    # 8/10 is a candidate, 7/10 is WATCH only, no auto delivery.
                    tier="CANDIDATE_8" if min_hits>=8 else "WATCH_7"
                    eligible.append({
                        "tier":tier,"home":row["home"],"away":row["away"],
                        "event_id":row["event_id"],"xbet_id":str(xb["I"]),
                        "start_msk":row["start_msk"],"scope":scope,"family":family,
                        "direction":direction,"line":line,"odd":odd,
                        "side_a_hits":count_a,"side_b_hits":count_b,
                        "raw_hits":vals["raw_hits"],"correction_points":vals["correction_points"],
                        "model_mean":vals["historical_mean"],"margin_points":vals["margin_points"],
                        "has_h2h_coefficient":row["has_h2h_coefficient"],
                        "market_match_quality":round(float(quality),3),
                        "full_match_overtime_settlement_unverified":scope=="FULL_MATCH",
                        "quote_utc":datetime.now(timezone.utc).isoformat()
                    })
            output["inspected"]+=1
            output["book_market_lanes"]+=len(lanes)
            output["priced_rows"].append({"event_id":row["event_id"],
                "match":row["home"]+" — "+row["away"],"quotes":eligible,
                "market_coverage":meta.get("coverage")})
        output["status"]="OK" if xevents else "NO_XBET_INDEX"
    except Exception as ex:
        output["status"]="ERROR";output["error"]=type(ex).__name__+":"+str(ex)
    return output

def report():
    manifest=load("baseline/manifest.json")
    raw=[]
    for p in sorted(ROOT.glob("shard_*.json")):
        raw+=json.loads(p.read_text(encoding="utf-8"))
    if len(raw)!=len(manifest["fixtures"]) or len({x["event_id"] for x in raw})!=len(raw):
        raise SystemExit(f"INCOMPLETE_SHARD_COVERAGE processed={len(raw)}, expected={len(manifest['fixtures'])}")
    pricing=prematch_price(raw)
    allquotes=[x for m in pricing["priced_rows"] for x in m["quotes"]]
    strict=[x for x in allquotes if x["tier"]=="CANDIDATE_8" and x["has_h2h_coefficient"]]
    watch=[x for x in allquotes if x["tier"]=="WATCH_7" and x["has_h2h_coefficient"]]
    no_coeff=[x for x in allquotes if not x["has_h2h_coefficient"]]
    # Old screening is compared against the same bookmaker quote and history
    # and the same positive line margin. Corrected counts are new 7/8 filter.
    def oldhits(x):return min(x["raw_hits"])>=8
    old8=sum(oldhits(x) for x in strict+watch)
    # One per match. Prefer higher minimum hit count, larger edge, then odds;
    # exclude FULL_MATCH from actionable candidate list until settlement known.
    selectable=[x for x in strict if not x["full_match_overtime_settlement_unverified"]]
    best={}
    for x in selectable:
        if x["event_id"] not in best or (
            min(x["side_a_hits"],x["side_b_hits"]),x["margin_points"],x["odd"]
        )>(min(best[x["event_id"]]["side_a_hits"],best[x["event_id"]]["side_b_hits"]),
           best[x["event_id"]]["margin_points"],best[x["event_id"]]["odd"]):
            best[x["event_id"]]=x
    report={"date":DATE,"snapshot_utc":datetime.now(timezone.utc).isoformat(),
        "initial_fixtures":len(manifest["fixtures"]),"history_statuses":dict(Counter(x["status"] for x in raw)),
        "full_history":sum(x["status"]=="HISTORY_READY" for x in raw),
        "h2h_coefficient":sum(x.get("has_h2h_coefficient") is True for x in raw),
        "price":pricing,"strong_8_quotes":len(strict),"watch_7_quotes":len(watch),
        "no_h2h_coefficient_quotes":len(no_coeff),
        "old_raw_atleast8_for_current_qualified_quotes":old8,
        "one_per_match_shadows":sorted(best.values(),key=lambda x:x["start_msk"]),
        "warning":"Backtest/research only; bookmaker quote snapshots change, history of only 10 games is noisy. Full-game quarter total history excludes OT, bookmaker full game settlements may include OT and require manual rule verification. No claimed model ROI."}
    save("summary.json",report)
    save("all_games.json",sorted(raw,key=lambda x:x.get("start_ts") or 0))
    log={k:v for k,v in report.items() if k!="price"}
    print("PREMATCH_FINAL "+json.dumps(log,ensure_ascii=False),flush=True)
    print("PREMATCH_XBET "+json.dumps({"status":pricing["status"],"index":pricing["xbet_index"],"mapped":pricing["mapped"],
        "inspected":pricing["inspected"],"market_lanes":pricing["book_market_lanes"],"error":pricing.get("error")},ensure_ascii=False),flush=True)
    for x in sorted(best.values(),key=lambda v:v["start_msk"]):
        print("PREMATCH_STRONG_PICK "+json.dumps(x,ensure_ascii=False),flush=True)
    for x in strict:
        if x["full_match_overtime_settlement_unverified"]:
            print("PREMATCH_FULL_GAME_RESEARCH_ONLY "+json.dumps(x,ensure_ascii=False),flush=True)
    for x in watch[:30]:
        print("PREMATCH_WATCH_7 "+json.dumps(x,ensure_ascii=False),flush=True)
    text=[
        "# GOOL Basketball PREMATCH — opponent-adjusted 7/10 vs 8/10",
        f"Input day: {DATE} MSK, originally {len(raw)} future fixtures",
        f"Complete history: {report['full_history']}; H2H correction: {report['h2h_coefficient']}",
        f"Bookmaker: {pricing['status']} events={pricing['xbet_index']} mapped={pricing['mapped']} inspected={pricing['inspected']}",
        f"8/10 strong quotes with H2H: {len(strict)}; 7/10 WATCH: {len(watch)}",
        f"One per game (research-only): {len(best)}",
        "",
        "| Start MSK | Match | Scope | Family | Market | Odds | 10-game A/B | Correction |",
        "|---|---|---|---|---|---:|---|---:|"]
    for x in sorted(best.values(),key=lambda v:v["start_msk"]):
        text.append(f"| {x['start_msk']} | {x['home']} — {x['away']} | {x['scope']} | {x['family']} | {x['direction']} {x['line']} | {x['odd']:.2f} | {x['side_a_hits']}/10 {x['side_b_hits']}/10 | {x['correction_points']:+.2f} |")
    text.append("\nFull-game markets require OT settlement rule verification before real use. No Telegram was sent.")
    (ROOT/"report.md").write_text("\n".join(text),encoding="utf-8")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["shard","report"])
    parser.add_argument("--index",type=int,default=0)
    parser.add_argument("--total",type=int,default=12)
    parser.add_argument("--workers",type=int,default=2)
    z=parser.parse_args()
    if z.mode=="shard":shard(z.index,z.total,z.workers)
    else:report()
