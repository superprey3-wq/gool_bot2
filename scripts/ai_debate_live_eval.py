from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.ai_debate_reviewer import (
    DebateCandidate,
    OllamaDebateReviewer,
    calibration_records_from_jsonl,
    fit_reviewer_weight,
)
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.v4_prematch_engine import (
    PrematchPick,
    build_prematch_candidates,
    rank_prematch_for_delivery,
)
from gool_bot2.v4_shadow_report import _analyse_fixtures
from gool_bot2.xbet_prematch_market import XBetPrematchCollector, find_prematch_market


WINDOW_MINUTES = int(os.getenv("GOOL_AI_DEBATE_WINDOW_MINUTES", "240"))
MAX_FIXTURES = int(os.getenv("GOOL_AI_DEBATE_MAX_FIXTURES", "18"))
MAX_DEBATES = int(os.getenv("GOOL_AI_DEBATE_MAX_CANDIDATES", "3"))
MODEL = os.getenv("GOOL_AI_PREMATCH_MODEL", "qwen3:1.7b")


def _fusion(match, base_quality: float) -> tuple[dict, float, dict]:
    fs = FlashscoreProvider()
    ctx = PrematchDataFusion(fs).context(match, limit=8)
    profile = build_prematch_goal_profile(
        {"match": {"home": match.home, "away": match.away}, "prematch_context": ctx}
    )
    samples = [
        int((profile.get(k) or {}).get("pair_sample") or 0)
        for k in ("first_half", "second_half", "full_match")
    ]
    sample = max(samples or [0])
    coverage = dict(ctx.get("source_coverage") or {})
    active_sources = sum(1 for v in coverage.values() if int(v or 0) > 0)
    quality = max(
        float(base_quality or 0),
        min(1.0, 0.78 * min(1.0, sample / 8.0) + 0.22 * min(1.0, active_sources / 3.0)),
    )
    full = profile.get("full_match") or {}
    first = profile.get("first_half") or {}
    second = profile.get("second_half") or {}
    evidence = {
        "source_coverage": coverage,
        "full_match": {
            k: full.get(k)
            for k in (
                "pair_sample", "home_expected_goals", "away_expected_goals",
                "expected_total", "over_1_5_rate", "over_2_5_rate",
                "over_3_5_rate", "btts_rate",
            )
            if full.get(k) is not None
        },
        "first_half": {
            k: first.get(k)
            for k in ("pair_sample", "expected_total", "over_0_5_rate", "over_1_5_rate")
            if first.get(k) is not None
        },
        "second_half": {
            k: second.get(k)
            for k in ("pair_sample", "expected_total", "over_0_5_rate", "over_1_5_rate")
            if second.get(k) is not None
        },
    }
    return profile, quality, evidence


