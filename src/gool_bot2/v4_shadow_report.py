from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .providers.common import pair_score
from .providers.flashscore import FlashscoreProvider
from .v4_prematch_engine import devig_three_way, devig_two_way
from .xbet_prematch_market import XBetPrematchCollector


def _pct(x: float) -> str:
    return f"{100.0*x:.1f}%"


def _match_xbet(fs_match, xbet_rows: list[dict], min_score: float = 0.78) -> tuple[dict | None, float]:
    best = None; best_score = 0.0
    for row in xbet_rows:
        score = pair_score(fs_match.home, fs_match.away, str(row.get("home") or ""), str(row.get("away") or ""))
        if score > best_score:
            best, best_score = row, score
    return (best, best_score) if best_score >= min_score else (None, best_score)


def build_market_report(state: dict, limit: int = 30) -> str:
    fs = FlashscoreProvider()
    live = fs.live_matches()
    fixtures = fs.scheduled_matches()
    msk = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk)
    fixtures = [m for m in fixtures if (lambda ts: ts and datetime.fromtimestamp(float(ts), tz=msk).date() == now_msk.date() and float(ts) > datetime.now(timezone.utc).timestamp())((m.meta or {}).get("scheduled_start_ts"))]
    xbet_rows = [r for r in (state.get("matches") or {}).values() if isinstance(r, dict)]
    lines = [
        "GOOL V4 SHADOW · FLASHSCORE LIVE + TODAY PREMATCH · MSK",
        f"flashscore_live={len(live)} today_remaining={len(fixtures)} xbet_usable={len(xbet_rows)} captured={state.get('captured_at')} latency_ms={state.get('latency_ms',0)}",
        f"report_time_msk={now_msk.strftime('%Y-%m-%d %H:%M:%S MSK')}",
        "",
        "=== LIVE NOW ===",
    ]
    for i, m in enumerate(live, 1):
        lines.append(f"L{i:02d}. {m.home} — {m.away} | {m.league or '?'} | {int(m.minute or 0)}' | {int(m.home_score or 0)}:{int(m.away_score or 0)} | FS={m.provider_match_id}")
    lines.extend([
        "",
        "=== PREMATCH TODAY REMAINING ===",
    ])
    matched = 0
    for i, match in enumerate(fixtures[:max(1, limit)], 1):
        row, score = _match_xbet(match, xbet_rows)
        ts = (match.meta or {}).get("scheduled_start_ts")
        try:
            kickoff = datetime.fromtimestamp(float(ts), tz=msk).strftime("%Y-%m-%d %H:%M MSK")
        except (TypeError, ValueError, OSError):
            kickoff = "?"
        lines.append(f"{i:02d}. {match.home} — {match.away} | {match.league or '?'} | {kickoff} | FS={match.provider_match_id}")
        if not row:
            lines.append(f"    1xBet: no confident match (best_match={score:.3f})")
            continue
        matched += 1
        lines.append(f"    1xBet id={row.get('event_id','?')} match={score:.3f}")
        x = row.get("match_1x2") or {}
        try:
            oh, od, oa = float(x["home"]), float(x["draw"]), float(x["away"])
            fh, fd, fa = devig_three_way(oh, od, oa)
            lines.append(f"    1X2: П1 {oh:.2f} ({_pct(fh)}) | X {od:.2f} ({_pct(fd)}) | П2 {oa:.2f} ({_pct(fa)})")
        except (KeyError, TypeError, ValueError):
            lines.append("    1X2: unavailable")
        shown = 0
        for total in row.get("match_totals") or []:
            try:
                line = float(total["line"]); over = float(total["over"]); under = float(total["under"])
                fo, fu = devig_two_way(over, under)
            except (KeyError, TypeError, ValueError):
                continue
            lines.append(f"    Total {line:g}: ТБ {over:.2f} ({_pct(fo)}) | ТМ {under:.2f} ({_pct(fu)})")
            shown += 1
            if shown >= 3: break
        if not shown: lines.append("    Totals: unavailable")
    lines.insert(2, f"matched_to_xbet={matched}/{min(len(fixtures), max(1, limit))}")
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
