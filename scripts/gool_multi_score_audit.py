from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector
from gool_bot2.xbet_robust_event_guard import install as install_event_guard


def _int_pair(node: Any) -> tuple[int, int] | None:
    if not isinstance(node, dict):
        return None
    try:
        home = int(node.get("S1"))
        away = int(node.get("S2"))
    except (TypeError, ValueError):
        return None
    if home < 0 or away < 0:
        return None
    return home, away


def _score_probe(game: dict[str, Any] | None) -> dict[str, Any]:
    """Small structural probe for 1xBet score fields without dumping the whole game."""
    if not isinstance(game, dict):
        return {"game": "missing", "candidates": []}

    sc = game.get("SC")
    fs = sc.get("FS") if isinstance(sc, dict) else None
    candidates: list[dict[str, Any]] = []

    def walk(value: Any, path: str, depth: int) -> None:
        if depth > 6 or len(candidates) >= 20:
            return
        if isinstance(value, dict):
            pair = _int_pair(value)
            if pair is not None:
                candidates.append({"path": path or "/", "score": [pair[0], pair[1]]})
            for key, child in value.items():
                if key in {"GE", "E"} and depth >= 3:
                    continue
                walk(child, f"{path}/{key}"[-180:], depth + 1)
        elif isinstance(value, list):
            for idx, child in enumerate(value[:20]):
                walk(child, f"{path}[{idx}]"[-180:], depth + 1)

    walk(game, "", 0)
    return {
        "sc_type": type(sc).__name__,
        "sc_keys": sorted(str(key) for key in sc.keys())[:40] if isinstance(sc, dict) else [],
        "fs_type": type(fs).__name__,
        "fs_value": fs if isinstance(fs, (dict, list, str, int, float, type(None))) else str(type(fs).__name__),
        "candidates": candidates,
    }


def _market_counts(row: dict[str, Any] | None) -> dict[str, int]:
    markets = (row or {}).get("markets") or {}
    btts = markets.get("btts") or {}
    return {
        "match_total": len(markets.get("match_total") or []),
        "first_half_total": len(markets.get("first_half_total") or []),
        "home_total": len(markets.get("home_total") or []),
        "away_total": len(markets.get("away_total") or []),
        "btts": int(btts.get("yes") is not None) + int(btts.get("no") is not None),
    }


def _snapshot(collector: RobustXBetMarketCollector, cycle: int) -> dict[str, Any]:
    flashscore = FlashscoreProvider().live_matches()
    state = collector.collect_once()
    xbet_rows = state.get("matches") or {}
    game_cache = getattr(collector, "_robust_guard_cycle_games", {}) or {}

    rows: list[dict[str, Any]] = []
    for match in flashscore:
        mid = str(match.provider_match_id)
        minute = int(match.minute or 0)
        fs_score = [int(match.home_score or 0), int(match.away_score or 0)]
        market_row = xbet_rows.get(mid)
        if market_row is None:
            rows.append({
                "flashscore_event_id": mid,
                "home": match.home,
                "away": match.away,
                "league": match.league,
                "minute": minute,
                "flashscore_score": fs_score,
                "signal_window": 10 <= minute <= 85,
                "mapped": False,
                "status": "UNMAPPED",
            })
            continue

        event_id = str(market_row.get("xbet_event_id") or "")
        verified = bool(market_row.get("score_verified"))
        xbet_score = [market_row.get("xbet_score_home"), market_row.get("xbet_score_away")]
        guard_reason = market_row.get("repricing_guard_reason")
        score_desync = bool(market_row.get("score_desync"))
        timeline_desync = bool(market_row.get("timeline_score_desync"))

        if not verified:
            status = "SCORE_UNREAD"
        elif score_desync:
            status = "SCORE_DESYNC"
        elif timeline_desync:
            status = "TIMELINE_DESYNC"
        elif market_row.get("repricing_guard"):
            status = str(guard_reason or "REPRICE_GUARD")
        else:
            status = "READY"

        row = {
            "flashscore_event_id": mid,
            "xbet_event_id": event_id,
            "home": match.home,
            "away": match.away,
            "league": match.league,
            "minute": minute,
            "flashscore_score": fs_score,
            "xbet_score": xbet_score,
            "signal_window": 10 <= minute <= 85,
            "mapped": True,
            "score_verified": verified,
            "score_desync": score_desync,
            "timeline_score_desync": timeline_desync,
            "repricing_guard": bool(market_row.get("repricing_guard")),
            "repricing_guard_reason": guard_reason,
            "status": status,
            "market_counts": _market_counts(market_row),
        }
        if not verified:
            row["score_probe"] = _score_probe(game_cache.get(event_id))
        rows.append(row)

    signal_rows = [row for row in rows if row.get("signal_window")]
    mapped_signal = [row for row in signal_rows if row.get("mapped")]
    unread = [row for row in mapped_signal if not row.get("score_verified")]
    ready = [row for row in mapped_signal if row.get("status") == "READY"]
    return {
        "cycle": cycle,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "flashscore_live": len(rows),
        "signal_window_live": len(signal_rows),
        "mapped_signal_window": len(mapped_signal),
        "score_unread_signal_window": len(unread),
        "ready_signal_window": len(ready),
        "xbet_root": state.get("root"),
        "rows": rows,
    }


