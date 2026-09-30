from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.brain_v3_full_match import (
    apply_full_match_brain_v3_to_experts,
    apply_full_match_strong_live,
)
from gool_bot2.brain_v3_memory import build_brain_v3_memory
from gool_bot2.live_for_against_judge import evaluate_argument_judge
from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
from gool_bot2.live_momentum import LiveMomentumTracker
from gool_bot2.match_context import provider_count
from gool_bot2.multi_runtime import _data_quality
from gool_bot2.providers.fusion import FootballDataFusion


POLL_SECONDS = int(os.getenv("GOOL_LIVE_COMPARE_POLL_SECONDS", "55"))
POLL_COUNT = int(os.getenv("GOOL_LIVE_COMPARE_POLL_COUNT", "7"))
MAX_MATCHES = int(os.getenv("GOOL_LIVE_COMPARE_MAX_MATCHES", "8"))


def _window(minute: int) -> bool:
    return 8 <= minute <= 43 or 46 <= minute <= 85


def _active_market(minute: int) -> str | None:
    if 8 <= minute <= 43:
        return "GOAL_BEFORE_HT"
    if 46 <= minute <= 85:
        return "ANOTHER_GOAL"
    return None


def _enrich_many(fusion: FootballDataFusion, matches: list) -> list[dict]:
    out = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(matches)))) as pool:
        futures = {pool.submit(fusion.enrich_flashscore_match, match): match for match in matches}
        for fut in as_completed(futures):
            try:
                out.append(fut.result())
            except Exception as exc:
                m = futures[fut]
                print(f"COMPARE_ENRICH_FAIL {m.home} - {m.away} {type(exc).__name__}:{exc}", flush=True)
    return out


def main() -> None:
    fusion = FootballDataFusion()
    momentum = LiveMomentumTracker(max_points=30)

    initial = [
        m for m in fusion.flashscore.live_matches()
        if _window(int(m.minute or 0)) and not bool((m.meta or {}).get("is_finished"))
    ]
    initial.sort(key=lambda m: int(m.minute or 0))
    initial = initial[:MAX_MATCHES]
    tracked_ids = {str(m.provider_match_id) for m in initial}
    print(
        f"COMPARE_START live_in_window={len(initial)} tracked={len(tracked_ids)} "
        f"polls={POLL_COUNT} every={POLL_SECONDS}s",
        flush=True,
    )
    for m in initial:
        print(f"TRACK {m.minute}' {m.home} - {m.away} {m.home_score}:{m.away_score}", flush=True)

    if not tracked_ids:
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "NO_LIVE_MATCHES",
            "rows": [],
        }
        Path("live_brain_compare.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("COMPARE_NO_LIVE_MATCHES", flush=True)
        return

    latest: dict[str, dict] = {}
    for poll in range(POLL_COUNT):
        master = {
            str(m.provider_match_id): m
            for m in fusion.flashscore.live_matches()
            if str(m.provider_match_id) in tracked_ids
        }
        matches = list(master.values())
        records = _enrich_many(fusion, matches)
        print(f"COMPARE_POLL {poll + 1}/{POLL_COUNT} active={len(records)}", flush=True)

        for record in records:
            match = record.get("match") or {}
            mid = str(match.get("flashscore_event_id") or "")
            if not mid:
                continue
            record = momentum.attach(record)
            build_brain_v3_memory(record, mid)
            latest[mid] = record
            mem = record.get("brain_v3_memory") or {}
            mom = record.get("live_momentum") or {}
            print(
                f"SNAP {match.get('minute')}' {match.get('home')} - {match.get('away')} "
                f"{match.get('home_score')}:{match.get('away_score')} "
                f"sources={provider_count(record)} state={((mem.get('pressure') or {}).get('state'))} "
                f"epoch={((mem.get('score_epoch') or {}).get('age_minutes'))} "
                f"xg5={mom.get('xg_total_last_5m')}",
                flush=True,
            )

        if poll < POLL_COUNT - 1:
            time.sleep(POLL_SECONDS)

    rows = []
    for mid, record in latest.items():
        match = record.get("match") or {}
        minute = int(match.get("minute") or 0)
        market = _active_market(minute)
        if market is None:
            continue

        quality = _data_quality(record)
        brain = apply_full_match_brain_v3_to_experts(
            record, {}, data_quality=quality, prematch_profile=None
        )
        brain = apply_full_match_strong_live(record, {}, brain)

        v4 = next((d for d in evaluate_live_goals(record) if d.market == market), None)
        judge = evaluate_argument_judge(record)

        brain_status = str(brain.get("status") or "WATCH")
        v4_status = "NONE" if v4 is None else v4.decision
        judge_status = "NONE" if judge is None else judge.decision

        binary_brain = "BET" if brain_status == "BET" else "NO_BET"
        binary_v4 = "BET" if v4_status == "BET" else "NO_BET"
        binary_judge = "BET" if judge_status == "BET" else "NO_BET"
        unanimous = len({binary_brain, binary_v4, binary_judge}) == 1

        row = {
            "event_id": mid,
            "home": match.get("home"),
            "away": match.get("away"),
            "minute": minute,
            "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
            "market": market,
            "data_quality": round(float(quality), 3),
            "brain_v3": {
                "status": brain_status,
                "probability": brain.get("probability"),
                "state": brain.get("match_state"),
                "xg5": (brain.get("recent") or {}).get("xg5"),
                "xg10": (brain.get("recent") or {}).get("xg10"),
                "blocks": list(brain.get("blocks") or []),
            },
            "live_brain_v4": None if v4 is None else v4.to_dict(),
            "for_against_judge": None if judge is None else judge.to_dict(),
            "unanimous": unanimous,
            "votes": {
                "brain_v3": binary_brain,
                "live_brain_v4": binary_v4,
                "for_against_judge": binary_judge,
            },
        }
        rows.append(row)

        print(
            f"COMPARE_RESULT {minute}' {match.get('home')} - {match.get('away')} "
            f"{match.get('home_score')}:{match.get('away_score')} | {market} | "
            f"V3={brain_status}/p={brain.get('probability')} | "
            f"V4={v4_status}/p={None if v4 is None else v4.probability} | "
            f"JUDGE={judge_status}/FOR={None if judge is None else judge.for_score}/"
            f"AGAINST={None if judge is None else judge.against_score}/"
            f"SCORE={None if judge is None else judge.judge_score} | "
            f"{'AGREE' if unanimous else 'DISAGREE'}",
            flush=True,
        )

    rows.sort(
        key=lambda r: (
            r["votes"]["brain_v3"] == "BET",
            r["votes"]["live_brain_v4"] == "BET",
            r["votes"]["for_against_judge"] == "BET",
            r["minute"],
        ),
        reverse=True,
    )
    summary = {
        "matches": len(rows),
        "v3_bets": sum(r["votes"]["brain_v3"] == "BET" for r in rows),
        "v4_bets": sum(r["votes"]["live_brain_v4"] == "BET" for r in rows),
        "judge_bets": sum(r["votes"]["for_against_judge"] == "BET" for r in rows),
        "unanimous": sum(bool(r["unanimous"]) for r in rows),
        "disagreements": sum(not bool(r["unanimous"]) for r in rows),
    }
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "poll_seconds": POLL_SECONDS,
        "poll_count": POLL_COUNT,
        "summary": summary,
        "rows": rows,
    }
    Path("live_brain_compare.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== LIVE BRAIN VS FOR/AGAINST/JUDGE ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
