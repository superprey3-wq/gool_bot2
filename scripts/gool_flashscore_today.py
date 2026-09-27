from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.flashscore_odds import fetch_event_odds,exact_trend_price
from gool_bot2.v4_shadow_report import _analyse_fixtures
from gool_bot2.v4_prematch_engine import PrematchPick,choose_delivery
from gool_bot2.odds_journal import append_price_snapshot,append_sqlite_snapshot

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
  p,x,r=z; priced.append(p); meta[p.event_id]=x; m=r["match"]
  kw=dict(event_id=p.event_id,home=m.home,away=m.away,league=m.league,kickoff_ts=p.kickoff_ts,trend=p.market,odds=p.odds,bookmaker=x["bookmaker"],market_probability=p.market_probability,model_probability=p.model_probability)
  append_price_snapshot(**kw); append_sqlite_snapshot(**kw,data_quality=p.data_quality)
print("EXACT_PRIMARY_PRICED",len(priced),flush=True)
print("PRICED_TREND_COUNTS",dict(Counter(p.market for p in priced)),flush=True)

d=choose_delivery(priced)
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