def _final_report(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    latest = snapshots[-1] if snapshots else {"rows": []}
    rows = list(latest.get("rows") or [])
    signal_rows = [row for row in rows if row.get("signal_window")]
    mapped = [row for row in signal_rows if row.get("mapped")]
    unread = [row for row in mapped if not row.get("score_verified")]
    desync = [row for row in mapped if row.get("score_desync") or row.get("timeline_score_desync")]
    ready = [row for row in mapped if row.get("status") == "READY"]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cycles": len(snapshots),
        "summary": {
            "flashscore_live": len(rows),
            "signal_window_live": len(signal_rows),
            "mapped_signal_window": len(mapped),
            "score_read_ok": len(mapped) - len(unread),
            "score_unread": len(unread),
            "score_or_timeline_desync": len(desync),
            "ready_now": len(ready),
        },
        "snapshots": snapshots,
    }


def _markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    latest = report["snapshots"][-1] if report.get("snapshots") else {"rows": []}
    lines = [
        "# GOOL Multi — 1xBet score verification audit",
        "",
        f"Cycles: **{report['cycles']}**",
        f"Flashscore LIVE: **{s['flashscore_live']}**",
        f"Signal window 10-85': **{s['signal_window_live']}**",
        f"Mapped to 1xBet: **{s['mapped_signal_window']}**",
        f"1xBet score read OK: **{s['score_read_ok']}**",
        f"1xBet score unread: **{s['score_unread']}**",
        f"Score/timeline desync: **{s['score_or_timeline_desync']}**",
        f"Ready now: **{s['ready_now']}**",
        "",
        "| Match | Min | FS | 1xBet | Status | Markets M/1H/H/A/BTTS |",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in latest.get("rows") or []:
        if not row.get("signal_window"):
            continue
        fs = row.get("flashscore_score") or [None, None]
        xs = row.get("xbet_score") or [None, None]
        counts = row.get("market_counts") or {}
        market_text = "-" if not counts else "/".join(str(counts.get(key, 0)) for key in ("match_total", "first_half_total", "home_total", "away_total", "btts"))
        xbet_text = "—" if xs[0] is None or xs[1] is None else f"{xs[0]}:{xs[1]}"
        lines.append(
            f"| {row.get('home')} — {row.get('away')} | {row.get('minute')} | {fs[0]}:{fs[1]} | {xbet_text} | {row.get('status')} | {market_text} |"
        )

    unread_rows = [row for row in latest.get("rows") or [] if row.get("signal_window") and row.get("mapped") and not row.get("score_verified")]
    if unread_rows:
        lines += ["", "## Score unread diagnostics", ""]
        for row in unread_rows:
            probe = row.get("score_probe") or {}
            lines.append(f"### {row.get('home')} — {row.get('away')} ({row.get('xbet_event_id')})")
            lines.append(f"- SC keys: `{probe.get('sc_keys')}`")
            lines.append(f"- FS type/value: `{probe.get('fs_type')}` / `{probe.get('fs_value')}`")
            lines.append(f"- S1/S2 candidates: `{probe.get('candidates')}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-cycles", type=int, default=12)
    parser.add_argument("--interval", type=float, default=20.0)
    parser.add_argument("--out-dir", default="artifacts/gool_multi_score_audit")
    args = parser.parse_args()

    install_event_guard()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    collector = RobustXBetMarketCollector(out_dir / "xbet_state.json", out_dir / "xbet_history.jsonl")

    snapshots: list[dict[str, Any]] = []
    max_cycles = max(1, int(args.max_cycles))
    for cycle in range(1, max_cycles + 1):
        snap = _snapshot(collector, cycle)
        snapshots.append(snap)
        print(
            "SCORE_AUDIT "
            f"cycle={cycle}/{max_cycles} live={snap['flashscore_live']} "
            f"signal={snap['signal_window_live']} mapped={snap['mapped_signal_window']} "
            f"unread={snap['score_unread_signal_window']} ready={snap['ready_signal_window']}",
            flush=True,
        )
        # Once every mapped match in the betting window exposes an actual 1xBet
        # score, the original technical question is answered. Desync/repricing may
        # remain and is intentionally reported as a legitimate safety block.
        if cycle >= 2 and snap["mapped_signal_window"] > 0 and snap["score_unread_signal_window"] == 0:
            break
        if cycle < max_cycles:
            time.sleep(max(0.0, float(args.interval)))

    report = _final_report(snapshots)
    (out_dir / "score_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
