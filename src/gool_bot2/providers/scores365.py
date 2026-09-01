from __future__ import annotations

import time
from urllib.parse import urlencode
from typing import Any

from .common import ProviderMatch, http_json, pair_score

BASE = "https://webws.365scores.com/web"


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "-"): return None
        return float(value)
    except (TypeError, ValueError): return None


def _truthy(row: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool) and value: return True
        if isinstance(value, (int, float)) and value != 0: return True
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1", "big chance", "bigchance"}: return True
    return False


def _inside_box(shot: dict[str, Any]) -> bool:
    if _truthy(shot, "isInsideBox", "insideBox", "isInBox"): return True
    label = " ".join(str(shot.get(k) or "") for k in ("area", "location", "shotArea", "description")).lower()
    return "inside" in label and "box" in label


def _on_target(shot: dict[str, Any]) -> bool:
    if _truthy(shot, "isOnTarget", "onTarget"): return True
    label = " ".join(str(shot.get(k) or "") for k in ("result", "type", "description", "eventTypeName")).lower()
    return any(token in label for token in ("goal", "saved", "save", "on target"))


def _embedded_history(game: dict[str, Any], home: str, away: str, limit: int = 10) -> dict[str, Any]:
    rows=[];seen=set()
    def name(v):
        if isinstance(v,dict): return str(v.get("name") or v.get("competitorName") or "").strip()
        return str(v or "").strip()
    def score(node, side):
        for key in (f"{side}Score", f"{side}_score", "score"):
            v=node.get(key)
            if key=="score" and isinstance(v,dict): v=v.get(side)
            if isinstance(v,dict): v=v.get("current") or v.get("score") or v.get("value")
            try:
                if v not in (None,"","-"): return int(float(v))
            except (TypeError,ValueError): pass
        comp=node.get("homeCompetitor" if side=="home" else "awayCompetitor")
        if isinstance(comp,dict):
            for k in ("score","currentScore"):
                try:
                    if comp.get(k) not in (None,"","-"): return int(float(comp.get(k)))
                except (TypeError,ValueError): pass
        return None
    def walk(v,path=""):
        if isinstance(v,dict):
            lp=path.lower();hist=any(t in lp for t in ("h2h","headtohead","form","recent","previous","lastgames","lastmatches"))
            h=name(v.get("home") or v.get("homeTeam") or v.get("homeCompetitor"));a=name(v.get("away") or v.get("awayTeam") or v.get("awayCompetitor"));hs=score(v,"home");aws=score(v,"away")
            if hist and h and a and hs is not None and aws is not None:
                stamp=str(v.get("startTime") or v.get("timestamp") or v.get("date") or "");key=(h.casefold(),a.casefold(),hs,aws,stamp)
                if key not in seen: seen.add(key);rows.append({"home":h,"away":a,"home_score":hs,"away_score":aws,"timestamp":stamp,"source":"365scores"})
            for k,c in v.items(): walk(c,f"{path}/{k}")
        elif isinstance(v,list):
            for c in v: walk(c,path)
    walk(game)
    def similar(n,t): return pair_score(t,t,n,n)>=.72
    def has(r,t): return similar(str(r.get("home") or ""),t) or similar(str(r.get("away") or ""),t)
    hc=home.casefold().strip();ac=away.casefold().strip();hr=[r for r in rows if has(r,hc)][:limit];ar=[r for r in rows if has(r,ac)][:limit]
    return {"source":"365scores_embedded","home_recent":hr,"away_recent":ar,"home_at_home":[r for r in hr if similar(str(r.get("home") or ""),hc)][:limit],"away_away":[r for r in ar if similar(str(r.get("away") or ""),ac)][:limit],"h2h":[r for r in rows if has(r,hc) and has(r,ac)][:limit],"raw_matches":len(rows)}


