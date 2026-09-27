from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path

def _load(path: Path):
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}

def append_price_snapshot(*, event_id, home, away, league, kickoff_ts, trend, odds, bookmaker, market_probability, model_probability, path=None):
    p=Path(path or os.getenv("GOOL_ODDS_JOURNAL","data/odds_history.json"))
    data=_load(p); key=f"{event_id}:{trend}"; now=datetime.now(timezone.utc)
    kickoff=float(kickoff_ts or 0); hours=(kickoff-now.timestamp())/3600 if kickoff else None
    row=data.setdefault(key,{"event_id":str(event_id),"home":home,"away":away,"league":league,"kickoff_ts":kickoff,"trend":trend,"snapshots":[]})
    snap={"ts":now.isoformat(),"hours_to_kickoff":round(hours,2) if hours is not None else None,"odds":round(float(odds),3),"bookmaker":bookmaker,"market_probability":round(float(market_probability),5),"model_probability":round(float(model_probability),5)}
    snaps=row["snapshots"]
    if not snaps or snaps[-1].get("odds")!=snap["odds"] or snaps[-1].get("bookmaker")!=snap["bookmaker"]:
        snaps.append(snap)
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return snap

def clv_summary(row: dict):
    snaps=row.get("snapshots") or []
    if len(snaps)<2:return None
    first,last=snaps[0],snaps[-1]
    a=float(first["odds"]); b=float(last["odds"])
    return {"opening_odds":a,"closing_odds":b,"odds_move_pct":round((a/b-1)*100,2),"snapshots":len(snaps)}

def append_sqlite_snapshot(*, event_id, home, away, league, kickoff_ts, trend, odds, bookmaker, market_probability, model_probability, data_quality=1.0, db_path=None):
    import time
    from .history_db import connect
    db=connect(db_path or os.getenv("GOOL_HISTORY_DB","data/gool_history.sqlite"))
    now=time.time(); kickoff=float(kickoff_ts or 0); hours=(kickoff-now)/3600 if kickoff else None
    db.execute("""INSERT INTO picks(event_id,trend,home,away,league,kickoff_ts,created_ts,model_probability,data_quality)
      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id,trend) DO UPDATE SET model_probability=excluded.model_probability,data_quality=excluded.data_quality""",
      (str(event_id),trend,home,away,league,kickoff,now,float(model_probability),float(data_quality)))
    db.execute("""INSERT OR IGNORE INTO odds_snapshots(event_id,trend,captured_ts,hours_to_kickoff,odds,bookmaker,market_probability)
      VALUES(?,?,?,?,?,?,?)""",(str(event_id),trend,now,hours,float(odds),bookmaker,float(market_probability)))
    db.commit(); db.close()
