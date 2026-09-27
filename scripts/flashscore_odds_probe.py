import json, os, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from gool_bot2.providers.flashscore import FlashscoreProvider

URL="https://global.ds.lsapp.eu/odds/pq_graphql"
HEAD={"User-Agent":"Mozilla/5.0","Accept":"*/*","Referer":"https://www.flashscore.com/","Origin":"https://www.flashscore.com","x-fsign":"SW9D1eZo"}

def fetch(m):
    q=urllib.parse.urlencode({"_hash":"oce","eventId":m.provider_match_id,"projectId":"5","geoIpCode":"US","geoIpSubdivisionCode":"USCA"})
    try:
        with urllib.request.urlopen(urllib.request.Request(URL+"?"+q,headers=HEAD),timeout=12) as r:
            d=json.load(r)
        x=(d.get("data") or {}).get("findOddsByEventId") or {}
        return m,200,x.get("odds") or []
    except Exception as e:
        return m,getattr(e,"code",0),[]

fixtures=FlashscoreProvider().scheduled_matches_for_day(0)
print("FS_ALL_START",len(fixtures),flush=True)
http=Counter(); markets=Counter(); covered=0; entries=0; examples=[]
with ThreadPoolExecutor(max_workers=int(os.getenv("FS_ODDS_WORKERS","20"))) as pool:
    futures=[pool.submit(fetch,m) for m in fixtures]
    for i,f in enumerate(as_completed(futures),1):
        m,code,odds=f.result(); http[code]+=1
        if odds:
            covered+=1; entries+=len(odds)
            for e in odds:
                markets[str(e.get("bettingScope"))+":"+str(e.get("bettingType"))]+=1
            if len(examples)<10:
                examples.append([m.home,m.away,len(odds)])
        if i%100==0 or i==len(fixtures):
            print("FS_PROGRESS",i,len(fixtures),"covered",covered,flush=True)
print("FS_ALL_SUMMARY",json.dumps({"fixtures":len(fixtures),"http":dict(http),"with_odds":covered,"without_odds":len(fixtures)-covered,"entries":entries}),flush=True)
print("FS_MARKETS",json.dumps(markets.most_common()),flush=True)
print("FS_EXAMPLES",json.dumps(examples,ensure_ascii=False),flush=True)
