from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.flashscore_odds import fetch_event_odds,exact_trend_price
from gool_bot2.full_market_brain import analyze_full_market
from gool_bot2.prematch_full_market_runtime import production_full_market_picks
from gool_bot2.v4_shadow_report import _analyse_fixtures,_trend_signals,_primary_trend,_brain_score
from gool_bot2.v4_prematch_engine import PrematchPick,choose_delivery,build_prematch_candidates,global_super_publish_pool
from gool_bot2.global_super10 import enabled as global_super_enabled,football_rows_from_picks,publish_candidates as publish_global_super_candidates,maybe_deliver_global_super10
from gool_bot2.odds_journal import append_price_snapshot,append_sqlite_snapshot
from gool_bot2.v4_prematch_delivery import emit_delivery_selection,retry_pending_prematch_deliveries
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.football_v5_shadow import append_first_snapshots, forecast_from_history
from gool_bot2.football_v5_production import production_v5_picks
from gool_bot2.football_v6_consensus_shadow import evaluate_football_v6_prematch
from gool_bot2.active_challenger_decisions import football_v6_tournament_picks, football_v6_active_picks
from gool_bot2.football_v6_flashscore_quote import flashscore_fulltime_quote, flashscore_fulltime_quotes
from gool_bot2.xbet_prematch_market import XBetPrematchCollector,find_prematch_market
from gool_bot2.prematch_status import update_prematch_status
from gool_bot2.prematch_confidence import select_confident_prematch_rows
from gool_bot2.prematch_quality import prematch_evidence_quality
from gool_bot2.prematch_team_regime import apply_team_regime
from gool_bot2.prematch_value_hunter import analysis_value_rows,select_best_value_pick,diagnose_value_rows,select_value_scan_rows
from gool_bot2.tournament_express import build_catalog, write_catalog, _priority as tournament_priority
from gool_bot2.tournament_coverage import choose_quote_coverage
from gool_bot2.v4_value_hunter_delivery import emit_value_hunter
from pathlib import Path
import os
import time

MSK=ZoneInfo("Europe/Moscow")
V5_ACTIVE=str(os.getenv("GOOL_FOOTBALL_V5_ACTIVE","1")).lower() in {"1","true","yes","on"}
V6_ACTIVE=str(os.getenv("GOOL_FOOTBALL_V6_ACTIVE","1")).lower() in {"1","true","yes","on"}
def when(p):
 return datetime.fromtimestamp(float(p.kickoff_ts),MSK).strftime("%d.%m %H:%M МСК") if p.kickoff_ts else "время н/д"
def leg(p,meta):
 x=meta[p.event_id]
 return f"{p.league or 'Турнир н/д'} | {when(p)} | {p.home} -- {p.away} | {p.selection} @ {p.odds:.2f} [{x['bookmaker']}] | p={p.model_probability:.3f} edge={p.edge:+.3f}"

fs=FlashscoreProvider(); now=datetime.now(timezone.utc).timestamp(); day=datetime.now(MSK).date()
all_daily_fixtures=[m for m in fs.scheduled_matches_for_day(0) if (m.meta or {}).get("scheduled_start_ts") and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]),MSK).date()==day]
fixtures=[m for m in all_daily_fixtures if float(m.meta["scheduled_start_ts"])>now]
print("PIPELINE_START",len(fixtures),flush=True)
# Telegram's tournament menu must not stay blank for the entire (often long)
# Brain/history+LineFeed scan. Publish today's calendar immediately.
initial_catalog,_=build_catalog(
 all_daily_fixtures,[],now_ts=now,report_date=day.isoformat(),scan_status="discovering",
)
write_catalog(initial_catalog)
print("TOURNAMENT_CALENDAR_READY",{"fixtures":initial_catalog["fixture_count"],"tournaments":len(initial_catalog["tournaments"])},flush=True)
update_prematch_status(
 stage="brain",
 fixtures=len(fixtures),
 brain_analysed=0,
 brain_sample_ge6=0,
 brain_profile_available=0,
 brain_stage1_eligible=0,
 brain_eligible=0,
 brain_failures=0,
 fusion_checked=0,
 fusion_rescue_requested=0,
 fusion_rescued=0,
 fusion_failures=0,
 shortlist=0,
 shortlist_cap=0,
 shortlist_qualified=0,
 shortlist_rejected_sample=0,
 shortlist_rejected_agreement=0,
 shortlist_rejected_separation=0,
 shortlist_rejected_probability=0,
 shortlist_rejected_quality=0,
 shortlist_rescued_quality=0,
 priced=0,
 mode="PENDING",
 singles=0,
 doubles=0,
 super=False,
 xbet_matches=0,
 xbet_refreshed=0,
 delivered_cards=0,
 delivered_entries=0,
 delivered_parlays=0,
 value_hunter_stage="waiting_for_full_market",
 value_hunter_scanned_matches=0,
 value_hunter_modeled_markets=0,
 value_hunter_high_odds_markets=0,
 value_hunter_qualified=0,
 value_hunter_candidates=0,
 value_hunter_sent=0,
 value_hunter_rejects={},
)
analysed_rows,fail=_analyse_fixtures(fs,fixtures)
# V5 SHADOW is independent of published GOOL V4. Persist only the first
# READY forecast per event, before kickoff, to prevent hindsight bias.
if str(os.getenv("GOOL_FOOTBALL_V5_SHADOW_ENABLED","1")).casefold() in {"1","true","yes","on"}:
 v5_ready=[]
 v5_states=Counter()
 for item in analysed_rows:
  m=item["match"]; v5=dict(item.get("football_v5_shadow") or {})
  v5_states[str(v5.get("status") or "UNKNOWN")]+=1
  if v5.get("status")=="READY":
   v5_ready.append({
    "event_id":str(m.provider_match_id),"league":str(m.league or ""),
    "home":m.home,"away":m.away,
    "kickoff":float((m.meta or {}).get("scheduled_start_ts") or 0),
    "v5":v5,
   })
 v5_path=Path(os.getenv("RUNTIME_DATA_DIR","data"))/"football_v5"/f"prematch_{day.isoformat()}.jsonl"
 v5_saved=append_first_snapshots(v5_path,v5_ready)
 print("FOOTBALL_V5_SHADOW",{
  "scanned":len(analysed_rows),"ready":len(v5_ready),
  "new_saved":v5_saved,"reasons":dict(v5_states),"path":str(v5_path),
 },flush=True)
