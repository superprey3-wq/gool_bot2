from __future__ import annotations
import os
from gool_bot2.history_db import connect

def report(path=None):
 db=connect(path or os.getenv("GOOL_HISTORY_DB","data/gool_history.sqlite"))
 rows=db.execute("""SELECT p.trend,
 COUNT(*) settled,
 SUM(CASE WHEN p.result='WIN' THEN 1 ELSE 0 END) wins,
 AVG((SELECT odds FROM odds_snapshots o WHERE o.event_id=p.event_id AND o.trend=p.trend ORDER BY captured_ts ASC LIMIT 1)) opening,
 AVG((SELECT odds FROM odds_snapshots o WHERE o.event_id=p.event_id AND o.trend=p.trend ORDER BY captured_ts DESC LIMIT 1)) closing
 FROM picks p WHERE p.result IS NOT NULL GROUP BY p.trend ORDER BY settled DESC""").fetchall()
 print("=== GOOL PERFORMANCE ===")
 for trend,n,w,op,cl in rows:
  hit=w/n if n else 0; clv=(op/cl-1) if op and cl else 0
  print(f"{trend} n={n} wins={w} hit={hit:.1%} opening={op or 0:.2f} closing={cl or 0:.2f} avg_CLV={clv:+.1%}")
 db.close()
if __name__=="__main__":report()
