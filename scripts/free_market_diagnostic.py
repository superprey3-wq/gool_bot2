from __future__ import annotations
import json,time,re
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def get_json(url,timeout=15):
    req=Request(url,headers={"User-Agent":UA,"Accept":"application/json,text/plain,*/*","Referer":"https://www.matchbook.com/"})
    try:
        with urlopen(req,timeout=timeout) as r:
            b=r.read();return {"ok":True,"data":json.loads(b.decode("utf-8","replace")),"status":r.status}
    except HTTPError as e:return {"ok":False,"status":e.code,"error":str(e)}
    except Exception as e:return {"ok":False,"error":f"{type(e).__name__}:{e}"}

def start_ts(e):
    raw=e.get("start")
    if not raw:return 0
    try:return datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp()
    except:return 0

def events():
    r=get_json("https://api.matchbook.com/edge/rest/events?offset=0&per-page=300&sport-ids=15")
    return ((r.get("data") or {}).get("events") or []) if r.get("ok") else []

def is_match_total(m):
    name=str(m.get("name") or "").strip().lower()
    if name not in {"total","1st half total","first half total"}: return False
    labels=[str(r.get("name") or "").upper() for r in (m.get("runners") or [])]
    return any(x.startswith("OVER ") for x in labels) and any(x.startswith("UNDER ") for x in labels)

def market_has_prices(m):
    return any((r.get("prices") or []) for r in (m.get("runners") or []))

def pick_candidate():
    now=time.time();c=[]
    for e in events():
        st=start_ts(e)
        age=(now-st)/60 if st else 9999
        if not (0 <= age <= 50): continue
        if str(e.get("status") or "").lower() not in {"open","in-running","live"}: continue
        for m in (e.get("markets") or []):
            if not is_match_total(m) or not market_has_prices(m): continue
            score=float(m.get("volume") or 0)
            labels=[str(r.get("name") or "").upper() for r in (m.get("runners") or [])]
            if any("OVER 2.5" in x for x in labels): score+=100000
            elif any("OVER 1.5" in x for x in labels): score+=50000
            c.append((score,float(e.get("volume") or 0),-age,e,m))
    c.sort(reverse=True,key=lambda x:(x[0],x[1],x[2]))
    return c

def ladder(r):
    win=[];lose=[]
    for p in r.get("prices") or []:
        d=p.get("decimal-odds");a=p.get("available-amount");s=p.get("side")
        if isinstance(d,(int,float)) and isinstance(a,(int,float)):
            if s=="win":win.append((float(d),float(a)))
            elif s=="lose":lose.append((float(d),float(a)))
    win.sort(key=lambda x:x[0]);lose.sort(key=lambda x:x[0],reverse=True)
    return win,lose

def rm(r):
    w,l=ladder(r);wd=sum(a for _,a in w[:3]);ld=sum(a for _,a in l[:3])
    return {"name":r.get("name"),"volume":float(r.get("volume") or 0),
            "best_win":w[0][0] if w else None,"best_lose":l[0][0] if l else None,
            "win_depth":round(wd,2),"lose_depth":round(ld,2),
            "imbalance":round((wd-ld)/(wd+ld),5) if wd+ld else None}

def snap(eid,mid):
    es=events();e=next((x for x in es if str(x.get("id"))==str(eid)),None)
    if not e:return {"ok":False,"error":"event disappeared"}
    m=next((x for x in (e.get("markets") or []) if str(x.get("id"))==str(mid)),None)
    if not m:return {"ok":False,"error":"market disappeared"}
    return {"ok":True,"at":datetime.now(timezone.utc).isoformat(),"event":e.get("name"),
            "start":e.get("start"),"status":e.get("status"),"event_volume":float(e.get("volume") or 0),
            "market":m.get("name"),"market_volume":float(m.get("volume") or 0),
            "runners":[rm(r) for r in (m.get("runners") or [])]}

def summary(ss):
    g=[x for x in ss if x.get("ok")]
    if len(g)<2:return {"ok":False}
    f,l=g[0],g[-1];fm={r["name"]:r for r in f["runners"]};lm={r["name"]:r for r in l["runners"]}
    out={"ok":True,"seconds":round((datetime.fromisoformat(l["at"])-datetime.fromisoformat(f["at"])).total_seconds(),1),
         "event":l["event"],"market":l["market"],"event_volume_delta":round(l["event_volume"]-f["event_volume"],2),
         "market_volume_delta":round(l["market_volume"]-f["market_volume"],2),"runners":{}}
    for n,r in lm.items():
        o=fm.get(n,{})
        out["runners"][n]={"volume_delta":round(r["volume"]-float(o.get("volume") or 0),2),
                           "best_win_start":o.get("best_win"),"best_win_end":r.get("best_win"),
                           "imbalance_start":o.get("imbalance"),"imbalance_end":r.get("imbalance")}
    return out

def main():
    c=pick_candidate()
    preview=[{"event":e.get("name"),"start":e.get("start"),"status":e.get("status"),
              "event_volume":e.get("volume"),"market":m.get("name"),"market_volume":m.get("volume")}
             for _,_,_,e,m in c[:10]]
    if not c:
        result={"error":"no first-half Matchbook total with prices","candidates":[]}
    else:
        _,_,_,e,m=c[0];ss=[]
        for i in range(7):
            ss.append(snap(e.get("id"),m.get("id")))
            if i<6:time.sleep(10)
        result={"candidate_count":len(c),"candidate_preview":preview,"selected":preview[0],
                "snapshots":ss,"summary":summary(ss)}
    print("=== MATCHBOOK FIRST-HALF DIRECT TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
