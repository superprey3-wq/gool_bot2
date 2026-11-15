"""Read-only independent Flashscore Odds multi-country probe on GOOL2 CI.

Country is a declared geoIpCode API parameter, not a VPN or a source of
guaranteed executable bets. The fixture ID stays fixed across countries.
"""
from __future__ import annotations

from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

from gool_bot2.providers.flashscore import FlashscoreProvider


OUT=Path("reports/progruz1-flashscore-regions-latest.json")
REGIONS=("GB","SE","DE","NL","FI","IT","ES")
BASE="https://global.ds.lsapp.eu/odds/pq_graphql"


def get_odds(fixture,geo):
    mid=str(fixture.provider_match_id)
    params=urllib.parse.urlencode({
        "_hash":"oce","eventId":mid,"projectId":2,
        "geoIpCode":geo,"geoIpSubdivisionCode":""})
    url=BASE+"?"+params
    headers={
        "User-Agent":"Mozilla/5.0",
        "Accept":"application/json,text/plain,*/*",
        "Referer":"https://www.flashscore.com/",
        "Origin":"https://www.flashscore.com",
        "Accept-Language":"en-US,en;q=0.9"}
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url,headers=headers),timeout=8) as res:
            data=json.loads(res.read(5000000).decode("utf-8-sig"))
        holder=(data.get("data") or {}).get("findOddsByEventId") or {}
        book={
            str(entry.get("bookmaker",{}).get("id")):str(
                entry.get("bookmaker",{}).get("name") or "")
            for entry in ((holder.get("settings") or {}).get("bookmakers") or [])
            if isinstance(entry,dict) and isinstance(entry.get("bookmaker"),dict)}
        has_total=set()
        has_live_total=set()
        scopes=set()
        exact_books=set()
        exact_with_150=set()
        market_sample=[]
        for market in holder.get("odds") or []:
            if not isinstance(market,dict):
                continue
            if len(market_sample)<2:
                market_sample.append({
                    "bettingType":market.get("bettingType"),
                    "bettingScope":market.get("bettingScope"),
                    "hasLiveBettingOffers":market.get("hasLiveBettingOffers"),
                    "selection_example":(market.get("odds") or [None])[0]})
            if str(market.get("bettingType") or "").upper()!="OVER_UNDER":
                continue
            bk=book.get(str(market.get("bookmakerId")),"book_id_"+
                        str(market.get("bookmakerId")))
            has_total.add(bk)
            scope=str(market.get("bettingScope") or "").upper()
            if market.get("hasLiveBettingOffers") is not True:
                continue
            active=[item for item in market.get("odds") or []
                    if isinstance(item,dict) and item.get("active") is True]
            if not active:
                continue
            has_live_total.add(bk)
            scopes.add(scope)
            minute=int(fixture.minute or 0)
            desired=("FIRST_HALF" if 1<=minute<=35
                     else "FULL_TIME" if 46<=minute<=80 else None)
            if scope!=desired:
                continue
            try:
                wanted=int(fixture.home_score)+int(fixture.away_score)+.5
            except (ValueError,TypeError):
                continue
            sides=defaultdict(list)
            for offer in active:
                try:
                    handicap=offer.get("handicap")
                    if isinstance(handicap,dict):
                        handicap=handicap.get("value")
                    if abs(float(handicap)-wanted)>1e-6:
                        continue
                    side=str(offer.get("selection") or "").upper()
                    if side in ("OVER","UNDER"):
                        sides[side].append(float(offer["value"]))
                except (TypeError,ValueError,KeyError):
                    continue
            if len(sides["OVER"])==1 and len(sides["UNDER"])==1:
                over,under=sides["OVER"][0],sides["UNDER"][0]
                if over>1 and under>1:
                    exact_books.add(bk)
                    margin=1/over+1/under-1
                    if 1.50<=over<=3.00 and 0<=margin<=.14:
                        exact_with_150.add(bk)
        return {
            "id":mid,"geo":geo,"ok":True,
            "bookmakers_returned":list(book.values()),
            "total_books":sorted(has_total),
            "active_total_books":sorted(has_live_total),
            "scopes":sorted(scopes),
            "exact_books":sorted(exact_books),
            "exact_1_50_books":sorted(exact_with_150),
            "sample":market_sample[:1],
            "error":"",
        }
    except Exception as exc:
        return {"id":mid,"geo":geo,"ok":False,
                "bookmakers_returned":[],"total_books":[],
                "active_total_books":[],"scopes":[],
                "exact_books":[],"exact_1_50_books":[],
                "error":("HTTP_"+str(exc.code))
                        if getattr(exc,"code",None) else type(exc).__name__}


