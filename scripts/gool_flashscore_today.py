from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.flashscore_odds import fetch_event_odds,exact_trend_price
from gool_bot2.full_market_brain import analyze_full_market
from gool_bot2.prematch_full_market_runtime import production_full_market_picks
from gool_bot2.v4_shadow_report import _analyse_fixtures,_trend_signals,_primary_trend,_brain_score
from gool_bot2.v4_prematch_engine import PrematchPick,choose_delivery,build_prematch_candidates
from gool_bot2.odds_journal import append_price_snapshot,append_sqlite_snapshot
from gool_bot2.v4_prematch_delivery import emit_delivery_selection,retry_pending_prematch_deliveries
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.xbet_prematch_market import XBetPrematchCollector,find_prematch_market
from gool_bot2.prematch_status import update_prematch_status
from gool_bot2.prematch_confidence import select_confident_prematch_rows
from gool_bot2.prematch_quality import prematch_evidence_quality
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
update_prematch_status(stage="brain", fixtures=len(fixtures), brain_eligible=0, priced=0)
rows,fail=_analyse_fixtures(fs,fixtures); rows=[r for r in rows if r.get("primary_trend")]
update_prematch_status(stage="market_lookup", fixtures=len(fixtures), brain_eligible=len(rows), brain_failures=len(fail))
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
   quality=prematch_evidence_quality(profile, source_coverage=history.get("source_coverage") or {})
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

brain_eligible_total=len(rows)
rows,shortlist_stats=select_confident_prematch_rows(rows)
print("PREMATCH_SHORTLIST",shortlist_stats,flush=True)
update_prematch_status(
 stage="market_lookup",
 brain_eligible=brain_eligible_total,
 shortlist=len(rows),
 shortlist_cap=int(shortlist_stats.get("cap") or 0),
 shortlist_qualified=int(shortlist_stats.get("qualified") or 0),
 shortlist_rejected_sample=int(shortlist_stats.get("rejected_sample") or 0),
 shortlist_rejected_agreement=int(shortlist_stats.get("rejected_agreement") or 0),
 shortlist_rejected_separation=int(shortlist_stats.get("rejected_separation") or 0),
 shortlist_rejected_probability=int(shortlist_stats.get("rejected_probability") or 0),
)

# Market-choice stage: the football brain selects interesting matches, then the
# market brain compares every supported real market instead of blindly betting
# the single primary trend.
runtime=Path(os.getenv("RUNTIME_DATA_DIR","data"))
xbet_state=Path(os.getenv("XBET_PREMATCH_STATE",str(runtime/"live"/"xbet_prematch_market.json")))
try:
 xbet_result=XBetPrematchCollector(xbet_state).collect_once(targets=[r["match"] for r in rows])
 update_prematch_status(stage="pricing", xbet_matches=len((xbet_result or {}).get("matches") or {}), xbet_refreshed=int((xbet_result or {}).get("refreshed") or 0))
except Exception as exc:
 update_prematch_status(stage="pricing", xbet_error=f"{type(exc).__name__}:{exc}")
 print("PREMATCH_XBET_MARKET_ERROR",type(exc).__name__,str(exc),flush=True)

