from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone,timedelta
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.flashscore_odds import fetch_event_odds,exact_trend_price
from gool_bot2.v4_shadow_report import _analyse_fixtures
from gool_bot2.v4_prematch_engine import PrematchPick,rank_prematch_for_delivery,build_super_accumulator
fs=FlashscoreProvider(); now=datetime.now(timezone.utc).timestamp(); tz=timezone(timedelta(hours=3)); day=datetime.now(tz).date()
fixtures=[m for m in fs.scheduled_matches_for_day(0) if (m.meta or {}).get("scheduled_start_ts") and float((m.meta or {}).get("scheduled_start_ts"))>now and datetime.fromtimestamp(float((m.meta or {}).get("scheduled_start_ts")),tz).date()==day]
print("PIPELINE_START",len(fixtures),flush=True)
rows,fail=_analyse_fixtures(fs,fixtures); rows=[r for r in rows if r.get("primary_trend")]
print("BRAIN_ELIGIBLE",len(rows),"FAIL",len(fail),flush=True)
def one(r):
 try:
  p=exact_trend_price(fetch_event_odds(r["match"].provider_match_id),r["primary_trend"]["name"])
 except Exception:return None
 if not p:return None
 m=r["match"]; t=r["primary_trend"]
 return PrematchPick(str(m.provider_match_id),m.home,m.away,t["name"],t["name"],p["best_odds"],float(t["probability"]),p["market_probability"],float(r["quality"])),p
priced=[]; meta={}
with ThreadPoolExecutor(max_workers=20) as pool:
 for f in as_completed([pool.submit(one,r) for r in rows]):
  z=f.result()
  if z: priced.append(z[0]); meta[z[0].event_id]=z[1]
print("EXACT_PRIMARY_PRICED",len(priced),flush=True)
print("=== ORDINARS ===",flush=True)
for i,(p,tier) in enumerate(rank_prematch_for_delivery(priced,limit=20,max_per_event=1),1):
 x=meta[p.event_id]; print(f"S{i:02d}. {p.home} -- {p.away} | {p.selection} @ {p.odds:.2f} [{x['bookmaker']}] | {tier} | model={p.model_probability:.3f} market={p.market_probability:.3f} edge={p.edge:+.3f} EV={p.expected_value:+.3f} q={p.data_quality:.2f}",flush=True)
print("=== SUPER 10 ===",flush=True); s=build_super_accumulator(priced,target_legs=10)
if not s: print("NO QUALIFIED SUPER 10",flush=True)
else:
 for i,p in enumerate(s["legs"],1):
  x=meta[p.event_id]; print(f"X{i:02d}. {p.home} -- {p.away} | {p.selection} @ {p.odds:.2f} [{x['bookmaker']}] | model={p.model_probability:.3f} edge={p.edge:+.3f} q={p.data_quality:.2f}",flush=True)
 print(f"SUPER combined_odds={s['combined_odds']:.2f} probability={s['combined_probability']:.4f} EV={s['expected_value']:+.3f}",flush=True)
