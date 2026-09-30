from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from gool_bot2.ai_prematch_reviewer import AICandidate, OllamaPrematchReviewer
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.common import pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.prematch_fusion import PrematchDataFusion


MSK = ZoneInfo("Europe/Moscow")

TARGETS = [
    {
        "id": "st_truiden2_nijlen",
        "home": "St. Truiden 2",
        "away": "Nijlen",
        "league": "BELGIUM: Second Amateur Division Group VFV B",
        "kickoff_msk": "21:00",
        "candidate": {
            "candidate_id": "st_truiden2_nijlen:over25",
            "market": "match_total",
            "selection": "over 2.5",
            "odds": 1.70,
        },
    },
    {
        "id": "quilmes2_estudiantes2",
        "home": "Quilmes 2",
        "away": "Estudiantes Rio Cuarto 2",
        "league": "Argentina: Reserve League - Clausura",
        "kickoff_msk": "21:00",
        "candidate": {
            "candidate_id": "quilmes2_estudiantes2:under25",
            "market": "match_total",
            "selection": "under 2.5",
            "odds": 1.67,
        },
    },
    {
        "id": "eastleigh_southend",
        "home": "Eastleigh",
        "away": "Southend",
        "league": "England: National League",
        "kickoff_msk": "21:45",
        "candidate": {
            "candidate_id": "eastleigh_southend:btts_yes",
            "market": "btts",
            "selection": "yes",
            "odds": 1.62,
        },
    },
    {
        "id": "lyon_chelsea_w",
        "home": "OL Lyonnes W",
        "away": "Chelsea W",
        "league": "UEFA Champions League Women",
        "kickoff_msk": "22:00",
        "candidate": {
            "candidate_id": "lyon_chelsea_w:over25",
            "market": "match_total",
            "selection": "over 2.5",
            "odds": 1.57,
        },
    },
]


def _fixture_score(target: dict, match) -> float:
    score = pair_score(target["home"], target["away"], match.home, match.away)
    if score < 0.55:
        return score
    try:
        ts = float((match.meta or {}).get("scheduled_start_ts") or 0)
        if ts:
            actual = datetime.fromtimestamp(ts, MSK).strftime("%H:%M")
            if actual == target["kickoff_msk"]:
                score += 0.05
    except Exception:
        pass
    return min(1.0, score)


def _find_fixture(target: dict, fixtures: list) -> tuple[object | None, float]:
    best = None
    best_score = 0.0
    for match in fixtures:
        score = _fixture_score(target, match)
        if score > best_score:
            best = match
            best_score = score
    if best_score < 0.68:
        return None, best_score
    return best, best_score


def _same_team(a: str, b: str) -> bool:
    return pair_score(a, a, b, b) >= 0.72


def _team_summary(rows: list, team: str) -> dict:
    w = d = l = gf = ga = scored = conceded = over25 = btts = 0
    used = 0
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        try:
            hs = int(row.get("home_score"))
            aws = int(row.get("away_score"))
        except (TypeError, ValueError):
            continue
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        if _same_team(team, home):
            f, a = hs, aws
        elif _same_team(team, away):
            f, a = aws, hs
        else:
            continue
        used += 1
        gf += f
        ga += a
        scored += int(f > 0)
        conceded += int(a > 0)
        over25 += int(f + a > 2)
        btts += int(f > 0 and a > 0)
        if f > a:
            w += 1
        elif f == a:
            d += 1
        else:
            l += 1
    return {
        "matches": used, "w": w, "d": d, "l": l,
        "gf": gf, "ga": ga, "scored_in": scored, "conceded_in": conceded,
        "over_2_5_in": over25, "btts_in": btts,
    }


def _h2h_summary(rows: list, home_team: str, away_team: str) -> dict:
    home_wins = draws = away_wins = home_scored = away_scored = over25 = btts = total_goals = 0
    used = 0
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        try:
            hs = int(row.get("home_score"))
            aws = int(row.get("away_score"))
        except (TypeError, ValueError):
            continue
        rh = str(row.get("home") or "")
        ra = str(row.get("away") or "")
        if _same_team(home_team, rh) and _same_team(away_team, ra):
            h, a = hs, aws
        elif _same_team(home_team, ra) and _same_team(away_team, rh):
            h, a = aws, hs
        else:
            continue
        used += 1
        total_goals += h + a
        home_scored += int(h > 0)
        away_scored += int(a > 0)
        over25 += int(h + a > 2)
        btts += int(h > 0 and a > 0)
        if h > a:
            home_wins += 1
        elif h == a:
            draws += 1
        else:
            away_wins += 1
    return {
        "matches": used, "home_wins": home_wins, "draws": draws, "away_wins": away_wins,
        "home_scored_in": home_scored, "away_scored_in": away_scored,
        "over_2_5_in": over25, "btts_in": btts,
        "avg_total": None if not used else round(total_goals / used, 2),
    }