stage1_rows=[
 r for r in analysed_rows
 if (r.get("football_v5_shadow") or {}).get("status")=="READY"
] if (V5_ACTIVE or V6_ACTIVE) else [r for r in analysed_rows if r.get("primary_trend")]
sample_ge6=sum(1 for r in analysed_rows if int(r.get("sample") or 0)>=6)
profile_available=sum(
 1 for r in analysed_rows
 if bool((r.get("profile") or {}).get("full_match",{}).get("available"))
)
update_prematch_status(
 stage="market_lookup",
 fixtures=len(fixtures),
 brain_analysed=len(analysed_rows),
 brain_stage1_eligible=len(stage1_rows),
 brain_sample_ge6=sample_ge6,
 brain_profile_available=profile_available,
 brain_eligible=len(stage1_rows),
 brain_failures=len(fail),
)
print(
 "BRAIN_STAGE1",
 {"fixtures":len(fixtures),"analysed":len(analysed_rows),"sample_ge6":sample_ge6,
  "profile_available":profile_available,"eligible":len(stage1_rows),"failures":len(fail)},
 flush=True,
)

# Second-stage football confirmation. Normally enrich the strongest stage-1
# candidates. If Flashscore H2H is temporarily thin, use a bounded rescue pool
# so independent FotMob/365Scores evidence can recover the pipeline instead of
# allowing one source to reduce the whole day to zero bets.
base_fusion_limit=max(0,min(80,int(os.getenv("GOOL_PREMATCH_FUSION_LIMIT","40"))))
rescue_trigger=max(0,int(os.getenv("GOOL_PREMATCH_FUSION_RESCUE_TRIGGER","20")))
fusion_limit=80 if len(stage1_rows)<rescue_trigger else base_fusion_limit
fusion_limit=max(base_fusion_limit,min(80,fusion_limit))
fusion_workers=max(2,min(12,int(os.getenv("GOOL_PREMATCH_FUSION_WORKERS","6"))))

stage1_ids={str(r["match"].provider_match_id) for r in stage1_rows}
rescue_pool=[r for r in analysed_rows if str(r["match"].provider_match_id) not in stage1_ids]
rescue_pool.sort(
 key=lambda r:(
  bool((r.get("profile") or {}).get("full_match",{}).get("available")),
  int(r.get("sample") or 0),
  -float((r["match"].meta or {}).get("scheduled_start_ts") or 0),
 ),
 reverse=True,
)
fusion_rows=list(stage1_rows[:fusion_limit])
if len(fusion_rows)<fusion_limit:
 fusion_rows.extend(rescue_pool[:fusion_limit-len(fusion_rows)])
rescue_requested=sum(1 for r in fusion_rows if str(r["match"].provider_match_id) not in stage1_ids)
update_prematch_status(
 stage="fusion",
 fusion_checked=0,
 fusion_rescue_requested=rescue_requested,
 fusion_rescued=0,
 fusion_failures=0,
)
print(
 "PREMATCH_FUSION_PLAN",
 {"stage1":len(stage1_rows),"limit":fusion_limit,"rescue_requested":rescue_requested},
 flush=True,
)
if fusion_rows:
 def enrich(r):
  try:
   local_fs=FlashscoreProvider(); fusion=PrematchDataFusion(local_fs); m=r["match"]
   history=fusion.context(m,limit=10)
   profile=build_prematch_goal_profile({"match":{"home":m.home,"away":m.away},"prematch_context":history})
   v5=forecast_from_history(
    home=m.home,away=m.away,
    kickoff=float((m.meta or {}).get("scheduled_start_ts") or 0),context=history,
   )
   if v5.get("status") != "READY":
    v5=r.get("football_v5_shadow") or v5
   samples=[int((profile.get(k) or {}).get("pair_sample") or 0) for k in ("first_half","second_half","full_match")]
   sample=max(samples or [0])
   quality=prematch_evidence_quality(profile, source_coverage=history.get("source_coverage") or {})
   trends=_trend_signals(profile,quality); primary=_primary_trend(trends)
   return {**r,"profile":profile,"football_v5_shadow":v5,"sample":sample,"quality":quality,"brain_score":_brain_score(profile,quality),
           "trends":trends,"primary_trend":primary,"sources":history.get("sources") or [],
           "source_coverage":history.get("source_coverage") or {}},None
  except Exception as exc:
   return r,f"FUSION_{type(exc).__name__}"
 enriched=[]; fusion_fail=0
 with ThreadPoolExecutor(max_workers=fusion_workers) as pool:
  for fut in as_completed([pool.submit(enrich,r) for r in fusion_rows]):
   row,err=fut.result(); enriched.append(row); fusion_fail+=int(bool(err))
 by_id={str(r["match"].provider_match_id):r for r in enriched}
 # Keep every original stage-1 candidate and add any rescue fixture that becomes
 # evidence-backed after Fusion. Do not discard non-enriched stage-1 rows.
 rows=[by_id.get(str(r["match"].provider_match_id),r) for r in stage1_rows]
 existing_ids={str(r["match"].provider_match_id) for r in rows}
 rescued=[]
 for r in enriched:
  event_id=str(r["match"].provider_match_id)
  if event_id in existing_ids or (not (V5_ACTIVE or V6_ACTIVE) and not r.get("primary_trend")):
   continue
  rows.append(r); existing_ids.add(event_id); rescued.append(r)
 rows=[
  r for r in rows if (r.get("football_v5_shadow") or {}).get("status")=="READY"
 ] if (V5_ACTIVE or V6_ACTIVE) else [r for r in rows if r.get("primary_trend")]
 rows.sort(key=lambda r:(float(r.get("brain_score") or 0),float(r.get("quality") or 0)),reverse=True)
 print(
  "PREMATCH_FUSION",
  {"checked":len(fusion_rows),"kept":len(rows),"rescued":len(rescued),"fail":fusion_fail},
  flush=True,
 )
 update_prematch_status(
  stage="market_lookup",
  brain_stage1_eligible=len(stage1_rows),
  fusion_checked=len(fusion_rows),
  fusion_rescue_requested=rescue_requested,
  fusion_rescued=len(rescued),
  fusion_failures=fusion_fail,
 )

