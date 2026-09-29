from __future__ import annotations

import json, time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def get_json(url, timeout=15, referer="https://www.google.com/"):
    req=Request(url,headers={"User-Agent":UA,"Accept":"application/json,text/plain,*/*","Referer":referer})
    try:
        with urlopen(req,timeout=timeout) as r:
            raw=r.read()
            return {"ok":True,"status":r.status,"data":json.loads(raw.decode("utf-8","replace")),"bytes":len(raw)}
    except HTTPError as e:
        return {"ok":False,"status":e.code,"error":str(e)}
    except Exception as e:
        return {"ok":False,"status":None,"error":f"{type(e).__name__}:{e}"}

def ts(v):
    try:return float(v or 0)
    except:return 0.0

def pick_fotmob():
    d=datetime.now(timezone.utc).strftime("%Y%m%d")
    r=get_json(f"https://www.fotmob.com/api/matches?date={d}",referer="https://www.fotmob.com/")
    out={"source":"fotmob","ok":r.get("ok"),"status":r.get("status"),"bytes":r.get("bytes")}
    if not r.get("ok"): out["error"]=r.get("error"); return out
    rows=[]
    for lg in (r["data"] or {}).get("leagues") or []:
        for m in lg.get("matches") or []:
            rows.append((lg,m))
    now=time.time();live=[];future=[]
    for lg,m in rows:
        st=str(m.get("status") or m.get("statusId") or "").lower()
        started=bool(m.get("started"))
        finished=bool(m.get("finished"))
        if started and not finished: live.append((lg,m))
        raw=m.get("status",{}).get("utcTime") if isinstance(m.get("status"),dict) else m.get("utcTime")
        if raw:
            try:
                from datetime import datetime as dt
                t=dt.fromisoformat(str(raw).replace("Z","+00:00")).timestamp()
                if t>=now-300: future.append((t,lg,m))
            except: pass
    if live: lg,m=live[0];mode="live"
    elif future: _,lg,m=sorted(future,key=lambda x:x[0])[0];mode="prematch"
    elif rows: lg,m=rows[0];mode="fallback"
    else: return {**out,"error":"no matches","matches":0}
    mid=m.get("id") or m.get("matchId")
    out.update({"matches":len(rows),"mode":mode,"event_id":mid,"league":lg.get("name"),"home":(m.get("home") or {}).get("name"),"away":(m.get("away") or {}).get("name"),"detail":{k:v for k,v in get_json(f"https://www.fotmob.com/api/matchDetails?matchId={mid}",referer="https://www.fotmob.com/").items() if k!="data"}})
    return out

def pick_365():
    q=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1})
    r=get_json("https://webws.365scores.com/web/games/?"+q,referer="https://www.365scores.com/")
    out={"source":"365scores","ok":r.get("ok"),"status":r.get("status"),"bytes":r.get("bytes")}
    if not r.get("ok"):out["error"]=r.get("error");return out
    games=(r["data"] or {}).get("games") or []
    live=[g for g in games if g.get("statusGroup")==3]
    g=(live or games or [None])[0]
    if not g:return {**out,"error":"no games","games":0}
    gid=g.get("id")
    q2=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","gameId":gid,"topBookmaker":14})
    out.update({"games":len(games),"mode":"live" if live else "prematch","event_id":gid,"home":(g.get("homeCompetitor") or {}).get("name"),"away":(g.get("awayCompetitor") or {}).get("name"),"detail":{k:v for k,v in get_json("https://webws.365scores.com/web/game/?"+q2,referer="https://www.365scores.com/").items() if k!="data"}})
    return out

def sofa_probe():
    d=datetime.now(timezone.utc).date().isoformat()
    r=get_json(f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{d}",referer="https://www.sofascore.com/")
    return {"source":"sofascore","ok":r.get("ok"),"status":r.get("status"),"bytes":r.get("bytes"),"error":r.get("error")}

def walk_numbers(obj,path=""):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f"{path}.{k}" if path else k
            lk=str(k).lower()
            if isinstance(v,(int,float)) and any(x in lk for x in ("volume","matched","amount","liquid","available")):
                out.append((p,float(v)))
            out.extend(walk_numbers(v,p))
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:100]):out.extend(walk_numbers(v,f"{path}[{i}]"))
    return out

def extract_matchbook_snapshot():
    url="https://api.matchbook.com/edge/rest/events?offset=0&per-page=8&sport-ids=15"
    r=get_json(url,referer="https://www.matchbook.com/")
    out={"source":"matchbook","ok":r.get("ok"),"status":r.get("status"),"bytes":r.get("bytes")}
    if not r.get("ok"):out["error"]=r.get("error");return out,None
    data=r["data"] or {};events=data.get("events") or []
    now=time.time()
    def start(e):
        raw=e.get("start") or e.get("start-time") or e.get("start_time")
        if isinstance(raw,(int,float)):return float(raw)/1000 if raw>1e11 else float(raw)
        if raw:
            try:return datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp()
            except:return 0
        return 0
    inrun=[e for e in events if e.get("in-running") or e.get("in_running") or e.get("status")=="in-running"]
    event=(inrun or sorted(events,key=lambda e:abs(start(e)-now)) or [None])[0]
    if not event:return {**out,"events":0},data
    markets=event.get("markets") or []
    interesting=[]
    for m in markets:
        name=str(m.get("name") or m.get("market-name") or "")
        if any(x in name.lower() for x in ("over","under","goal","total","match odds")):
            runners=[]
            for runner in (m.get("runners") or [])[:6]:
                prices=[]
                for p in (runner.get("prices") or [])[:8]:
                    prices.append({k:p.get(k) for k in ("odds","decimal-odds","side","available-amount","amount","volume") if p.get(k) is not None})
                runners.append({"name":runner.get("name"),"prices":prices})
            interesting.append({"id":m.get("id"),"name":name,"status":m.get("status"),"volume":m.get("volume"),"runners":runners})
            if len(interesting)>=4:break
    nums=walk_numbers(event)
    out.update({"events":len(events),"event":{"id":event.get("id"),"name":event.get("name"),"start":event.get("start"),"status":event.get("status"),"in_running":event.get("in-running"),"market_count":len(markets)},"markets":interesting,"money_fields":nums[:30]})
    return out,data

def compact_money(snapshot):
    vals=snapshot.get("money_fields") or []
    return round(sum(v for _,v in vals),4)

def main():
    result={"captured_at":datetime.now(timezone.utc).isoformat(),"sofascore":sofa_probe(),"fotmob":pick_fotmob(),"scores365":pick_365()}
    mb1,_=extract_matchbook_snapshot()
    result["matchbook_snapshot_1"]=mb1
    time.sleep(20)
    mb2,_=extract_matchbook_snapshot()
    result["matchbook_snapshot_2"]=mb2
    result["matchbook_delta"]={"same_event":(mb1.get("event") or {}).get("id")==(mb2.get("event") or {}).get("id"),"money_field_sum_1":compact_money(mb1),"money_field_sum_2":compact_money(mb2),"delta":round(compact_money(mb2)-compact_money(mb1),4)}
    print("=== FREE SOURCE + MONEY FLOW DIAGNOSTIC ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    with open("diagnostic_result.json","w",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)

if __name__=="__main__":main()
