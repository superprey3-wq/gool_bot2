"""Read-only one-shot live Flashscore probe for the standalone original collector.

Never contacts 1xBet, Telegram, models, or writes bets.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
os.environ.setdefault("GOOL_FOOTBALL_AUTOINSTALL", "0")
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.brain_v3_memory import build_brain_v3_memory
from gool_bot2.brain_v3_decision import evaluate_brain_v3
from gool_bot2.live_goal_brain_v4 import evaluate_live_goals


def main():
    os.environ.setdefault("GOOL_BROWSER_ENABLE","0")
    p=FlashscoreProvider()
    matches=p.live_matches()
    eligible=[m for m in matches if (1<=int(m.minute or 0)<=35 or 46<=int(m.minute or 0)<=95)
              and not m.is_halftime]
    selected=eligible[:3]
    results=[]
    for m in selected:
        eid=str(m.provider_match_id)
        stats=p.fetch_stats(eid)
        record={
            "match":{"flashscore_event_id":eid,"home":m.home,"away":m.away,
                     "minute":int(m.minute or 0),"home_score":m.home_score,
                     "away_score":m.away_score,"is_halftime":m.is_halftime},
            "providers":{"flashscore":{"stats":stats,"meta":m.meta or {}}},
            "captured_at":"2026-10-22T08:00:00+00:00",
        }
        memory=build_brain_v3_memory(record,eid)
        decision=evaluate_brain_v3(record,data_quality=min(1.,len(stats)/4))
        v4=evaluate_live_goals(record)
        results.append({
            "minute":m.minute,"stat_fields":sorted(stats),
            "v3":decision.get("status"),
            "v3_blocks":list(decision.get("blocks") or [])[:6],
            "v4":[d.decision for d in v4],
            "memory_points":memory.get("snapshot_count"),
        })
    print("GOOL_ORIGINAL_REAL_FLASHSCORE "+json.dumps({
        "live":len(matches),"eligible":len(eligible),"probed":results,
        "needs_two_observations":"V3/V4 require live snapshots across actual minutes",
    },ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
