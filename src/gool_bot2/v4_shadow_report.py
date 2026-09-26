from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .v4_prematch_engine import devig_three_way, devig_two_way
from .xbet_prematch_market import XBetPrematchCollector


def _pct(x: float) -> str:
    return f"{100.0*x:.1f}%"


def build_market_report(state: dict, limit: int = 30) -> str:
    matches = [r for r in (state.get("matches") or {}).values() if isinstance(r, dict)]
    matches.sort(key=lambda r: float(r.get("scheduled_start_ts") or 9e18))
    lines = [
        "GOOL V4 SHADOW · REAL PREMATCH MARKET",
        f"captured={state.get('captured_at')} index={state.get('index_events',0)} refreshed={state.get('refreshed',0)} usable={len(matches)} latency_ms={state.get('latency_ms',0)}",
        "",
    ]
    for i, row in enumerate(matches[:max(1, limit)], 1):
        ts = row.get("scheduled_start_ts")
        try:
            kickoff = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (TypeError, ValueError, OSError):
            kickoff = "?"
        lines.append(f"{i:02d}. {row.get('home','?')} — {row.get('away','?')} | {kickoff} | id={row.get('event_id','?')}")
        x = row.get("match_1x2") or {}
        try:
            oh, od, oa = float(x["home"]), float(x["draw"]), float(x["away"])
            fh, fd, fa = devig_three_way(oh, od, oa)
            lines.append(f"    1X2: П1 {oh:.2f} ({_pct(fh)}) | X {od:.2f} ({_pct(fd)}) | П2 {oa:.2f} ({_pct(fa)})")
        except (KeyError, TypeError, ValueError):
            lines.append("    1X2: unavailable")
        total_rows = row.get("match_totals") or []
        shown = 0
        for total in total_rows:
            try:
                line = float(total["line"]); over = float(total["over"]); under = float(total["under"])
                fo, fu = devig_two_way(over, under)
            except (KeyError, TypeError, ValueError):
                continue
            lines.append(f"    Total {line:g}: ТБ {over:.2f} ({_pct(fo)}) | ТМ {under:.2f} ({_pct(fu)})")
            shown += 1
            if shown >= 3:
                break
        if not shown:
            lines.append("    Totals: unavailable")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="/tmp/gool_v4_shadow_xbet.json")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    state = XBetPrematchCollector(Path(args.state)).collect_once()
    print(build_market_report(state, args.limit), flush=True)


if __name__ == "__main__":
    main()