def main() -> None:
    now = time.time()
    end = now + WINDOW_MINUTES * 60
    fs = FlashscoreProvider()
    fixtures = []
    for match in fs.scheduled_matches_for_day(0):
        ts = float((match.meta or {}).get("scheduled_start_ts") or 0)
        if now < ts <= end:
            fixtures.append(match)
    fixtures.sort(key=lambda m: float((m.meta or {}).get("scheduled_start_ts") or 0))
    fixtures = fixtures[:MAX_FIXTURES]
    print(f"DEBATE_FIXTURES window={WINDOW_MINUTES} count={len(fixtures)}", flush=True)
    if not fixtures:
        raise SystemExit("No upcoming Flashscore fixtures in the diagnostic window")

    analysed, failures = _analyse_fixtures(fs, fixtures)
    eligible = [row for row in analysed if row.get("primary_trend")]
    eligible = eligible[: min(len(eligible), 10)]
    print(
        f"DEBATE_ANALYSED analysed={len(analysed)} evidence_eligible={len(eligible)} failures={len(failures)}",
        flush=True,
    )
    if not eligible:
        raise SystemExit("No evidence-eligible fixtures")

    enriched = []
    with ThreadPoolExecutor(max_workers=min(6, len(eligible))) as pool:
        jobs = {
            pool.submit(_fusion, row["match"], float(row.get("quality") or 0)): row
            for row in eligible
        }
        for fut in as_completed(jobs):
            row = jobs[fut]
            try:
                profile, quality, evidence = fut.result()
                enriched.append({**row, "profile": profile, "quality": quality, "debate_evidence": evidence})
            except Exception as exc:
                print(
                    f"DEBATE_FUSION_FAIL {row['match'].home} - {row['match'].away} "
                    f"{type(exc).__name__}:{exc}",
                    flush=True,
                )

    state_path = Path("data/live/ai_debate_xbet.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    XBetPrematchCollector(state_path).collect_once(targets=[row["match"] for row in enriched])

    raw_picks: list[PrematchPick] = []
    evidence_by_event: dict[str, dict] = {}
    for row in enriched:
        match = row["match"]
        market = find_prematch_market(match.home, match.away, path=state_path)
        if not market:
            print(f"DEBATE_NO_MARKET {match.home} - {match.away}", flush=True)
            continue
        kickoff = float((match.meta or {}).get("scheduled_start_ts") or 0)
        picks = build_prematch_candidates(
            event_id=str(match.provider_match_id),
            home=match.home,
            away=match.away,
            profile=row["profile"],
            market=market,
            data_quality=float(row["quality"]),
        )
        for p in picks:
            raw_picks.append(PrematchPick(
                event_id=p.event_id,
                home=p.home,
                away=p.away,
                market=p.market,
                selection=p.selection,
                odds=p.odds,
                model_probability=p.model_probability,
                market_probability=p.market_probability,
                data_quality=p.data_quality,
                league=match.league or "",
                kickoff_ts=kickoff,
            ))
        evidence_by_event[str(match.provider_match_id)] = row["debate_evidence"]

    ranked = rank_prematch_for_delivery(raw_picks, limit=max(1, MAX_DEBATES), max_per_event=1)
    print(f"DEBATE_RANKED candidates={len(ranked)} raw_picks={len(raw_picks)}", flush=True)
    if not ranked:
        raise SystemExit("No GOOL-qualified candidate available for debate")

    calibration_path = Path(os.getenv("GOOL_AI_DEBATE_CALIBRATION_PATH", "data/ai_debate_calibration.jsonl"))
    history = calibration_records_from_jsonl(calibration_path)
    fit = fit_reviewer_weight(history, prior_weight=0.35)
    reviewer_weight = fit.weight if fit else 0.35
    print(
        f"DEBATE_CALIBRATION records={len(history)} reviewer_weight={reviewer_weight:.3f} "
        f"fit={None if fit is None else fit.__dict__}",
        flush=True,
    )

    reviewer = OllamaDebateReviewer(
        model=MODEL,
        reviewer_weight=reviewer_weight,
        max_contextual_delta=0.08,
        timeout_seconds=90,
    )

    output = {
        "model": MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_minutes": WINDOW_MINUTES,
        "reviewer_weight": reviewer_weight,
        "calibration_fit": None if fit is None else fit.__dict__,
        "cases": [],
    }

    for pick, tier in ranked:
        candidate_id = f"{pick.event_id}:c{len(output['cases']) + 1}"
        candidate = DebateCandidate(
            candidate_id=candidate_id,
            event_id=str(pick.event_id),
            match=f"{pick.home} - {pick.away}",
            market=pick.market,
            selection=pick.selection,
            odds=pick.odds,
            model_probability=pick.model_probability,
            market_probability=pick.market_probability,
            edge=pick.edge,
            expected_value=pick.expected_value,
            data_quality=pick.data_quality,
            math_qualified=True,
            evidence=evidence_by_event.get(str(pick.event_id), {}),
        )
        print(
            f"DEBATE_START {candidate.match} | {candidate.market}:{candidate.selection} "
            f"@{candidate.odds:.3f} p={candidate.model_probability:.3f} "
            f"edge={candidate.edge:+.3f} ev={candidate.expected_value:+.3f} tier={tier}",
            flush=True,
        )
        started = time.time()
        verdict = reviewer.review(candidate)
        row = {
            "tier": tier,
            "elapsed_s": round(time.time() - started, 2),
            "candidate": candidate.model_dump(),
            "verdict": verdict.model_dump(),
        }
        output["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)

    approved = [row for row in output["cases"] if row["verdict"]["decision"] == "BET"]
    output["approved_count"] = len(approved)
    output["approved"] = [
        {
            "match": row["candidate"]["match"],
            "market": row["candidate"]["market"],
            "selection": row["candidate"]["selection"],
            "odds": row["candidate"]["odds"],
            "base_probability": row["verdict"]["base_probability"],
            "adjusted_probability": row["verdict"]["adjusted_probability"],
            "judge_confidence": row["verdict"]["judge_confidence"],
        }
        for row in approved
    ]

    with open("ai_debate_live_result.json", "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print("=== GOOL AI DEBATE RESULT ===", flush=True)
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
