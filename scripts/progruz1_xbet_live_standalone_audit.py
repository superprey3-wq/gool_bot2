"""Independent full football LIVE 1xBet benchmark on GOOL2 GitHub Actions.

Uses GOOL2's existing robust collector with legacy & v3 fallback. It
only observes prices; it neither posts Telegram signals nor places bets.
Counts approximate identity separately from VERIFIED bookmaker score and
exact current-period totals.
"""
from __future__ import annotations

import json
import pathlib
import time
import threading
from datetime import datetime, timezone
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector
from gool_bot2 import xbet_market_pressure as market


OUT=pathlib.Path("reports/progruz1-xbet-source-latest.json")


def run():
    started=time.monotonic()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    collector=RobustXBetMarketCollector(
        pathlib.Path("reports/progruz1-xbet-rawstate.json"),
        pathlib.Path("reports/progruz1-xbet-temp-history.jsonl"))
    captured={}
    indexed_scores={}
    index_lock=threading.Lock()

    # Preserve the robust collector's original LIVE-index behaviour while
    # retaining each returned 1xBet event's authoritative score field.
    # No extra requests, no time-sensitive score guessed from Flashscore.
    def capture_index_rows(root, query):
        payload=market._http_json(
            f"{root}/Get1x2_VZip?{query}",timeout=7.0)
        values=payload.get("Value") if isinstance(payload,dict) else None
        if not isinstance(values,list):
            return root,[]
        rows=[]
        for event in values:
            if not isinstance(event,dict):
                continue
            event_id=str(event.get("I") or "").strip()
            home=str(event.get("O1") or "").strip()
            away=str(event.get("O2") or "").strip()
            if not event_id or not home or not away:
                continue
            rows.append({"event_id":event_id,"home":home,"away":away,"root":root})
            sc=(event.get("SC") or {}).get("FS") or {}
            try:
                actual=(int(sc["S1"]),int(sc["S2"]))
            except (KeyError,ValueError,TypeError):
                continue
            if min(actual)<0:
                continue
            with index_lock:
                indexed_scores.setdefault(event_id,set()).add(actual)
        return root,rows

    collector._index_rows=capture_index_rows
    original_game=collector._game

    def remember_game(event_id):
        game=original_game(event_id)
        if game:
            captured[str(event_id)]=game
        return game

    collector._game=remember_game
    state=collector.collect_once()
    rows=list((state.get("matches") or {}).values())
    by_book={}
    for row in rows:
        event_id=str(row.get("xbet_event_id") or "")
        game=captured.get(event_id) or {}
        sc=(game.get("SC") or {}).get("FS") or {}
        try:
            if sc.get("S1") is None or sc.get("S2") is None:
                book_score=None
            else:
                book_score=(int(sc["S1"]),int(sc["S2"]))
        except (ValueError,TypeError,KeyError):
            book_score=None
        try:
            fs_score=(int(row["score_home"]),int(row["score_away"]))
            minute=int(row["minute"])
        except (KeyError,ValueError,TypeError):
            continue
        match_total=fs_score[0]+fs_score[1]+0.5
        second=46<=minute<=80
        first=1<=minute<=35
        period="FIRST_HALF" if first else "FULL_TIME" if second else "OUTSIDE_WINDOW"
        m=row.get("markets") or {}
        choices=m.get("first_half_total") if first else (
            m.get("match_total") if second else [])
        matches=[]
        for price in choices or []:
            try:
                line=float(price.get("line"))
                over=float(price.get("over"))
                under=float(price.get("under"))
                margin=1.0/over+1.0/under-1.0
            except (ValueError,TypeError,ZeroDivisionError):
                continue
            if abs(line-match_total)<1e-6 and 0<=margin<=.14:
                matches.append({"line":line,"over":over,
                                "under":under,"margin":round(margin,4)})
        paired=bool(matches)
        synced=book_score==fs_score
        index_options=indexed_scores.get(event_id) or set()
        index_score=next(iter(index_options)) if len(index_options)==1 else None
        index_synced=index_score==fs_score
        # Index score and price are two independent responses; this is a
        # candidate, not a verified bookmaker executable price.
        indexed_price=bool(
            paired and index_synced and first+second==1
            and 1.50<=matches[0]["over"]<=3.00)
        confirmed_price=bool(
            paired and synced and first+second==1
            and 1.50<=matches[0]["over"]<=3.00)
        by_book[event_id]={
            "id":str(row.get("flashscore_event_id")),
            "match":str(row.get("home"))+" — "+str(row.get("away")),
            "minute":minute,
            "period":period,
            "flashscore_score":list(fs_score),
            "xbet_game_score":list(book_score) if book_score else None,
            "xbet_index_score":list(index_score) if index_score is not None else None,
            "index_score_synced":index_synced,
            "potential_price_with_index_score":indexed_price,
            "score_synced":synced,"paired_target_total":paired,
            "exact_over_1_50":confirmed_price,
            "actual_price":matches[0] if matches else None,
        }
    values=list(by_book.values())
    synced=sum(x["score_synced"] for x in values)
    report={
        "observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "status":"COMPLETE",
        "source":"GOOL2_RobustXBetMarketCollector",
        "source_mode":state.get("index_source"),
        "legacy_root":state.get("root"),
        "index_football_events":state.get("index_events"),
        "index_root_counts":state.get("index_root_counts"),
        "v3_attempts":state.get("index_v3_attempts"),
        "index_cache_used":state.get("index_cache_used"),
        "flashscore_live":state.get("flashscore_live"),
        "name_matched_UNVERIFIED":state.get("mapped_events"),
        "got_live_market_trees":state.get("fetched_live_markets"),
        "game_market_unavailable":state.get("game_market_unavailable"),
        "detail_game_score_synced":synced,
        "index_game_score_synced":sum(
            x["index_score_synced"] for x in values),
        "index_score_unknown":sum(
            x["xbet_index_score"] is None for x in values),
        "index_and_detail_score_both_agree":sum(
            x["score_synced"] and x["index_score_synced"] for x in values),
        "potential_next_goal_1_50_with_index_score":sum(
            x["potential_price_with_index_score"] for x in values),
        "detail_score_unknown":sum(x["xbet_game_score"] is None for x in values),
        "detail_score_mismatch":sum(
            x["xbet_game_score"] is not None and not x["score_synced"]
            for x in values),
        "exact_live_next_goal_1_50_verified":sum(
            x["exact_over_1_50"] for x in values),
        "verified_examples":values[:35],
        "duration_seconds":round(time.monotonic()-started,2),
        "limits":(
            "Name-matched means GOOL2 fuzzy mapping, not confirmed identity. "
            "Verified next goal requires detail score agreement, exact period "
            "and 1.50-3.00 Over+Under with <=14% margin. "
            "No bookmaker executable-order verification.")
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("XBET_GOOL2_FULL_LIVE_REPORT",json.dumps({
        k:v for k,v in report.items()
        if k not in ("verified_examples","v3_attempts","index_root_counts")
    },ensure_ascii=False),flush=True)
    return report


if __name__=="__main__":
    run()
