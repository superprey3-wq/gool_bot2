from __future__ import annotations
import json,time,re
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from datetime import datetime,timezone

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
ROOT="https://1xbet.fi/service-api/LiveFeed"
INDEX_Q="sports=1&count=1000&lng=en&mode=4&country=1&getEmpty=true"
HEADERS={
 "User-Agent":UA,"Accept":"application/json,*/*","Origin":"https://1xbet.com",
 "Referer":"https://1xbet.com/live/football/","X-Requested-With":"XMLHttpRequest",
 "is-srv":"false","x-app-n":"__BETTING_APP__","x-svc-source":"__BETTING_APP__","x-mobile-project-id":"0"
}

def get(url,timeout=10):
    try:
        with urlopen(Request(url,headers=HEADERS),timeout=timeout) as r:
            d=json.loads(r.read().decode("utf-8","replace"))
            return d if isinstance(d,dict) else {}
    except Exception as e:return {"__error__":f"{type(e).__name__}:{e}"}

def nodes(o,out=None,path=""):
    out=[] if out is None else out
    if isinstance(o,dict):
        if "T" in o and "C" in o:
            try:
                odd=float(o.get("C"));t=int(o.get("T"));line=None if o.get("P") is None else float(o.get("P"))
            except: odd=0;t=-1;line=None
            if odd>1.001 and t>0:
                out.append({"T":t,"C":odd,"P":line,"G":o.get("G"),"path":path,
                            "sub":bool(re.search(r"(?:^|/)SG\[\d+\]",path))})
        for k,v in o.items():nodes(v,out,f"{path}/{k}"[-260:])
    elif isinstance(o,list):
        for i,v in enumerate(o):nodes(v,out,f"{path}[{i}]"[-260:])
    return out

def totals(game):
    ns=nodes(game); groups=(17,4)
    for g in groups:
        by={}
        for n in ns:
            if n["sub"] or n["P"] is None or int(n.get("G") or -1)!=g:continue
            if n["T"] not in (9,10):continue
            r=by.setdefault(float(n["P"]),{"line":float(n["P"])})
            r["over" if n["T"]==9 else "under"]=float(n["C"])
        rows=[by[k] for k in sorted(by)]
        if rows:return rows
    return []

def fair(row):
    o=row.get("over");u=row.get("under")
    if not o or not u:return None
    a=1/float(o);b=1/float(u)
    return a/(a+b) if a+b else None

def stats(game):
    sc=game.get("SC") or {}
    rows=[]
    st=sc.get("ST") or []
    for block in st:
        if not isinstance(block,dict):continue
        vals=block.get("Value") or []
        for x in vals:
            if isinstance(x,dict) and x.get("N"):
                rows.append((str(x["N"]),x.get("S1"),x.get("S2")))
    wanted={"xG","Attacks","Dangerous attacks","Possession %","Shots on target","Shots off target","Corner","Red card"}
    d={n:[a,b] for n,a,b in rows if n in wanted}
    return {
      "period":sc.get("CPS"),"clock":sc.get("SLS"),"ts":sc.get("TS"),
      "score":sc.get("FS"),"stats":d
    }

def index():
    p=get(f"{ROOT}/Get1x2_VZip?{INDEX_Q}")
    return p.get("Value") if isinstance(p.get("Value"),list) else []

def choose_event():
    rows=index()
    # Prefer live national teams still in first half and 0-0/0-1/1-0 to avoid post-goal distortion.
    candidates=[]
    for e in rows:
        if not isinstance(e,dict):continue
        h=str(e.get("O1") or "");a=str(e.get("O2") or "");sc=e.get("SC") or {}
        if str(sc.get("CPS") or "").lower()!="1st half":continue
        fs=sc.get("FS") or {}
        s1=int(fs.get("S1") or 0);s2=int(fs.get("S2") or 0)
        if s1+s2>1:continue
        name=(h+" "+a).lower()
        # target known current internationals first
        priority=0
        for pair in [("Lesotho","Morocco"),("South Sudan","Egypt"),("Mozambique","Sudan"),("Ethiopia","Senegal"),("Bulgaria U19","Romania U19")]:
            if pair[0].lower() in h.lower() and pair[1].lower() in a.lower(): priority=10;break
        candidates.append((priority,-(s1+s2),str(e.get("I") or ""),h,a,s1,s2))
    candidates.sort(reverse=True)
    return candidates[0] if candidates else None

def game(event_id):
    params={"id":event_id,"lng":"en","cfview":0,"isSubGames":"true","GroupEvents":"true",
            "allEventsGroupSubGames":"true","countevents":250,"grMode":2}
    p=get(f"{ROOT}/GetGameZip?{urlencode(params)}")
    v=p.get("Value")
    return v if isinstance(v,dict) else {}

def choose_line(rows,current_goals):
    # GOOL 'another goal' target is current total + 0.5 if quoted.
    target=current_goals+0.5
    exact=next((r for r in rows if abs(float(r["line"])-target)<1e-9 and r.get("over") and r.get("under")),None)
    if exact:return exact
    paired=[r for r in rows if r.get("over") and r.get("under")]
    if not paired:return None
    # otherwise closest active total around current score
    return min(paired,key=lambda r:abs(float(r["line"])-target))

def snapshot(event_id):
    g=game(event_id)
    info=stats(g);fs=(g.get("SC") or {}).get("FS") or {}
    goals=int(fs.get("S1") or 0)+int(fs.get("S2") or 0)
    rows=totals(g);sel=choose_line(rows,goals)
    return {
      "at":datetime.now(timezone.utc).isoformat(),
      "info":info,"totals":rows,
      "selected":None if not sel else {**sel,"fair_over":round(fair(sel),6) if fair(sel) is not None else None}
    }

def summarize(ss):
    valid=[s for s in ss if (s.get("selected") or {}).get("fair_over") is not None]
    if len(valid)<2:return {"ok":False,"reason":"not enough paired total quotes"}
    f,l=valid[0],valid[-1]
    # only compare same line to avoid mistaking line migration for price movement
    line=f["selected"]["line"]
    same=[]
    for s in valid:
        row=next((r for r in s.get("totals") or [] if abs(float(r["line"])-float(line))<1e-9 and r.get("over") and r.get("under")),None)
        if row:
            same.append({"at":s["at"],"over":row["over"],"under":row["under"],"fair_over":round(fair(row),6),
                         "clock":s["info"].get("clock"),"score":s["info"].get("score"),"stats":s["info"].get("stats")})
    if len(same)<2:return {"ok":False,"reason":"selected line disappeared","line":line}
    a,b=same[0],same[-1]
    return {"ok":True,"line":line,"points":same,
            "over_odd_start":a["over"],"over_odd_end":b["over"],
            "fair_over_start":a["fair_over"],"fair_over_end":b["fair_over"],
            "fair_over_delta_pp":round((b["fair_over"]-a["fair_over"])*100,3),
            "score_start":a["score"],"score_end":b["score"]}

def main():
    ch=choose_event()
    if not ch:
        result={"error":"no suitable 1H national-team event"}
    else:
        _,_,eid,h,a,s1,s2=ch
        ss=[]
        for i in range(7):
            ss.append(snapshot(eid))
            if i<6:time.sleep(12)
        result={"event":{"id":eid,"home":h,"away":a,"score_at_pick":[s1,s2]},
                "snapshots":ss,"summary":summarize(ss)}
    print("=== 1XBET NATIONAL FIRST-HALF ODDS SEQUENCE ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
