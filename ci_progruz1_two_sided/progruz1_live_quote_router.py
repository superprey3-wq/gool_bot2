"""Production LIVE next-goal quotes, keyed by verified Flashscore fixture ID.

Flashscore Odds regions are primary. Fonbet's paired/score-verified LIVE
quotes remain fallback. This module never fabricates sportsbook markets.
It contains only independently active OVER/UNDER pairs of the exact
next-goal line; odds/region are observed, not proof of bet executability.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor,as_completed
import os
import time


DEFAULT_REGIONS=("GB","FI","SE","ES","IT","DE","NL","FR","NO","PL","US","CA")


def _regions():
    requested=os.getenv("GOOL_FS_LIVE_ODDS_REGIONS","")
    allowed=set(DEFAULT_REGIONS)
    chosen=[p.strip().upper() for p in requested.split(",")
            if p.strip().upper() in allowed] if requested else []
    return tuple(dict.fromkeys(chosen or DEFAULT_REGIONS))


def _window(fs):
    from gool_flashscore_goal_brain import _second_half_started
    try:
        minute=int(fs.minute)
        score=(int(fs.home_score),int(fs.away_score))
        if min(score)<0 or max(score)>40 or getattr(fs,"is_halftime",False):
            return None
        second=_second_half_started(
            minute,(getattr(fs,"meta",None) or {}).get("status_code"))
    except (TypeError,ValueError,AttributeError):
        return None
    if second and 45<=minute<=80:
        return ("Матч",score,minute)
    if not second and 1<=minute<=35:
        return ("1-й тайм",score,minute)
    return None


def _source_label(book,geo):
    return str(book)[:60]+" · Flashscore "+geo


def quote_for_flashscore(fs,root,kickoff,*,regions=None,
                         request_fn=None,max_regions=4):
    """Read same fixture ID in several display regions, no team matching.

    Use only an exact +0.5 Asian/European total (NOT integer Asian O1.0).
    The API's exact-quote extractor validates selection, scope, live flag,
    and both prices from one bookmaker. No stale price cache.
    """
    from tools.progruz1_live_odds_audit import fetch_flashscore_odds
    window=_window(fs)
    if window is None:
        return [],{"reason":"OUTSIDE_GOAL_ENTRY_WINDOW","requests":0}
    period,score,minute=window
    get=request_fn or fetch_flashscore_odds
    regions=tuple(regions or _regions())
    attempts=[]
    found=[]
    for geo in regions[:max(1,int(max_regions))]:
        result=get(fs,geo)
        attempts.append({"geo":geo,"ok":bool(result.get("responded")),
                         "error":result.get("error") or ""})
        if not result.get("responded"):
            continue
        for offer in result.get("exact_next_goal_quotes") or []:
            if not offer.get("over_1_50_plus"):
                continue
            book=str(offer.get("bookmaker") or "").strip()
            if not book or book.startswith("id_"):
                continue
            try:
                over=float(offer["over"]);under=float(offer["under"])
                line=float(offer["line"])
                margin=1/over+1/under-1
            except (ValueError,TypeError,KeyError,ZeroDivisionError):
                continue
            # Do not let a malformed response bypass the already validated
            # bookmaker/line/period and current Flashscore score.
            correct_scope=("FIRST_HALF" if period=="1-й тайм" else "FULL_TIME")
            if (offer.get("period")!=correct_scope or
                    abs(line-(sum(score)+.5))>1e-6 or
                    not 1.50<=over<=3 or not 0<=margin<=.14):
                continue
            found.append({
                "root_id":str(root),"feed_root_id":str(root),
                "kickoff":int(kickoff),
                "event_id":"fs-odds:"+str(fs.provider_match_id)+":"+book+":"+geo,
                "match":str(fs.home)+" — "+str(fs.away),
                "period":period,"line":line,"over":over,"under":under,
                "match_score":score,"minute":minute,
                "market_kind":"general_total","current_half":period,
                "quote_provider":book,"quote_region":geo,
                "quote_label":_source_label(book,geo),
                "quote_source":"FLASHSCORE_ODDS",
            })
        # The fallback regions are for coverage, not to cherry-pick
        # several different geo quotations from the same bookmaker.
        if found:
            break
    return found,{"requests":len(attempts),"attempts":attempts,
                  "reason":"FOUND" if found else "NO_EXACT_LIVE_PRICE"}


def priced_round(fixtures,*,fallback_by_root=None,max_workers=4,
                 regions=None,regions_by_root=None,
                 request_fn=None,max_regions=4):
    """Network-budgeted batch; old Fonbet is only a price fallback.

    fixtures: [(root, kickoff, flashscore fixture)].
    All fixtures are already known from Flashscore and in entry windows.
    """
    fallback_by_root=fallback_by_root or {}
    regions_by_root=regions_by_root or {}
    jobs=list(fixtures)
    result={}
    if not jobs:
        return result,{"checked":0,"with_flashscore_quote":0,
                       "with_fonbet_fallback":0,"unpriced":0,"geo_requests":0}
    with ThreadPoolExecutor(max_workers=max(1,min(8,int(max_workers)))) as pool:
        future_map={pool.submit(
            quote_for_flashscore,fs,root,kick,
            regions=regions_by_root.get(str(root),regions),
            request_fn=request_fn,max_regions=max_regions
        ): (str(root),fs) for root,kick,fs in jobs}
        for finished in as_completed(future_map):
            root,fs=future_map[finished]
            try:
                primary,meta=finished.result()
            except Exception as exc:
                primary=[]
                meta={"reason":"SOURCE_ERROR_"+type(exc).__name__,"requests":0}
            # Flashscore's hasLiveBettingOffers/active flags do NOT
            # guarantee the displayed bookmaker quote is still available
            # at the present score. The odds API is an indicative scout,
            # not an executable LIVE bookmaker feed. A 3:0 match could
            # otherwise show O3.5 @2.90 while a current bookmaker offers
            # O3.5 @1.30. Repeatedly fetching that stale price does NOT
            # make it live. Keep indicative quotes for coverage ONLY.
            meta=dict(meta or {})
            meta["flashscore_reference_quotes"]=len(primary)
            meta["flashscore_reference_only"]=bool(primary)
            old=list(fallback_by_root.get(root) or [])
            window=_window(fs)
            correct_score=window[1] if window else None
            chosen=[]
            if window:
                wanted_period,_,_=window
                for q in old:
                    try:
                        actual=tuple(q.get("match_score") or ())
                        line=float(q["line"])
                        over=float(q["over"])
                        under=float(q["under"])
                        margin=1/over+1/under-1
                        # A 2H match total is different from 2H-only;
                        # accept only a verified exact market/score/line.
                        correct_period=q.get("period")==wanted_period
                        exact_line=sum(correct_score)+.5
                    except (ValueError,TypeError,KeyError,ZeroDivisionError):
                        continue
                    if (actual!=correct_score or not correct_period or
                            abs(line-exact_line)>1e-6 or
                            not 1.50<=over<=3.00 or
                            not 0<=margin<=.14):
                        continue
                    chosen.append(dict(
                        q,quote_provider="Fonbet",quote_region="",
                        quote_label="Fonbet LIVE · проверенная линия",
                        quote_source="FONBET_LIVE"))
            source="FONBET_LIVE" if chosen else "UNPRICED"
            if not chosen and primary:
                meta["reason"]="FLASHSCORE_ODDS_REFERENCE_ONLY"
            result[root]={"quotes":chosen,"source":source,"meta":meta}
    return result,{
        "checked":len(jobs),
        "with_flashscore_quote":sum(
            r["source"]=="FLASHSCORE_ODDS" for r in result.values()),
        "with_fonbet_fallback":sum(
            r["source"]=="FONBET_LIVE" for r in result.values()),
        "unpriced":sum(r["source"]=="UNPRICED" for r in result.values()),
        "geo_requests":sum(r["meta"].get("requests",0)
                           for r in result.values()),
        "flashscore_reference_only":sum(
            bool(r["meta"].get("flashscore_reference_only"))
            for r in result.values()),
    }
