from __future__ import annotations

import json, time, math, re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

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

def norm(s):
    s=str(s or "").lower()
    s=re.sub(r"\b(fc|fk|cf|u23|u21|u20|women|w)\b"," ",s)
    return " ".join(s.split())

def sim(a,b):
    return SequenceMatcher(None,norm(a),norm(b)).ratio()

def event_start_ts(e):
    raw=e.get("start") or e.get("start-time") or e.get("start_time")
    if isinstance(raw,(int,float)): return float(raw)/1000 if raw>1e11 else float(raw)
    if raw:
        try:return datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp()
        except:return 0.0
    return 0.0

def is_inrunning(e):
    status=str(e.get("status") or "").lower()
    return bool(e.get("in-running") or e.get("in_running") or "in-running" in status or status=="live")

def pick_live_event():
    r=get_json("https://api.matchbook.com/edge/rest/events?offset=0&per-page=60&sport-ids=15")
    if not r.get("ok"): return None,r
    events=(r["data"] or {}).get("events") or []
    live=[]
    for e in events:
        if not is_inrunning(e): continue
        markets=e.get("markets") or []
        totals=[]
        for m in markets:
            name=str(m.get("name") or "").lower()
            if "total" in name and any(("over" in str(x.get("name") or "").lower()) for x in (m.get("runners") or [])):
                totals.append(m)
        if totals:
            vol=float(e.get("volume") or 0)
            live.append((vol,e))
    if not live:return None,r
    live.sort(key=lambda x:x[0],reverse=True)
    return live[0][1],r

def pick_total_market(event):
    c=[]
    for m in event.get("markets") or []:
        name=str(m.get("name") or "")
        if "total" not in name.lower(): continue
        runners=m.get("runners") or []
        labels=[str(r.get("name") or "").upper() for r in runners]
        if not any("OVER" in x for x in labels): continue
        score=float(m.get("volume") or 0)
        if any("OVER 2.5" in x for x in labels): score+=100000
        elif any("OVER 1.5" in x for x in labels): score+=50000
        c.append((score,m))
    if not c:return None
    c.sort(key=lambda x:x[0],reverse=True)
    return c[0][1]

def parse_price_ladder(runner):
    win=[];lose=[]
    for p in runner.get("prices") or []:
        dec=p.get("decimal-odds")
        amt=p.get("available-amount")
        side=str(p.get("side") or "")
        if not isinstance(dec,(int,float)) or not isinstance(amt,(int,float)): continue
        row=(float(dec),float(amt))
        (win if side=="win" else lose if side=="lose" else []).append(row)
    # For Matchbook's runner prices treat "win" and "lose" as opposite sides.
    win.sort(key=lambda x:x[0])
    lose.sort(key=lambda x:x[0],reverse=True)
    return win,lose

def runner_metrics(r):
    win,lose=parse_price_ladder(r)
    best_win=win[0] if win else (None,None)
    best_lose=lose[0] if lose else (None,None)
    win_depth=sum(a for _,a in win[:3])
    lose_depth=sum(a for _,a in lose[:3])
    # Use best executable win price as directional price proxy; do not call it fair probability.
    price_proxy=best_win[0]
    proxy_prob=(1/price_proxy) if price_proxy and price_proxy>0 else None
    imb=(win_depth-lose_depth)/(win_depth+lose_depth) if (win_depth+lose_depth)>0 else None
    return {
      "name":r.get("name"),
      "runner_volume":float(r.get("volume") or 0),
      "best_win_odds":best_win[0],"best_win_amount":best_win[1],
      "best_lose_odds":best_lose[0],"best_lose_amount":best_lose[1],
      "win_depth_top3":round(win_depth,4),"lose_depth_top3":round(lose_depth,4),
      "price_proxy":price_proxy,"proxy_prob":round(proxy_prob,6) if proxy_prob else None,
      "depth_imbalance":round(imb,6) if imb is not None else None
    }

def mb_snapshot(event_id=None, market_id=None):
    r=get_json("https://api.matchbook.com/edge/rest/events?offset=0&per-page=60&sport-ids=15")
    if not r.get("ok"):return {"ok":False,"error":r.get("error"),"status":r.get("status")}
    events=(r["data"] or {}).get("events") or []
    event=None
    if event_id is not None:
        event=next((e for e in events if str(e.get("id"))==str(event_id)),None)
    if event is None:
        event,_=pick_live_event()
    if event is None:return {"ok":False,"error":"no live event"}
    market=None
    if market_id is not None:
        market=next((m for m in event.get("markets") or [] if str(m.get("id"))==str(market_id)),None)
    if market is None:market=pick_total_market(event)
    if market is None:return {"ok":False,"error":"no total market","event_name":event.get("name")}
    return {
      "ok":True,"captured_at":datetime.now(timezone.utc).isoformat(),
      "event_id":event.get("id"),"event_name":event.get("name"),"event_volume":float(event.get("volume") or 0),
      "event_status":event.get("status"),"event_start":event.get("start"),
      "market_id":market.get("id"),"market_name":market.get("name"),"market_volume":float(market.get("volume") or 0),
      "runners":[runner_metrics(r) for r in market.get("runners") or []],
    }

