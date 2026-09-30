from __future__ import annotations

import json
import os
import time

from gool_bot2.ai_prematch_reviewer import AICandidate, OllamaPrematchReviewer


CASES = [
    {
        "id": "araba_hapoel",
        "match": {
            "home": "Araba",
            "away": "Hapoel Migdal HaEmek",
            "competition": "Israel",
            "final_result_hidden": True,
        },
        "facts": {
            "home_last4": {"wins": 0, "draws": 0, "losses": 4, "gf": 1, "ga": 13, "scored_in": 1},
            "away_last4": {"wins": 2, "draws": 1, "losses": 1, "gf": 10, "ga": 7, "scored_2plus_in": 4},
            "h2h": {"matches": 3, "home_wins": 0, "draws": 0, "away_wins": 3, "scores": ["1-2", "1-2", "0-2"]},
        },
        "candidates": [
            {"candidate_id": "araba:away_o05", "market": "away_total", "selection": "over 0.5"},
            {"candidate_id": "araba:away_o15", "market": "away_total", "selection": "over 1.5"},
            {"candidate_id": "araba:under35", "market": "match_total", "selection": "under 3.5"},
            {"candidate_id": "araba:over25", "market": "match_total", "selection": "over 2.5"},
            {"candidate_id": "araba:btts_no", "market": "btts", "selection": "no"},
            {"candidate_id": "araba:x2", "market": "double_chance", "selection": "X2"},
        ],
    },
    {
        "id": "bryne_kolbotn",
        "match": {
            "home": "Bryne W",
            "away": "Kolbotn W",
            "competition": "Norway Division 1 Women",
            "final_result_hidden": True,
        },
        "facts": {
            "home_last5": {"wins": 3, "draws": 0, "losses": 2, "gf": 17, "ga": 8, "scored_in": 3},
            "away_last5": {"wins": 2, "draws": 1, "losses": 2, "gf": 10, "ga": 10, "scored_in": 5},
            "h2h": {"matches": 2, "home_wins": 2, "draws": 0, "away_wins": 0, "scores": ["1-0", "2-0"], "under_2_5_in": 2},
        },
        "candidates": [
            {"candidate_id": "bryne:home_o05", "market": "home_total", "selection": "over 0.5"},
            {"candidate_id": "bryne:home_o15", "market": "home_total", "selection": "over 1.5"},
            {"candidate_id": "bryne:under35", "market": "match_total", "selection": "under 3.5"},
            {"candidate_id": "bryne:over25", "market": "match_total", "selection": "over 2.5"},
            {"candidate_id": "bryne:btts_yes", "market": "btts", "selection": "yes"},
            {"candidate_id": "bryne:home_or_draw", "market": "double_chance", "selection": "1X"},
        ],
    },
]


def main() -> None:
    model = os.getenv("GOOL_AI_PREMATCH_MODEL", "qwen3:4b")
    reviewer = OllamaPrematchReviewer(model=model, timeout_seconds=240, think=False)
    output = {"model": model, "think": False, "cases": []}

    for case in CASES:
        print(f"REVIEW {case['id']} with {model}", flush=True)
        started = time.time()
        row = {"id": case["id"]}
        try:
            review = reviewer.review(
                match_context=case["match"],
                deterministic_facts=case["facts"],
                candidates=[AICandidate.model_validate(x) for x in case["candidates"]],
            )
            row["elapsed_s"] = round(time.time() - started, 2)
            row["review"] = review.model_dump()
        except Exception as exc:
            row["elapsed_s"] = round(time.time() - started, 2)
            row["error"] = f"{type(exc).__name__}: {exc}"
        output["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)

    path = "fast_ai_reviewer_result.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"WROTE {path}", flush=True)


if __name__ == "__main__":
    main()
