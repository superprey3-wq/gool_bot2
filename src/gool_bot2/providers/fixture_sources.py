from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from .common import ProviderMatch, http_json, pair_score
from .fotmob import FotMobProvider


class SofaScoreFixtures:
    name = "sofascore"
    def day(self, date_key: str) -> list[ProviderMatch]:
        code, data = http_json(f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{date_key}", timeout=10)
        if code != 200 or not isinstance(data, dict):
            return []
        out = []
        for e in data.get("events") or []:
            if not isinstance(e, dict): continue
            h=(e.get("homeTeam") or {}).get("name"); a=(e.get("awayTeam") or {}).get("name"); mid=e.get("id")
            if not h or not a or mid is None: continue
            t=(e.get("tournament") or {}).get("name") or ""
            out.append(ProviderMatch(provider=self.name, provider_match_id=str(mid), home=str(h), away=str(a), league=str(t), meta={"scheduled_start_ts":float(e.get("startTimestamp") or 0)}))
        return out


class Scores365Fixtures:
    name = "365scores"
    def day(self, date_key: str) -> list[ProviderMatch]:
        params={"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1,"startDate":date_key,"endDate":date_key}
        code,data=http_json("https://webws.365scores.com/web/games/?"+urlencode(params),headers={"User-Agent":"Mozilla/5.0","Accept":"*/*","Referer":"https://www.365scores.com/"},timeout=10)
        if code != 200 or not isinstance(data,dict): return []
        out=[]
        for g in data.get("games") or []:
            if not isinstance(g,dict): continue
            h=(g.get("homeCompetitor") or {}).get("name");a=(g.get("awayCompetitor") or {}).get("name");mid=g.get("id")
            if not h or not a or mid is None: continue
            raw=g.get("startTime") or g.get("startTimestamp") or 0
            try:
                ts=datetime.fromisoformat(str(raw).replace("Z","+00:00")).timestamp() if isinstance(raw,str) else float(raw)
                if ts>100000000000: ts/=1000
            except Exception: ts=0
            comp=g.get("competition") or {}; league=comp.get("name") if isinstance(comp,dict) else ""
            out.append(ProviderMatch(provider=self.name,provider_match_id=str(mid),home=str(h),away=str(a),league=str(league or ""),meta={"scheduled_start_ts":ts}))
        return out


def dedupe(rows: list[ProviderMatch]) -> list[ProviderMatch]:
    out=[]
    for m in sorted(rows,key=lambda x: float((x.meta or {}).get("scheduled_start_ts") or 0)):
        ts=float((m.meta or {}).get("scheduled_start_ts") or 0)
        hit=False
        for x in out:
            xt=float((x.meta or {}).get("scheduled_start_ts") or 0)
            if abs(ts-xt)<=20*60 and pair_score(m.home,m.away,x.home,x.away)>=.84:
                hit=True;break
        if not hit: out.append(m)
    return out


def all_fixture_sources(date_key: str) -> tuple[dict[str,int],list[ProviderMatch]]:
    sources={}
    fm=FotMobProvider().scheduled_matches_for_day(date_key);sources["fotmob"]=len(fm)
    sofa=SofaScoreFixtures().day(date_key);sources["sofascore"]=len(sofa)
    s365=Scores365Fixtures().day(date_key);sources["365scores"]=len(s365)
    merged=dedupe(fm+sofa+s365)
    return sources,merged