# Stage 2: once the cheap scan has produced candidates, normalize every
# surviving row onto the same evidence-quality scale. Enriched rows naturally
# score higher when Fusion supplied venue/H2H/multiple sources, while rows that
# did not need expensive enrichment are no longer left at artificial quality=1.
normalized_rows=[]
for r in rows:
 evidence_quality=prematch_evidence_quality(
  r["profile"],
  source_coverage=r.get("source_coverage") or {},
 )
 trends=_trend_signals(r["profile"],evidence_quality)
 primary=_primary_trend(trends)
 if primary is None and not (V5_ACTIVE or V6_ACTIVE):
  continue
 normalized_rows.append({
  **r,
  "quality":evidence_quality,
  "brain_score":_brain_score(r["profile"],evidence_quality),
  "trends":trends,
  "primary_trend":primary,
 })
rows=normalized_rows
rows.sort(key=lambda r:(float(r.get("brain_score") or 0),float(r.get("quality") or 0)),reverse=True)

print("BRAIN_ELIGIBLE_AFTER_FUSION",len(rows),flush=True)
print("PRIMARY_TREND_COUNTS",dict(Counter((r.get("primary_trend") or {}).get("name","V5_DIRECT") for r in rows)),flush=True)

brain_eligible_total=len(rows)
value_scan_rows=[] if (V5_ACTIVE or V6_ACTIVE) else select_value_scan_rows(rows)
if V5_ACTIVE or V6_ACTIVE:
 cap=max(1,int(os.getenv("GOOL_FOOTBALL_V5_SHORTLIST_CAP","240")))
 rows.sort(key=lambda r:(
  float((r.get("football_v5_shadow") or {}).get("quality") or 0),
  float((r.get("football_v5_shadow") or {}).get("opponent_strength_coverage") or 0),
  float(r.get("quality") or 0),
 ),reverse=True)
 qualified=len(rows)
 # Large tournament coverage is independent of the global daily shortlist:
 # every READY Champions/Europa/Conference/major international fixture can
 # be priced for the all-matches coupon, even if outside the top cap.
 whole_pool=list(rows)
 rows=rows[:cap]
 selected_ids={str(r["match"].provider_match_id) for r in rows}
 promoted=[]
 for r in whole_pool:
  event_id=str(r["match"].provider_match_id)
  if event_id not in selected_ids and tournament_priority(str(r["match"].league or "")) < 10:
   promoted.append(r)
   selected_ids.add(event_id)
 rows.extend(promoted)
 print("TOURNAMENT_PRICE_PROMOTION",len(promoted),"shortlist",len(rows),flush=True)
 shortlist_stats={"cap":cap,"qualified":qualified,"rejected_quality":0,"mode":"FOOTBALL_V6_ACTIVE" if V6_ACTIVE else "FOOTBALL_V5_ACTIVE"}
 print("FOOTBALL_V6_ACTIVE_SHORTLIST" if V6_ACTIVE else "FOOTBALL_V5_ACTIVE_SHORTLIST",{
  "eligible":qualified,"price_shortlist":len(rows),"cap":cap,
 },flush=True)
else:
 rows,shortlist_stats=select_confident_prematch_rows(rows)
# Reserve coupon analysis is independent of whether football history has
# enough observations for V6. Every tournament fixture must have its genuine
# bookmaker-quote families checked, even if stage1/cap excluded the event.
selected_ids={str(r["match"].provider_match_id) for r in rows}
coverage_only_matches=[m for m in fixtures if tournament_priority(str(m.league or "")) < 10
                       and str(m.provider_match_id) not in selected_ids]
print("TOURNAMENT_COVERAGE_EXTRA",len(coverage_only_matches),flush=True)
print("PREMATCH_SHORTLIST",shortlist_stats,flush=True)
print("VALUE_HUNTER_POOL",{"selected":len(value_scan_rows),"normal_shortlist":len(rows)},flush=True)
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
 shortlist_rejected_quality=int(shortlist_stats.get("rejected_quality") or 0),
 shortlist_rescued_quality=int(shortlist_stats.get("rescued_quality") or 0),
 value_hunter_scan_pool=len(value_scan_rows),
)

# Market-choice stage: the football brain selects interesting matches, then the
# market brain compares every supported real market instead of blindly betting
# the single primary trend.
runtime=Path(os.getenv("RUNTIME_DATA_DIR","data"))
xbet_state=Path(os.getenv("XBET_PREMATCH_STATE",str(runtime/"live"/"xbet_prematch_market.json")))
try:
 xbet_result=XBetPrematchCollector(xbet_state).collect_once(targets=[r["match"] for r in rows]+coverage_only_matches)
 update_prematch_status(stage="pricing", xbet_matches=len((xbet_result or {}).get("matches") or {}), xbet_refreshed=int((xbet_result or {}).get("refreshed") or 0))
