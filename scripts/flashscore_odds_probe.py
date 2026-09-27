import json, os, urllib.parse, urllib.request, urllib.error
from gool_bot2.providers.flashscore import FlashscoreProvider

VARIANTS=(
 ("p5-oce","https://global.ds.lsapp.eu/odds/pq_graphql","oce","5"),
 ("p2-oce","https://global.ds.lsapp.eu/odds/pq_graphql","oce","2"),
 ("p2-oce-2host","https://2.ds.lsapp.eu/pq_graphql","oce","2"),
 ("p46-ope","https://46.ds.lsapp.eu/pq_graphql","ope","46"),
)
HEAD={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Referer":"https://www.flashscore.com/","Origin":"https://www.flashscore.com","Accept":"*/*","Accept-Language":"sv-SE,sv;q=0.9,en;q=0.8","x-fsign":"SW9D1eZo"}

def get(event_id, geo):
    for label,base,hsh,project in VARIANTS:
        params={"_hash":hsh,"eventId":event_id,"projectId":project,"geoIpCode":geo,"geoIpSubdivisionCode":os.getenv("FS_ODDS_GEO_SUB","USCA")}
        try:
            req=urllib.request.Request(base+"?"+urllib.parse.urlencode(params),headers=HEAD)
            with urllib.request.urlopen(req,timeout=12) as r:
                raw=r.read().decode("utf-8","replace")
            print("ODDS_HTTP",event_id,label,"status=200","body=",raw[:500],flush=True)
            data=json.loads(raw)
            found=(data.get("data") or {}).get("findOddsByEventId")
            if found:
                return label,found
        except urllib.error.HTTPError as e:
            raw=e.read().decode("utf-8","replace")
            print("ODDS_HTTP",event_id,label,"status=",e.code,"body=",raw[:700],flush=True)
        except Exception as e:
            print("ODDS_ERR",event_id,label,type(e).__name__,str(e)[:160],flush=True)
    return "",{}

fs=FlashscoreProvider()
fixtures=fs.scheduled_matches_for_day(0)
geo=os.getenv("FS_ODDS_GEO","US")
print("FS_ODDS_PROBE_ALL fixtures=",len(fixtures),"geo=",geo,flush=True)
for m in fixtures:
    variant,data=get(m.provider_match_id,geo)
    print("ODDS_EVENT",m.provider_match_id,m.home,"--",m.away,"variant=",variant,"type=",type(data).__name__,flush=True)
    odds=data.get("odds") or []\n    scopes={}\n    for e in odds:\n        key=f"{e.get(\'bettingScope\')}:{e.get(\'bettingType\')}"\n        scopes[key]=scopes.get(key,0)+1\n    print("ODDS_COUNT",m.provider_match_id,"entries=",len(odds),"markets=",scopes,flush=True)
