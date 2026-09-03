from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


def _line(rows: list[dict[str, Any]], value: float) -> dict[str, Any] | None:
    for row in rows or []:
        try:
            if abs(float(row.get("line")) - float(value)) < 1e-9:
                return dict(row)
        except (TypeError, ValueError):
            continue
    return None


def _odd(row: dict[str, Any] | None) -> float | None:
    if not row or row.get("over") is None:
        return None
    try:
        return float(row.get("over"))
    except (TypeError, ValueError):
        return None


def _required_markets(match: Any, market_row: dict[str, Any] | None) -> dict[str, Any]:
    hs = int(match.home_score or 0)
    aws = int(match.away_score or 0)
    minute = int(match.minute or 0)
    total = hs + aws
    markets = (market_row or {}).get("markets") or {}
    mt = list(markets.get("match_total") or [])
    ht1 = list(markets.get("first_half_total") or [])
    home = list(markets.get("home_total") or [])
    away = list(markets.get("away_total") or [])
    btts = markets.get("btts") or {}

    checks = {
        "another_goal": _odd(_line(mt, total + 0.5)),
        "two_more_goals": _odd(_line(mt, total + 1.5)),
        "home_goal": _odd(_line(home, hs + 0.5)),
        "away_goal": _odd(_line(away, aws + 0.5)),
        "btts_yes": None if btts.get("yes") is None else float(btts.get("yes")),
        "first_half_goal": _odd(_line(ht1, total + 0.5)) if 0 < minute <= 45 else None,
    }
    core_keys = ("another_goal", "two_more_goals", "home_goal", "away_goal", "btts_yes")
    core_missing = [key for key in core_keys if checks.get(key) is None]
    return {
        "checks": checks,
        "core_available": [key for key in core_keys if checks.get(key) is not None],
        "core_missing": core_missing,
        "all_core_available": not core_missing,
        "first_half_applicable": 0 < minute <= 45,
        "first_half_available": 0 < minute <= 45 and checks.get("first_half_goal") is not None,
        "decoded_counts": {
            "match_total": len(mt),
            "first_half_total": len(ht1),
            "home_total": len(home),
            "away_total": len(away),
            "btts": int(btts.get("yes") is not None) + int(btts.get("no") is not None),
        },
        "decoded_lines": {
            "match_total": [row.get("line") for row in mt],
            "first_half_total": [row.get("line") for row in ht1],
            "home_total": [row.get("line") for row in home],
            "away_total": [row.get("line") for row in away],
        },
    }


def _snapshot(collector: RobustXBetMarketCollector, index: int) -> dict[str, Any]:
    captured = datetime.now(timezone.utc).isoformat()
    flashscore = FlashscoreProvider().live_matches()
    state = collector.collect_once()
    xbet = state.get("matches") or {}
    rows: list[dict[str, Any]] = []
    for match in flashscore:
        mid = str(match.provider_match_id)
        market_row = xbet.get(mid)
        minute = int(match.minute or 0)
        coverage = _required_markets(match, market_row)
        rows.append({
            "flashscore_event_id": mid,
            "xbet_event_id": None if market_row is None else market_row.get("xbet_event_id"),
            "home": match.home,
            "away": match.away,
            "league": match.league,
            "minute": minute,
            "active_standard_window": 0 < minute <= 75,
            "active_another_goal_window": 10 <= minute <= 85,
            "active_first_half_window": 0 < minute <= 45,
            "score": [int(match.home_score or 0), int(match.away_score or 0)],
            "mapped_to_1xbet": market_row is not None,
            "xbet_minute": None if market_row is None else market_row.get("minute"),
            "xbet_score": None if market_row is None else [market_row.get("score_home"), market_row.get("score_away")],
            "market_captured_at": None if market_row is None else market_row.get("captured_at"),
            "coverage": coverage,
            "pressure": {} if market_row is None else market_row.get("pressure") or {},
        })
    return {
        "snapshot": index,
        "captured_at": captured,
        "flashscore_live_count": len(flashscore),
        "xbet_mapped_count": sum(1 for row in rows if row.get("mapped_to_1xbet")),
        "xbet_root": state.get("root"),
        "xbet_index_root_counts": dict(getattr(collector, "_index_root_counts", {}) or {}),
        "xbet_index_merged_events": len(getattr(collector, "_event_roots", {}) or {}),
        "xbet_index_cache_used": bool(getattr(collector, "_index_cache_used", False)),
        "xbet_index_cache_age_seconds": getattr(collector, "_index_cache_age_seconds", None),
        "xbet_latency_ms": state.get("latency_ms"),
        "matches": rows,
    }


