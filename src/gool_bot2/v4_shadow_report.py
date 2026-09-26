from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .providers.common import pair_score
from .providers.flashscore import FlashscoreProvider
from .providers.prematch_fusion import PrematchDataFusion
from .prematch_goal_profile import build_prematch_goal_profile
from .v4_prematch_engine import (
    build_prematch_candidates, devig_three_way, devig_two_way, rank_prematch_for_delivery,
)
from .xbet_prematch_market import XBetPrematchCollector


def _pct(x: float) -> str:
    return f"{100.0*x:.1f}%"


def _match_xbet(fs_match, xbet_rows: list[dict], min_score: float = 0.72) -> tuple[dict | None, float]:
    """Match by teams plus kickoff proximity; never accept a weak name-only collision."""
    fs_ts = float((fs_match.meta or {}).get("scheduled_start_ts") or 0)
    best = None
    best_score = 0.0
    for row in xbet_rows:
        names = pair_score(fs_match.home, fs_match.away, str(row.get("home") or ""), str(row.get("away") or ""))
        try:
            xb_ts = float(row.get("scheduled_start_ts") or 0)
        except (TypeError, ValueError):
            xb_ts = 0.0
        if fs_ts and xb_ts:
            delta = abs(fs_ts - xb_ts)
            if delta > 3 * 3600:
                continue
            time_score = max(0.0, 1.0 - delta / (3 * 3600))
            score = 0.82 * names + 0.18 * time_score
        else:
            score = names
        if score > best_score:
            best, best_score = row, score
    return (best, best_score) if best_score >= min_score else (None, best_score)


def build_market_report(state: dict, limit: int = 30, fixtures=None, live=None, allowed_event_ids: set[str] | None = None) -> str:
    fs = FlashscoreProvider()
    live = fs.live_matches() if live is None else live
    fixtures = fs.scheduled_matches() if fixtures is None else fixtures
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
    all_candidates = []
    report_fixtures = [m for m in fixtures if allowed_event_ids is None or str(m.provider_match_id) in allowed_event_ids]
    for i, match in enumerate(report_fixtures[:max(1, limit)], 1):
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
        try:
            history = fs.fetch_match_history(match.provider_match_id, match.home, match.away, limit=10) or {}
            history["sources"] = list(dict.fromkeys([*(history.get("sources") or []), "flashscore_h2h"]))
            record = {"match": {"home": match.home, "away": match.away}, "prematch_context": history}
            profile = build_prematch_goal_profile(record)
            full = profile.get("full_match") or {}
            sample = int(full.get("pair_sample") or 0)
            quality = min(1.0, sample / 8.0)
            candidates = build_prematch_candidates(
                event_id=match.provider_match_id, home=match.home, away=match.away,
                profile=profile, market=row, data_quality=quality,
            )
            all_candidates.extend(candidates)
            lines.append(f"    GOOL profile: sample={sample} quality={quality:.2f} candidates={len(candidates)}")
        except Exception as exc:
            lines.append(f"    GOOL profile: unavailable ({type(exc).__name__})")
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
    shortlist = rank_prematch_for_delivery(all_candidates, limit=8, max_per_event=1)
    lines.extend(["", "=== GOOL PREMATCH SHORTLIST ==="])
    if not shortlist:
        lines.append("NO QUALIFIED PICKS")
    for i, (pick, tier) in enumerate(shortlist, 1):
        lines.append(
            f"P{i:02d}. {pick.home} — {pick.away} | {pick.selection} @ {pick.odds:.2f} | "
            f"{tier} | model={_pct(pick.model_probability)} market={_pct(pick.market_probability)} "
            f"edge={100*pick.edge:+.1f}pp EV={100*pick.expected_value:+.1f}% quality={pick.data_quality:.2f}"
        )
    lines.insert(2, f"matched_to_xbet={matched}/{min(len(report_fixtures), max(1, limit))}")
    return "\n".join(lines)