except Exception as exc:
 update_prematch_status(stage="pricing", xbet_error=f"{type(exc).__name__}:{exc}")
 print("PREMATCH_XBET_MARKET_ERROR",type(exc).__name__,str(exc),flush=True)

def one(r):
 m=r["match"]; kick=float((m.meta or {}).get("scheduled_start_ts") or 0)
 market=find_prematch_market(m.home,m.away,path=xbet_state,kickoff_ts=kick)
 picks=[]
 info=None
 odds_payload=None
 value_pick=None
 value_diag={"modeled_markets":0,"high_odds_markets":0,"qualified":0,"rejects":{}}
 if V5_ACTIVE or V6_ACTIVE:
  # Never silently publish a V4 pick when V5/V6 rejects the fixture.
  forecast=r.get("football_v5_shadow") or {}
  try:
   odds_payload=fetch_event_odds(m.provider_match_id)
   full_analysis=analyze_full_market(odds_payload,r["profile"],quality=float(r["quality"]))
  except Exception as exc:
   full_analysis=None
   print("FOOTBALL_V5_FULL_MARKET_UNAVAILABLE",m.home,"-",m.away,type(exc).__name__,flush=True)
  picks,info=production_v5_picks(
   forecast,event_id=str(m.provider_match_id),home=m.home,away=m.away,
   league=m.league or "",kickoff_ts=kick,
   xbet=market,full_analysis=full_analysis,
  )
  if V6_ACTIVE or str(os.getenv("GOOL_FOOTBALL_V6_SHADOW_ENABLED","1")).casefold() in {"1","true","yes","on"}:
   try:
    v6_quote=market or flashscore_fulltime_quote(odds_payload)
    quote_source="1xbet" if market else "flashscore" if v6_quote else "none"
    quote_offers=([(market,"1xbet")] if market else [])+[
     (item,"flashscore") for item in flashscore_fulltime_quotes(odds_payload)
    ]
    coverage,coverage_source,coverage_book,coverage_diag=choose_quote_coverage(
     quote_offers,event_id=str(m.provider_match_id),home=m.home,away=m.away,
     league=m.league or "",kickoff_ts=kick,
    )
    r["tournament_coverage_candidates"]=coverage
    r["tournament_coverage_quote_source"]=coverage_source
    r["tournament_coverage_bookmaker"]=coverage_book
    r["tournament_coverage_diagnostic"]=coverage_diag
    v6_shadow=evaluate_football_v6_prematch(
     forecast,r.get("profile") or {},v6_quote,
     event_id=str(m.provider_match_id),active_picks=picks,
    )
    v6_shadow["quote_source"]=(
     "1xbet" if market else
     "flashscore" if v6_quote else "none"
    )
    v6_shadow["bookmaker"]=str((v6_quote or {}).get("bookmaker") or v6_shadow["quote_source"])
    r={**r,"football_v6_shadow":v6_shadow}
    print("FOOTBALL_V6_SHADOW",m.home,"-",m.away,
     "state",v6_shadow.get("status"),
     "checked",v6_shadow.get("quoted_markets_checked",0),
     "qualified",v6_shadow.get("qualified_shadow_markets",0),
     "reason",v6_shadow.get("reason",""),flush=True)
   except Exception as exc:
    print("FOOTBALL_V6_SHADOW_WAIT",m.home,"-",m.away,
     type(exc).__name__,str(exc),flush=True)
  if V6_ACTIVE:
   # In V6 mode, DO NOT fall back to old V5 picks after V6 declines.
   # The same real bookmaker quote is mandatory.
   picks,info=football_v6_active_picks(
    r.get("football_v6_shadow") or {},
    event_id=str(m.provider_match_id),home=m.home,away=m.away,
    league=m.league or "",kickoff_ts=kick,
   )
   tournament_alternatives,tournament_meta=football_v6_tournament_picks(
    r.get("football_v6_shadow") or {},
    event_id=str(m.provider_match_id),home=m.home,away=m.away,
    league=m.league or "",kickoff_ts=kick,
   )
   r["tournament_candidates"]=tournament_alternatives
   r["tournament_diagnostic"]={
    "reason":str((info or {}).get("reject") or (r.get("football_v6_shadow") or {}).get("reason") or ""),
    "quoted_markets_checked":int((r.get("football_v6_shadow") or {}).get("quoted_markets_checked") or 0),
    "qualified_active":len(tournament_alternatives),
    "qualified_strict":len([p for p in tournament_alternatives if f"{p.market}|{p.selection}" not in set(tournament_meta.get("tournament_value_promotions") or [])]),
    "tournament_value_promotions":list(tournament_meta.get("tournament_value_promotions") or []),
    "rejection_reasons":dict((r.get("football_v6_shadow") or {}).get("rejection_reasons") or {}),
    "quote_source":str((r.get("football_v6_shadow") or {}).get("quote_source") or ""),
    "coverage_source":str(r.get("tournament_coverage_quote_source") or ""),
    "coverage_bookmaker":str(r.get("tournament_coverage_bookmaker") or ""),
    "coverage_diagnostic":dict(r.get("tournament_coverage_diagnostic") or {}),
   }
   print("FOOTBALL_V6_TOURNAMENT",m.home,"-",m.away,
    "alternatives",len(tournament_alternatives),"quoted",r["tournament_diagnostic"]["quoted_markets_checked"],flush=True)
   print("FOOTBALL_V6_ACTIVE",m.home,"-",m.away,
    "status","BET" if picks else "WAIT",
    "reject",(info or {}).get("reject",""),
    "odds",f"{picks[0].odds:.2f}" if picks else "-",flush=True)
  # V6 ACTIVE overwrites V5 shortlist. Evaluate the scenario hypotheses
  # against the ACTUAL final V6 picks and persist in V6 first-snapshot
  # research archive, including WAIT/no-standalone-pick fixtures.
  try:
   from gool_bot2.prematch_strategy_scenarios import evaluate_prematch_scenarios
   # Unlike FT goals, a pre-kickoff first-half lambda must come from
   # the FIRST_HALF historical segment, not an arbitrary 45% of FT.
   research_forecast=dict(forecast)
   historical_first_half=dict(((r.get("profile") or {}).get("first_half") or {}))
   research_forecast["first_half"]={
    "home_lambda":historical_first_half.get("home_expected_goals"),
    "away_lambda":historical_first_half.get("away_expected_goals"),
   }
   strategy_scenarios=evaluate_prematch_scenarios(
    "football", football_forecast=research_forecast,
    football_quote=market or flashscore_fulltime_quote(odds_payload),
    qualified_picks=picks,
   )
   r["prematch_scenarios"]=strategy_scenarios
   if isinstance(r.get("football_v6_shadow"),dict):
    r["football_v6_shadow"]["prematch_scenarios"]=strategy_scenarios
   info={**dict(info or {}),"prematch_scenarios":strategy_scenarios}
   if not picks:
    from gool_bot2.prematch_scenario_active import (
     recover_football_v6_prematch,recover_football_first_half_goal,
    )
    restored,restored_meta=([],{})
    if V6_ACTIVE:
     restored,restored_meta=recover_football_v6_prematch(
      strategy_scenarios,tournament_candidates=r.get("tournament_candidates") or [],
      shadow=dict(r.get("football_v6_shadow") or {}),
     )
    if not restored:
     restored,restored_meta=recover_football_first_half_goal(
      strategy_scenarios,pregame_profile=dict(r.get("profile") or {}),
      quote=market,football_quality=float(forecast.get("quality") or 0.0),
      event_id=str(m.provider_match_id),home=m.home,away=m.away,
      league=m.league or "",kickoff_ts=kick,
     )
    if restored:
     picks=restored
     info={**info,**restored_meta}
     r["prematch_scenario_active"]=dict(restored_meta)
     print("GOOL_FOOTBALL_PREMATCH_SCENARIO_ACTIVE",m.home,"-",m.away,
      "scenarios",restored_meta.get("scenario_ids",[]),
      "selection",picks[0].selection,"odd",picks[0].odds,flush=True)
   print("GOOL_FOOTBALL_PREMATCH_SCENARIOS",m.home,"-",m.away,
    "matched",strategy_scenarios.get("matched_count",0),
    "qualified",strategy_scenarios.get("qualified_pattern_count",0),flush=True)
  except Exception as exc:
   print("GOOL_FOOTBALL_PREMATCH_SCENARIOS_ERROR",m.home,"-",m.away,type(exc).__name__,flush=True)
  if picks:
   print("FOOTBALL_V6_PREMATCH" if V6_ACTIVE else "FOOTBALL_V5_PREMATCH",m.home,"-",m.away,
    "candidates",len(picks),"best",picks[0].market,picks[0].selection,
    f"@{picks[0].odds:.2f}",f"p={picks[0].model_probability:.3f}",
    "opp_cov",forecast.get("opponent_strength_coverage"),flush=True)
  else:
   print("FOOTBALL_V6_WAIT" if V6_ACTIVE else "FOOTBALL_V5_WAIT",m.home,"-",m.away,info.get("reject"),flush=True)
  return picks,info,r,None,value_diag

 # Production priority 1: the tested full-market brain compares the real market
 # catalogue and may choose a supported high-confidence BET instead of being tied
 # to the legacy trend family. Unsupported settlement shapes stay research-only.
 if str(os.getenv("GOOL_PREMATCH_FULL_MARKET_ACTIVE","1")).lower() in {"1","true","yes","on"}:
  try:
   odds_payload=fetch_event_odds(m.provider_match_id)
   full_analysis=analyze_full_market(odds_payload,r["profile"],quality=float(r["quality"]))

   # VALUE HUNTER examines every modeled FULL_TIME market, including prices
   # intentionally too large for the ordinary PREMATCH product.
   hunter_rows=[]
   for hp,hm in analysis_value_rows(
    full_analysis,
    event_id=str(m.provider_match_id),home=m.home,away=m.away,
    league=m.league or "",kickoff_ts=kick,
   ):
    before=float(hp.model_probability)
    hq,regime=apply_team_regime(hp,r["profile"])
    hmeta={**hm,"probability_before_regime":before}
    if regime.get("tags"):
     hmeta["team_regime"]=regime
    hunter_rows.append((hq,hmeta))
   value_diag=diagnose_value_rows(hunter_rows)
   value_pick=select_best_value_pick(hunter_rows)
   if value_pick:
    vp,vm=value_pick
    print(
     "VALUE_HUNTER_CANDIDATE",m.home,"-",m.away,
     vp.market,vp.selection,f"@{vp.odds:.2f}",
     f"p={vp.model_probability:.3f}",
     f"edge={vp.edge*100:+.1f}pp",
     f"ev={vp.expected_value*100:+.1f}%",
     f"score={float(vm.get('value_score') or 0):.1f}",
     flush=True,
    )

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

 if picks:
  adjusted=[]
  candidate_meta=dict((info or {}).get("candidate_meta") or {})
  for p in picks:
   q,regime=apply_team_regime(p,r["profile"])
   adjusted.append(q)
   if regime.get("tags"):
    key=f"{q.market}|{q.selection}"
    specific=dict(candidate_meta.get(key) or {})
    specific["team_regime"]=regime
    candidate_meta[key]=specific
    print(
     "PREMATCH_TEAM_REGIME",m.home,"-",m.away,
     q.market,q.selection,
     f"adj={float(regime.get('adjustment_pp') or 0):+.1f}pp",
     ",".join(regime.get("tags") or []),
     flush=True,
    )
  picks=adjusted
  if info is not None and candidate_meta:
   info={**info,"candidate_meta":candidate_meta}
 return picks,info,r,value_pick,value_diag