def _movement(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in snapshots:
        for row in snap.get("matches") or []:
            by_match[str(row.get("flashscore_event_id"))].append(row)
    out: dict[str, Any] = {}
    for mid, rows in by_match.items():
        rows.sort(key=lambda row: str(row.get("market_captured_at") or ""))
        odds_changed: set[str] = set()
        minute_changed = False
        score_changed = False
        mapped_observations = 0
        previous = None
        for row in rows:
            if row.get("mapped_to_1xbet"):
                mapped_observations += 1
            if previous is not None:
                minute_changed = minute_changed or int(row.get("minute") or 0) != int(previous.get("minute") or 0)
                score_changed = score_changed or list(row.get("score") or []) != list(previous.get("score") or [])
                cur = ((row.get("coverage") or {}).get("checks") or {})
                old = ((previous.get("coverage") or {}).get("checks") or {})
                for key in set(cur) | set(old):
                    if cur.get(key) is not None and old.get(key) is not None and cur.get(key) != old.get(key):
                        odds_changed.add(key)
            previous = row
        out[mid] = {
            "home": rows[-1].get("home"),
            "away": rows[-1].get("away"),
            "observations": len(rows),
            "mapped_observations": mapped_observations,
            "minute_changed": minute_changed,
            "score_changed": score_changed,
            "odds_changed": sorted(odds_changed),
            "live_sync_evidence": bool((minute_changed or score_changed) and odds_changed),
        }
    return out


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator * 100.0 / denominator, 1) if denominator else 0.0


def _summary(snapshots: list[dict[str, Any]], movement: dict[str, Any]) -> dict[str, Any]:
    latest = snapshots[-1] if snapshots else {"matches": []}
    rows = list(latest.get("matches") or [])
    mapped = [row for row in rows if row.get("mapped_to_1xbet")]

    standard = [row for row in rows if row.get("active_standard_window")]
    standard_mapped = [row for row in standard if row.get("mapped_to_1xbet")]
    standard_complete = [row for row in standard_mapped if (row.get("coverage") or {}).get("all_core_available")]

    another = [row for row in rows if row.get("active_another_goal_window")]
    another_mapped = [row for row in another if row.get("mapped_to_1xbet")]
    another_line = [row for row in another_mapped if ((row.get("coverage") or {}).get("checks") or {}).get("another_goal") is not None]

    first_half = [row for row in rows if row.get("active_first_half_window")]
    first_half_mapped = [row for row in first_half if row.get("mapped_to_1xbet")]
    first_half_line = [row for row in first_half_mapped if (row.get("coverage") or {}).get("first_half_available")]

    missing_standard: dict[str, int] = defaultdict(int)
    for row in standard_mapped:
        for key in ((row.get("coverage") or {}).get("core_missing") or []):
            missing_standard[str(key)] += 1

    sync = sum(1 for row in movement.values() if row.get("live_sync_evidence"))
    return {
        "flashscore_live": len(rows),
        "mapped_1xbet": len(mapped),
        "mapping_pct": _pct(len(mapped), len(rows)),
        "standard_window_live": len(standard),
        "standard_window_mapped": len(standard_mapped),
        "standard_window_mapping_pct": _pct(len(standard_mapped), len(standard)),
        "all_five_standard_slots": len(standard_complete),
        "all_five_pct_of_mapped": _pct(len(standard_complete), len(standard_mapped)),
        "another_goal_window_live": len(another),
        "another_goal_window_mapped": len(another_mapped),
        "another_goal_line_available": len(another_line),
        "another_goal_line_pct_of_mapped": _pct(len(another_line), len(another_mapped)),
        "first_half_live": len(first_half),
        "first_half_mapped": len(first_half_mapped),
        "first_half_total_available": len(first_half_line),
        "first_half_total_pct_of_mapped": _pct(len(first_half_line), len(first_half_mapped)),
        "missing_by_standard_market": dict(sorted(missing_standard.items())),
        "matches_with_minute_or_score_plus_odds_movement": sync,
        "snapshots": len(snapshots),
        "note": "This audit validates real Flashscore->1xBet mapping, classic x.5 totals, the real 1st-half total subgame and the late another-goal market window. Asian integer/quarter totals are excluded. It does not fabricate GOOL model probabilities when production model files are unavailable in GitHub Actions.",
    }


