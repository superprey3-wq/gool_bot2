from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA="""
CREATE TABLE IF NOT EXISTS picks(
 event_id TEXT NOT NULL, trend TEXT NOT NULL, home TEXT, away TEXT, league TEXT,
 kickoff_ts REAL, created_ts REAL, model_probability REAL, data_quality REAL,
 result TEXT, final_home INTEGER, final_away INTEGER, settled_ts REAL,
 PRIMARY KEY(event_id,trend)
);
CREATE TABLE IF NOT EXISTS odds_snapshots(
 event_id TEXT NOT NULL, trend TEXT NOT NULL, captured_ts REAL NOT NULL,
 hours_to_kickoff REAL, odds REAL, bookmaker TEXT, market_probability REAL,
 PRIMARY KEY(event_id,trend,captured_ts)
);
CREATE INDEX IF NOT EXISTS idx_snap_event ON odds_snapshots(event_id,trend,captured_ts);
"""
def connect(path="data/gool_history.sqlite"):
 p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(p); db.executescript(SCHEMA); return db
