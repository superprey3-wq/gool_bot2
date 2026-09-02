from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def event_id(row: dict[str, Any]) -> str:
    match = row.get("match") if isinstance(row.get("match"), dict) else {}
    return str(match.get("event_id") or row.get("event_id") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    matches = load(args.matches)
    matches = matches if isinstance(matches, list) else []
    all_ids = {str(m.get("event_id") or "") for m in matches if isinstance(m, dict) and m.get("event_id")}

    summary: dict[str, Any] = {
        "flashscore_live_tested": len(matches),
        "sources": {},
        "union": {},
    }
    covered_union: set[str] = set()

    for path in args.inputs:
        rows = load(path)
        if not isinstance(rows, list):
            continue
        source = str(rows[0].get("source") if rows and isinstance(rows[0], dict) else path.stem)
        ok_rows = [r for r in rows if isinstance(r, dict) and r.get("ok")]
        ids = {event_id(r) for r in ok_rows if event_id(r)}
        covered_union.update(ids)
        total = len(rows)
        ok = len(ok_rows)
        source_ok = any(bool(r.get("source_ok")) for r in rows if isinstance(r, dict))
        summary["sources"][source] = {
            "source_retrieved_live_odds": source_ok,
            "matched_live": ok,
            "tested_live": total,
            "coverage": ok / total if total else 0.0,
        }

    uncovered = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        eid = str(m.get("event_id") or "")
        if eid and eid not in covered_union:
            uncovered.append({
                "event_id": eid,
                "home": m.get("home"),
                "away": m.get("away"),
                "minute": m.get("minute"),
                "score": f"{m.get('home_score', 0)}:{m.get('away_score', 0)}",
            })

    summary["union"] = {
        "matched_by_at_least_one_source": len(covered_union & all_ids),
        "tested_live": len(matches),
        "coverage": (len(covered_union & all_ids) / len(matches)) if matches else 0.0,
        "uncovered_count": len(uncovered),
        "uncovered": uncovered,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("ALL_LIVE_ODDS_SUMMARY")
    print(f"  Flashscore live tested: {len(matches)}")
    for source, stats in summary["sources"].items():
        print(
            f"  {source}: live_odds={stats['source_retrieved_live_odds']} "
            f"matched={stats['matched_live']}/{stats['tested_live']} coverage={stats['coverage']:.1%}"
        )
    union = summary["union"]
    print(
        f"  UNION: {union['matched_by_at_least_one_source']}/{union['tested_live']} "
        f"coverage={union['coverage']:.1%} uncovered={union['uncovered_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
