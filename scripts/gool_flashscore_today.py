from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.flashscore_odds import fetch_event_odds,exact_trend_price
from gool_bot2.v4_shadow_report import _analyse_fixtures,_trend_signals,_primary_trend,_brain_score
from gool_bot2.v4_prematch_engine import PrematchPick,choose_delivery
from gool_bot2.odds_journal import append_price_snapshot,append_sqlite_snapshot
from gool_bot2.v4_prematch_delivery import emit_delivery_selection,retry_pending_prematch_deliveries
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from pathlib import Path
import os

MSK=ZoneInfo("Europe/Moscow")
def when(p):
 return datetime.fromtimestamp(float(p.kickoff_ts),MSK).strftime("%d.%m %H:%M МСК") if p.kickoff_ts else "время н/д"
def leg(p,meta):
 x=meta[p.event_id]
 return f"{p.league or 'Турнир н/д'} | {when(p)} | {p.home} -- {p.away} | {p.selection} @ {p.odds:.2f} [{x['bookmaker']}] | p={p.model_probability:.3f} edge={p.edge:+.3f}"

fs=FlashscoreProvider(); now=datetime.now(timezone.utc).timestamp(); day=datetime.now(MSK).date()
fixtures=[m for m in fs.scheduled_matches_for_day(0) if (m.meta or {}).get("scheduled_start_ts") and float(m.meta["scheduled_start_ts"])>now and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]),MSK).date()==day]
print("PIPELINE_START",len(fixtures),flush=True)
rows,fail=_analyse_fixtures(fs,fixtures); rows=[r for r in rows if r.get("primary_trend")]
print("BRAIN_ELIGIBLE",len(rows),"FAIL",len(fail),flush=True)

# Second-stage football confirmation. Do not fan out secondary providers across the
# whole slate: only candidates that already passed the cheap Flashscore brain are enriched.
fusion_limit=max(0,min(80,int(os.getenv("GOOL_PREMATCH_FUSION_LIMIT","40"))))
fusion_workers=max(2,min(12,int(os.getenv("GOOL_PREMATCH_FUSION_WORKERS","6"))))
fusion_rows=rows[:fusion_limit]
if fusion_rows:
 def enrich(r):
  try:
   local_fs=FlashscoreProvider(); fusion=PrematchDataFusion(local_fs); m=r["match"]
   history=fusion.context(m,limit=10)
   profile=build_prematch_goal_profile({"match":{"home":m.home,"away":m.away},"prematch_context":history})
   samples=[int((profile.get(k) or {}).get("pair_sample") or 0) for k in ("first_half","second_half","full_match")]
   sample=max(samples or [0])
   coverage=sum(1 for v in (history.get("source_coverage") or {}).values() if int(v or 0)>0)
   sample_quality=min(1.0,sample/8.0)
   source_quality=min(1.0,coverage/3.0)
   quality=max(float(r.get("quality") or 0.0), min(1.0,0.78*sample_quality+0.22*source_quality))
   trends=_trend_signals(profile,quality); primary=_primary_trend(trends)
   return {**r,"profile":profile,"sample":sample,"quality":quality,"brain_score":_brain_score(profile,quality),
           "trends":trends,"primary_trend":primary,"sources":history.get("sources") or [],
           "source_coverage":history.get("source_coverage") or {}},None
  except Exception as exc:
   return r,f"FUSION_{type(exc).__name__}"
 enriched=[]; fusion_fail=0
 with ThreadPoolExecutor(max_workers=fusion_workers) as pool:
  for fut in as_completed([pool.submit(enrich,r) for r in fusion_rows]):
   row,err=fut.result(); enriched.append(row); fusion_fail+=int(bool(err))
 by_id={str(r["match"].provider_match_id):r for r in enriched}
 rows=[by_id.get(str(r["match"].provider_match_id),r) for r in rows]
 rows=[r for r in rows if r.get("primary_trend")]
 rows.sort(key=lambda r:(float(r.get("brain_score") or 0),float(r.get("quality") or 0)),reverse=True)
 print("PREMATCH_FUSION",{"checked":len(fusion_rows),"kept":len(rows),"fail":fusion_fail},flush=True)

