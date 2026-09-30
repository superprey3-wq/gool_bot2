from __future__ import annotations

import json
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


def _compact_history(ctx: dict) -> dict:
    out = {}
    for key in ("home_recent", "away_recent", "home_at_home", "away_away", "h2h"):
        rows = []
        for row in (ctx.get(key) or [])[:8]:
            if not isinstance(row, dict):
                continue
            rows.append({
                k: row.get(k)
                for k in ("home", "away", "home_score", "away_score", "timestamp")
                if row.get(k) is not None
            })
        out[key] = rows
    out["source_coverage"] = ctx.get("source_coverage") or {}
    out["sources"] = ctx.get("sources") or []
    return out


def _facts(fs: FlashscoreProvider, match) -> dict:
    fusion = PrematchDataFusion(fs)
    ctx = fusion.context(match, limit=8)
    profile = build_prematch_goal_profile(
        {
            "match": {"home": match.home, "away": match.away},
            "prematch_context": ctx,
        }
    )
    return {
        "history": _compact_history(ctx),
        "goal_profile": profile,
    }


def main() -> None:
    model = "qwen3:4b"
    reviewer = OllamaPrematchReviewer(model=model, timeout_seconds=240, think=False)
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
