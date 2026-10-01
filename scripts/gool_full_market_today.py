from __future__ import annotations

import json
import math
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gool_bot2.flashscore_odds import fetch_event_odds
from gool_bot2.full_market_brain import analyze_full_market
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.prematch_fusion import PrematchDataFusion

MSK = ZoneInfo("Europe/Moscow")


def quality(profile: dict, context: dict) -> tuple[float, int, int]:
    samples = [
        int((profile.get(k) or {}).get("pair_sample") or 0)
        for k in ("first_half", "second_half", "full_match")
    ]
    sample = max(samples or [0])
    coverage = sum(
        1 for value in (context.get("source_coverage") or {}).values()
        if int(value or 0) > 0
    )
    q = min(1.0, 0.78 * min(1.0, sample / 8.0) + 0.22 * min(1.0, coverage / 3.0))
    return q, sample, coverage


def rank_score(row: dict) -> float:
    p = float(row.get("model_probability") or 0.0)
    q = float(row.get("quality") or 0.0)
    edge = max(0.0, float(row.get("edge") or 0.0))
    ev = max(0.0, float(row.get("expected_value") or 0.0))
    return (
        0.55 * p
        + 0.20 * q
        + 0.15 * min(edge, 0.20) / 0.20
        + 0.10 * min(ev, 0.25) / 0.25
    )


def compact(row: dict, match: dict) -> dict:
    return {
        "event_id": match["event_id"],
        "home": match["home"],
        "away": match["away"],
        "league": match["league"],
        "kickoff_ts": match["kickoff_ts"],
        "scope": row.get("scope"),
        "market_type": row.get("market_type"),
        "selection": row.get("selection"),
        "odds": row.get("odds"),
        "bookmaker": row.get("bookmaker"),
        "probability": row.get("model_probability"),
        "honest_probability": row.get("honest_probability"),
        "probability_range_low": row.get("probability_range_low"),
        "probability_range_high": row.get("probability_range_high"),
        "raw_model_probability": row.get("raw_model_probability"),
        "market_probability": row.get("market_probability"),
        "calibration_sample": row.get("calibration_sample"),
        "calibration_confidence": row.get("calibration_confidence"),
        "calibration_source": row.get("calibration_source"),
        "profile_sample": row.get("profile_sample"),
        "edge": row.get("edge"),
        "ev": row.get("expected_value"),
        "quality": row.get("quality"),
        "status": row.get("status"),
        "scope_source": row.get("scope_source"),
        "rank_score": rank_score(row),
    }


def analyse_fixture(match) -> dict:
    fs = FlashscoreProvider()
    fusion = PrematchDataFusion(fs)
    context = fusion.context(match, limit=10)
    profile = build_prematch_goal_profile({
        "match": {"home": match.home, "away": match.away},
        "prematch_context": context,
    })
    q, sample, coverage = quality(profile, context)
    odds = fetch_event_odds(match.provider_match_id)
    analysis = analyze_full_market(odds, profile, quality=q)
    candidates = list(analysis.get("candidates") or [])
    primary = (
        next((x for x in candidates if x.get("status") == "BET"), None)
        or next((x for x in candidates if x.get("status") == "LEAN"), None)
        or (candidates[0] if candidates else None)
    )
    return {
        "event_id": str(match.provider_match_id),
        "home": match.home,
        "away": match.away,
        "league": match.league,
        "kickoff_ts": float((match.meta or {}).get("scheduled_start_ts") or 0),
        "quality": q,
        "sample": sample,
        "source_coverage": coverage,
        "sources": context.get("sources") or [],
        "primary": primary,
        "candidates": candidates,
        "modeled_market_types": analysis.get("modeled_market_types") or [],
        "unmodeled_market_types": analysis.get("unmodeled_market_types") or [],
    }