print("BRAIN_ELIGIBLE_AFTER_FUSION",len(rows),flush=True)
print("PRIMARY_TREND_COUNTS",dict(Counter(r["primary_trend"]["name"] for r in rows)),flush=True)

def one(r):
 try:p=exact_trend_price(fetch_event_odds(r["match"].provider_match_id),r["primary_trend"]["name"])
 except Exception:return None
 if not p:return None
 m=r["match"]; t=r["primary_trend"]; kick=float((m.meta or {}).get("scheduled_start_ts") or 0)
 pick=PrematchPick(str(m.provider_match_id),m.home,m.away,t["name"],t["name"],p["best_odds"],float(t["probability"]),p["market_probability"],float(r["quality"]),m.league or "",kick)
 return pick,p,r

priced=[]; meta={}
with ThreadPoolExecutor(max_workers=20) as pool:
 for f in as_completed([pool.submit(one,r) for r in rows]):
  z=f.result()
  if not z:continue
  p,x,r=z; priced.append(p); m=r["match"]
  fs_meta=dict(m.meta or {})
  home_logo=fs.team_logo_url(str(fs_meta.get("home_team_slug") or ""),str(fs_meta.get("home_team_id") or ""))
  away_logo=fs.team_logo_url(str(fs_meta.get("away_team_slug") or ""),str(fs_meta.get("away_team_id") or ""))
  if home_logo: fs_meta["home_logo_url"]=home_logo
  if away_logo: fs_meta["away_logo_url"]=away_logo
  meta[p.event_id]={**x, "flashscore_meta": fs_meta}
  kw=dict(event_id=p.event_id,home=m.home,away=m.away,league=m.league,kickoff_ts=p.kickoff_ts,trend=p.market,odds=p.odds,bookmaker=x["bookmaker"],market_probability=p.market_probability,model_probability=p.model_probability)
  append_price_snapshot(**kw); append_sqlite_snapshot(**kw,data_quality=p.data_quality)
print("EXACT_PRIMARY_PRICED",len(priced),flush=True)
print("PRICED_TREND_COUNTS",dict(Counter(p.market for p in priced)),flush=True)

d=choose_delivery(priced)
journal=Path(os.getenv("GOOL_MULTI_JOURNAL_PATH") or (Path(os.getenv("RUNTIME_DATA_DIR","data"))/"live"/"gool_multi_journal.json"))
if str(os.getenv("GOOL_PREMATCH_DELIVER","0")).lower() in {"1","true","yes","on"}:
 retried=retry_pending_prematch_deliveries(journal)
 if retried:
  print("PREMATCH_RETRY",{"cards":retried},"journal",journal,flush=True)
 delivered=emit_delivery_selection(d,meta,journal)
 print("PREMATCH_DELIVERY",delivered,"journal",journal,flush=True)
print("=== GOOL DELIVERY",d["mode"],"===",flush=True)
if d["super"]:
 print("SUPER 10",flush=True)
 for i,p in enumerate(d["super"]["legs"],1):print(f"X{i:02d}. "+leg(p,meta),flush=True)
 print(f"SUPER combined_odds={d['super']['combined_odds']:.2f} probability={d['super']['combined_probability']:.4f}",flush=True)
for n,a in enumerate(d["doubles"],1):
 print(f"DOUBLE {n} combined_odds={a['combined_odds']:.2f} probability={a['combined_probability']:.4f}",flush=True)
 for i,p in enumerate(a["legs"],1):print(f"D{n}.{i}. "+leg(p,meta),flush=True)
if d["singles"]:
 print("ORDINARS",flush=True)
 for i,item in enumerate(d["singles"],1):
  p=item[0] if isinstance(item,tuple) else item
  print(f"S{i:02d}. "+leg(p,meta),flush=True)
if d["mode"]=="NO_BET":print("NO QUALIFIED BETS",flush=True)
