from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import targeted_new_sources_live_audit as base  # noqa: E402

KEPT_SOURCES = [
    "kambi_unibet",
    "entain_ladbrokes",
    "pointsbet",
    "pinnacle_arcadia",
]

KEPT_GROUPS = [
    "entain.graphql",
    "pointsbet.sports",
    "pinnacle.sports",
]


def target_from_fs(row: dict[str, Any]) -> dict[str, Any]:
    raw_league = str(row.get("league") or "")
    if ":" in raw_league:
        country, league = raw_league.split(":", 1)
    else:
        country, league = "", raw_league
    event_id = str(row.get("event_id") or "").strip()
    home = str(row.get("home") or "").strip()
    away = str(row.get("away") or "").strip()
    key = event_id or re.sub(r"[^a-z0-9]+", "_", f"{home}_{away}".casefold()).strip("_")
    return {
        "key": key,
        "event_id": event_id,
        "home": home,
        "away": away,
        "country": country.strip(),
        "league": league.strip(),
        "home_aliases": [home],
        "away_aliases": [away],
    }


def select_same_events(rows: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    by_id = {str(r.get("event_id") or ""): r for r in rows if str(r.get("event_id") or "")}
    out: dict[str, dict[str, Any] | None] = {}
    for target in targets:
        eid = str(target.get("event_id") or "")
        if eid and eid in by_id:
            out[target["key"]] = by_id[eid]
            continue
        ranked = sorted(
            ((base.target_score(row, target), row) for row in rows),
            key=lambda x: x[0],
            reverse=True,
        )
        out[target["key"]] = ranked[0][1] if ranked and ranked[0][0] >= 0.72 else None
    return out


async def capture_kept(mcp: Any, selected: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["kambi_unibet"] = await base.source_kambi(selected)
    result["entain_ladbrokes"] = await base.source_entain(mcp, selected)
    result["pointsbet"] = await base.source_pointsbet(mcp, selected)
    result["pinnacle_arcadia"] = await base.source_pinnacle(mcp, selected)
    return result


async def run(args: argparse.Namespace) -> int:
    from sportsdata_mcp.config import Config
    from sportsdata_mcp.server import build_server

    args.output_dir.mkdir(parents=True, exist_ok=True)

    initial_live = await asyncio.to_thread(base.fs_rows)
    targets = [target_from_fs(row) for row in initial_live]
    base.TARGETS = targets
    base.SOURCES = KEPT_SOURCES
    base.GROUPS = KEPT_GROUPS

    base.write_json(args.output_dir / "initial_flashscore_live.json", initial_live)
    base.write_json(args.output_dir / "targets.json", targets)
    print(f"FINAL_KEPT_AUDIT initial_flashscore_live={len(initial_live)}", flush=True)
    for row in initial_live:
        print(
            f"  FS {row.get('minute')}m {row.get('home')} - {row.get('away')} "
            f"{row.get('home_score')}:{row.get('away_score')} id={row.get('event_id')}",
            flush=True,
        )

    mcp, registry = build_server(Config(enabled_groups=KEPT_GROUPS))
    snapshots: list[dict[str, Any]] = []
    try:
        tools = sorted(t.name for t in await mcp.list_tools())
        base.write_json(args.output_dir / "registered_tools.json", tools)
        for idx in range(1, max(1, args.snapshots) + 1):
            captured_at = base.utc_now()
            all_live = await asyncio.to_thread(base.fs_rows)
            selected = select_same_events(all_live, targets)
            flashscore = {key: base.fs_state(row) for key, row in selected.items()}
            print(f"SNAPSHOT {idx}/{args.snapshots} at={captured_at} current_flashscore_live={len(all_live)}", flush=True)
            sources = await capture_kept(mcp, selected)
            for source in KEPT_SOURCES:
                matched = sum(
                    1
                    for row in (sources[source].get("matches") or {}).values()
                    if row.get("status") == "matched"
                )
                print(
                    f"  {source}: state={sources[source].get('state')} matched_with_quotes={matched}/{len(targets)}",
                    flush=True,
                )
            snap = {
                "snapshot": idx,
                "captured_at": captured_at,
                "flashscore": flashscore,
                "sources": sources,
            }
            snapshots.append(snap)
            base.write_json(args.output_dir / "snapshots" / f"snapshot_{idx}.json", snap)
            if idx < args.snapshots:
                await asyncio.sleep(max(1, args.interval))
    finally:
        await registry.aclose()

    summary = base.summarize(snapshots)
    summary["initial_flashscore_live_matches"] = len(initial_live)
    summary["kept_sources"] = KEPT_SOURCES
    summary["source_coverage"] = {}
    for source in KEPT_SOURCES:
        counts = summary.get("source_summary", {}).get(source, {})
        quote_events = sum(counts.get(v, 0) for v in ("PROVEN", "FOUND_NO_CHANGE", "ODDS_CHANGED_NO_FS_PROGRESS"))
        summary["source_coverage"][source] = {
            "quote_events": quote_events,
            "total_events": len(targets),
            "coverage_pct": round(100.0 * quote_events / len(targets), 1) if targets else 0.0,
            "proven": counts.get("PROVEN", 0),
            "verdicts": counts,
        }

    base.write_json(args.output_dir / "audit.json", snapshots)
    base.write_json(args.output_dir / "summary.json", summary)
    print(
        "FINAL_KEPT_AUDIT_RESULT "
        + json.dumps(
            {
                "initial_flashscore_live_matches": summary["initial_flashscore_live_matches"],
                "source_coverage": summary["source_coverage"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("final_kept_sources_audit"))
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
