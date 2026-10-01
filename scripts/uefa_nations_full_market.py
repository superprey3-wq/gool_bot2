from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from gool_bot2.flashscore_odds import fetch_event_odds
from gool_bot2.full_market_brain import analyze_full_market
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.common import pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.prematch_fusion import PrematchDataFusion


TARGETS = [
    ("Azerbaijan", "Liechtenstein"),
    ("Germany", "Serbia"),
    ("Greece", "Netherlands"),
    ("Denmark", "Portugal"),
    ("Wales", "Norway"),
    ("Israel", "Kosovo"),
    ("Republic of Ireland", "Austria"),
    ("Malta", "Gibraltar"),
]


def _quality(profile: dict, context: dict) -> tuple[float, int, int]:
    samples = [
        int((profile.get(k) or {}).get("pair_sample") or 0)
        for k in ("first_half", "second_half", "full_match")
    ]
    sample = max(samples or [0])
    coverage = sum(
        1 for v in (context.get("source_coverage") or {}).values()
        if int(v or 0) > 0
    )
    sample_quality = min(1.0, sample / 8.0)
    source_quality = min(1.0, coverage / 3.0)
    q = min(1.0, 0.78 * sample_quality + 0.22 * source_quality)
    return q, sample, coverage


def _compact(row: dict) -> dict:
    return {
        "scope": row.get("scope"),
        "market_type": row.get("market_type"),
        "selection": row.get("selection"),
        "odds": row.get("odds"),
        "bookmaker": row.get("bookmaker"),
        "p": row.get("model_probability"),
        "honest_p": row.get("honest_probability"),
        "p_low": row.get("probability_range_low"),
        "p_high": row.get("probability_range_high"),
        "calibration_sample": row.get("calibration_sample"),
        "calibration_confidence": row.get("calibration_confidence"),
        "calibration_source": row.get("calibration_source"),
        "profile_sample": row.get("profile_sample"),
        "raw_p": row.get("raw_model_probability"),
        "market_p": row.get("market_probability"),
        "edge": row.get("edge"),
        "ev": row.get("expected_value"),
        "raw_ev": row.get("raw_expected_value"),
        "status": row.get("status"),
        "confidence_score": row.get("confidence_score"),
        "confidence_grade": row.get("confidence_grade"),
        "confidence_components": row.get("confidence_components"),
        "decision": row.get("decision"),
        "settlement": row.get("settlement"),
        "observations": row.get("observations"),
    }


