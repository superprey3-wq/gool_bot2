from __future__ import annotations
import json, statistics, urllib.parse, urllib.request
URL="https://global.ds.lsapp.eu/odds/pq_graphql"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"*/*","Referer":"https://www.flashscore.com/","Origin":"https://www.flashscore.com","x-fsign":"SW9D1eZo"}
TREND_MARKET={"1H_OVER_0.5":("FIRST_HALF","OVER_UNDER",.5,"OVER"),"1H_OVER_1.5":("FIRST_HALF","OVER_UNDER",1.5,"OVER"),"2H_OVER_0.5":("SECOND_HALF","OVER_UNDER",.5,"OVER"),"2H_OVER_1.5":("SECOND_HALF","OVER_UNDER",1.5,"OVER"),"FT_OVER_2.5":("FULL_TIME","OVER_UNDER",2.5,"OVER"),"FT_OVER_3.5":("FULL_TIME","OVER_UNDER",3.5,"OVER"),"FT_UNDER_2.5":("FULL_TIME","OVER_UNDER",2.5,"UNDER"),"BTTS_YES":("FULL_TIME","BOTH_TEAMS_TO_SCORE",None,"YES"),"BTTS_NO":("FULL_TIME","BOTH_TEAMS_TO_SCORE",None,"NO")}
def fetch_event_odds(event_id,timeout=12):
 q=urllib.parse.urlencode({"_hash":"oce","eventId":event_id,"projectId":"5","geoIpCode":"US","geoIpSubdivisionCode":"USCA"})
 with urllib.request.urlopen(urllib.request.Request(URL+"?"+q,headers=HEADERS),timeout=timeout) as r:d=json.load(r)
 return (d.get("data") or {}).get("findOddsByEventId") or {}
def exact_trend_price(data,trend):
 target=TREND_MARKET.get(trend)
 if not target:return None
 scope,kind,line,selection=target; yes=[]; no=[]; best=(0.0,None); names={}
 for b in ((data.get("settings") or {}).get("bookmakers") or []):
  z=b.get("bookmaker") or {}; names[z.get("id")]=z.get("name")
 for row in data.get("odds") or []:
  if row.get("bettingScope")!=scope or row.get("bettingType")!=kind:continue
  bid=row.get("bookmakerId")
  for item in row.get("odds") or []:
   try:odd=float(item.get("value"))
   except (TypeError,ValueError):continue
   if odd<=1:continue
   if kind=="OVER_UNDER":
    try:line_ok=abs(float((item.get("handicap") or {}).get("value"))-line)<1e-6
    except (TypeError,ValueError):line_ok=False
    if not line_ok:continue
    side=str(item.get("selection") or "").upper()
   else:
    flag=item.get("bothTeamsToScore"); side="YES" if flag is True else "NO" if flag is False else str(flag).upper()
   if side==selection:
    yes.append(odd)
    if odd>best[0]:best=(odd,names.get(bid,str(bid)))
   elif side in ("OVER","UNDER","YES","NO"):no.append(odd)
 if not yes or not no:return None
 a,b=statistics.median(yes),statistics.median(no); fair=(1/a)/((1/a)+(1/b))
 return {"best_odds":best[0],"bookmaker":best[1],"market_probability":fair,"bookmakers":len(yes),"median_odds":a}