def choose_one_per_event(rows: list[dict], limit: int, excluded: set[str] | None = None) -> list[dict]:
    excluded = excluded or set()
    ordered = sorted(rows, key=rank_score, reverse=True)
    out = []
    seen = set(excluded)
    for row in ordered:
        event_id = str(row["event_id"])
        if event_id in seen:
            continue
        seen.add(event_id)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def main() -> None:
    fs = FlashscoreProvider()
    now = datetime.now(timezone.utc).timestamp()
    today = datetime.now(MSK).date()
    fixtures = [
        m for m in fs.scheduled_matches_for_day(0)
        if (m.meta or {}).get("scheduled_start_ts")
        and float(m.meta["scheduled_start_ts"]) > now
        and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]), MSK).date() == today
    ]
    print(f"FULL_DAY_START fixtures={len(fixtures)} date_msk={today.isoformat()}", flush=True)

    workers = max(4, min(14, int(os.getenv("GOOL_FULL_MARKET_WORKERS", "10"))))
    matches = []
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyse_fixture, m): m for m in fixtures}
        done = 0
        for future in as_completed(futures):
            m = futures[future]
            try:
                item = future.result()
                matches.append(item)
            except Exception as exc:
                failures.append({
                    "event_id": str(m.provider_match_id),
                    "home": m.home,
                    "away": m.away,
                    "error": f"{type(exc).__name__}:{exc}",
                })
            done += 1
            if done % 10 == 0 or done == len(fixtures):
                print(f"FULL_DAY_PROGRESS checked={done}/{len(fixtures)} ok={len(matches)} fail={len(failures)}", flush=True)

    all_rows = []
    primary_types = Counter()
    status_counts = Counter()
    for match in matches:
        p = match.get("primary") or {}
        status = str(p.get("status") or "SKIP")
        status_counts[status] += 1
        if p:
            primary_types[(str(p.get("scope")), str(p.get("market_type")))] += 1
        for row in match.get("candidates") or []:
            if row.get("status") in {"BET", "LEAN"}:
                all_rows.append(compact(row, match))

    bet_rows = [
        row for row in all_rows
        if row["status"] == "BET"
        and 1.40 <= float(row["odds"] or 0) <= 2.40
        and float(row["probability"] or 0) >= 0.64
        and float(row["quality"] or 0) >= 0.60
    ]
    singles = choose_one_per_event(bet_rows, 5)
    single_ids = {str(x["event_id"]) for x in singles}

    acca_pool = [
        row for row in bet_rows
        if str(row["event_id"]) not in single_ids
        and 1.35 <= float(row["odds"] or 0) <= 2.10
        and float(row["probability"] or 0) >= 0.66
        and float(row["quality"] or 0) >= 0.65
    ]
    acca_legs = choose_one_per_event(acca_pool, 3)
    if len(acca_legs) < 2:
        acca_legs = choose_one_per_event(acca_pool, 2)
    combined_odds = math.prod(float(x["odds"]) for x in acca_legs) if len(acca_legs) >= 2 else None
    combined_p = math.prod(float(x["probability"]) for x in acca_legs) if len(acca_legs) >= 2 else None

    bet_type_counts = Counter((row["scope"], row["market_type"]) for row in bet_rows)
    report = {
        "date_msk": today.isoformat(),
        "fixture_count": len(fixtures),
        "analysed_count": len(matches),
        "failures": failures,
        "status_counts": dict(status_counts),
        "primary_market_types": {
            f"{a}:{b}": n for (a, b), n in sorted(primary_types.items())
        },
        "qualified_bet_market_types": {
            f"{a}:{b}": n for (a, b), n in sorted(bet_type_counts.items())
        },
        "singles": singles,
        "accumulator": {
            "legs": acca_legs,
            "combined_odds": combined_odds,
            "combined_probability": combined_p,
            "expected_value": None if combined_odds is None else combined_odds * combined_p - 1.0,
        },
        "matches": [
            {
                **{k: v for k, v in m.items() if k != "candidates"},
                "top_actionable": [
                    compact(x, m) for x in (m.get("candidates") or [])
                    if x.get("status") in {"BET", "LEAN"}
                ][:8],
            }
            for m in matches
        ],
    }
    Path("full_market_today.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("FULL_DAY_SUMMARY " + json.dumps({
        "fixtures": len(fixtures),
        "analysed": len(matches),
        "failures": len(failures),
        "status_counts": dict(status_counts),
        "bet_market_types": report["qualified_bet_market_types"],
    }, ensure_ascii=False), flush=True)

    print("FULL_DAY_SINGLES", flush=True)
    for i, x in enumerate(singles, 1):
        print(
            f"S{i}. {x['home']} - {x['away']} | {x['scope']} {x['market_type']} "
            f"{x['selection']} @{float(x['odds']):.2f} honest={float(x['honest_probability'] or 0):.4f} "
            f"range={float(x['probability_range_low'] or 0):.4f}-{float(x['probability_range_high'] or 0):.4f} "
            f"raw={float(x['raw_model_probability'] or 0):.4f} market={float(x['market_probability'] or 0):.4f} "
            f"hist_n={int(x['calibration_sample'] or 0)} edge={float(x['edge'] or 0):+.4f} ev={float(x['ev'] or 0):+.4f}",
            flush=True,
        )

    if len(acca_legs) >= 2:
        print(
            f"FULL_DAY_ACCA legs={len(acca_legs)} odds={combined_odds:.3f} "
            f"p={combined_p:.4f} ev={combined_odds * combined_p - 1.0:+.4f}",
            flush=True,
        )
        for i, x in enumerate(acca_legs, 1):
            print(
                f"A{i}. {x['home']} - {x['away']} | {x['scope']} {x['market_type']} "
                f"{x['selection']} @{float(x['odds']):.2f} honest={float(x['honest_probability'] or 0):.4f} "
                f"hist_n={int(x['calibration_sample'] or 0)}",
                flush=True,
            )
    else:
        print("FULL_DAY_ACCA none", flush=True)


if __name__ == "__main__":
    main()
