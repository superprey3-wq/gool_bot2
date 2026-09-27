from __future__ import annotations
import os,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from gool_bot2.flashscore_odds import fetch_event_odds,exact_trend_price
from gool_bot2.history_db import connect

def due(hours_to_kickoff,last_age_minutes):
    if hours_to_kickoff <= 0:return False
    interval=15 if hours_to_kickoff<=2 else 30 if hours_to_kickoff<=6 else 60 if hours_to_kickoff<=12 else 180 if hours_to_kickoff<=36 else 360
    return last_age_minutes is None or last_age_minutes>=interval

def refresh(db_path=None):
    path=db_path or os.getenv("GOOL_HISTORY_DB","data/gool_history.sqlite"); db=connect(path); now=time.time()
    rows=db.execute("""SELECT p.event_id,p.trend,p.kickoff_ts,p.model_probability,
      (SELECT MAX(captured_ts) FROM odds_snapshots o WHERE o.event_id=p.event_id AND o.trend=p.trend)
      FROM picks p WHERE p.result IS NULL AND p.kickoff_ts>?""",(now,)).fetchall()
    targets=[]
    for eid,trend,kickoff,model,last in rows:
        h=(float(kickoff)-now)/3600; age=None if last is None else (now-float(last))/60
        if due(h,age):targets.append((eid,trend,kickoff,model))
    def one(row):
        eid,trend,kickoff,model=row
        try:return row,exact_trend_price(fetch_event_odds(eid),trend)
        except Exception:return row,None
    saved=0
    with ThreadPoolExecutor(max_workers=int(os.getenv("GOOL_ODDS_WORKERS","12"))) as pool:
        for f in as_completed([pool.submit(one,x) for x in targets]):
            (eid,trend,kickoff,model),p=f.result()
            if not p:continue
            db.execute("""INSERT OR IGNORE INTO odds_snapshots(event_id,trend,captured_ts,hours_to_kickoff,odds,bookmaker,market_probability)
              VALUES(?,?,?,?,?,?,?)""",(eid,trend,time.time(),(float(kickoff)-time.time())/3600,p["best_odds"],p["bookmaker"],p["market_probability"])); saved+=1
    db.commit(); db.close(); return len(rows),len(targets),saved
if __name__=="__main__":
    print("ODDS_REFRESH active=%d due=%d saved=%d"%refresh(),flush=True)