def value_only(r):
 m=r["match"]; kick=float((m.meta or {}).get("scheduled_start_ts") or 0)
 value_pick=None
 value_diag={"modeled_markets":0,"high_odds_markets":0,"qualified":0,"rejects":{}}
 try:
  odds_payload=fetch_event_odds(m.provider_match_id)
  full_analysis=analyze_full_market(odds_payload,r["profile"],quality=float(r["quality"]))
  hunter_rows=[]
  for hp,hm in analysis_value_rows(
   full_analysis,
   event_id=str(m.provider_match_id),home=m.home,away=m.away,
   league=m.league or "",kickoff_ts=kick,
  ):
   before=float(hp.model_probability)
   hq,regime=apply_team_regime(hp,r["profile"])
   hmeta={**hm,"probability_before_regime":before}
   if regime.get("tags"):
    hmeta["team_regime"]=regime
   hunter_rows.append((hq,hmeta))
  value_diag=diagnose_value_rows(hunter_rows)
  value_pick=select_best_value_pick(hunter_rows)
  if value_pick:
   vp,vm=value_pick
   print(
    "VALUE_HUNTER_CANDIDATE_WIDE",m.home,"-",m.away,
    vp.market,vp.selection,f"@{vp.odds:.2f}",
    f"p={vp.model_probability:.3f}",
    f"edge={vp.edge*100:+.1f}pp",
    f"ev={vp.expected_value*100:+.1f}%",
    f"score={float(vm.get('value_score') or 0):.1f}",
    flush=True,
   )
 except Exception as exc:
  print("VALUE_HUNTER_WIDE_ERROR",m.home,"-",m.away,type(exc).__name__,str(exc),flush=True)
 return r,value_pick,value_diag

