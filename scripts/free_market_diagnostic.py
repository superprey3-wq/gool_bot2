from __future__ import annotations
import json,time,re
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from datetime import datetime,timezone

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
SX="https://api.sx.bet"
BASE_TOKEN="0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B"
ODDS_PRECISION=10**20
USDC_DECIMALS=1_000_000
XBET_ROOTS=[
 "https://1xbet.com/service-api/LiveFeed","https://1xbet.com/LiveFeed",
 "https://1xbet.fi/service-api/LiveFeed","https://1xbet.fi/LiveFeed"
]
XBET_INDEX=[
 "sports=1&count=1000&lng=en&mode=4&country=1&getEmpty=true",
 "sports=1&count=1000&lng=en&mode=4&country=137&gr=285&virtualSports=true&noFilterBlockEvent=true&getEmpty=true"
]

def req_json(url,headers=None,timeout=10):
    h={"User-Agent":UA,"Accept":"application/json,*/*"}
    if headers:h.update(headers)
    try:
        with urlopen(Request(url,headers=h),timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8","replace"))
    except Exception as e:
        return {"__error__":f"{type(e).__name__}:{e}"}

def norm(s):
    s=str(s or "").lower()
    s=re.sub(r"\b(fc|fk|cf|sc|afc)\b"," ",s)
    s=re.sub(r"[^a-z0-9а-яё ]+"," ",s)
    return " ".join(s.split())

def sim(a,b): return SequenceMatcher(None,norm(a),norm(b)).ratio()

def live_365_first_half():
    q=urlencode({"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1})
    data=req_json("https://webws.365scores.com/web/games/?"+q,{"Referer":"https://www.365scores.com/"})
    games=data.get("games") or []
    out=[]
    for g in games:
        if g.get("statusGroup")!=3:continue
        raw=str(g.get("gameTime") or g.get("gameTimeDisplay") or "")
        m=re.search(r"(\d{1,3})",raw)
        minute=int(m.group(1)) if m else None
        if minute is not None and minute>45:continue
        home=(g.get("homeCompetitor") or {}).get("name") or ""
        away=(g.get("awayCompetitor") or {}).get("name") or ""
        out.append({"id":g.get("id"),"home":home,"away":away,"minute":minute,"time":raw,
                    "score":[(g.get("homeCompetitor") or {}).get("score"),(g.get("awayCompetitor") or {}).get("score")]})
    return out

def sx_markets():
    params={"sportIds":"5","liveOnly":"true","onlyMainLine":"true","type":"2","pageSize":"100"}
    data=req_json(SX+"/markets/active?"+urlencode(params))
    body=data.get("data") if isinstance(data,dict) and "data" in data else data
    if isinstance(body,dict):return [x for x in body.get("markets",[]) if isinstance(x,dict)]
    return body if isinstance(body,list) else []

def sx_orders(hashes):
    if not hashes:return []
    data=req_json(SX+"/orders?"+urlencode({"marketHashes":",".join(hashes[:30]),"baseToken":BASE_TOKEN}))
    body=data.get("data") if isinstance(data,dict) and "data" in data else data
    return [x for x in body if isinstance(x,dict)] if isinstance(body,list) else []

def line_from(m):
    for key in ("line","outcomeOneName","outcomeTwoName"):
        v=m.get(key)
        if key=="line":
            try:
                if v not in (None,""):return float(v)
            except:pass
        else:
            z=re.search(r"([0-9]+(?:\.[0-9]+)?)",str(v or ""))
            if z:return float(z.group(1))
    return None

def over_one(m):
    a=str(m.get("outcomeOneName") or "").lower();b=str(m.get("outcomeTwoName") or "").lower()
    if "over" in a and "under" in b:return True
    if "under" in a and "over" in b:return False
    return None

def taker_quote(rows,taker_one):
    levels=defaultdict(float)
    for row in rows:
        if bool(row.get("isMakerBettingOutcomeOne"))==taker_one:continue
        try:
            pct=int(str(row.get("percentageOdds") or "0"));total=int(str(row.get("totalBetSize") or "0"));fill=int(str(row.get("fillAmount") or "0"))
        except:continue
        remain=total-fill
        if pct<=0 or pct>=ODDS_PRECISION or remain<=0:continue
        raw=(remain*ODDS_PRECISION)//pct-remain
        usdc=raw/USDC_DECIMALS
        prob=max(0.0,min(1.0,1.0-pct/ODDS_PRECISION))
        if usdc>0 and prob>0:levels[round(prob,8)]+=usdc
    if not levels:return {"probability":0.0,"odd":0.0,"available_usdc":0.0}
    p=min(levels)
    return {"probability":p,"odd":1/p if p else 0.0,"available_usdc":round(sum(levels.values()),2)}

def sx_board():
    ms=sx_markets()
    hashes=[str(m.get("marketHash") or "") for m in ms if m.get("marketHash")]
    orders=[]
    for i in range(0,len(hashes),30):orders.extend(sx_orders(hashes[i:i+30]))
    ob=defaultdict(list)
    for o in orders:ob[str(o.get("marketHash") or "")].append(o)
    out=[]
    for m in ms:
        if int(float(m.get("sportId") or 0))!=5:continue
        h=str(m.get("teamOneName") or "").strip();a=str(m.get("teamTwoName") or "").strip();mh=str(m.get("marketHash") or "")
        line=line_from(m);one=over_one(m)
        if not h or not a or not mh or line is None or one is None:continue
        over=taker_quote(ob[mh],one);under=taker_quote(ob[mh],not one)
        liq=over["available_usdc"]+under["available_usdc"]
        if liq<=0:continue
        out.append({"home":h,"away":a,"line":line,"market_hash":mh,"liquidity_usdc":round(liq,2),"TB":over,"TM":under})
    out.sort(key=lambda x:x["liquidity_usdc"],reverse=True)
    return {"markets_seen":len(ms),"orders_seen":len(orders),"events":out}

def xbet_probe():
    headers={"Origin":"https://1xbet.com","Referer":"https://1xbet.com/live/football/","X-Requested-With":"XMLHttpRequest",
             "is-srv":"false","x-app-n":"__BETTING_APP__","x-svc-source":"__BETTING_APP__","x-mobile-project-id":"0"}
    probes=[];payload=None
    for root in XBET_ROOTS:
        for q in XBET_INDEX:
            url=f"{root}/Get1x2_VZip?{q}"
            d=req_json(url,headers,8)
            ok=isinstance(d,dict) and "__error__" not in d
            probes.append({"url":url,"ok":ok,"keys":list(d.keys())[:10] if isinstance(d,dict) else []})
            if ok and payload is None:payload=d
    names=[]
    def walk(o):
        if isinstance(o,dict):
            h=o.get("O1");a=o.get("O2")
            if h and a:names.append({"home":str(h),"away":str(a),"id":o.get("I"),"score":o.get("SC")})
            for v in o.values():walk(v)
        elif isinstance(o,list):
            for v in o[:3000]:walk(v)
    if payload:walk(payload)
    uniq=[];seen=set()
    for x in names:
        k=(x["home"],x["away"],str(x["id"]))
        if k not in seen:seen.add(k);uniq.append(x)
    return probes,uniq

def main():
    g365=live_365_first_half()
    s1=sx_board();time.sleep(20);s2=sx_board()
    sx2=s2["events"];sx1={ (e["home"],e["away"],e["line"]):e for e in s1["events"] }
    for e in sx2:
        old=sx1.get((e["home"],e["away"],e["line"]))
        if old:
            e["flow_20s"]={"TB_prob_delta_pp":round((e["TB"]["probability"]-old["TB"]["probability"])*100,3),
                           "TB_liq_delta":round(e["TB"]["available_usdc"]-old["TB"]["available_usdc"],2),
                           "TM_prob_delta_pp":round((e["TM"]["probability"]-old["TM"]["probability"])*100,3),
                           "TM_liq_delta":round(e["TM"]["available_usdc"]-old["TM"]["available_usdc"],2)}
    cross=[]
    for g in g365:
        best=None;score=0
        for e in sx2:
            s=(sim(g["home"],e["home"])+sim(g["away"],e["away"]))/2
            if s>score:best,score=e,s
        if best and score>=0.72:cross.append({"365":g,"score":round(score,3),"sxbet":best})
    cross.sort(key=lambda x:x["sxbet"]["liquidity_usdc"],reverse=True)
    probes,xmatches=xbet_probe()
    result={"captured_at":datetime.now(timezone.utc).isoformat(),"365_first_half_count":len(g365),"365_examples":g365[:30],
            "sxbet_markets_seen":s2["markets_seen"],"sxbet_orders_seen":s2["orders_seen"],"sxbet_events_count":len(sx2),
            "sxbet_top":sx2[:20],"cross_matches":cross[:10],"xbet_probes":probes,"xbet_matches_found":len(xmatches),"xbet_examples":xmatches[:30]}
    print("=== NATIONAL-TEAM LIVE MARKET TEST ===")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    open("diagnostic_result.json","w",encoding="utf-8").write(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