def one(r):
 m=r["match"]; kick=float((m.meta or {}).get("scheduled_start_ts") or 0)
 market=find_prematch_market(m.home,m.away,path=xbet_state)
 picks=[]
 info=None
 odds_payload=None

 # Production priority 1: the tested full-market brain compares the real market
 # catalogue and may choose a supported high-confidence BET instead of being tied
 # to the legacy trend family. Unsupported settlement shapes stay research-only.
 if str(os.getenv("GOOL_PREMATCH_FULL_MARKET_ACTIVE","1")).lower() in {"1","true","yes","on"}:
  try:
   odds_payload=fetch_event_odds(m.provider_match_id)
   full_analysis=analyze_full_market(odds_payload,r["profile"],quality=float(r["quality"]))
   selected_rows=production_full_market_picks(
    full_analysis,
    event_id=str(m.provider_match_id),home=m.home,away=m.away,
    league=m.league or "",kickoff_ts=kick,
    min_confidence=float(os.getenv("GOOL_PREMATCH_FULL_MARKET_MIN_CONFIDENCE","70")),
   )
   if selected_rows:
    picks=[pick for pick,_meta in selected_rows]
    primary_pick,primary_meta=selected_rows[0]
    candidate_meta={
     f"{pick.market}|{pick.selection}": meta_row
     for pick,meta_row in selected_rows
    }
    info={
     **primary_meta,
     "bookmaker":primary_meta.get("bookmaker") or "Flashscore odds",
     "full_market_active":True,
     "candidate_meta":candidate_meta,
    }
    print(
     "PREMATCH_FULL_MARKET",
     m.home,"-",m.away,
     primary_pick.market,primary_pick.selection,f"@{primary_pick.odds:.2f}",
     f"p={primary_pick.model_probability:.3f}",
     f"confidence={float(primary_meta.get('full_market_confidence') or 0):.1f}",
     f"alternatives={max(0,len(selected_rows)-1)}",
     flush=True,
    )
  except Exception as exc:
   print("PREMATCH_FULL_MARKET_ERROR",m.home,"-",m.away,type(exc).__name__,str(exc),flush=True)

 # Production priority 2: preserve the existing 1xBet multi-market engine when
 # the full-market brain has no journal-safe BET.
 if not picks and market:
  raw=build_prematch_candidates(
   event_id=str(m.provider_match_id),home=m.home,away=m.away,
   profile=r["profile"],market=market,data_quality=float(r["quality"]),
  )
  for q in raw:
   picks.append(PrematchPick(
    q.event_id,q.home,q.away,q.market,q.selection,q.odds,
    q.model_probability,q.market_probability,q.data_quality,m.league or "",kick,
   ))
  info={"bookmaker":"1xBet","market_match_score":market.get("match_score"),"full_market_active":False}

 # Final fallback preserves the previous exact-trend behaviour.
 if not picks:
  try:
   if odds_payload is None:
    odds_payload=fetch_event_odds(m.provider_match_id)
   price=exact_trend_price(odds_payload,r["primary_trend"]["name"])
  except Exception:
   price=None
  if price:
   t=r["primary_trend"]
   picks=[PrematchPick(
    str(m.provider_match_id),m.home,m.away,t["name"],t["name"],
    price["best_odds"],float(t["probability"]),price["market_probability"],
    float(r["quality"]),m.league or "",kick,
   )]
   info=price
 return picks,info,r

priced=[]; meta={}
with ThreadPoolExecutor(max_workers=20) as pool:
 for ftr in as_completed([pool.submit(one,r) for r in rows]):
  picks,x,r=ftr.result()
  if not picks or not x:continue
  m=r["match"]; fs_meta=dict(m.meta or {})
  home_logo=fs.team_logo_url(str(fs_meta.get("home_team_slug") or ""),str(fs_meta.get("home_team_id") or ""))
  away_logo=fs.team_logo_url(str(fs_meta.get("away_team_slug") or ""),str(fs_meta.get("away_team_id") or ""))
  if home_logo: fs_meta["home_logo_url"]=home_logo
  if away_logo: fs_meta["away_logo_url"]=away_logo
  meta[str(m.provider_match_id)]={**x,"flashscore_meta":fs_meta}
  for p in picks:
   priced.append(p)
   kw=dict(event_id=p.event_id,home=m.home,away=m.away,league=m.league,kickoff_ts=p.kickoff_ts,trend=p.market,odds=p.odds,bookmaker=x.get("bookmaker") or "1xBet",market_probability=p.market_probability,model_probability=p.model_probability)
   append_price_snapshot(**kw); append_sqlite_snapshot(**kw,data_quality=p.data_quality)
print("MULTI_MARKET_PRICED",len(priced),flush=True)
update_prematch_status(stage="delivery", priced=len(priced))
print("PRICED_MARKET_COUNTS",dict(Counter(p.market for p in priced)),flush=True)

max_singles=max(0,int(os.getenv("GOOL_PREMATCH_MAX_SINGLES","0")))
max_doubles=max(0,int(os.getenv("GOOL_PREMATCH_MAX_DOUBLES","0")))
d=choose_delivery(
 priced,
 max_singles=None if max_singles<=0 else max_singles,
 max_doubles=None if max_doubles<=0 else max_doubles,
)
journal=Path(os.getenv("GOOL_MULTI_JOURNAL_PATH") or (Path(os.getenv("RUNTIME_DATA_DIR","data"))/"live"/"gool_multi_journal.json"))
delivered={"cards":0,"entries":0,"parlays":0}
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

update_prematch_status(
 stage="done",
 fixtures=len(fixtures),
 brain_eligible=len(rows),
 priced=len(priced),
 mode=str(d.get("mode") or "NO_BET"),
 singles=len(d.get("singles") or []),
 doubles=len(d.get("doubles") or []),
 super=bool(d.get("super")),
 delivered_cards=int(delivered.get("cards") or 0),
 delivered_entries=int(delivered.get("entries") or 0),
 delivered_parlays=int(delivered.get("parlays") or 0),
)
