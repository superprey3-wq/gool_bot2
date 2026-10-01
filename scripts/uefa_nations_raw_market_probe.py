from __future__ import annotations
import json
from datetime import datetime,timezone,timedelta
from gool_bot2.flashscore_odds import fetch_event_odds
from gool_bot2.providers.flashscore import FlashscoreProvider

def main():
    fs=FlashscoreProvider(); msk=timezone(timedelta(hours=3)); day=datetime.now(msk).date(); now=datetime.now(timezone.utc).timestamp()
    fixtures=[m for m in fs.parse_master_scheduled(fs._feed("f_1_0_3_en_1",timeout=12,max_hosts=1))
              if (m.meta or {}).get("scheduled_start_ts") and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]),msk).date()==day
              and float(m.meta["scheduled_start_ts"])>now]
    m=next((x for x in fixtures if x.home=="Germany" and x.away=="Serbia"),None)
    if m is None:
        print("RAW_PROBE_MATCH_NOT_FOUND",flush=True); return
    data=fetch_event_odds(m.provider_match_id)
    seen=set()
    for row in data.get("odds") or []:
        k=(str(row.get("bettingScope") or ""),str(row.get("bettingType") or ""))
        if k in seen: continue
        seen.add(k)
        print("RAW_MARKET",k[0],k[1],flush=True)
        print("RAW_ROW_KEYS",json.dumps(sorted(row.keys()),ensure_ascii=False),flush=True)
        print("RAW_ITEMS",json.dumps((row.get("odds") or [])[:12],ensure_ascii=False),flush=True)

if __name__=="__main__": main()
