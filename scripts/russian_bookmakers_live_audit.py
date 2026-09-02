from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import live_bookmaker_variants_audit as legacy

SOURCES = {
    "1xbet": [
        ("https://1xbetbd.com/LineFeed/Get1x2_VZip", {"sports":1,"count":500,"lng":"en","tf":2200000,"tz":3,"mode":4,"country":19,"getEmpty":"true","gr":925}),
        ("https://1xbet.com/LineFeed/Get1x2_VZip", {"sports":1,"count":500,"lng":"en","tf":2200000,"tz":3,"mode":4,"country":19,"getEmpty":"true","gr":925}),
    ],
    "fonbet": [
        ("https://line-lb51.bk6bba-resources.com/events/listBase", {"scopeMarket":1600,"lang":"ru"}),
    ],
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"

def fetch(url: str, params: dict[str, Any]) -> tuple[str, Any, str | None]:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent":UA,"Accept":"application/json,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw=r.read()
        return "ok", json.loads(raw), None
    except Exception as e:
        s=f"{type(e).__name__}: {e}"
        low=s.lower()
        return ("blocked" if any(x in low for x in ("403","451","forbidden","geo","access denied")) else "error"), None, s

def source_snapshot(name: str, fs: list[dict[str, Any]]) -> dict[str, Any]:
    attempts=[]; payload=None; state="error"
    for url,params in SOURCES[name]:
        st,data,err=fetch(url,params); attempts.append({"url":url,"state":st,"error":err})
        if st=="ok": payload=data; state="ok"; break
        state=st
    candidates=legacy.extract_candidates(payload) if payload is not None else []
    rows={}
    for m in fs:
        cand,score,rev=legacy.best_candidate(m,candidates)
        if cand is None:
            rows[m["event_id"]]={"status":"not_found"}; continue
        quotes=sorted(set(cand.get("quote_tokens") or []))
        rows[m["event_id"]]={"status":"matched" if quotes else "matched_no_quotes","book_home":cand.get("home"),"book_away":cand.get("away"),"mapping_score":round(score,4),"reversed":rev,"quote_tokens":quotes,"fingerprint":"|".join(quotes)}
    return {"state":state,"attempts":attempts,"candidate_count":len(candidates),"matches":rows}

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output-dir",type=Path,default=Path("russian_bookmakers_audit")); ap.add_argument("--snapshots",type=int,default=3); ap.add_argument("--interval",type=int,default=35); a=ap.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    snaps=[]
    for i in range(1,a.snapshots+1):
        fs=legacy.flashscore_rows(); sources={k:source_snapshot(k,fs) for k in SOURCES}; snap={"snapshot":i,"flashscore":fs,"sources":sources}; snaps.append(snap); (a.output_dir/f"snapshot_{i}.json").write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding="utf-8")
        print("RU_BOOK_AUDIT",i,"flashscore",len(fs),{k:(v["state"],v["candidate_count"]) for k,v in sources.items()},flush=True)
        if i<a.snapshots: time.sleep(a.interval)
    initial={m["event_id"]:m for m in snaps[0]["flashscore"]}; summary={"snapshot_count":len(snaps),"initial_flashscore_live_matches":len(initial),"sources":{}}
    for src in SOURCES:
        verdicts={}; proven=[]
        for eid,m0 in initial.items():
            fsstates=[]; rows=[]
            for s in snaps:
                fm=next((x for x in s["flashscore"] if x.get("event_id")==eid),None)
                if fm: fsstates.append((fm.get("minute"),fm.get("home_score"),fm.get("away_score")))
                rows.append((s["sources"][src].get("matches") or {}).get(eid,{"status":"not_found"}))
            quoted=[r for r in rows if r.get("status")=="matched" and r.get("fingerprint")]
            progress=len(set(fsstates))>1; changed=len({r["fingerprint"] for r in quoted})>1
            states=[s["sources"][src]["state"] for s in snaps]
            if len(quoted)>=2 and progress and changed: v="PROVEN"; proven.append({"event_id":eid,"home":m0.get("home"),"away":m0.get("away")})
            elif len(quoted)>=2 and changed: v="ODDS_CHANGED_NO_FS_PROGRESS"
            elif quoted: v="FOUND_NO_CHANGE"
            elif any(r.get("status")=="matched_no_quotes" for r in rows): v="MATCHED_NO_QUOTES"
            elif all(x=="blocked" for x in states): v="BLOCKED_GEO"
            elif all(x=="error" for x in states): v="SOURCE_ERROR"
            else: v="NOT_FOUND"
            verdicts[v]=verdicts.get(v,0)+1
        summary["sources"][src]={"verdicts":verdicts,"proven":proven,"states":[s["sources"][src]["state"] for s in snaps],"attempts":snaps[-1]["sources"][src]["attempts"]}
    (a.output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print("RU_BOOK_FINAL",json.dumps(summary,ensure_ascii=False),flush=True); return 0
if __name__=="__main__": raise SystemExit(main())
