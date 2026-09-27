import json, os, urllib.parse, urllib.request
from gool_bot2.providers.flashscore import FlashscoreProvider

BASES=("https://global.ds.lsapp.eu/odds/pq_graphql","https://2.ds.lsapp.eu/pq_graphql")
HEAD={"User-Agent":"Mozilla/5.0","Referer":"https://www.flashscore.com/","Origin":"https://www.flashscore.com","Accept":"application/json,*/*"}

def get(event_id, geo):
    params={"_hash":"oce","eventId":event_id,"projectId":"2","geoIpCode":geo}
    for base in BASES:
        try:
            req=urllib.request.Request(base+"?"+urllib.parse.urlencode(params),headers=HEAD)
            with urllib.request.urlopen(req,timeout=12) as r:
                data=json.load(r)
            odds=(data.get("data") or {}).get("findOddsByEventId") or {}
            if odds: return base,odds
        except Exception as e:
            print("ODDS_ERR",event_id,base,type(e).__name__,str(e)[:120],flush=True)
    return "",{}

fs=FlashscoreProvider()
fixtures=fs.scheduled_matches_for_day(0)[:8]
geo=os.getenv("FS_ODDS_GEO","SE")
print("FS_ODDS_PROBE fixtures=",len(fixtures),"geo=",geo,flush=True)
for m in fixtures:
    base,data=get(m.provider_match_id,geo)
    print("ODDS_EVENT",m.provider_match_id,m.home,"--",m.away,"base=",base,"keys=",list(data)[:20],flush=True)
    print("ODDS_JSON",json.dumps(data,ensure_ascii=False)[:5000],flush=True)