class Scores365Provider:
    name = "365scores"
    def __init__(self) -> None:
        self._live_cache=(0.0,[]);self._detail_cache={}
    def _get(self,path,params):
        return http_json(BASE+"/"+path.lstrip("/")+"?"+urlencode(params),headers={"User-Agent":"Mozilla/5.0","Accept":"*/*","Referer":"https://www.365scores.com/"},timeout=9)
    def live_rows(self):
        now=time.time()
        if now-self._live_cache[0]<120:return self._live_cache[1]
        code,data=self._get("games/",{"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","sports":1});rows=[]
        if code==200 and isinstance(data,dict):rows=[g for g in (data.get("games") or []) if isinstance(g,dict) and g.get("statusGroup")==3]
        self._live_cache=(now,rows);return rows
    def find_match(self,home,away):
        best=None;best_score=0.0
        for game in self.live_rows():
            h=(game.get("homeCompetitor") or {}).get("name");a=(game.get("awayCompetitor") or {}).get("name");score=pair_score(home,away,str(h or ""),str(a or ""))
            if score>best_score:best,best_score=game,score
        return (best,best_score) if best_score>=.72 else (None,best_score)
    def _detail(self,game_id):
        now=time.time();cached=self._detail_cache.get(str(game_id))
        if cached and now-cached[0]<120:return cached[1]
        code,data=self._get("game/",{"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","gameId":game_id,"topBookmaker":14});game=(data.get("game") or {}) if code==200 and isinstance(data,dict) else {};self._detail_cache[str(game_id)]=(now,game);return game
    def prematch_context(self,home,away,limit=10):
        hit,score=self.find_match(home,away)
        if not hit:return {"source":"365scores_embedded","raw_matches":0,"match_score":score}
        gid=hit.get("id")
        if not gid:return {"source":"365scores_embedded","raw_matches":0,"match_score":score}
        ctx=_embedded_history(self._detail(str(gid)),home,away,limit);ctx["match_score"]=round(score,3);return ctx
    def enrich(self,home,away):
        hit,score=self.find_match(home,away)
        if not hit:return None
        game_id=hit.get("id")
        if not game_id:return None
        game=self._detail(str(game_id));shots=(((game.get("chartEvents") or {}).get("events") or []) if isinstance(game,dict) else []);home_id=(hit.get("homeCompetitor") or {}).get("id");away_id=(hit.get("awayCompetitor") or {}).get("id")
        xg=[0.,0.];xgot=[0.,0.];counts=[0.,0.];sot=[0.,0.];inside=[0.,0.];big=[0.,0.];high_xg=[0.,0.];has_xg=has_xgot=False;quality=[]
        for shot in shots:
            if not isinstance(shot,dict):continue
            side=0 if shot.get("competitorId")==home_id else 1;counts[side]+=1;xv=_num(shot.get("xg"));xgv=_num(shot.get("xgot"))
            if xv is not None:xg[side]+=xv;has_xg=True;high_xg[side]+=1 if xv>=.20 else 0
            if xgv is not None:xgot[side]+=xgv;has_xgot=True
            if _on_target(shot):sot[side]+=1
            if _inside_box(shot):inside[side]+=1
            if _truthy(shot,"isBigChance","bigChance"):big[side]+=1
            quality.append({"side":"home" if side==0 else "away","minute":shot.get("minute") or shot.get("gameTime"),"xg":xv,"xgot":xgv,"inside_box":_inside_box(shot),"on_target":_on_target(shot),"big_chance":_truthy(shot,"isBigChance","bigChance")})
        events=game.get("events") or [] if isinstance(game,dict) else [];reds=[0.,0.];yellows=[0.,0.]
        for event in events:
            if not isinstance(event,dict):continue
            cid=event.get("competitorId") or (event.get("competitor") or {}).get("id");side=0 if cid==home_id else (1 if cid==away_id else None)
            if side is None:continue
            et=event.get("eventType") or {};eid=et.get("id") if isinstance(et,dict) else event.get("eventTypeId");nm=str((et.get("name") if isinstance(et,dict) else "") or event.get("type") or "").lower()
            if eid==3 or "red card" in nm:reds[side]+=1
            if "yellow" in nm:yellows[side]+=1
        stats={"shotmap_shots":(counts[0],counts[1]),"shots":(counts[0],counts[1]),"shots_on_target":(sot[0],sot[1]),"shots_inside_box":(inside[0],inside[1]),"big_chances":(big[0],big[1]),"high_xg_shots":(high_xg[0],high_xg[1]),"red_cards":(reds[0],reds[1]),"yellow_cards":(yellows[0],yellows[1])}
        if has_xg:stats["xg"]=(round(xg[0],3),round(xg[1],3))
        if has_xgot:stats["xgot"]=(round(xgot[0],3),round(xgot[1],3))
        return ProviderMatch(provider=self.name,provider_match_id=str(game_id),home=home,away=away,stats=stats,meta={"match_score":round(score,3),"red_cards":int(sum(reds)),"has_shotmap":bool(shots),"has_stats":bool(game.get("hasStats")),"has_lineups":bool(game.get("hasLineups")),"shot_quality":quality[-40:]})