def main() -> None:
    fs = FlashscoreProvider()
    fusion = PrematchDataFusion(fs)
    msk = timezone(timedelta(hours=3))
    today = datetime.now(msk).date()
    now_ts = datetime.now(timezone.utc).timestamp()

    scheduled = [
        m for m in fs.parse_master_scheduled(fs._feed("f_1_0_3_en_1", timeout=12, max_hosts=1))
        if (m.meta or {}).get("scheduled_start_ts")
        and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]), msk).date() == today
        and float(m.meta["scheduled_start_ts"]) > now_ts
    ]

    matches = []
    missing = []
    for home, away in TARGETS:
        best = None
        score = 0.0
        for m in scheduled:
            s = pair_score(m.home, m.away, home, away)
            if s > score:
                best, score = m, s
        if best is not None and score >= 0.72:
            matches.append(best)
        else:
            missing.append({"home": home, "away": away, "best_score": round(score, 3)})

    print(f"FULL_MARKET_TARGETS total={len(TARGETS)} found={len(matches)} missing={len(missing)}", flush=True)

    report = {
        "date_msk": today.isoformat(),
        "target_count": len(TARGETS),
        "found_count": len(matches),
        "missing": missing,
        "matches": [],
    }
    winner_types = Counter()
    bet_types = Counter()
    status_counts = Counter()
    all_unmodeled = Counter()

    for m in matches:
        try:
            context = fusion.context(m, limit=10)
            profile = build_prematch_goal_profile({
                "match": {"home": m.home, "away": m.away},
                "prematch_context": context,
            })
            quality, sample, coverage = _quality(profile, context)
            odds = fetch_event_odds(m.provider_match_id)
            analysis = analyze_full_market(odds, profile, quality=quality)
        except Exception as exc:
            print(f"FULL_MARKET_FAIL {m.home} - {m.away} {type(exc).__name__}:{exc}", flush=True)
            report["matches"].append({
                "home": m.home, "away": m.away, "status": "ERROR",
                "error": f"{type(exc).__name__}:{exc}",
            })
            continue

        candidates = list(analysis.get("candidates") or [])
        actionable = [x for x in candidates if x.get("status") in {"BET", "LEAN"}]
        primary = analysis.get("best_pick")
        status = str((primary or {}).get("decision") or "SKIP")
        status_counts[status] += 1
        if primary:
            winner_types[(primary.get("scope"), primary.get("market_type"))] += 1
        for x in candidates:
            if x.get("status") == "BET":
                bet_types[(x.get("scope"), x.get("market_type"))] += 1
        for x in analysis.get("unmodeled_market_types") or []:
            all_unmodeled[(x["scope"], x["type"])] += int(x.get("selections") or 0)

        top = [_compact(x) for x in candidates[:20]]
        top_actionable = [_compact(x) for x in actionable[:12]]
        item = {
            "event_id": m.provider_match_id,
            "home": m.home,
            "away": m.away,
            "league": m.league,
            "kickoff_ts": float((m.meta or {}).get("scheduled_start_ts") or 0),
            "quality": round(quality, 4),
            "sample": sample,
            "source_coverage": coverage,
            "sources": context.get("sources") or [],
            "profile_goals": {
                "first_half": {
                    "home": (profile.get("first_half") or {}).get("home_expected_goals"),
                    "away": (profile.get("first_half") or {}).get("away_expected_goals"),
                },
                "second_half": {
                    "home": (profile.get("second_half") or {}).get("home_expected_goals"),
                    "away": (profile.get("second_half") or {}).get("away_expected_goals"),
                },
                "full_match": {
                    "home": (profile.get("full_match") or {}).get("home_expected_goals"),
                    "away": (profile.get("full_match") or {}).get("away_expected_goals"),
                },
            },
            "modeled_market_types": analysis.get("modeled_market_types") or [],
            "unmodeled_market_types": analysis.get("unmodeled_market_types") or [],
            "candidate_count": len(candidates),
            "actionable_count": len(actionable),
            "primary": None if primary is None else _compact(primary),
            "top_actionable": top_actionable,
            "top20": top,
            "all_candidates": [_compact(x) for x in candidates],
        }
        report["matches"].append(item)

        p = item["primary"] or {}
        print(
            f"FULL_MARKET_RESULT {m.home} - {m.away} | {status} | "
            f"{p.get('scope')} {p.get('market_type')} {p.get('selection')} "
            f"@{p.get('odds')} honest={p.get('honest_p')} range={p.get('p_low')}-{p.get('p_high')} "
            f"raw={p.get('raw_p')} market={p.get('market_p')} hist_n={p.get('calibration_sample')} "
            f"edge={p.get('edge')} ev={p.get('ev')} confidence={p.get('confidence_score')} "
            f"grade={p.get('confidence_grade')} | "
            f"modeled_types={len(item['modeled_market_types'])} candidates={len(candidates)} "
            f"actionable={len(actionable)} unmodeled_types={len(item['unmodeled_market_types'])}",
            flush=True,
        )
        for i, x in enumerate(top_actionable[:6], 1):
            print(
                f"  A{i}. {x['status']} {x['scope']} {x['market_type']} {x['selection']} "
                f"@{x['odds']} honest={x['honest_p']} range={x['p_low']}-{x['p_high']} "
                f"raw={x['raw_p']} market={x['market_p']} hist_n={x['calibration_sample']} "
                f"edge={x['edge']} ev={x['ev']}",
                flush=True,
            )

    report["summary"] = {
        "status_counts": dict(status_counts),
        "primary_market_types": {
            f"{scope}:{typ}": n for (scope, typ), n in sorted(winner_types.items())
        },
        "bet_market_types": {
            f"{scope}:{typ}": n for (scope, typ), n in sorted(bet_types.items())
        },
        "unmodeled_market_types": [
            {"scope": scope, "type": typ, "selections": n}
            for (scope, typ), n in sorted(all_unmodeled.items())
        ],
    }
    Path("uefa_nations_full_market.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FULL_MARKET_SUMMARY " + json.dumps(report["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