def probe_365(match_name):
    q=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1})
    r=get_json("https://webws.365scores.com/web/games/?"+q,referer="https://www.365scores.com/")
    out={"ok":r.get("ok"),"status":r.get("status")}
    if not r.get("ok"):out["error"]=r.get("error");return out
    games=(r["data"] or {}).get("games") or []
    parts=[x.strip() for x in re.split(r"\s+vs\s+|\s+-\s+",str(match_name),maxsplit=1)]
    h=parts[0] if parts else "";a=parts[1] if len(parts)>1 else ""
    best=None;score=0
    for g in games:
        gh=(g.get("homeCompetitor") or {}).get("name") or ""
        ga=(g.get("awayCompetitor") or {}).get("name") or ""
        s=(sim(h,gh)+sim(a,ga))/2 if a else sim(h,gh)
        if s>score:best,score=g,s
    out["match_score"]=round(score,3)
    if best:
        out["home"]=(best.get("homeCompetitor") or {}).get("name")
        out["away"]=(best.get("awayCompetitor") or {}).get("name")
        out["game_id"]=best.get("id")
        out["status_group"]=best.get("statusGroup")
        out["game_time"]=best.get("gameTime") or best.get("gameTimeDisplay")
        out["score"]=[(best.get("homeCompetitor") or {}).get("score"),(best.get("awayCompetitor") or {}).get("score")]
    return out

def summarize(snaps):
    good=[s for s in snaps if s.get("ok")]
    if len(good)<2:return {"ok":False,"error":"not enough snapshots"}
    f,l=good[0],good[-1]
    out={
      "ok":True,"event_name":l["event_name"],"market_name":l["market_name"],
      "seconds":round((datetime.fromisoformat(l["captured_at"])-datetime.fromisoformat(f["captured_at"])).total_seconds(),1),
      "event_volume_delta":round(l["event_volume"]-f["event_volume"],4),
      "market_volume_delta":round(l["market_volume"]-f["market_volume"],4),
      "runners":{}
    }
    fm={r["name"]:r for r in f["runners"]};lm={r["name"]:r for r in l["runners"]}
    for n,row in lm.items():
        old=fm.get(n,{})
        p1=old.get("price_proxy");p2=row.get("price_proxy")
        q1=old.get("proxy_prob");q2=row.get("proxy_prob")
        out["runners"][n]={
          "price_start":p1,"price_end":p2,
          "price_delta":round(p2-p1,5) if isinstance(p1,(int,float)) and isinstance(p2,(int,float)) else None,
          "proxy_prob_delta_pp":round((q2-q1)*100,4) if isinstance(q1,(int,float)) and isinstance(q2,(int,float)) else None,
          "volume_delta":round(float(row.get("runner_volume") or 0)-float(old.get("runner_volume") or 0),4),
          "imbalance_start":old.get("depth_imbalance"),"imbalance_end":row.get("depth_imbalance"),
        }
    return out

def classify(summary):
    if not summary.get("ok"):return {"label":"NO_DATA"}
    runners=summary.get("runners") or {}
    over=next((v for k,v in runners.items() if str(k).upper().startswith("OVER")),None)
    under=next((v for k,v in runners.items() if str(k).upper().startswith("UNDER")),None)
    score=0;reasons=[]
    if over:
        vd=over.get("volume_delta") or 0
        pd=over.get("proxy_prob_delta_pp") or 0
        imb=over.get("imbalance_end")
        if vd>50: score+=2;reasons.append(f"over_volume+{vd:.1f}")
        elif vd>10: score+=1;reasons.append(f"over_volume+{vd:.1f}")
        if pd>1:score+=2;reasons.append(f"over_prob+{pd:.2f}pp")
        elif pd>0.25:score+=1;reasons.append(f"over_prob+{pd:.2f}pp")
        if isinstance(imb,(int,float)) and imb>0.25:score+=1;reasons.append(f"over_imbalance={imb:.2f}")
    label="STRONG_OVER_FLOW" if score>=4 else "OVER_FLOW" if score>=2 else "NEUTRAL"
    return {"label":label,"score":score,"reasons":reasons}

def main():
    first=mb_snapshot()
    if not first.get("ok"):
        result={"error":first}
    else:
        eid=first["event_id"];mid=first["market_id"]
        snaps=[first]
        # ~2 minutes, 9 total points
        for _ in range(8):
            time.sleep(15)
            snaps.append(mb_snapshot(eid,mid))
        summ=summarize(snaps)
        result={"captured_at":datetime.now(timezone.utc).isoformat(),"snapshots":snaps,"summary":summ,"flow":classify(summ),"scores365":probe_365(first["event_name"])}
    print("=== LIVE MATCHBOOK FLOW TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    with open("diagnostic_result.json","w",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)

if __name__=="__main__":main()