def _markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# GOOL MULTI LIVE audit — standard totals",
        "",
        f"Captured: {report['created_at']}",
        f"Snapshots: {s['snapshots']}",
        f"Flashscore LIVE: **{s['flashscore_live']}**",
        f"Mapped to 1xBet (all LIVE): **{s['mapped_1xbet']} ({s['mapping_pct']}%)**",
        f"Standard GOOL window 1-75': **{s['standard_window_live']}**, mapped **{s['standard_window_mapped']} ({s['standard_window_mapping_pct']}%)**",
        f"All five standard slots (+1, +2, ITB1, ITB2, BTTS): **{s['all_five_standard_slots']} / {s['standard_window_mapped']} ({s['all_five_pct_of_mapped']}%)**",
        f"Another-goal window 10-85': **{s['another_goal_window_live']}**, mapped **{s['another_goal_window_mapped']}**, line available **{s['another_goal_line_available']} ({s['another_goal_line_pct_of_mapped']}%)**",
        f"First-half LIVE: **{s['first_half_live']}**, mapped **{s['first_half_mapped']}**, 1H total +1 available **{s['first_half_total_available']} ({s['first_half_total_pct_of_mapped']}%)**",
        f"Matches with minute/score movement plus odds movement: **{s['matches_with_minute_or_score_plus_odds_movement']}**",
        "",
        "## 1xBet index roots by snapshot",
        "",
    ]
    for snap in report.get("snapshots") or []:
        lines.append(
            f"- snapshot {snap.get('snapshot')}: merged_events={snap.get('xbet_index_merged_events')} "
            f"mapped={snap.get('xbet_mapped_count')} cache={snap.get('xbet_index_cache_used')} "
            f"cache_age={snap.get('xbet_index_cache_age_seconds')} roots={snap.get('xbet_index_root_counts')}"
        )
    lines += ["", "## Missing standard slots among mapped matches <=75'", ""]
    missing = s["missing_by_standard_market"]
    if missing:
        for key, count in missing.items():
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")
    lines += [
        "",
        "## Latest LIVE coverage",
        "",
        "| Match | Min | Score | 1xBet | +1 | +2 | ITB1 | ITB2 | BTTS | 1H +1 |",
        "|---|---:|---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]
    latest = report["snapshots"][-1] if report.get("snapshots") else {"matches": []}
    for row in latest.get("matches") or []:
        c = ((row.get("coverage") or {}).get("checks") or {})
        mark = lambda key: "✅" if c.get(key) is not None else "—"
        first_half_mark = mark("first_half_goal") if row.get("active_first_half_window") else "n/a"
        lines.append(
            f"| {row.get('home')} — {row.get('away')} | {row.get('minute')} | {row.get('score',[0,0])[0]}:{row.get('score',[0,0])[1]} | "
            f"{'✅' if row.get('mapped_to_1xbet') else '—'} | {mark('another_goal')} | {mark('two_more_goals')} | "
            f"{mark('home_goal')} | {mark('away_goal')} | {mark('btts_yes')} | {first_half_mark} |"
        )
    lines += ["", "## Important", "", s["note"], ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--out-dir", default="artifacts/gool_multi_live_audit")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    collector = RobustXBetMarketCollector(out_dir / "xbet_market_state.json", out_dir / "xbet_market_history.jsonl")
    snapshots: list[dict[str, Any]] = []
    for index in range(1, max(1, args.snapshots) + 1):
        snap = _snapshot(collector, index)
        snapshots.append(snap)
        print(
            f"AUDIT_SNAPSHOT {index}/{args.snapshots} flashscore={snap['flashscore_live_count']} "
            f"xbet={snap['xbet_mapped_count']} merged_events={snap.get('xbet_index_merged_events')} "
            f"cache={snap.get('xbet_index_cache_used')} cache_age={snap.get('xbet_index_cache_age_seconds')} "
            f"root={snap.get('xbet_root')} roots={snap.get('xbet_index_root_counts')} latency_ms={snap.get('xbet_latency_ms')}",
            flush=True,
        )
        if index < max(1, args.snapshots):
            time.sleep(max(1.0, args.interval))

    movement = _movement(snapshots)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary(snapshots, movement),
        "movement": movement,
        "snapshots": snapshots,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = _markdown(report)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md, flush=True)


if __name__ == "__main__":
    main()
