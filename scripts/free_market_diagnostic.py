from __future__ import annotations
import json,time,re,importlib.util
from pathlib import Path
from urllib.request import Request,urlopen
from datetime import datetime,timezone

ROOTDIR=Path(__file__).resolve().parents[1]
def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
THREAT=load_module("threat_sequence_expert",ROOTDIR/"src/gool_bot2/threat_sequence_expert.py")

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
FSIGN="SW9D1eZo"
BASES=("https://local-global.flashscore.ninja/2/x/feed",)
STAT_MAP={"432":"xg","34":"shots","13":"sot","16":"corners","459":"big","471":"touches_box"}

def text(url,headers,timeout=4):
    try:
        with urlopen(Request(url,headers=headers),timeout=timeout) as r:
            return r.read().decode("utf-8","replace")
    except Exception:return ""

def fs(path):
    h={"User-Agent":UA,"x-fsign":FSIGN,"Origin":"https://www.flashscore.com","Referer":"https://www.flashscore.com/","Accept":"*/*","Cache-Control":"no-cache"}
    for b in BASES:
        body=text(f"{b}/{path}",h)
        if body and not body.lstrip().startswith("<"): return body
    return ""

def fields(raw):
    out={}
    for tok in raw.split("¬"):
        if "÷" in tok:
            k,v=tok.split("÷",1)
            if k and k not in out:out[k]=v
    return out

def asint(v,d=0):
    try:return int(float(str(v)))
    except:return d

def minute(f,now):
    ac=f.get("AC","");ao=asint(f.get("AO"));ad=asint(f.get("AD"))
    if ac=="38":return 45
    if ac=="12":
        base=ao or ad; return max(1,min(45,(max(0,now-base)//60)+1 if base else 1))
    if ac=="13":
        base=ao or ad; return max(46,min(90,45+(max(0,now-base)//60)+1 if base else 46))
    return 1

def live_matches():
    now=int(time.time()); found={}; league=""
    for path in ("f_1_0_3_en_1","f_1_0_0_en_1"):
        body=fs(path)
        for chunk in body.split("~"):
            if chunk.startswith("ZA÷"): league=fields(chunk).get("ZA","").strip(); continue
            if not chunk.startswith("AA÷"):continue
            eid,sep,rest=chunk[3:].partition("¬")
            if not sep:continue
            f=fields(rest)
            if f.get("AB")!="2":continue
            home=(f.get("AE") or f.get("CX") or "").strip();away=(f.get("AF") or "").strip()
            if not home or not away:continue
            found[eid]={"id":eid,"home":home,"away":away,"league":league,"minute":minute(f,now),
                        "score":[asint(f.get("AG"),asint(f.get("AT"))),asint(f.get("AH"),asint(f.get("AU")))],
                        "status":f.get("AC")}
    return sorted(found.values(),key=lambda x:(x["minute"],x["league"],x["home"]))

def stats(eid):
    body=fs(f"df_st_1_{eid}"); out={}
    for chunk in body.split("~"):
        m=re.search(r"SD(?:÷|¬)(\d+).*?SH(?:÷|¬)([^¬~]+).*?SI(?:÷|¬)([^¬~]+)",chunk)
        if not m:continue
        sid,h,a=m.groups();name=STAT_MAP.get(sid)
        if not name:continue
        def num(x):
            try:return float(str(x).replace("%","").strip())
            except:return 0.0
        out[name]=[num(h),num(a)]
    return out

def point(match):
    st=stats(match["id"])
    def total(k):
        v=st.get(k)
        return None if not v else float(v[0])+float(v[1])
    xg=total("xg")
    if xg is None:
        sh=total("shots") or 0;sot=total("sot") or 0;big=total("big") or 0;box=total("touches_box") or 0;cor=total("corners") or 0
        xg=0.025*sh+0.07*sot+0.18*big+0.01*box+0.008*cor if sum([sh,sot,big,box,cor])>0 else 0.0
    return {"at":datetime.now(timezone.utc).isoformat(),"score":match["score"],"minute":match["minute"],
            "xg_total":xg,"sot_total":total("sot") or 0.0,"shots_total":total("shots") or 0.0,
            "danger_total":0.0,"corners_total":total("corners") or 0.0,"over_prob":None,"raw_stats":st}

def choose(ms,n=3):
    # prioritize 1H matches with at least some attack stats
    rows=[]
    for m in ms:
        if m["minute"]>45:continue
        st=stats(m["id"])
        richness=sum(1 for k in ("xg","shots","sot","corners","big","touches_box") if k in st)
        activity=sum(sum(v) for v in st.values()) if st else 0
        rows.append((richness,activity,-m["minute"],m))
    rows.sort(reverse=True,key=lambda x:(x[0],x[1],x[2]))
    return [x[3] for x in rows[:n]]

def main():
    ms=live_matches(); selected=choose(ms,3)
    histories={m["id"]:[] for m in selected}
    for i in range(6):
        current={m["id"]:m for m in live_matches()}
        for base in selected:
            m=current.get(base["id"],base)
            histories[base["id"]].append(point(m))
        if i<5:time.sleep(60)
    results=[]
    latest={m["id"]:m for m in live_matches()}
    for m in selected:
        hist=histories[m["id"]]
        states=[]
        for end in range(3,len(hist)+1):
            states.append({"point":end,"threat":THREAT.evaluate_threat_sequence(hist[:end])})
        final_match=latest.get(m["id"],m)
        goal_happened=(sum(final_match.get("score") or [0,0]) > sum(m.get("score") or [0,0]))
        max_state="QUIET"
        order={"NO_DATA":0,"RESET":0,"QUIET":1,"WARM":2,"BUILDING":3,"SURGE":4}
        max_score=-1
        for s in states:
            st=(s.get("threat") or {}).get("state","QUIET")
            if order.get(st,0)>max_score:
                max_score=order.get(st,0); max_state=st
        results.append({
            "match_start":m,
            "match_end":final_match,
            "history":hist,
            "states":states,
            "max_state":max_state,
            "goal_happened_during_window":goal_happened,
            "score_delta":sum(final_match.get("score") or [0,0])-sum(m.get("score") or [0,0])
        })
    payload={"captured_at":datetime.now(timezone.utc).isoformat(),"flashscore_live_count":len(ms),
             "selected_count":len(selected),"window_seconds":300,"results":results}
    print("=== FLASHSCORE LIVE THREAT-SEQUENCE TEST ===")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