def _brain_score(profile: dict, quality: float) -> float:
    """Price-free football-interest score. Odds must not decide what we analyse."""
    periods = [profile.get("first_half") or {}, profile.get("second_half") or {}, profile.get("full_match") or {}]
    signals: list[float] = []
    for period in periods:
        if not period.get("available"):
            continue
        over = period.get("over") or {}
        for value in over.values():
            try:
                p = float(value)
            except (TypeError, ValueError):
                continue
            signals.append(abs(p - 0.5) * 2.0)
        for key in ("home", "away"):
            team = period.get(key) or {}
            for metric in ("scored_rate", "conceded_rate"):
                try:
                    p = float(team.get(metric))
                except (TypeError, ValueError):
                    continue
                signals.append(abs(p - 0.5) * 2.0)
    if not signals:
        return 0.0
    signals.sort(reverse=True)
    tendency = sum(signals[:4]) / min(4, len(signals))
    return max(0.0, min(1.0, 0.70 * tendency + 0.30 * quality))


def _analyse_fixtures(fs: FlashscoreProvider, fixtures: list) -> tuple[list[dict], dict[str, str]]:
    analysed: list[dict] = []
    reasons: dict[str, str] = {}
    fusion = PrematchDataFusion(fs)
    for match in fixtures:
        try:
            history = fs.fetch_match_history(match.provider_match_id, match.home, match.away, limit=10) or {}
            history["sources"] = list(dict.fromkeys([*(history.get("sources") or []), "flashscore_h2h"]))
            profile = build_prematch_goal_profile({"match": {"home": match.home, "away": match.away}, "prematch_context": history})
            samples = [int((profile.get(k) or {}).get("pair_sample") or 0) for k in ("first_half", "second_half", "full_match")]
            sample = max(samples or [0]); quality = min(1.0, sample / 8.0)
            score = _brain_score(profile, quality)
            analysed.append({"match": match, "profile": profile, "sample": sample, "quality": quality, "brain_score": score, "sources": history.get("sources") or []})
        except Exception as exc:
            reasons[str(match.provider_match_id)] = f"PROFILE_{type(exc).__name__}"
    analysed.sort(key=lambda row: (row["brain_score"], row["quality"]), reverse=True)
    return analysed, reasons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="/tmp/gool_v4_shadow_xbet.json")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    fs = FlashscoreProvider(); live = fs.live_matches(); fixtures = fs.scheduled_matches()
    msk = timezone(timedelta(hours=3)); now_msk = datetime.now(msk); now_ts = datetime.now(timezone.utc).timestamp()
    remaining = [m for m in fixtures if (m.meta or {}).get("scheduled_start_ts") and datetime.fromtimestamp(float((m.meta or {}).get("scheduled_start_ts")), tz=msk).date() == now_msk.date() and float((m.meta or {}).get("scheduled_start_ts")) > now_ts]

    # Stage 1: GOOL brain analyses every Flashscore fixture with no bookmaker input.
    analysed, failures = _analyse_fixtures(fs, remaining)
    # Rank football evidence first. Do not let an arbitrary absolute threshold
    # starve the price stage: strong tendencies qualify directly; otherwise the
    # best evidence-backed fixtures form a small exploration floor.
    eligible = [row for row in analysed if row["quality"] >= 0.50 and row["brain_score"] > 0.0]
    strong = [row for row in eligible if row["brain_score"] >= 0.42]
    if strong:
        brain_candidates = strong[:12]
    else:
        brain_candidates = eligible[:min(6, len(eligible))]

    # Stage 2: only now ask 1xBet for prices on selected football candidates.
    selected_ids = {str(row["match"].provider_match_id) for row in brain_candidates}
    state = XBetPrematchCollector(Path(args.state)).collect_once(targets=[row["match"] for row in brain_candidates])
    report = build_market_report(state, args.limit, fixtures=fixtures, live=live, allowed_event_ids=selected_ids)
    print(f"PREMATCH_FUNNEL fs={len(remaining)} analysed={len(analysed)} brain_selected={len(brain_candidates)} odds_requested={len(brain_candidates)} profile_failures={len(failures)}", flush=True)
    for row in brain_candidates:
        m=row["match"]
        print(f"BRAIN {m.home} — {m.away} score={row['brain_score']:.3f} quality={row['quality']:.2f} sample={row['sample']}", flush=True)
    print(report, flush=True)


if __name__ == "__main__":
    main()