def summarize(rows):
    a=list(rows)
    return {
        "checked":len(a),
        "ok":sum(r["ok"] for r in a),
        "with_any_over_under":sum(bool(r["total_books"]) for r in a),
        "with_active_over_under":sum(bool(r["active_total_books"]) for r in a),
        "exact_next_goal_line":sum(bool(r["exact_books"]) for r in a),
        "exact_goal_1_50":sum(bool(r["exact_1_50_books"]) for r in a),
        "bookmakers":dict(Counter(
            b for row in a for b in row["active_total_books"]).most_common(25)),
        "errors":dict(Counter(r["error"] for r in a if r["error"])),
    }


def query_matches(fixtures,geos):
    rows={g:{} for g in geos}
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks={
            pool.submit(get_odds,fixture,geo):(
                geo,str(fixture.provider_match_id))
            for fixture in fixtures for geo in geos}
        for done in as_completed(tasks):
            geo,mid=tasks[done]
            rows[geo][mid]=done.result()
    return rows


def main():
    started=time.monotonic()
    fixtures=list({
        str(f.provider_match_id):f
        for f in FlashscoreProvider().live_matches()
    }.values())
    ordered=sorted(fixtures,key=lambda f:(
        str(f.league or ""),int(f.minute or 0),str(f.provider_match_id)))
    sample_count=min(22,len(ordered))
    sample=[ordered[int(i*len(ordered)/sample_count)]
            for i in range(sample_count)] if sample_count else []
    sample_result=query_matches(sample,REGIONS)
    sample_summary={geo:summarize(d.values())
                    for geo,d in sample_result.items()}
    rank=sorted(REGIONS,key=lambda geo:(
        -sample_summary[geo]["with_active_over_under"],
        -sample_summary[geo]["exact_goal_1_50"],geo))
    full_geos=list(dict.fromkeys(("GB","SE",*rank)))[:4]
    # Complete regional coverage up to 180 fixtures, no false claim
    # of checking more matches if today's catalogue exceeds that limit.
    all_games=ordered[:180]
    sample_ids={str(f.provider_match_id) for f in sample}
    rest=[f for f in all_games if str(f.provider_match_id) not in sample_ids]
    bulk=query_matches(rest,full_geos)
    full={}
    for geo in full_geos:
        full[geo]={**sample_result.get(geo,{}),**bulk.get(geo,{})}
    full_summary={geo:summarize(d.values()) for geo,d in full.items()}
    union_live=set().union(*[
        {mid for mid,row in d.items() if row["active_total_books"]}
        for d in full.values()]) if full else set()
    union_exact=set().union(*[
        {mid for mid,row in d.items() if row["exact_1_50_books"]}
        for d in full.values()]) if full else set()
    gb_live={mid for mid,r in full.get("GB",{}).items()
             if r["active_total_books"]}
    se_live={mid for mid,r in full.get("SE",{}).items()
             if r["active_total_books"]}
    gb_exact={mid for mid,r in full.get("GB",{}).items()
              if r["exact_1_50_books"]}
    report={
        "utc":datetime.now(timezone.utc).isoformat(),
        "status":"COMPLETE",
        "flashscore_live":len(fixtures),
        "full_checked":len(all_games),
        "sample_checked":len(sample),
        "geos_sampled":list(REGIONS),
        "geos_full":full_geos,
        "sample_by_geo":sample_summary,
        "full_by_geo":full_summary,
        "combined_live_total_fixtures":len(union_live),
        "combined_exact_1_50_fixtures":len(union_exact),
        "live_total_additional_vs_GB":len(union_live-gb_live),
        "sweden_additional_vs_GB":len(se_live-gb_live),
        "exact_additional_vs_GB":len(union_exact-gb_exact),
        "regional_bookmakers":{
            geo:sorted(set(b for row in d.values()
                for b in row["bookmakers_returned"]))[:60]
            for geo,d in full.items()},
        "examples_sweden_extra":[
            {"id":f.provider_match_id,"match":str(f.home)+" — "+str(f.away),
             "minute":f.minute,
             "se":full["SE"].get(str(f.provider_match_id),{}).get(
                 "active_total_books",[])}
            for f in all_games
            if str(f.provider_match_id) in se_live-gb_live][:30],
        "sample_response_shape":{
            geo:next((r.get("sample",[]) for r in full[geo].values()
                     if r.get("sample")),[])
            for geo in full_geos},
        "duration_seconds":round(time.monotonic()-started,2),
        "notes":"Changing geoIpCode is not changing outgoing IP. A 'live' flag is not proof of executable quote."
    }
    OUT.parent.mkdir(exist_ok=True,parents=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),
                   encoding="utf-8")
    print("FLASH_GEO_AUDIT",json.dumps({
        k:v for k,v in report.items() if k not in (
            "sample_response_shape","regional_bookmakers",
            "examples_sweden_extra","sample_by_geo")},
        ensure_ascii=False),flush=True)
    return report


if __name__=="__main__":
    main()
