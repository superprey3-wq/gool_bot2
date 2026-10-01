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


def _build_conservative_accumulator(matches: list[dict]) -> dict:
    """Build a small conservative accumulator from the full 8-match scan.

    Never forces a leg from every match. It prefers full-time, simpler markets,
    positive EV, higher honest probability and a reasonably useful price.
    """
    allowed_types = {
        "OVER_UNDER",
        "DOUBLE_CHANCE",
        "DRAW_NO_BET",
        "ASIAN_HANDICAP",
        "EUROPEAN_HANDICAP",
    }

    def pick_pool(honest_floor: float, low_floor: float, odds_cap: float) -> list[dict]:
        per_match = []
        for item in matches:
            if item.get("status") == "ERROR":
                continue
            options = []
            for x in item.get("all_candidates") or []:
                if x.get("status") not in {"BET", "LEAN"}:
                    continue
                if x.get("scope") != "FULL_TIME":
                    continue
                if x.get("market_type") not in allowed_types:
                    continue
                try:
                    odds = float(x.get("odds") or 0.0)
                    honest = float(x.get("honest_p") or 0.0)
                    p_low = float(x.get("p_low") or 0.0)
                    ev = float(x.get("ev") or 0.0)
                    confidence = float(x.get("confidence_score") or 0.0)
                except (TypeError, ValueError):
                    continue
                if not (1.25 <= odds <= odds_cap):
                    continue
                if honest < honest_floor or p_low < low_floor or ev < 0.0:
                    continue

                # Conservative ranking: probability first, then lower bound and
                # confidence; lightly penalise price inflation.
                risk_score = (
                    honest
                    + 0.30 * p_low
                    + 0.0010 * confidence
                    - 0.03 * max(0.0, odds - 1.40)
                )
                row = dict(x)
                row.update({
                    "home": item.get("home"),
                    "away": item.get("away"),
                    "league": item.get("league"),
                    "risk_score": round(risk_score, 6),
                })
                options.append(row)

            if options:
                per_match.append(max(options, key=lambda z: float(z.get("risk_score") or 0.0)))
        return per_match

    strict = pick_pool(honest_floor=0.65, low_floor=0.50, odds_cap=1.62)
    policy = "strict"
    pool = strict
    if len(pool) < 2:
        pool = pick_pool(honest_floor=0.62, low_floor=0.47, odds_cap=1.72)
        policy = "fallback"

    pool.sort(
        key=lambda z: (
            float(z.get("risk_score") or 0.0),
            float(z.get("honest_p") or 0.0),
        ),
        reverse=True,
    )

    # Keep the ticket small: accumulator risk compounds quickly.
    legs = pool[:3]
    combined_odds = 1.0
    naive_joint = 1.0
    naive_joint_low = 1.0
    for leg in legs:
        combined_odds *= float(leg.get("odds") or 1.0)
        naive_joint *= float(leg.get("honest_p") or 0.0)
        naive_joint_low *= float(leg.get("p_low") or 0.0)

    return {
        "policy": policy,
        "analysed_matches": len([m for m in matches if m.get("status") != "ERROR"]),
        "eligible_match_legs": len(pool),
        "legs": legs,
        "combined_odds": round(combined_odds, 3) if legs else None,
        "naive_joint_probability": round(naive_joint, 4) if legs else None,
        "naive_joint_low": round(naive_joint_low, 4) if legs else None,
        "note": (
            "Joint probabilities are a simple independence product and are not a guarantee. "
            "The accumulator deliberately does not force one leg from every analysed match."
        ),
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

    accumulator = _build_conservative_accumulator(report["matches"])
    report["conservative_accumulator"] = accumulator
    print(
        "CONSERVATIVE_EXPRESS "
        + json.dumps(
            {
                "policy": accumulator["policy"],
                "analysed_matches": accumulator["analysed_matches"],
                "eligible_match_legs": accumulator["eligible_match_legs"],
                "legs": len(accumulator["legs"]),
                "combined_odds": accumulator["combined_odds"],
                "naive_joint_probability": accumulator["naive_joint_probability"],
                "naive_joint_low": accumulator["naive_joint_low"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    for i, leg in enumerate(accumulator["legs"], 1):
        print(
            f"  E{i}. {leg.get('home')} - {leg.get('away')} | "
            f"{leg.get('scope')} {leg.get('market_type')} {leg.get('selection')} "
            f"@{leg.get('odds')} honest={leg.get('honest_p')} "
            f"range={leg.get('p_low')}-{leg.get('p_high')} "
            f"ev={leg.get('ev')} confidence={leg.get('confidence_score')} "
            f"grade={leg.get('confidence_grade')}",
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