priced=[]; meta={}; value_candidates=[]
tournament_priced=[]
tournament_coverage_priced=[]
# Fixtures failing the early history/profile gate must remain visible in
# the tournament report; otherwise 0/7 gives no actionable explanation.
tournament_diagnostics={str(m.provider_match_id):{"reason":"not_priced"} for m in fixtures}
football_v6_rows=[]
value_diag_total={"scanned_matches":0,"modeled_markets":0,"high_odds_markets":0,"qualified":0,"rejects":Counter()}
def _publish_tournament_progress():
 partial,_=build_catalog(
  all_daily_fixtures,(tournament_priced if V6_ACTIVE else priced),
  coverage_picks=tournament_coverage_priced,
  now_ts=time.time(),report_date=day.isoformat(),
  scan_status="pricing",processed_count=value_diag_total["scanned_matches"],
  price_targets=len(rows),already_shrunk=V6_ACTIVE,
  fixture_diagnostics=tournament_diagnostics,
 )
 write_catalog(partial)
 print("TOURNAMENT_PRICING_PROGRESS",{
  "processed":value_diag_total["scanned_matches"],"targets":len(rows),
  "priced_events":len({p.event_id for p in priced}),
 },flush=True)
# Tell users which phase is active even before the first successful price.
_publish_tournament_progress()
with ThreadPoolExecutor(max_workers=20) as pool:
 for ftr in as_completed([pool.submit(one,r) for r in rows]):
  picks,x,r,value_pick,value_diag=ftr.result()
  value_diag_total["scanned_matches"]+=1
  value_diag_total["modeled_markets"]+=int(value_diag.get("modeled_markets") or 0)
  value_diag_total["high_odds_markets"]+=int(value_diag.get("high_odds_markets") or 0)
  value_diag_total["qualified"]+=int(value_diag.get("qualified") or 0)
  value_diag_total["rejects"].update(value_diag.get("rejects") or {})
  m=r["match"]; fs_meta=dict(m.meta or {})
  tournament_priced.extend(r.get("tournament_candidates") or [])
  tournament_coverage_priced.extend(r.get("tournament_coverage_candidates") or [])
  if V6_ACTIVE:
   tournament_diagnostics[str(m.provider_match_id)]=dict(r.get("tournament_diagnostic") or {"reason":"no_v6_diagnostic"})
  v6_shadow=r.get("football_v6_shadow")
  if isinstance(v6_shadow,dict):
   football_v6_rows.append({
    "event_id":str(m.provider_match_id),
    "home":m.home,"away":m.away,"league":m.league or "",
    "kickoff":float((m.meta or {}).get("scheduled_start_ts") or 0),
    "v6":v6_shadow,
   })
  home_logo=fs.team_logo_url(str(fs_meta.get("home_team_slug") or ""),str(fs_meta.get("home_team_id") or ""))
  away_logo=fs.team_logo_url(str(fs_meta.get("away_team_slug") or ""),str(fs_meta.get("away_team_id") or ""))
  if home_logo: fs_meta["home_logo_url"]=home_logo
  if away_logo: fs_meta["away_logo_url"]=away_logo
  if V6_ACTIVE and r.get("tournament_candidates"):
   # Independent tournament legs can exist even when the ordinary standalone
   # candidate is absent. Preserve the verified bookmaker and crests so the
   # complete coupon doesn't show a blank source or pretend to be priced by GOOL.
   quote_source=str((r.get("football_v6_shadow") or {}).get("quote_source") or "")
   meta[str(m.provider_match_id)]={
    "bookmaker":"1xBet" if quote_source=="1xbet" else "Flashscore odds" if quote_source=="flashscore" else "",
    "flashscore_meta":fs_meta,
   }
  if r.get("tournament_coverage_candidates") and not r.get("tournament_candidates"):
   quote_source=str(r.get("tournament_coverage_quote_source") or "")
   meta[str(m.provider_match_id)]={
    "bookmaker":str(r.get("tournament_coverage_bookmaker") or ("1xBet" if quote_source=="1xbet" else "Flashscore odds")),
    "flashscore_meta":fs_meta,
   }
  if value_pick:
   vp,vm=value_pick
   value_candidates.append((vp,{**vm,"flashscore_meta":fs_meta}))
  if not picks or not x:
   if value_diag_total["scanned_matches"]%12==0:
    _publish_tournament_progress()
   continue
  meta[str(m.provider_match_id)]={**x,"flashscore_meta":fs_meta}
  for p in picks:
   priced.append(p)
   kw=dict(event_id=p.event_id,home=m.home,away=m.away,league=m.league,kickoff_ts=p.kickoff_ts,trend=p.market,odds=p.odds,bookmaker=x.get("bookmaker") or "1xBet",market_probability=p.market_probability,model_probability=p.model_probability)
   append_price_snapshot(**kw); append_sqlite_snapshot(**kw,data_quality=p.data_quality)
  if value_diag_total["scanned_matches"]%12==0:
   _publish_tournament_progress()
