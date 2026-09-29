from __future__ import annotations

import json, time, math
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def get_json(url, timeout=15, referer="https://www.matchbook.com/"):
    req=Request(url,headers={"User-Agent":UA,"Accept":"application/json,text/plain,*/*","Referer":referer})
    try:
        with urlopen(req,timeout=timeout) as r:
            raw=r.read()
            return {"ok":True,"status":r.status,"data":json.loads(raw.decode("utf-8","replace")),"bytes":len(raw)}
    except HTTPError as e:
        return {"ok":False,"status":e.code,"error":str(e)}
    except Exception as e:
        return {"ok":False,"status":None,"error":f"{type(e).__name__}:{e}"}

def event_start_ts(e):
    raw=e.get("start") or e.get("start-time") or e.get("start_time")
    if isinstance(raw,(int,float)): return float(raw)/1000 if raw>1e11 else float(raw)
    if raw:
        try:return datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp()
        except:return 0.0
    return 0.0

def normalize_name(s):
    return " ".join(str(s or "").lower().replace("fc ","").replace("fk ","").replace("cf ","").split())

def select_matchbook_event():
    r=get_json("https://api.matchbook.com/edge/rest/events?offset=0&per-page=30&sport-ids=15")
    if not r.get("ok"): return None,r
    events=(r["data"] or {}).get("events") or []
    now=time.time()
    ranked=[]
    for e in events:
        markets=e.get("markets") or []
        total_markets=[m for m in markets if "total" in str(m.get("name") or "").lower()]
        if not total_markets: continue
        t=event_start_ts(e)
        status=str(e.get("status") or "")
        inrun=bool(e.get("in-running") or e.get("in_running") or "in-running" in status.lower())
        future=(t>=now-300 and t<=now+6*3600)
        if inrun or future:
            ranked.append((0 if inrun else 1, abs(t-now), e))
    if not ranked:
        return None,r
    ranked.sort(key=lambda x:(x[0],x[1]))
    return ranked[0][2],r

def pick_total_market(event):
    candidates=[]
    for m in event.get("markets") or []:
        name=str(m.get("name") or "")
        if "total" not in name.lower(): continue
        runners=m.get("runners") or []
        names=[str(x.get("name") or "").upper() for x in runners]
        score=0
        if any("OVER 2.5" in n for n in names): score+=10
        if any("UNDER 2.5" in n for n in names): score+=10
        if "MATCH" not in name.upper(): score+=1
        candidates.append((score,m))
    if not candidates:return None
    candidates.sort(key=lambda x:x[0],reverse=True)
    return candidates[0][1]

def level_side_amount(runner, side):
    vals=[]
    for p in runner.get("prices") or []:
        if str(p.get("side") or "")!=side: continue
        dec=p.get("decimal-odds")
        amt=p.get("available-amount")
        if isinstance(dec,(int,float)) and isinstance(amt,(int,float)):
            vals.append((float(dec),float(amt)))
    if not vals:return None,None,0.0
    vals.sort(key=lambda x:x[0], reverse=(side=="lose"))
    best=vals[0]
    return best[0],best[1],sum(a for _,a in vals[:3])

def runner_metrics(r):
    win_odds,win_amt,win_depth=level_side_amount(r,"win")
    lose_odds,lose_amt,lose_depth=level_side_amount(r,"lose")
    mid=None
    if win_odds and lose_odds:
        mid=(win_odds+lose_odds)/2
    elif win_odds: mid=win_odds
    elif lose_odds: mid=lose_odds
    implied=(1/mid) if mid and mid>0 else None
    imb=None
    if win_depth+lose_depth>0:
        imb=(win_depth-lose_depth)/(win_depth+lose_depth)
    return {
        "name":r.get("name"),"runner_volume":r.get("volume"),
        "best_win_odds":win_odds,"best_win_amount":win_amt,"win_depth_top3":round(win_depth,4),
        "best_lose_odds":lose_odds,"best_lose_amount":lose_amt,"lose_depth_top3":round(lose_depth,4),
        "mid_odds":round(mid,5) if mid else None,
        "implied_prob":round(implied,6) if implied else None,
        "depth_imbalance":round(imb,6) if imb is not None else None,
    }

def snapshot():
    event,_=select_matchbook_event()
    if not event:return {"ok":False,"error":"no suitable event"}
    market=pick_total_market(event)
    if not market:return {"ok":False,"error":"no total market","event":event.get("name")}
    return {
        "ok":True,
        "captured_at":datetime.now(timezone.utc).isoformat(),
        "event_id":event.get("id"),"event_name":event.get("name"),"event_start":event.get("start"),"event_status":event.get("status"),
        "market_id":market.get("id"),"market_name":market.get("name"),"market_volume":float(market.get("volume") or 0),
        "event_volume":float(event.get("volume") or 0),
        "runners":[runner_metrics(r) for r in market.get("runners") or []],
    }

def series(n=6,interval=10):
    out=[]
    for i in range(n):
        out.append(snapshot())
        if i<n-1: time.sleep(interval)
    return out

def derive(series):
    good=[s for s in series if s.get("ok")]
    if len(good)<2:return {"ok":False,"error":"not enough snapshots"}
    first,last=good[0],good[-1]
    result={
        "ok":True,"event_name":last.get("event_name"),"market_name":last.get("market_name"),
        "seconds":round((datetime.fromisoformat(last["captured_at"])-datetime.fromisoformat(first["captured_at"])).total_seconds(),1),
        "market_volume_delta":round(float(last.get("market_volume") or 0)-float(first.get("market_volume") or 0),4),
        "event_volume_delta":round(float(last.get("event_volume") or 0)-float(first.get("event_volume") or 0),4),
        "runners":{}
    }
    f={r["name"]:r for r in first.get("runners") or []}
    l={r["name"]:r for r in last.get("runners") or []}
    for name,row in l.items():
        old=f.get(name,{})
        p1=old.get("implied_prob");p2=row.get("implied_prob")
        o1=old.get("mid_odds");o2=row.get("mid_odds")
        result["runners"][name]={
            "odds_start":o1,"odds_end":o2,
            "odds_delta":round(o2-o1,5) if isinstance(o1,(int,float)) and isinstance(o2,(int,float)) else None,
            "prob_start":p1,"prob_end":p2,
            "prob_delta_pp":round((p2-p1)*100,4) if isinstance(p1,(int,float)) and isinstance(p2,(int,float)) else None,
            "imbalance_start":old.get("depth_imbalance"),"imbalance_end":row.get("depth_imbalance"),
            "runner_volume_delta":round(float(row.get("runner_volume") or 0)-float(old.get("runner_volume") or 0),4),
        }
    return result

def main():
    snaps=series(n=6,interval=10)
    result={"captured_at":datetime.now(timezone.utc).isoformat(),"snapshots":snaps,"derived":derive(snaps)}
    print("=== MATCHBOOK TOTAL FLOW TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    with open("diagnostic_result.json","w",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)

if __name__=="__main__":main()
