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
from datetime import datetime, timezone
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


OUT=pathlib.Path("reports/progruz1-xbet-source-latest.json")


def run():
    started=time.monotonic()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    collector=RobustXBetMarketCollector(
        pathlib.Path("reports/progruz1-xbet-rawstate.json"),
        pathlib.Path("reports/progruz1-xbet-temp-history.jsonl"))
    captured={}
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