# Dedicated real-quote scan for the remainder of today's tournament slate.
# These matches were dropped by the independent-model shortage or shortlist,
# not by the bookmaker's ability to quote the game.
if coverage_only_matches:
 def collect_reserve(m):
  kick=float((m.meta or {}).get("scheduled_start_ts") or 0)
  market=find_prematch_market(m.home,m.away,path=xbet_state,kickoff_ts=kick)
  quote_offers=([(market,"1xbet")] if market else [])
  try:
   odds=fetch_event_odds(m.provider_match_id)
   quote_offers.extend((item,"flashscore") for item in flashscore_fulltime_quotes(odds))
  except Exception as exc:
   print("TOURNAMENT_COVERAGE_ODDS_MISSING",m.home,m.away,type(exc).__name__,flush=True)
  out,source,book,stats=choose_quote_coverage(
   quote_offers,event_id=str(m.provider_match_id),
   home=m.home,away=m.away,league=m.league or "",kickoff_ts=kick,
  )
  return m,out,source,book,stats
 with ThreadPoolExecutor(max_workers=min(8,len(coverage_only_matches))) as coverage_pool:
  for fut in as_completed([coverage_pool.submit(collect_reserve,m) for m in coverage_only_matches]):
   m,candidates,source,book,stats=fut.result()
   tournament_coverage_priced.extend(candidates)
   event_id=str(m.provider_match_id)
   tournament_diagnostics[event_id]={
    "reason":"coverage_only_no_v6_profile",
    "quoted_markets_checked":len(candidates),
    "qualified_active":0,
    "coverage_candidates":len(candidates),
    "coverage_bookmaker":book,
    "coverage_source":source,
    "coverage_diagnostic":stats,
   }
   fs_meta=dict(m.meta or {})
   home_logo=fs.team_logo_url(str(fs_meta.get("home_team_slug") or ""),str(fs_meta.get("home_team_id") or ""))
   away_logo=fs.team_logo_url(str(fs_meta.get("away_team_slug") or ""),str(fs_meta.get("away_team_id") or ""))
   if home_logo:fs_meta["home_logo_url"]=home_logo
   if away_logo:fs_meta["away_logo_url"]=away_logo
   if candidates:
    meta[event_id]={"bookmaker":book or ("1xBet" if source=="1xbet" else "Flashscore odds"),
                    "flashscore_meta":fs_meta}
   print("TOURNAMENT_COVERAGE_RESERVE",m.home,m.away,
    "real_quotes",len(candidates),"books_checked",stats["books_checked"],
    "all_candidates",stats["all_candidates"],"safe_candidates",stats["safe_candidates"],flush=True)
# Save the last candidates even if the total was not a multiple of 12.
_publish_tournament_progress()

# Immutable pre-kickoff V6 comparison records; these never send Telegram.
if football_v6_rows:
 v6_path=runtime/"football_v6"/f"prematch_{day.isoformat()}.jsonl"
 v6_saved=append_first_snapshots(v6_path,football_v6_rows)
 print("FOOTBALL_V6_SHADOW_SUMMARY",{
  "evaluated":len(football_v6_rows),"first_snapshots_saved":v6_saved,
  "qualified":sum(int((r.get("v6") or {}).get("qualified_shadow_markets") or 0) for r in football_v6_rows),
  "path":str(v6_path),
 },flush=True)

# VALUE scans a wider post-Fusion pool without widening ordinary PREMATCH.
normal_ids={str(r["match"].provider_match_id) for r in rows}
value_extra_rows=[r for r in value_scan_rows if str(r["match"].provider_match_id) not in normal_ids]
if value_extra_rows:
 with ThreadPoolExecutor(max_workers=min(12,len(value_extra_rows))) as value_pool:
  for ftr in as_completed([value_pool.submit(value_only,r) for r in value_extra_rows]):
   r,value_pick,value_diag=ftr.result()
   value_diag_total["scanned_matches"]+=1
   value_diag_total["modeled_markets"]+=int(value_diag.get("modeled_markets") or 0)
   value_diag_total["high_odds_markets"]+=int(value_diag.get("high_odds_markets") or 0)
   value_diag_total["qualified"]+=int(value_diag.get("qualified") or 0)
   value_diag_total["rejects"].update(value_diag.get("rejects") or {})
   if value_pick:
    m=r["match"]; fs_meta=dict(m.meta or {})
    home_logo=fs.team_logo_url(str(fs_meta.get("home_team_slug") or ""),str(fs_meta.get("home_team_id") or ""))
    away_logo=fs.team_logo_url(str(fs_meta.get("away_team_slug") or ""),str(fs_meta.get("away_team_id") or ""))
    if home_logo: fs_meta["home_logo_url"]=home_logo
    if away_logo: fs_meta["away_logo_url"]=away_logo
    vp,vm=value_pick
    value_candidates.append((vp,{**vm,"flashscore_meta":fs_meta}))

