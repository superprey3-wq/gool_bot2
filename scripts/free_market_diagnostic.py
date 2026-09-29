from __future__ import annotations
import json,time,re
from datetime import datetime,timezone
from difflib import SequenceMatcher
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def get_json(url,timeout=15,referer="https://www.google.com/"):
    req=Request(url,headers={"User-Agent":UA,"Accept":"application/json,text/plain,*/*","Referer":referer})
    try:
        with urlopen(req,timeout=timeout) as r:
            b=r.read();return {"ok":True,"status":r.status,"data":json.loads(b.decode("utf-8","replace")),"bytes":len(b)}
    except HTTPError as e:return {"ok":False,"status":e.code,"error":str(e)}
    except Exception as e:return {"ok":False,"status":None,"error":f"{type(e).__name__}:{e}"}

def norm(s):
    s=str(s or "").lower()
    s=s.replace("ž","z").replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
    s=re.sub(r"\b(fc|fk|cf|sc|afc|u23|u21|u20|u19|women|w)\b"," ",s)
    s=re.sub(r"[^a-z0-9а-яё ]+"," ",s)
    return " ".join(s.split())

def sim(a,b): return SequenceMatcher(None,norm(a),norm(b)).ratio()

def first_half_365():
    q=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1})
    r=get_json("https://webws.365scores.com/web/games/?"+q,referer="https://www.365scores.com/")
    if not r.get("ok"):return [],{"error":r}
    games=(r["data"] or {}).get("games") or []
    out=[]
    for g in games:
        sg=g.get("statusGroup")
        gt=str(g.get("gameTime") or g.get("gameTimeDisplay") or g.get("statusText") or "")
        # 365 statusGroup=3 is live; exclude halftime/2H by minute/text when available.
        minute=None
        m=re.search(r"(\d{1,3})",gt)
        if m:
            try:minute=int(m.group(1))
            except:pass
        is_live=(sg==3)
        first_half=is_live and (minute is None or minute<=45) and not any(x in gt.lower() for x in ("half","ht","2nd","second"))
        if first_half:
            out.append({
                "id":g.get("id"),"home":(g.get("homeCompetitor") or {}).get("name"),
                "away":(g.get("awayCompetitor") or {}).get("name"),"time":gt,"minute":minute,
                "score":[(g.get("homeCompetitor") or {}).get("score"),(g.get("awayCompetitor") or {}).get("score")]
            })
    return out,{"games_total":len(games),"first_half_live":len(out)}

def mb_events():
    r=get_json("https://api.matchbook.com/edge/rest/events?offset=0&per-page=200&sport-ids=15",referer="https://www.matchbook.com/")
    return ((r.get("data") or {}).get("events") or []) if r.get("ok") else []

def split_mb_name(name):
    parts=re.split(r"\s+vs\s+|\s+v\s+|\s+-\s+",str(name),maxsplit=1,flags=re.I)
    return (parts+[None,None])[:2]

def find_match(live,events):
    best=None;score=0
    for e in events:
        mh,ma=split_mb_name(e.get("name"))
        if not mh or not ma:continue
        s=(sim(live["home"],mh)+sim(live["away"],ma))/2
        if s>score:best,score=e,s
    return best,score

def total_market(e):
    cand=[]
    for m in e.get("markets") or []:
        name=str(m.get("name") or "")
        runners=m.get("runners") or []
        labels=[str(x.get("name") or "").upper() for x in runners]
        if "TOTAL" not in name.upper():continue
        if not any("OVER" in x for x in labels):continue
        score=float(m.get("volume") or 0)
        if any("OVER 2.5" in x for x in labels):score+=100000
        elif any("OVER 1.5" in x for x in labels):score+=50000
        cand.append((score,m))
    cand.sort(key=lambda x:x[0],reverse=True)
    return cand[0][1] if cand else None

def ladder(r):
    win=[];lose=[]
    for p in r.get("prices") or []:
        d=p.get("decimal-odds");a=p.get("available-amount");s=p.get("side")
        if isinstance(d,(int,float)) and isinstance(a,(int,float)):
            (win if s=="win" else lose if s=="lose" else []).append((float(d),float(a)))
    win.sort(key=lambda x:x[0]);lose.sort(key=lambda x:x[0],reverse=True)
    return win,lose

def metrics(r):
    w,l=ladder(r);wd=sum(a for _,a in w[:3]);ld=sum(a for _,a in l[:3])
    best=w[0][0] if w else None
    return {
      "name":r.get("name"),"volume":float(r.get("volume") or 0),
      "price":best,"proxy_prob":round(1/best,6) if best else None,
      "win_depth":round(wd,2),"lose_depth":round(ld,2),
      "imbalance":round((wd-ld)/(wd+ld),6) if wd+ld else None
    }

def snapshot(event_id,market_id):
    events=mb_events();e=next((x for x in events if str(x.get("id"))==str(event_id)),None)
    if not e:return {"ok":False,"error":"event disappeared"}
    m=next((x for x in e.get("markets") or [] if str(x.get("id"))==str(market_id)),None)
    if not m:return {"ok":False,"error":"market disappeared","event":e.get("name")}
    return {
      "ok":True,"at":datetime.now(timezone.utc).isoformat(),"event":e.get("name"),"status":e.get("status"),
      "event_volume":float(e.get("volume") or 0),"market":m.get("name"),"market_volume":float(m.get("volume") or 0),
      "runners":[metrics(r) for r in m.get("runners") or []]
    }

def summarize(snaps):
    g=[s for s in snaps if s.get("ok")]
    if len(g)<2:return {"ok":False}
    f,l=g[0],g[-1];fm={x["name"]:x for x in f["runners"]};lm={x["name"]:x for x in l["runners"]}
    out={"ok":True,"seconds":round((datetime.fromisoformat(l["at"])-datetime.fromisoformat(f["at"])).total_seconds(),1),
         "event":l["event"],"market":l["market"],"event_volume_delta":round(l["event_volume"]-f["event_volume"],2),
         "market_volume_delta":round(l["market_volume"]-f["market_volume"],2),"runners":{}}
    for n,x in lm.items():
        o=fm.get(n,{})
        out["runners"][n]={
          "volume_delta":round(x["volume"]-float(o.get("volume") or 0),2),
          "price_start":o.get("price"),"price_end":x.get("price"),
          "prob_delta_pp":round((x["proxy_prob"]-o["proxy_prob"])*100,3) if x.get("proxy_prob") and o.get("proxy_prob") else None,
          "imbalance_start":o.get("imbalance"),"imbalance_end":x.get("imbalance")
        }
    return out

def main():
    live,meta=first_half_365();events=mb_events()
    matches=[]
    for g in live:
        e,s=find_match(g,events)
        if e and s>=0.62:
            m=total_market(e)
            if m:
                matches.append((s,float(m.get("volume") or 0),g,e,m))
    matches.sort(key=lambda x:(x[1],x[0]),reverse=True)
    if not matches:
        result={"error":"no matched 1H live game with total","365":meta,"live_examples":live[:20],"mb_events":len(events)}
    else:
        s,vol,g,e,m=matches[0]
        snaps=[]
        for i in range(9):
            snaps.append(snapshot(e.get("id"),m.get("id")))
            if i<8:time.sleep(15)
        result={"selected_365":g,"match_score":round(s,3),"matchbook_event":e.get("name"),"market":m.get("name"),
                "candidate_count":len(matches),"snapshots":snaps,"summary":summarize(snaps)}
    print("=== FIRST HALF LIVE FLOW TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
