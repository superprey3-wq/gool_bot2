from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_market_pressure import XBetMarketCollector


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
    total = hs + aws
    markets = (market_row or {}).get("markets") or {}
    mt = list(markets.get("match_total") or [])
    ht = list(markets.get("home_total") or [])
    at = list(markets.get("away_total") or [])
    btts = markets.get("btts") or {}
    checks = {
        "another_goal": _odd(_line(mt, total + 0.5)),
        "asian_middle": _odd(_line(mt, total + 1.0)),
        "two_more_goals": _odd(_line(mt, total + 1.5)),
        "home_goal": _odd(_line(ht, hs + 0.5)),
        "away_goal": _odd(_line(at, aws + 0.5)),
        "btts_yes": None if btts.get("yes") is None else float(btts.get("yes")),
    }
    return {
        "checks": checks,
        "available": [key for key, value in checks.items() if value is not None],
        "missing": [key for key, value in checks.items() if value is None],
        "all_required_available": all(value is not None for value in checks.values()),
        "decoded_counts": {
            "match_total": len(mt),
            "home_total": len(ht),
            "away_total": len(at),
            "btts": int(btts.get("yes") is not None) + int(btts.get("no") is not None),
        },
        "decoded_lines": {
            "match_total": [row.get("line") for row in mt],
            "home_total": [row.get("line") for row in ht],
            "away_total": [row.get("line") for row in at],
        },
    }


def _snapshot(collector: XBetMarketCollector, index: int) -> dict[str, Any]:
    captured = datetime.now(timezone.utc).isoformat()
    flashscore = FlashscoreProvider().live_matches()
    state = collector.collect_once()
    xbet = state.get("matches") or {}
    rows: list[dict[str, Any]] = []
    for match in flashscore:
        mid = str(match.provider_match_id)
        market_row = xbet.get(mid)
        coverage = _required_markets(match, market_row)
        rows.append({
            "flashscore_event_id": mid,
            "xbet_event_id": None if market_row is None else market_row.get("xbet_event_id"),
            "home": match.home,
            "away": match.away,
            "league": match.league,
            "minute": int(match.minute or 0),
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
        "xbet_mapped_count": len(xbet),
        "xbet_root": state.get("root"),
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
        rows.sort(key=lambda row: int(row.get("minute") or 0))
        odds_changed: set[str] = set()
        minute_changed = False
        score_changed = False
        previous = None
        for row in rows:
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
            "minute_changed": minute_changed,
            "score_changed": score_changed,
            "odds_changed": sorted(odds_changed),
            "live_sync_evidence": bool((minute_changed or score_changed) and odds_changed),
        }
    return out


def _summary(snapshots: list[dict[str, Any]], movement: dict[str, Any]) -> dict[str, Any]:
    latest = snapshots[-1] if snapshots else {"matches": []}
    rows = latest.get("matches") or []
    missing: dict[str, int] = defaultdict(int)
    for row in rows:
        for key in ((row.get("coverage") or {}).get("missing") or []):
            missing[str(key)] += 1
    mapped = sum(1 for row in rows if row.get("mapped_to_1xbet"))
    complete = sum(1 for row in rows if (row.get("coverage") or {}).get("all_required_available"))
    sync = sum(1 for row in movement.values() if row.get("live_sync_evidence"))
    return {
        "flashscore_live": len(rows),
        "mapped_1xbet": mapped,
        "mapping_pct": round(mapped * 100.0 / len(rows), 1) if rows else 0.0,
        "all_six_markets": complete,
        "all_six_markets_pct": round(complete * 100.0 / len(rows), 1) if rows else 0.0,
        "missing_by_market": dict(sorted(missing.items())),
        "matches_with_minute_or_score_plus_odds_movement": sync,
        "snapshots": len(snapshots),
        "note": "This audit validates real Flashscore->1xBet mapping and the six GOOL MULTI market families. It does not fabricate GOOL model probabilities when production model files are unavailable in GitHub Actions.",
    }


def _markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# GOOL MULTI LIVE audit",
        "",
        f"Captured: {report['created_at']}",
        f"Snapshots: {s['snapshots']}",
        f"Flashscore LIVE: **{s['flashscore_live']}**",
        f"Mapped to 1xBet: **{s['mapped_1xbet']} ({s['mapping_pct']}%)**",
        f"All six requested market slots present: **{s['all_six_markets']} ({s['all_six_markets_pct']}%)**",
        f"Matches with minute/score movement plus odds movement: **{s['matches_with_minute_or_score_plus_odds_movement']}**",
        "",
        "## Missing market slots",
        "",
    ]
    if s["missing_by_market"]:
        for key, count in s["missing_by_market"].items():
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")
    lines += ["", "## Latest LIVE coverage", "", "| Match | Min | Score | 1xBet | +1 | Asian +1.0 | +2 | ITB1 | ITB2 | BTTS |", "|---|---:|---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"]
    latest = report["snapshots"][-1] if report.get("snapshots") else {"matches": []}
    for row in latest.get("matches") or []:
        c = ((row.get("coverage") or {}).get("checks") or {})
        mark = lambda key: "✅" if c.get(key) is not None else "—"
        lines.append(
            f"| {row.get('home')} — {row.get('away')} | {row.get('minute')} | {row.get('score',[0,0])[0]}:{row.get('score',[0,0])[1]} | "
            f"{'✅' if row.get('mapped_to_1xbet') else '—'} | {mark('another_goal')} | {mark('asian_middle')} | {mark('two_more_goals')} | {mark('home_goal')} | {mark('away_goal')} | {mark('btts_yes')} |"
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
    collector = XBetMarketCollector(out_dir / "xbet_market_state.json", out_dir / "xbet_market_history.jsonl")
    snapshots: list[dict[str, Any]] = []
    for index in range(1, max(1, args.snapshots) + 1):
        snap = _snapshot(collector, index)
        snapshots.append(snap)
        print(
            f"AUDIT_SNAPSHOT {index}/{args.snapshots} flashscore={snap['flashscore_live_count']} "
            f"xbet={snap['xbet_mapped_count']} root={snap.get('xbet_root')} latency_ms={snap.get('xbet_latency_ms')}",
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