def _facts(fs: FlashscoreProvider, match) -> dict:
    fusion = PrematchDataFusion(fs)
    ctx = fusion.context(match, limit=8)
    profile = build_prematch_goal_profile(
        {"match": {"home": match.home, "away": match.away}, "prematch_context": ctx}
    )
    full = profile.get("full_match") or {}
    return {
        "home_recent": _team_summary(list(ctx.get("home_recent") or []), match.home),
        "away_recent": _team_summary(list(ctx.get("away_recent") or []), match.away),
        "home_at_home": _team_summary(list(ctx.get("home_at_home") or []), match.home),
        "away_away": _team_summary(list(ctx.get("away_away") or []), match.away),
        "h2h": _h2h_summary(list(ctx.get("h2h") or []), match.home, match.away),
        "gool_full_match": {
            "pair_sample": full.get("pair_sample"),
            "home_expected_goals": full.get("home_expected_goals"),
            "away_expected_goals": full.get("away_expected_goals"),
            "expected_total": full.get("expected_total"),
        },
        "source_coverage": ctx.get("source_coverage") or {},
    }


def main() -> None:
    model = os.getenv("GOOL_AI_PREMATCH_MODEL", "qwen3:1.7b")
    reviewer = OllamaPrematchReviewer(model=model, timeout_seconds=75, think=False)
    fs = FlashscoreProvider()
    now = datetime.now(timezone.utc).timestamp()
    fixtures = [
        m for m in fs.scheduled_matches_for_day(0)
        if float((m.meta or {}).get("scheduled_start_ts") or 0) > now
    ]
    print(f"LIVE_FIXTURES={len(fixtures)}", flush=True)

    resolved = []
    missing = []
    for target in TARGETS:
        match, score = _find_fixture(target, fixtures)
        if match is None:
            missing.append({"id": target["id"], "match_score": round(score, 3)})
            print(f"NOT_FOUND {target['home']} - {target['away']} score={score:.3f}", flush=True)
            continue
        print(
            f"FOUND {target['home']} - {target['away']} -> "
            f"{match.home} - {match.away} score={score:.3f}",
            flush=True,
        )
        resolved.append((target, match, score))

    facts_by_id = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(resolved)))) as pool:
        jobs = {pool.submit(_facts, FlashscoreProvider(), match): (target, match, score)
                for target, match, score in resolved}
        for fut in as_completed(jobs):
            target, match, score = jobs[fut]
            try:
                facts_by_id[target["id"]] = fut.result()
            except Exception as exc:
                facts_by_id[target["id"]] = {"fusion_error": f"{type(exc).__name__}: {exc}"}

    output = {
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resolved": len(resolved),
        "missing": missing,
        "cases": [],
    }
    error_count = 0

    for target, match, score in resolved:
        facts = facts_by_id.get(target["id"]) or {}
        row = {
            "id": target["id"],
            "fixture": f"{match.home} - {match.away}",
            "fixture_match_score": round(score, 3),
            "gool_pick": target["candidate"],
        }
        if "fusion_error" in facts:
            row["error"] = facts["fusion_error"]
            error_count += 1
            output["cases"].append(row)
            continue

        candidate = AICandidate.model_validate(target["candidate"])
        context = {
            "home": match.home,
            "away": match.away,
            "league": match.league or target["league"],
            "kickoff_msk": target["kickoff_msk"],
            "final_result_hidden": True,
            "task": "Review GOOL's proposed prematch single. Select it only if evidence supports it; otherwise SKIP.",
        }
        print(
            f"AI_REVIEW {row['fixture']} pick={candidate.selection} odds={candidate.odds}",
            flush=True,
        )
        started = time.time()
        try:
            review = reviewer.review(
                match_context=context,
                deterministic_facts=facts,
                candidates=[candidate],
            )
            row["elapsed_s"] = round(time.time() - started, 2)
            row["review"] = review.model_dump()
        except Exception as exc:
            error_count += 1
            row["elapsed_s"] = round(time.time() - started, 2)
            row["error"] = f"{type(exc).__name__}: {exc}"
        output["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)

    path = "fast_ai_reviewer_result.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)
    print(f"WROTE {path}", flush=True)

    if len(resolved) < 2:
        raise SystemExit(f"Only {len(resolved)} target fixture(s) were resolved")
    if error_count:
        raise SystemExit(f"{error_count} reviewer/fusion case(s) failed")


if __name__ == "__main__":
    main()
