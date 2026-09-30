from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from math import prod
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.v4_prematch_engine import (
    PrematchPick,
    blend_with_market,
    build_prematch_candidates,
)
from gool_bot2.v4_shadow_report import _analyse_fixtures
from gool_bot2.xbet_prematch_market import XBetPrematchCollector, find_prematch_market


WINDOW_MINUTES = int(os.getenv("GOOL_AI_ACCA_WINDOW_MINUTES", "150"))
MAX_FIXTURES = int(os.getenv("GOOL_AI_ACCA_MAX_FIXTURES", "12"))
MAX_CANDIDATES = int(os.getenv("GOOL_AI_ACCA_MAX_CANDIDATES", "10"))
MODEL = os.getenv("GOOL_AI_PREMATCH_MODEL", "qwen3:1.7b")
OLLAMA = os.getenv("GOOL_AI_PREMATCH_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


class AccaChoice(BaseModel):
    decision: str
    candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=420)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        try:
            x = float(value)
        except (TypeError, ValueError):
            return value
        if 1 < x <= 10:
            return x / 10
        if 10 < x <= 100:
            return x / 100
        return x


SYSTEM = """You are GOOL's accumulator assembler.
You receive only real prematch candidates that have ALREADY PASSED GOOL's deterministic numeric qualification gate.
GOOL's deterministic calculations are authoritative.

Rules:
- Return ACCA only when 2 or 3 supplied candidates form a coherent high-confidence accumulator.
- Otherwise return NO_ACCA with no candidate_ids.
- Use only exact candidate_ids from allowed_candidates.
- Never invent a market, line, price, fact, injury, result or candidate.
- Never select two candidates from the same event_id.
- Prefer robust conservative legs over aggressive legs.
- Do not select a leg just to reach 2 or 3 selections.
- Compare model_probability, edge, EV, quality, odds and evidence_support.
- IMPORTANT numeric semantics: model_probability >= 0.68 is already qualified; >= 0.72 is strong in this pool. data_quality >= 0.65 is qualified; >= 0.80 is strong; 1.0 is maximum quality.
- source_coverage values are COUNTS of observations/items from each source, NOT percentages. Never describe them as "<50%".
- pair_sample=8 is a full recent sample for this diagnostic; do not require 10.
- The same market type (for example Over 2.5) may appear in two DIFFERENT events. That is allowed and is not a conflict by itself.
- Do not reject an already-qualified leg merely because you would have preferred a different market not present in allowed_candidates.
- Reject a leg only for a concrete football/evidence contradiction or materially weaker numeric profile versus alternatives.
- Keep the explanation short.
- confidence is qualitative confidence in the ticket, not a calibrated win probability.
Return only the structured object.
"""


def _enrich(match, base_quality: float) -> tuple[dict, float, dict]:
    fs = FlashscoreProvider()
    fusion = PrematchDataFusion(fs)
    ctx = fusion.context(match, limit=8)
    profile = build_prematch_goal_profile(
        {"match": {"home": match.home, "away": match.away}, "prematch_context": ctx}
    )
    samples = [int((profile.get(k) or {}).get("pair_sample") or 0) for k in ("first_half", "second_half", "full_match")]
    sample = max(samples or [0])
    coverage = sum(1 for v in (ctx.get("source_coverage") or {}).values() if int(v or 0) > 0)
    quality = max(
        float(base_quality or 0),
        min(1.0, 0.78 * min(1.0, sample / 8.0) + 0.22 * min(1.0, coverage / 3.0)),
    )
    facts = {
        "source_coverage": ctx.get("source_coverage") or {},
        "full_match": {
            k: (profile.get("full_match") or {}).get(k)
            for k in ("pair_sample", "home_expected_goals", "away_expected_goals", "expected_total")
        },
    }
    return profile, quality, facts


def _qualifies(p: PrematchPick) -> bool:
    return (
        1.30 <= p.odds <= 1.90
        and p.model_probability >= 0.68
        and p.edge >= 0.03
        and p.expected_value >= 0.015
        and p.data_quality >= 0.65
    )


def _request(candidates: list[dict]) -> AccaChoice:
    schema = AccaChoice.model_json_schema()
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"allowed_candidates": candidates}, ensure_ascii=False, separators=(",", ":"))},
        ],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 260},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as res:
        raw = json.loads(res.read().decode())
    text = raw.get("message", {}).get("content", "")
    try:
        choice = AccaChoice.model_validate_json(text)
    except ValidationError as exc:
        raise RuntimeError(f"invalid model output: {exc}; raw={text[:700]!r}") from exc
    return choice


