from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from gool_bot2.flashscore_odds import fetch_event_odds
from gool_bot2.providers.common import pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider

TARGETS = [
    ("Azerbaijan", "Liechtenstein"),
    ("Germany", "Serbia"),
    ("Greece", "Netherlands"),
    ("Denmark", "Portugal"),
    ("Wales", "Norway"),
    ("Israel", "Kosovo"),
    ("Republic of Ireland", "Austria"),
    ("Malta", "Gibraltar"),
]


def item_view(item):
    h = item.get("handicap") or {}
    return {
        "selection": item.get("selection"),
        "value": item.get("value"),
        "handicap": h.get("value"),
        "bothTeamsToScore": item.get("bothTeamsToScore"),
        "homeAway": item.get("homeAway"),
        "winner": item.get("winner"),
        "name": item.get("name"),
    }


def main():
    fs=FlashscoreProvider()
    msk=timezone(timedelta(hours=3))
    day=datetime.now(msk).date()
    now=datetime.now(timezone.utc).timestamp()
    fixtures=[
        m for m in fs.parse_master_scheduled(fs._feed("f_1_0_3_en_1",timeout=12,max_hosts=1))
        if (m.meta or {}).get("scheduled_start_ts")
        and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]),msk).date()==day
        and float(m.meta["scheduled_start_ts"])>now
    ]
    found=[]
    for h,a in TARGETS:
        best=None; score=0
        for m in fixtures:
            s=pair_score(m.home,m.away,h,a)
            if s>score: best,score=m,s
        if best is not None and score>=.72:
            found.append(best)

    report={"date_msk":day.isoformat(),"matches":[]}
    global_types=Counter()
    for m in found:
        data=fetch_event_odds(m.provider_match_id)
        names={}
        for b in ((data.get("settings") or {}).get("bookmakers") or []):
            z=b.get("bookmaker") or {}
            names[z.get("id")]=z.get("name")
        rows=[]
        for row in data.get("odds") or []:
            scope=str(row.get("bettingScope") or "")
            kind=str(row.get("bettingType") or "")
            global_types[(scope,kind)]+=1
            samples=[item_view(x) for x in (row.get("odds") or [])[:12]]
            rows.append({
                "scope":scope,
                "type":kind,
                "bookmaker":names.get(row.get("bookmakerId"),row.get("bookmakerId")),
                "items":samples,
            })
        uniq=sorted({(r["scope"],r["type"]) for r in rows})
        print(f"MARKET_CATALOG {m.home} - {m.away} types={len(uniq)} rows={len(rows)}",flush=True)
        for scope,kind in uniq:
            print(f"  {scope} | {kind}",flush=True)
        report["matches"].append({
            "event_id":m.provider_match_id,"home":m.home,"away":m.away,"league":m.league,
            "market_types":[{"scope":a,"type":b} for a,b in uniq],
            "rows":rows,
        })
    report["global_market_types"]=[
        {"scope":k[0],"type":k[1],"rows":v}
        for k,v in sorted(global_types.items())
    ]
    Path("uefa_nations_market_catalog.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("GLOBAL_MARKET_TYPES",json.dumps(report["global_market_types"],ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
