from __future__ import annotations

import json
from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
from gool_bot2.providers.fusion import FootballDataFusion


def main() -> None:
    fusion = FootballDataFusion()
    records = [r for r in fusion.live_records() if 8 <= int((r.get("match") or {}).get("minute") or 0) <= 82]
    print(f"LIVE_BRAIN_V4 matches={len(records)}", flush=True)
    bets = 0
    for record in records[:20]:
        match = record.get("match") or {}
        decisions = evaluate_live_goals(record)
        for d in decisions:
            if d.decision == "BET":
                bets += 1
            print(
                f"{match.get('minute')}' {match.get('home')} - {match.get('away')} "
                f"{match.get('home_score')}:{match.get('away_score')} | {d.market} {d.decision} "
                f"p={d.probability:.3f} conf={d.confidence:.3f} score={d.score:.3f} source={d.data_source}",
                flush=True,
            )
    print(f"LIVE_BRAIN_V4_BETS {bets}", flush=True)


if __name__ == "__main__":
    main()