def main() -> None:
    now = time.time()
    end = now + WINDOW_MINUTES * 60
    fs = FlashscoreProvider()
    fixtures = []
    for m in fs.scheduled_matches_for_day(0):
        ts = float((m.meta or {}).get("scheduled_start_ts") or 0)
        if now < ts <= end:
            fixtures.append(m)
    fixtures.sort(key=lambda m: float((m.meta or {}).get("scheduled_start_ts") or 0))
    fixtures = fixtures[:MAX_FIXTURES]
    print(f"ACCA_FIXTURES window_min={WINDOW_MINUTES} count={len(fixtures)}", flush=True)
    for m in fixtures:
        print(f"FIXTURE {m.home} -- {m.away} | {m.league} | ts={(m.meta or {}).get('scheduled_start_ts')}", flush=True)
    if len(fixtures) < 2:
        raise SystemExit("fewer than two upcoming fixtures")

    analysed, failures = _analyse_fixtures(fs, fixtures)
    analysed = [r for r in analysed if r.get("primary_trend")]
    print(f"ACCA_ANALYSED eligible={len(analysed)} failures={len(failures)}", flush=True)

    enriched = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(analysed)))) as pool:
        futs = {pool.submit(_enrich, r["match"], float(r.get("quality") or 0)): r for r in analysed}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                profile, quality, facts = fut.result()
                enriched.append({**r, "profile": profile, "quality": quality, "ai_facts": facts})
            except Exception as exc:
                print(f"FUSION_FAIL {r['match'].home} -- {r['match'].away} {type(exc).__name__}:{exc}", flush=True)

    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    state_path = runtime / "live" / "ai_acca_xbet.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    XBetPrematchCollector(state_path).collect_once(targets=[r["match"] for r in enriched])

    event_best: dict[str, tuple[PrematchPick, dict]] = {}
    for r in enriched:
        m = r["match"]
        market = find_prematch_market(m.home, m.away, path=state_path)
        if not market:
            print(f"NO_MARKET {m.home} -- {m.away}", flush=True)
            continue
        raw = build_prematch_candidates(
            event_id=str(m.provider_match_id),
            home=m.home,
            away=m.away,
            profile=r["profile"],
            market=market,
            data_quality=float(r["quality"]),
        )
        kick = float((m.meta or {}).get("scheduled_start_ts") or 0)
        priced = [
            PrematchPick(
                p.event_id, p.home, p.away, p.market, p.selection, p.odds,
                p.model_probability, p.market_probability, p.data_quality,
                m.league or "", kick,
            )
            for p in raw
        ]
        blended = [blend_with_market(p) for p in priced]
        eligible = [p for p in blended if _qualifies(p)]
        eligible.sort(
            key=lambda p: (p.model_probability * p.data_quality, p.edge, p.expected_value),
            reverse=True,
        )
        if not eligible:
            print(f"NO_QUALIFIED {m.home} -- {m.away} raw={len(raw)}", flush=True)
            continue
        best = eligible[0]
        event_best[best.event_id] = (best, r["ai_facts"])
        print(
            f"QUALIFIED {m.home} -- {m.away} | {best.market}:{best.selection} @{best.odds:.2f} "
            f"p={best.model_probability:.3f} edge={best.edge:+.3f} ev={best.expected_value:+.3f} q={best.data_quality:.2f}",
            flush=True,
        )

    rows = list(event_best.values())
    rows.sort(
        key=lambda item: (
            item[0].model_probability * item[0].data_quality,
            item[0].edge,
            item[0].expected_value,
        ),
        reverse=True,
    )
    rows = rows[:MAX_CANDIDATES]
    candidates = []
    by_id = {}
    for idx, (p, facts) in enumerate(rows, 1):
        cid = f"{p.event_id}:leg{idx}"
        by_id[cid] = p
        candidates.append({
            "candidate_id": cid,
            "event_id": p.event_id,
            "match": f"{p.home} - {p.away}",
            "league": p.league,
            "market": p.market,
            "selection": p.selection,
            "odds": round(p.odds, 3),
            "model_probability": round(p.model_probability, 4),
            "edge": round(p.edge, 4),
            "expected_value": round(p.expected_value, 4),
            "data_quality": round(p.data_quality, 3),
            "evidence_support": facts,
        })

    print("ACCA_CANDIDATES", json.dumps(candidates, ensure_ascii=False), flush=True)

    if len(candidates) < 2:
        result = {
            "decision": "NO_ACCA",
            "reason": "Fewer than two GOOL-qualified candidates in the upcoming window.",
            "candidates": candidates,
        }
    else:
        choice = _request(candidates)
        ids = list(dict.fromkeys(choice.candidate_ids))
        if choice.decision != "ACCA":
            ids = []
        if choice.decision == "ACCA":
            if not (2 <= len(ids) <= 3):
                raise SystemExit(f"invalid leg count from model: {ids}")
            if any(cid not in by_id for cid in ids):
                raise SystemExit(f"model invented candidate ids: {ids}")
            events = [by_id[cid].event_id for cid in ids]
            if len(set(events)) != len(events):
                raise SystemExit(f"model selected duplicate event: {events}")
            legs = [by_id[cid] for cid in ids]
            result = {
                "decision": "ACCA",
                "model": MODEL,
                "confidence": choice.confidence,
                "reason": choice.reason,
                "combined_odds": round(prod(p.odds for p in legs), 3),
                "naive_combined_probability": round(prod(p.model_probability for p in legs), 4),
                "legs": [
                    {
                        "candidate_id": cid,
                        "match": f"{by_id[cid].home} - {by_id[cid].away}",
                        "league": by_id[cid].league,
                        "market": by_id[cid].market,
                        "selection": by_id[cid].selection,
                        "odds": by_id[cid].odds,
                        "model_probability": round(by_id[cid].model_probability, 4),
                        "edge": round(by_id[cid].edge, 4),
                        "expected_value": round(by_id[cid].expected_value, 4),
                        "quality": round(by_id[cid].data_quality, 3),
                    }
                    for cid in ids
                ],
                "candidate_pool": candidates,
            }
        else:
            result = {
                "decision": "NO_ACCA",
                "model": MODEL,
                "confidence": choice.confidence,
                "reason": choice.reason,
                "candidate_pool": candidates,
            }

    with open("ai_accumulator_live_result.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print("=== AI ACCUMULATOR RESULT ===", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
