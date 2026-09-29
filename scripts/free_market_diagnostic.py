from __future__ import annotations

import json, sys, time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def get_json(url, timeout=12):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*", "Referer": "https://www.sofascore.com/"})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return {"ok": True, "status": r.status, "data": json.loads(raw.decode("utf-8", "replace")), "bytes": len(raw)}
    except HTTPError as e:
        return {"ok": False, "status": e.code, "error": str(e)}
    except Exception as e:
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}"}

def sim(a,b):
    return SequenceMatcher(None, str(a).casefold(), str(b).casefold()).ratio()

def sofa_pick():
    date = datetime.now(timezone.utc).date().isoformat()
    r = get_json(f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{date}")
    if not r["ok"]:
        return {"source":"sofascore","discovery":r}
    events = (r["data"] or {}).get("events") or []
    now = time.time()
    def ts(e): return float(e.get("startTimestamp") or 0)
    live = [e for e in events if str((e.get("status") or {}).get("type") or "").lower() in {"inprogress","halftime"}]
    if live:
        e = sorted(live, key=ts)[0]
        mode = "live"
    else:
        future = [e for e in events if ts(e) >= now - 300]
        e = sorted(future or events, key=lambda x: abs(ts(x)-now))[0] if events else None
        mode = "prematch"
    if not e:
        return {"source":"sofascore","discovery":r,"error":"no events"}
    eid = e.get("id")
    home = ((e.get("homeTeam") or {}).get("name") or "?")
    away = ((e.get("awayTeam") or {}).get("name") or "?")
    return {
        "source":"sofascore","mode":mode,"event_id":eid,"home":home,"away":away,
        "start_ts":e.get("startTimestamp"),"status":e.get("status"),"score":e.get("homeScore"),
        "discovery":{"ok":True,"status":r["status"],"events":len(events),"bytes":r["bytes"]},
        "detail": get_json(f"https://www.sofascore.com/api/v1/event/{eid}"),
        "stats": get_json(f"https://www.sofascore.com/api/v1/event/{eid}/statistics"),
        "odds_all": get_json(f"https://www.sofascore.com/api/v1/event/{eid}/odds/1/all"),
    }

def fotmob_probe(home, away):
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    r = get_json(f"https://www.fotmob.com/api/matches?date={date}")
    out={"source":"fotmob","discovery":{"ok":r.get("ok"),"status":r.get("status"),"bytes":r.get("bytes")}}
    if not r.get("ok"): out["error"]=r.get("error"); return out
    leagues=(r["data"] or {}).get("leagues") or []
    matches=[]
    for lg in leagues:
        for m in lg.get("matches") or []:
            m["_league_name"]=lg.get("name")
            matches.append(m)
    best=None;score=0
    for m in matches:
        h=((m.get("home") or {}).get("name") or m.get("homeName") or "")
        a=((m.get("away") or {}).get("name") or m.get("awayName") or "")
        s=(sim(home,h)+sim(away,a))/2
        if s>score: best,score=m,s
    out["matches"]=len(matches);out["match_score"]=round(score,3)
    if best and score>=0.55:
        mid=best.get("id") or best.get("matchId")
        out.update({"event_id":mid,"home":((best.get("home") or {}).get("name") or best.get("homeName")),"away":((best.get("away") or {}).get("name") or best.get("awayName")),"league":best.get("_league_name")})
        out["detail"]=get_json(f"https://www.fotmob.com/api/matchDetails?matchId={mid}")
    return out

def scores365_probe(home, away):
    q=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1})
    r=get_json("https://webws.365scores.com/web/games/?"+q)
    out={"source":"365scores","discovery":{"ok":r.get("ok"),"status":r.get("status"),"bytes":r.get("bytes")}}
    if not r.get("ok"): out["error"]=r.get("error"); return out
    games=(r["data"] or {}).get("games") or []
    best=None;score=0
    for g in games:
        h=((g.get("homeCompetitor") or {}).get("name") or "")
        a=((g.get("awayCompetitor") or {}).get("name") or "")
        s=(sim(home,h)+sim(away,a))/2
        if s>score: best,score=g,s
    out["games"]=len(games);out["match_score"]=round(score,3)
    if best and score>=0.55:
        gid=best.get("id")
        out.update({"event_id":gid,"home":((best.get("homeCompetitor") or {}).get("name")),"away":((best.get("awayCompetitor") or {}).get("name"))})
        q2=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","gameId":gid,"topBookmaker":14})
        out["detail"]=get_json("https://webws.365scores.com/web/game/?"+q2)
    return out

def matchbook_probe():
    urls=[
      "https://api.matchbook.com/edge/rest/events?offset=0&per-page=5&sport-ids=15",
      "https://api.matchbook.com/edge/rest/events?offset=0&per-page=5"
    ]
    return {"source":"matchbook","probes":[{"url":u, **{k:v for k,v in get_json(u).items() if k!="data"}} for u in urls]}

def summarize_payload(x):
    if isinstance(x, dict):
        return {k:summarize_payload(v) for k,v in x.items() if k not in {"data"}}
    if isinstance(x, list):
        return [summarize_payload(v) for v in x[:10]]
    return x

def main():
    sofa=sofa_pick()
    result={"captured_at":datetime.now(timezone.utc).isoformat(),"sofascore":sofa}
    if sofa.get("event_id"):
        result["fotmob"]=fotmob_probe(sofa["home"],sofa["away"])
        result["scores365"]=scores365_probe(sofa["home"],sofa["away"])
    result["matchbook"]=matchbook_probe()

    print("=== FREE SOURCE DIAGNOSTIC ===")
    print(json.dumps(summarize_payload(result), ensure_ascii=False, indent=2))
    with open("diagnostic_result.json","w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)

if __name__=="__main__":
    main()
