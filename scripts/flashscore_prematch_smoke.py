from __future__ import annotations

import json
import os
import sys
from typing import Any

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.fotmob import FotMobProvider
from gool_bot2.providers.scores365 import Scores365Provider
from gool_bot2.team_history import fotmob_team_history


def _score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [int(r.get("home_score") or 0) + int(r.get("away_score") or 0) for r in rows]
    return {"n": len(rows), "totals": totals, "sum": sum(totals), "avg": round(sum(totals) / len(totals), 2) if totals else None, "all_zero": bool(totals) and all(v == 0 for v in totals), "sample": [{"id": r.get("event_id"), "home": r.get("home"), "away": r.get("away"), "score": [r.get("home_score"), r.get("away_score")], "section": r.get("section"), "timestamp": r.get("timestamp"), "source": r.get("source")} for r in rows[:5]]}


def _ctx_summary(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"source": ctx.get("source"), "raw_matches": int(ctx.get("raw_matches") or 0), "match_score": ctx.get("match_score"), "feed_present": ctx.get("feed_present"), "matched_home": ctx.get("matched_home"), "matched_away": ctx.get("matched_away"), "sections": ctx.get("section_counts"), "home": _score_summary(list(ctx.get("home_recent") or [])), "away": _score_summary(list(ctx.get("away_recent") or [])), "home_venue": _score_summary(list(ctx.get("home_at_home") or [])), "away_venue": _score_summary(list(ctx.get("away_away") or [])), "h2h": _score_summary(list(ctx.get("h2h") or []))}


def _usable(summary: dict[str, Any], minimum: int = 3) -> bool:
    return int((summary.get("home") or {}).get("n") or 0) >= minimum and int((summary.get("away") or {}).get("n") or 0) >= minimum


def _false_zero(summary: dict[str, Any]) -> bool:
    home = summary.get("home") or {}; away = summary.get("away") or {}
    return (int(home.get("n") or 0) >= 5 and bool(home.get("all_zero"))) or (int(away.get("n") or 0) >= 5 and bool(away.get("all_zero")))


def main() -> int:
    flashscore = FlashscoreProvider(); fotmob = FotMobProvider(); scores365 = Scores365Provider()
    live = [m for m in flashscore.live_matches() if 15 <= int(m.minute or 0) <= 80]
    limit_matches = int(os.getenv("SMOKE_MATCHES", "8")); checked = 0
    provider_success = {"flashscore": 0, "fotmob": 0, "365scores": 0}; failures: list[str] = []; raw_diag_printed = False
    print(json.dumps({"live_candidates": len(live), "sample_limit": limit_matches, "providers": list(provider_success)}, ensure_ascii=False))
    for match in live[:limit_matches]:
        contexts: dict[str, dict[str, Any]] = {}; errors: dict[str, str] = {}
        try:
            contexts["flashscore"] = flashscore.fetch_match_history(match.provider_match_id, match.home, match.away, limit=10)
            if not raw_diag_printed and contexts["flashscore"].get("feed_present") and not contexts["flashscore"].get("raw_matches"):
                body = flashscore._h2h_feed(match.provider_match_id)
                tokens = []
                for chunk in (body or "").split("~")[:30]:
                    if chunk:
                        tokens.append(chunk[:500])
                print("FLASHSCORE_H2H_RAW_DIAG " + json.dumps({"match": f"{match.home} - {match.away}", "id": match.provider_match_id, "length": len(body or ""), "chunks": tokens}, ensure_ascii=False))
                raw_diag_printed = True
        except Exception as exc: errors["flashscore"] = f"{type(exc).__name__}: {exc}"
        try:
            embedded = fotmob.prematch_context(match.home, match.away, limit=10); team = fotmob_team_history(fotmob, match.home, match.away, limit=10)
            if len(team.get("home_recent") or []) + len(team.get("away_recent") or []) >= len(embedded.get("home_recent") or []) + len(embedded.get("away_recent") or []): contexts["fotmob"] = team; contexts["fotmob_embedded"] = embedded
            else: contexts["fotmob"] = embedded; contexts["fotmob_team"] = team
        except Exception as exc: errors["fotmob"] = f"{type(exc).__name__}: {exc}"
        try: contexts["365scores"] = scores365.prematch_context(match.home, match.away, limit=10)
        except Exception as exc: errors["365scores"] = f"{type(exc).__name__}: {exc}"
        summaries = {name: _ctx_summary(ctx) for name, ctx in contexts.items()}; primary = {name: summaries.get(name) for name in ("flashscore", "fotmob", "365scores") if summaries.get(name) is not None}; usable = {name: _usable(summary) for name, summary in primary.items()}
        for name, ok in usable.items():
            if ok: provider_success[name] += 1
        checked += 1
        print("PREMATCH_MULTI_SMOKE " + json.dumps({"match": f"{match.home} - {match.away}", "id": match.provider_match_id, "minute": match.minute, "providers": summaries, "usable": usable, "errors": errors}, ensure_ascii=False))
        if not any(usable.values()): failures.append(f"{match.home} - {match.away}: no provider returned >=3 recent matches for both teams; errors={errors}")
        for name, summary in primary.items():
            if _false_zero(summary):
                alternatives = [other for other, ok in usable.items() if other != name and ok and not _false_zero(primary[other])]
                if alternatives: print(f"PROVIDER_SUPPRESS_CANDIDATE match={match.home} - {match.away} bad={name} alternatives={','.join(alternatives)}")
                else: failures.append(f"{match.home} - {match.away}: {name} parsed 5+ recent matches as all 0:0 and no clean provider fallback exists")
    if checked == 0:
        print("No suitable live matches were available; multi-provider smoke test is inconclusive."); return 0
    print("PROVIDER_COVERAGE " + json.dumps(provider_success, ensure_ascii=False))
    if failures:
        print("PREMATCH_MULTI_SMOKE_FAILURES")
        for failure in failures: print("- " + failure)
        return 1
    print(f"PREMATCH_MULTI_SMOKE_OK checked={checked} coverage={provider_success}"); return 0


if __name__ == "__main__": sys.exit(main())
