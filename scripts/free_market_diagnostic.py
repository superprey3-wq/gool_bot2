from __future__ import annotations
import json,time
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def get_json(url,timeout=15):
    req=Request(url,headers={"User-Agent":UA,"Accept":"application/json,text/plain,*/*","Referer":"https://www.matchbook.com/"})
    try:
        with urlopen(req,timeout=timeout) as r:
            b=r.read()
            return {"ok":True,"status":r.status,"data":json.loads(b.decode("utf-8","replace")),"bytes":len(b)}
    except HTTPError as e:
        return {"ok":False,"status":e.code,"error":str(e)}
    except Exception as e:
        return {"ok":False,"status":None,"error":f"{type(e).__name__}:{e}"}

def start_ts(e):
    raw=e.get("start")
    if not raw:return 0.0
    try:return datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp()
    except:return 0.0

def list_events():
    q=urlencode({"offset":0,"per-page":200,"sport-ids":15})
    r=get_json("https://api.matchbook.com/edge/rest/events?"+q)
    return ((r.get("data") or {}).get("events") or []),r

def get_markets(event_id):
    q=urlencode({
        "offset":0,"per-page":100,"states":"open,suspended",
        "include-prices":"true","price-depth":5,"price-mode":"expanded",
        "exchange-type":"back-lay","odds-type":"DECIMAL","minimum-liquidity":0
    })
    r=get_json(f"https://api.matchbook.com/edge/rest/events/{event_id}/markets?{q}")
    data=r.get("data") or {}
    return data.get("markets") or [],r

def get_market(event_id,market_id):
    q=urlencode({
        "include-prices":"true","price-depth":5,"price-mode":"expanded",
        "exchange-type":"back-lay","odds-type":"DECIMAL","minimum-liquidity":0
    })
    r=get_json(f"https://api.matchbook.com/edge/rest/events/{event_id}/markets/{market_id}?{q}")
    d=r.get("data")
    if isinstance(d,dict) and "market" in d:return d.get("market"),r
    return d if isinstance(d,dict) else None,r

def total_score(m):
    name=str(m.get("name") or "").strip().lower()
    if name not in {"total","1st half total","first half total"}: return None
    runners=m.get("runners") or []
    labels=[str(r.get("name") or "").upper() for r in runners]
    if not any(x.startswith("OVER ") for x in labels):return None
    if not any(x.startswith("UNDER ") for x in labels):return None
    if not any(r.get("prices") for r in runners):return None
    score=float(m.get("volume") or 0)
    if any("OVER 2.5" in x for x in labels):score+=100000
    elif any("OVER 1.5" in x for x in labels):score+=50000
    if name in {"1st half total","first half total"}:score+=25000
    return score

def pick():
    es,_=list_events();now=time.time();candidates=[];diagnostics=[]
    recent=[]
    for e in es:
        age=(now-start_ts(e))/60 if start_ts(e) else 9999
        if -5 <= age <= 55 and str(e.get("status") or "").lower() in {"open","in-running","live","suspended"}:
            recent.append((age,e))
    # Prefer likely first halves / just-started national-team games with actual live prices.
    for age,e in recent[:40]:
        ms,r=get_markets(e.get("id"))
        best=None
        for m in ms:
            s=total_score(m)
            if s is not None and (best is None or s>best[0]):best=(s,m)
        diagnostics.append({"event":e.get("name"),"age_min":round(age,1),"status":e.get("status"),
                            "event_volume":e.get("volume"),"markets_status":r.get("status"),
                            "markets_count":len(ms),"has_total":bool(best)})
        if best:
            candidates.append((best[0],float(e.get("volume") or 0),-abs(age-25),age,e,best[1]))
    candidates.sort(reverse=True,key=lambda x:(x[0],x[1],x[2]))
    return candidates,diagnostics

def ladder(r):
    back=[];lay=[]
    for p in r.get("prices") or []:
        dec=p.get("decimal-odds");amt=p.get("available-amount");side=str(p.get("side") or "")
        if not isinstance(dec,(int,float)) or not isinstance(amt,(int,float)):continue
        if side in {"back","win"}:back.append((float(dec),float(amt)))
        elif side in {"lay","lose"}:lay.append((float(dec),float(amt)))
    # back: highest decimal is best; lay: lowest decimal is best.
    back.sort(key=lambda x:x[0],reverse=True)
    lay.sort(key=lambda x:x[0])
    return back,lay

def runner_metrics(r):
    b,l=ladder(r);bd=sum(a for _,a in b[:3]);ld=sum(a for _,a in l[:3])
    return {
        "name":r.get("name"),"runner_volume":float(r.get("volume") or 0),
        "best_back":b[0][0] if b else None,"best_lay":l[0][0] if l else None,
        "back_depth3":round(bd,2),"lay_depth3":round(ld,2),
        "imbalance":round((bd-ld)/(bd+ld),5) if bd+ld else None
    }

def snapshot(eid,mid):
    m,r=get_market(eid,mid)
    if not r.get("ok") or not m:return {"ok":False,"status":r.get("status"),"error":r.get("error") or "no market"}
    return {
        "ok":True,"at":datetime.now(timezone.utc).isoformat(),
        "market_id":m.get("id"),"market":m.get("name"),"market_status":m.get("status"),
        "market_volume":float(m.get("volume") or 0),
        "runners":[runner_metrics(x) for x in (m.get("runners") or [])]
    }

def summarize(ss):
    g=[x for x in ss if x.get("ok")]
    if len(g)<2:return {"ok":False,"reason":"not enough snapshots"}
    f,l=g[0],g[-1];fm={r["name"]:r for r in f["runners"]};lm={r["name"]:r for r in l["runners"]}
    out={"ok":True,"seconds":round((datetime.fromisoformat(l["at"])-datetime.fromisoformat(f["at"])).total_seconds(),1),
         "market":l["market"],"market_volume_delta":round(l["market_volume"]-f["market_volume"],2),"runners":{}}
    for n,r in lm.items():
        o=fm.get(n,{})
        out["runners"][n]={
            "runner_volume_delta":round(r["runner_volume"]-float(o.get("runner_volume") or 0),2),
            "best_back_start":o.get("best_back"),"best_back_end":r.get("best_back"),
            "best_lay_start":o.get("best_lay"),"best_lay_end":r.get("best_lay"),
            "imbalance_start":o.get("imbalance"),"imbalance_end":r.get("imbalance"),
            "back_depth_start":o.get("back_depth3"),"back_depth_end":r.get("back_depth3"),
            "lay_depth_start":o.get("lay_depth3"),"lay_depth_end":r.get("lay_depth3")
        }
    return out

def main():
    c,diag=pick()
    if not c:
        result={"error":"no recent event with priced total via /markets include-prices","diagnostics":diag[:30]}
    else:
        _,_,_,age,e,m=c[0]
        ss=[]
        for i in range(7):
            ss.append(snapshot(e.get("id"),m.get("id")))
            if i<6:time.sleep(10)
        result={
            "selected":{"event":e.get("name"),"event_id":e.get("id"),"start":e.get("start"),
                        "age_min_at_start":round(age,1),"status":e.get("status"),"event_volume":e.get("volume"),
                        "market":m.get("name"),"market_id":m.get("id"),"market_volume":m.get("volume")},
            "candidate_count":len(c),"diagnostics":diag[:20],"snapshots":ss,"summary":summarize(ss)
        }
    print("=== MATCHBOOK LIVE DETAIL-ENDPOINT TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
