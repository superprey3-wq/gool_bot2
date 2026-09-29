from __future__ import annotations
import json,sys,time,re
from pathlib import Path
from difflib import SequenceMatcher
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from gool_bot2.sxbet_totals import fetch_sxbet_totals_state
from gool_bot2.xbet_market_pressure import ROOTS, INDEX_QUERIES, _http_json

UA="Mozilla/5.0"

def get_json(url,timeout=10):
    req=Request(url,headers={"User-Agent":UA,"Accept":"application/json,*/*","Referer":"https://www.365scores.com/"})
    try:
        with urlopen(req,timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8","replace"))
    except Exception:
        return {}

def norm(s):
    s=str(s or "").lower()
    s=re.sub(r"\b(fc|fk|cf|sc|afc)\b"," ",s)
    s=re.sub(r"[^a-z0-9а-яё ]+"," ",s)
    return " ".join(s.split())

def sim(a,b): return SequenceMatcher(None,norm(a),norm(b)).ratio()

def live_365_first_half():
    q=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1})
    data=get_json("https://webws.365scores.com/web/games/?"+q)
    games=data.get("games") or []
    out=[]
    for g in games:
        if g.get("statusGroup") != 3: continue
        raw=str(g.get("gameTime") or g.get("gameTimeDisplay") or "")
        m=re.search(r"(\d{1,3})",raw)
        minute=int(m.group(1)) if m else None
        if minute is not None and minute>45: continue
        home=(g.get("homeCompetitor") or {}).get("name") or ""
        away=(g.get("awayCompetitor") or {}).get("name") or ""
        # national-team heuristic: short federation-style names and youth tags
        low=(home+" "+away).lower()
        intl=bool(re.search(r"\b(u17|u18|u19|u20|u21|u23|women|w)\b",low)) or (
            len(home.split())<=3 and len(away.split())<=3 and not any(x in low for x in (" fc","fk ","club","academy","reserves"))
        )
        out.append({"id":g.get("id"),"home":home,"away":away,"minute":minute,"time":raw,
                    "score":[(g.get("homeCompetitor") or {}).get("score"),(g.get("awayCompetitor") or {}).get("score")],
                    "international_like":intl})
    return out

def sx_best_match(game,sx_events):
    best=None;score=0.0
    for e in sx_events:
        s=(sim(game["home"],e.get("home"))+sim(game["away"],e.get("away")))/2
        if s>score:best,score=e,s
    return best,score

def xbet_probe():
    probes=[]
    payload=None
    for root in ROOTS:
        for query in INDEX_QUERIES:
            url=f"{root}/Get1x2_VZip?{query}"
            data=_http_json(url,timeout=8.0)
            probes.append({"url":url,"ok":bool(data),"keys":list(data.keys())[:20] if isinstance(data,dict) else []})
            if data and payload is None: payload=data
    return probes,payload

def compact_xbet(payload):
    if not isinstance(payload,dict): return {"available":False}
    found=[]
    def walk(o):
        if isinstance(o,dict):
            name1=o.get("O1") or o.get("team1") or o.get("HomeName")
            name2=o.get("O2") or o.get("team2") or o.get("AwayName")
            if name1 and name2:
                found.append({"home":str(name1),"away":str(name2),"id":o.get("I") or o.get("id"),"score":o.get("SC"),"minute":o.get("SC")})
            for v in o.values(): walk(v)
        elif isinstance(o,list):
            for v in o[:2000]: walk(v)
    walk(payload)
    uniq=[];seen=set()
    for x in found:
        k=(x["home"],x["away"],str(x["id"]))
        if k in seen: continue
        seen.add(k);uniq.append(x)
    return {"available":True,"matches_found":len(uniq),"examples":uniq[:30]}

def main():
    games=live_365_first_half()
    sx1=fetch_sxbet_totals_state(timeout=6.0)
    time.sleep(25)
    sx2=fetch_sxbet_totals_state(timeout=6.0)

    sx_events=sx2.get("events") or []
    matched=[]
    for g in games:
        e,s=sx_best_match(g,sx_events)
        if e and s>=0.72:
            matched.append({"365":g,"score":round(s,3),"sxbet":e})
    matched.sort(key=lambda x:float((x["sxbet"] or {}).get("liquidity_usdc") or 0),reverse=True)

    probes,payload=xbet_probe()
    result={
      "captured_at":datetime.now(timezone.utc).isoformat(),
      "365_first_half_count":len(games),
      "365_international_like_count":sum(1 for g in games if g["international_like"]),
      "365_examples":[g for g in games if g["international_like"]][:25],
      "sxbet_available":sx2.get("available"),
      "sxbet_markets_seen":sx2.get("markets_seen"),
      "sxbet_orders_seen":sx2.get("orders_seen"),
      "sxbet_events_count":len(sx_events),
      "sxbet_top_events":sx_events[:20],
      "cross_matches":matched[:10],
      "xbet_probes":probes,
      "xbet_compact":compact_xbet(payload)
    }
    print("=== NATIONAL-TEAM LIVE MARKET TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
