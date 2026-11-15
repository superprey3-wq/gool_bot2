"""Read-only independent Flashscore Odds multi-country probe on GOOL2 CI.

Country is a declared geoIpCode API parameter, not a VPN or a source of
guaranteed executable bets. The fixture ID stays fixed across countries.
"""
from __future__ import annotations

from collections import Counter,defaultdict
import csv
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

from gool_bot2.providers.flashscore import FlashscoreProvider


OUT=Path("reports/progruz1-flashscore-regions-latest.json")
CSV_OUT=Path("reports/progruz1-flashscore-regions-all-matches.csv")
REGIONS=("GB","SE","FI","ES","IT","DE","NL","FR","NO","PL","US","CA")
CORE_REGIONS=("GB","SE","FI","ES")
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

    # No slice or arbitrary cap. All Flashscore LIVE event IDs get
    # exactly the same four-region base pass.
    core=query_matches(ordered,CORE_REGIONS)
    core_active={mid for geo in CORE_REGIONS for mid,row in core[geo].items()
                 if row["active_total_books"]}
    core_exact={mid for geo in CORE_REGIONS for mid,row in core[geo].items()
                if row["exact_1_50_books"]}

    # Query eight more regions only where a quote is missing, or the
    # current goal market is unavailable in an eligible entry window.
    missing=[f for f in ordered
        if str(f.provider_match_id) not in core_active or (
            (1<=int(f.minute or 0)<=35 or 46<=int(f.minute or 0)<=80)
            and str(f.provider_match_id) not in core_exact)]
    extra_regions=tuple(g for g in REGIONS if g not in CORE_REGIONS)
    extras=query_matches(missing,extra_regions)
    all_by_geo={**core,**extras}
    base_summary={g:summarize(rows.values()) for g,rows in core.items()}
    extra_summary={g:summarize(rows.values()) for g,rows in extras.items()}

    matches=[]
    statuses=Counter()
    all_active=set()
    all_exact=set()
    extra_active=set()
    extra_exact=set()
    for fixture in ordered:
        mid=str(fixture.provider_match_id)
        responses={g:rows[mid] for g,rows in all_by_geo.items()
                   if mid in rows}
        api_errors={g:r["error"] for g,r in responses.items()
                    if not r["ok"]}
        active={g:r["active_total_books"]
                for g,r in responses.items() if r["active_total_books"]}
        exact={g:r["exact_1_50_books"]
               for g,r in responses.items() if r["exact_1_50_books"]}
        market_present=any(r["total_books"] for r in responses.values())
        minute=int(fixture.minute or 0)
        in_window=1<=minute<=35 or 46<=minute<=80
        if active:
            all_active.add(mid)
            if not any(g in CORE_REGIONS for g in active):
                extra_active.add(mid)
        if exact:
            all_exact.add(mid)
            if not any(g in CORE_REGIONS for g in exact):
                extra_exact.add(mid)
        # Re-check failures distinguish NO_PRICE from UNKNOWN/API errors.
        if exact:
            status="EXACT_NEXT_GOAL_OVER_1_50_OBSERVED"
        elif active:
            status=("ACTIVE_OTHER_OR_UNVERIFIED_TOTAL"
                    if in_window else "ACTIVE_TOTAL_OUTSIDE_ENTRY_WINDOW")
        elif api_errors:
            status=("ALL_SOURCES_FAILED" if len(api_errors)==len(responses)
                    else "NO_PRICE_WITH_SOME_API_ERRORS")
        elif market_present:
            status="TOTAL_MARKET_PRESENT_NOT_ACTIVE"
        else:
            status="NO_TOTAL_MARKET_IN_12_REGION_RESPONSES"
        statuses[status]+=1
        matches.append({
            "fs_id":mid,
            "match":str(fixture.home)+" — "+str(fixture.away),
            "league":str(fixture.league or ""),
            "minute":minute,
            "score":[fixture.home_score,fixture.away_score],
            "in_entry_window":in_window,
            "regions_checked":sorted(responses),
            "api_errors":api_errors,
            "active_total_regions":active,
            "exact_over_1_50_regions":exact,
            "status":status,
        })

    gb_active={mid for mid,r in core.get("GB",{}).items()
               if r["active_total_books"]}
    se_active={mid for mid,r in core.get("SE",{}).items()
               if r["active_total_books"]}
    gb_exact={mid for mid,r in core.get("GB",{}).items()
              if r["exact_1_50_books"]}
    n=len(ordered)
    all_rows={r["fs_id"] for r in matches}
    missing_any=[r for r in matches if not r["active_total_regions"]]
    missing_target=[r for r in matches
                    if r["in_entry_window"] and not r["exact_over_1_50_regions"]]
    report={
        "utc":datetime.now(timezone.utc).isoformat(),
        "status":"COMPLETE" if len(all_rows)==n else "INCOMPLETE_IDS",
        "flashscore_live":n,
        "all_live_ids_checked":len(all_rows),
        "coverage_of_live_ids_pct":round(100*len(all_rows)/n,2) if n else 100,
        "base_regions":list(CORE_REGIONS),
        "fallback_regions":list(extra_regions),
        "fallback_matches":len(missing),
        "total_requests":sum(len(rows) for rows in all_by_geo.values()),
        "base_regions_summary":base_summary,
        "fallback_regions_summary":extra_summary,
        "with_active_totals_any_region":len(all_active),
        "with_exact_goal_over_1_50_any_region":len(all_exact),
        "without_active_totals_any_region":n-len(all_active),
        "additional_active_totals_fallback":len(extra_active),
        "additional_exact_goal_fallback":len(extra_exact),
        "active_total_additional_vs_GB":len(all_active-gb_active),
        "exact_goal_additional_vs_GB":len(all_exact-gb_exact),
        "sweden_active_only_vs_GB":len(se_active-gb_active),
        "entry_window_no_exact_target":len(missing_target),
        "errors_by_region":{
            g:dict(Counter(r["error"] for r in rows.values() if r["error"]))
            for g,rows in all_by_geo.items()},
        "status_breakdown":dict(statuses),
        "missing_active_total":[r for r in missing_any],
        "missing_exact_target_in_window":[r for r in missing_target],
        "all_matches":matches,
        "duration_seconds":round(time.monotonic()-started,2),
        "notes":(
            "100% means every Flashscore LIVE ID was checked; this does not "
            "mean every match has a live available total. A display geo "
            "parameter does not alter physical IP, and odds are not "
            "guaranteed executable. API errors are not no-odds claims.")
    }
    OUT.parent.mkdir(exist_ok=True,parents=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),
                   encoding="utf-8")
    with CSV_OUT.open("w",newline="",encoding="utf-8-sig") as handle:
        columns=["fs_id","match","league","minute","score",
                 "in_entry_window","status","regions_checked",
                 "api_errors","active_total_regions",
                 "exact_over_1_50_regions"]
        writer=csv.DictWriter(handle,fieldnames=columns)
        writer.writeheader()
        for row in matches:
            writer.writerow({
                k:json.dumps(row[k],ensure_ascii=False)
                if isinstance(row[k],(dict,list)) else row[k]
                for k in columns})
    print("FLASH_GEO_ALL_LIVE",json.dumps(
        {k:v for k,v in report.items()
         if k not in ("all_matches","missing_active_total",
                      "missing_exact_target_in_window",
                      "base_regions_summary","fallback_regions_summary")},
        ensure_ascii=False),flush=True)
    print("MATCHES_CSV",CSV_OUT,flush=True)
    return report


if __name__=="__main__":
    main()