print("FOOTBALL_BRAIN_ACTIVE","V6" if V6_ACTIVE else "V5" if (V5_ACTIVE or V6_ACTIVE) else "V4",flush=True)
print("MULTI_MARKET_PRICED",len(priced),flush=True)
print("VALUE_HUNTER_SCAN",{
 "scanned_matches":value_diag_total["scanned_matches"],
 "modeled_markets":value_diag_total["modeled_markets"],
 "high_odds_markets":value_diag_total["high_odds_markets"],
 "qualified":value_diag_total["qualified"],
 "rejects":dict(value_diag_total["rejects"]),
},flush=True)
update_prematch_status(
 stage="delivery",
 priced=len(priced),
 value_hunter_stage="scanned",
 value_hunter_scan_pool=len(value_scan_rows),
 value_hunter_scanned_matches=int(value_diag_total["scanned_matches"]),
 value_hunter_modeled_markets=int(value_diag_total["modeled_markets"]),
 value_hunter_high_odds_markets=int(value_diag_total["high_odds_markets"]),
 value_hunter_qualified=int(value_diag_total["qualified"]),
 value_hunter_rejects=dict(value_diag_total["rejects"]),
)
print("PRICED_MARKET_COUNTS",dict(Counter(p.market for p in priced)),flush=True)

max_singles=max(0,int(os.getenv("GOOL_PREMATCH_MAX_SINGLES","0")))
max_doubles=max(0,int(os.getenv("GOOL_PREMATCH_MAX_DOUBLES","0")))
global_super_on=global_super_enabled()
football_super_pool=global_super_publish_pool(priced)
if global_super_on:
 published_global_football=publish_global_super_candidates(
  "football",
  football_rows_from_picks(football_super_pool,meta),
 )
 print("GLOBAL_SUPER10_POOL football",published_global_football,"priced",len(priced),"safe_publish_pool",len(football_super_pool),flush=True)
else:
 published_global_football=0
d=choose_delivery(
 priced,
 max_singles=None if max_singles<=0 else max_singles,
 max_doubles=None if max_doubles<=0 else max_doubles,
 include_super=not global_super_on,
 v6_active=V6_ACTIVE,
)
# Build full-tournament coupons from the ENTIRE today's fixture universe,
# not merely from singles or the strongest two matches in the league.
catalog,tournament_tickets=build_catalog(
 all_daily_fixtures,(tournament_priced if V6_ACTIVE else priced),
 coverage_picks=tournament_coverage_priced,
 now_ts=time.time(),report_date=day.isoformat(),
 scan_status="complete",processed_count=len(rows),price_targets=len(rows),
 already_shrunk=V6_ACTIVE,fixture_diagnostics=tournament_diagnostics,
)
write_catalog(catalog)
d["tournaments"]=tournament_tickets
print("TOURNAMENT_EXPRESS",{"fixtures":catalog["fixture_count"],"leagues":len(catalog["tournaments"]),"complete_tickets":len(tournament_tickets),"v6_independent_candidates":len(tournament_priced),"bookmaker_reserves":len(tournament_coverage_priced)},flush=True)
journal=Path(os.getenv("GOOL_MULTI_JOURNAL_PATH") or (Path(os.getenv("RUNTIME_DATA_DIR","data"))/"live"/"gool_multi_journal.json"))
value_delivered=emit_value_hunter(value_candidates,journal)
print("VALUE_HUNTER_DELIVERY",value_delivered,"candidates",len(value_candidates),flush=True)

delivered={"cards":0,"entries":0,"parlays":0}
if str(os.getenv("GOOL_PREMATCH_DELIVER","0")).lower() in {"1","true","yes","on"}:
 retried=retry_pending_prematch_deliveries(journal)
 if retried:
  print("PREMATCH_RETRY",{"cards":retried},"journal",journal,flush=True)
 delivered=emit_delivery_selection(d,meta,journal)
 print("PREMATCH_DELIVERY",delivered,"journal",journal,flush=True)
global_super_delivery=maybe_deliver_global_super10(
 delivery_enabled=str(os.getenv("GOOL_PREMATCH_DELIVER","0")).lower() in {"1","true","yes","on"}
) if global_super_on else {"status":"disabled"}
print("GLOBAL_SUPER10",global_super_delivery,flush=True)
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
 brain_eligible=brain_eligible_total,
 shortlist=len(rows),
 priced=len(priced),
 mode=str(d.get("mode") or "NO_BET"),
 singles=len(d.get("singles") or []),
 doubles=len(d.get("doubles") or []),
 super=bool(d.get("super")),
 delivered_cards=int(delivered.get("cards") or 0),
 delivered_entries=int(delivered.get("entries") or 0),
 delivered_parlays=int(delivered.get("parlays") or 0),
 global_super10_status=str(global_super_delivery.get("status") or ""),
 global_super10_football_pool=int(published_global_football),
 value_hunter_scan_pool=len(value_scan_rows),
 value_hunter_candidates=len(value_candidates),
 value_hunter_sent=int(value_delivered.get("entries") or 0),
 value_hunter_scanned_matches=int(value_diag_total["scanned_matches"]),
 value_hunter_modeled_markets=int(value_diag_total["modeled_markets"]),
 value_hunter_high_odds_markets=int(value_diag_total["high_odds_markets"]),
 value_hunter_qualified=int(value_diag_total["qualified"]),
 value_hunter_rejects=dict(value_diag_total["rejects"]),
)
