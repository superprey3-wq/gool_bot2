from __future__ import annotations

import json
import os

from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers import Scores365Provider


def main() -> None:
    os.environ.setdefault("SCORES365_HALF_HISTORY_MATCHES", "4")
    provider = Scores365Provider()
    live = provider.live_rows()
    tested = 0
    for hit in live:
        home = str((hit.get("homeCompetitor") or {}).get("name") or "")
        away = str((hit.get("awayCompetitor") or {}).get("name") or "")
        if not home or not away:
            continue
        method = getattr(provider, "half_prematch_context", None)
        if not callable(method):
            raise SystemExit("Scores365Provider.half_prematch_context is not installed")
        context = method(home, away, limit=8)
        half_rows = [
            row for row in [*(context.get("home_recent") or []), *(context.get("away_recent") or [])]
            if row.get("halftime_home_score") is not None and row.get("halftime_away_score") is not None
        ]
        if not half_rows:
            continue
        enriched = provider.enrich(home, away)
        meta = {} if enriched is None else dict(enriched.meta or {})
        score = [
            int(float((hit.get("homeCompetitor") or {}).get("score") or 0)),
            int(float((hit.get("awayCompetitor") or {}).get("score") or 0)),
        ]
        minute = int(float(hit.get("gameTime") or 0))
        record = {
            "match": {
                "home": home,
                "away": away,
                "minute": minute,
                "home_score": score[0],
                "away_score": score[1],
                "is_halftime": False,
            },
            "providers": {"365scores": {"meta": meta}, "flashscore": {"meta": {"goal_timeline": []}}},
            "prematch_context": context,
        }
        profile = build_prematch_goal_profile(record)
        print("HALF_PROFILE_REAL", json.dumps({
            "game_id": hit.get("id"),
            "home": home,
            "away": away,
            "minute": minute,
            "score": score,
            "half_rows": len(half_rows),
            "has_trends": context.get("has_trends"),
            "has_top_trends": context.get("has_top_trends"),
            "first_half": profile.get("first_half"),
            "second_half": profile.get("second_half"),
            "active": profile.get("active"),
        }, ensure_ascii=False, default=str))
        tested += 1
        if tested >= 2:
            break
    if tested == 0:
        raise SystemExit("No live 365Scores match yielded historical HT rows")


if __name__ == "__main__":
    main()
