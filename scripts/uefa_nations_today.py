from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from gool_bot2.providers.common import pair_score
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.v4_prematch_engine import (
    PrematchPick,
    blend_with_market,
    build_prematch_candidates,
    signal_tier,
)
from gool_bot2.v4_shadow_report import _match_xbet
from gool_bot2.xbet_prematch_market import XBetPrematchCollector


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


def family(p: PrematchPick) -> str:
    if p.market == "match_1x2":
        return "1X2"
    if p.market == "match_total":
        return "TOTAL"
    if p.market == "btts":
        return "BTTS"
    if p.market in {"home_total", "away_total"}:
        return "TEAM_TOTAL"
    return str(p.market or "OTHER").upper()


def pick_score(p: PrematchPick) -> tuple:
    tier = signal_tier(p)
    tier_rank = 2 if tier == "STRONG" else 1 if tier == "NORMAL" else 0
    return (
        tier_rank,
        p.expected_value,
        p.edge,
        p.model_probability,
        p.data_quality,
    )


def compact(p: PrematchPick | None) -> dict[str, Any] | None:
    if p is None:
        return None
    tier = signal_tier(p)
    return {
        "market_family": family(p),
        "market": p.market,
        "selection": p.selection,
        "odds": round(float(p.odds), 3),
        "probability": round(float(p.model_probability), 4),
        "market_probability": round(float(p.market_probability), 4),
        "edge": round(float(p.edge), 4),
        "ev": round(float(p.expected_value), 4),
        "quality": round(float(p.data_quality), 3),
        "tier": tier,
        "status": "BET" if tier == "STRONG" else "LEAN" if tier == "NORMAL" else "SKIP",
    }


def main() -> None:
    fs = FlashscoreProvider()
    msk = timezone(timedelta(hours=3))
    today = datetime.now(msk).date()
    now_ts = datetime.now(timezone.utc).timestamp()

    raw = fs.parse_master_scheduled(fs._feed("f_1_0_3_en_1", timeout=12, max_hosts=1))
    scheduled = [
        m for m in raw
        if (m.meta or {}).get("scheduled_start_ts")
        and datetime.fromtimestamp(float(m.meta["scheduled_start_ts"]), msk).date() == today
        and float(m.meta["scheduled_start_ts"]) > now_ts
    ]

    found = []
    missing = []
    for home, away in TARGETS:
        best = None
        best_score = 0.0
        for m in scheduled:
            score = pair_score(m.home, m.away, home, away)
            if score > best_score:
                best, best_score = m, score
        if best is not None and best_score >= 0.72:
            found.append(best)
        else:
            missing.append({"home": home, "away": away, "best_score": round(best_score, 3)})

    print(f"NATIONS_TARGETS total={len(TARGETS)} found={len(found)} missing={len(missing)}", flush=True)
    for m in found:
        print(f"NATIONS_FIXTURE {m.home} - {m.away} | {m.league} | FS={m.provider_match_id}", flush=True)
    if missing:
        print("NATIONS_MISSING " + json.dumps(missing, ensure_ascii=False), flush=True)

    fusion = PrematchDataFusion(fs)
    analysed = []
    for m in found:
        try:
            context = fusion.context(m, limit=10)
            profile = build_prematch_goal_profile({
                "match": {"home": m.home, "away": m.away},
                "prematch_context": context,
            })
            samples = [int((profile.get(k) or {}).get("pair_sample") or 0) for k in ("first_half", "second_half", "full_match")]
            sample = max(samples or [0])
            coverage = sum(1 for v in (context.get("source_coverage") or {}).values() if int(v or 0) > 0)
            quality = max(0.0, min(1.0, 0.78 * min(1.0, sample / 8.0) + 0.22 * min(1.0, coverage / 3.0)))
            analysed.append((m, profile, quality, sample, context))
        except Exception as exc:
            print(f"NATIONS_PROFILE_FAIL {m.home} - {m.away} {type(exc).__name__}:{exc}", flush=True)

    state_path = Path("/tmp/uefa_nations_today_xbet.json")
    try:
        state = XBetPrematchCollector(state_path).collect_once(targets=[x[0] for x in analysed])
    except Exception as exc:
        print(f"NATIONS_XBET_FAIL {type(exc).__name__}:{exc}", flush=True)
        state = {"matches": {}}
    xbet_rows = [r for r in (state.get("matches") or {}).values() if isinstance(r, dict)]

    results = []
    family_wins = {}
    for m, profile, quality, sample, context in analysed:
        market, match_score = _match_xbet(m, xbet_rows)
        if not market:
            results.append({
                "home": m.home, "away": m.away, "league": m.league,
                "status": "SKIP", "reason": "NO_CONFIDENT_MARKET_MATCH",
                "quality": round(quality, 3), "sample": sample,
                "market_match_score": round(float(match_score), 3),
            })
            print(f"NATIONS_RESULT {m.home} - {m.away} | SKIP no_market", flush=True)
            continue

        raw_candidates = build_prematch_candidates(
            event_id=str(m.provider_match_id),
            home=m.home,
            away=m.away,
            profile=profile,
            market=market,
            data_quality=quality,
        )
        blended = [blend_with_market(p) for p in raw_candidates]

        best_by_family = {}
        for fam in ("1X2", "TOTAL", "BTTS", "TEAM_TOTAL"):
            rows = [p for p in blended if family(p) == fam and 1.35 <= float(p.odds) <= 3.50]
            rows.sort(key=pick_score, reverse=True)
            best_by_family[fam] = rows[0] if rows else None

        all_best = [p for p in best_by_family.values() if p is not None]
        all_best.sort(key=pick_score, reverse=True)
        primary = all_best[0] if all_best else None
        alternative = all_best[1] if len(all_best) > 1 else None
        if primary:
            family_wins[family(primary)] = family_wins.get(family(primary), 0) + 1

        full = profile.get("full_match") or {}
        item = {
            "home": m.home,
            "away": m.away,
            "league": m.league,
            "kickoff_ts": float((m.meta or {}).get("scheduled_start_ts") or 0),
            "quality": round(quality, 3),
            "sample": sample,
            "sources": context.get("sources") or [],
            "market_match_score": round(float(match_score), 3),
            "model_goals": {
                "home": full.get("home_expected_goals"),
                "away": full.get("away_expected_goals"),
                "total": full.get("expected_total"),
            },
            "families": {k: compact(v) for k, v in best_by_family.items()},
            "primary": compact(primary),
            "alternative": compact(alternative),
        }
        item["status"] = item["primary"]["status"] if item.get("primary") else "SKIP"
        results.append(item)
        ptxt = item.get("primary") or {}
        atxt = item.get("alternative") or {}
        print(
            f"NATIONS_RESULT {m.home} - {m.away} | {item['status']} | "
            f"PRIMARY={ptxt.get('market_family')} {ptxt.get('selection')} @{ptxt.get('odds')} "
            f"p={ptxt.get('probability')} edge={ptxt.get('edge')} ev={ptxt.get('ev')} | "
            f"ALT={atxt.get('market_family')} {atxt.get('selection')} @{atxt.get('odds')}",
            flush=True,
        )

    report = {
        "date_msk": today.isoformat(),
        "target_count": len(TARGETS),
        "found_count": len(found),
        "missing": missing,
        "family_wins": family_wins,
        "results": results,
    }
    Path("uefa_nations_today.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== UEFA NATIONS TODAY REPORT ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
