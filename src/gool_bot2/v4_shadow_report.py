from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .providers.common import pair_score
from .providers.flashscore import FlashscoreProvider
from .providers.fotmob import FotMobProvider
from .providers.fixture_sources import all_fixture_sources
from .providers.prematch_fusion import PrematchDataFusion
from .prematch_goal_profile import build_prematch_goal_profile
from .v4_prematch_engine import (
    build_prematch_candidates, devig_three_way, devig_two_way, rank_prematch_for_delivery, build_accumulators,
)
from .xbet_prematch_market import XBetPrematchCollector
from .pinnacle_prematch_market import pinnacle_market_for_match


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


def build_market_report(state: dict, limit: int = 30, fixtures=None, live=None, allowed_event_ids: set[str] | None = None, target_date=None) -> str:
    fs = FlashscoreProvider()
    fusion = PrematchDataFusion(fs)
    live = fs.live_matches() if live is None else live
    fixtures = fs.scheduled_matches() if fixtures is None else fixtures
    msk = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk)
    report_date = target_date or now_msk.date()
    fixtures = [m for m in fixtures if (lambda ts: ts and datetime.fromtimestamp(float(ts), tz=msk).date() == report_date and float(ts) > datetime.now(timezone.utc).timestamp())((m.meta or {}).get("scheduled_start_ts"))]
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
        odds_source = "1xBet"
        if not row:
            lines.append(f"    1xBet: no confident match (best_match={score:.3f})")
            try:
                row, pin_score = pinnacle_market_for_match(match)
            except Exception as exc:
                row, pin_score = None, 0.0
                lines.append(f"    Pinnacle: unavailable ({type(exc).__name__})")
            if not row:
                lines.append(f"    Pinnacle: no confident market (best_match={pin_score:.3f})")
                continue
            odds_source = "Pinnacle"
            score = pin_score
        matched += 1
        lines.append(f"    {odds_source} id={row.get('event_id','?')} match={score:.3f}")
        try:
            history = fusion.context(match, limit=10)
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
            lines.append(f"    GOOL profile: unavailable ({type(exc).__name__}: {exc})")
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

def _trend_signals(profile: dict, quality: float) -> list[dict]:
    """High-conviction price-free tendencies. Require agreement, not one model probability."""
    if quality < 0.75:
        return []
    out: list[dict] = []
    first=profile.get("first_half") or {}; second=profile.get("second_half") or {}; full=profile.get("full_match") or {}
    n1=int(first.get("pair_sample") or 0); n2=int(second.get("pair_sample") or 0); nf=int(full.get("pair_sample") or 0)

    def num(v):
        try: return float(v)
        except (TypeError, ValueError): return None

    def consensus(name, model_p, sample, confirmations, *, min_p, min_confirm=3):
        p=num(model_p)
        vals=[num(x) for x in confirmations]
        vals=[x for x in vals if x is not None]
        positive=sum(x >= .65 for x in vals)
        if p is None or sample < 6 or p < min_p or len(vals) < min_confirm or positive < min_confirm:
            return
        agreement=sum(vals)/len(vals)
        strength=p * quality * (0.75 + 0.25*agreement)
        out.append({"name":name,"probability":p,"sample":sample,"confirmations":positive,"agreement":agreement,"strength":strength})

    for period,label,n in ((first,"1H",n1),(second,"2H",n2)):
        h=period.get("home") or {}; a=period.get("away") or {}; ov=period.get("over") or {}
        # A half-goal trend must be supported by both teams' scoring/conceding behaviour.
        consensus(f"{label}_OVER_0.5",ov.get("0.5"),n,
                  [h.get("scored_rate"),h.get("conceded_rate"),a.get("scored_rate"),a.get("conceded_rate")],
                  min_p=.76,min_confirm=3)
        consensus(f"{label}_OVER_1.5",ov.get("1.5"),n,
                  [(h.get("over") or {}).get("1.5"),(a.get("over") or {}).get("1.5"),
                   h.get("scored_rate"),a.get("scored_rate")],
                  min_p=.64,min_confirm=3)

    try:
        import math
        lam=float(full.get("expected_total")); hl=float(full.get("home_expected_goals")); al=float(full.get("away_expected_goals"))
        def pov(line):
            k=int(line)+1
            return 1.0-sum(math.exp(-lam)*lam**i/math.factorial(i) for i in range(k))
        # Full-time totals require expected-goal magnitude plus usable samples.
        if nf >= 6 and lam >= 3.0:
            consensus("FT_OVER_2.5",pov(2.5),nf,[min(1,lam/3.0),min(1,hl/1.25),min(1,al/1.25)],min_p=.66,min_confirm=3)
        if nf >= 6 and lam >= 3.8:
            consensus("FT_OVER_3.5",pov(3.5),nf,[min(1,lam/3.8),min(1,hl/1.45),min(1,al/1.45)],min_p=.58,min_confirm=3)
        if nf >= 6 and lam <= 2.15:
            consensus("FT_UNDER_2.5",1-pov(2.5),nf,[min(1,2.15/max(lam,.1)),min(1,1.15/max(hl,.1)),min(1,1.15/max(al,.1))],min_p=.64,min_confirm=3)
        home_score=1-math.exp(-hl); away_score=1-math.exp(-al); btts=home_score*away_score
        if hl >= 1.35 and al >= 1.20:
            consensus("BTTS_YES",btts,nf,[home_score,away_score,min(1,lam/2.8)],min_p=.64,min_confirm=3)
        if min(hl,al) <= .75:
            consensus("BTTS_NO",1-btts,nf,[1-min(home_score,away_score),min(1,2.5/max(lam,.1)),min(1,.8/max(min(hl,al),.1))],min_p=.66,min_confirm=3)
    except (TypeError, ValueError, KeyError):
        pass
    out.sort(key=lambda x:(x["strength"],x["probability"]),reverse=True)
    return out


def _brain_score(profile: dict, quality: float) -> float:
    trends=_trend_signals(profile,quality)
    return 0.0 if not trends else float(trends[0]["strength"])


def _analyse_fixtures(fs: FlashscoreProvider, fixtures: list) -> tuple[list[dict], dict[str, str]]:
    """Analyse the full field concurrently, with bounded work per runner."""
    analysed: list[dict] = []
    reasons: dict[str, str] = {}
    workers = max(4, min(32, int(os.getenv("GOOL_PREMATCH_WORKERS", "20"))))
    fixtures = list(fixtures)

    def one(match):
        local_fs = FlashscoreProvider()
        try:
            history = local_fs.fetch_match_history(match.provider_match_id, match.home, match.away, limit=10) or {}
            history["sources"] = list(dict.fromkeys([*(history.get("sources") or []), "flashscore_h2h"]))
            profile = build_prematch_goal_profile({"match": {"home": match.home, "away": match.away}, "prematch_context": history})
            samples = [int((profile.get(k) or {}).get("pair_sample") or 0) for k in ("first_half", "second_half", "full_match")]
            sample = max(samples or [0])
            quality = min(1.0, sample / 8.0)
            score = _brain_score(profile, quality)
            trends = _trend_signals(profile, quality)
            return {"match": match, "profile": profile, "sample": sample, "quality": quality, "brain_score": score, "trends": trends, "sources": history.get("sources") or [], "source_coverage": history.get("source_coverage") or {}}, None
        except Exception as exc:
            return None, f"PROFILE_{type(exc).__name__}"

    total = len(fixtures)
    progress_every = max(1, int(os.getenv("GOOL_PREMATCH_PROGRESS_EVERY", "100")))
    completed = 0
    print(f"PREMATCH_PROGRESS start total={total} workers={workers}", flush=True)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gool-pm") as pool:
        future_map = {pool.submit(one, m): m for m in fixtures}
        for fut in as_completed(future_map):
            m = future_map[fut]
            try:
                row, err = fut.result(timeout=1)
            except Exception as exc:
                row, err = None, f"PROFILE_{type(exc).__name__}"
            if row is not None:
                analysed.append(row)
            elif err:
                reasons[str(m.provider_match_id)] = err
            completed += 1
            if completed % progress_every == 0 or completed == total:
                print(
                    f"PREMATCH_PROGRESS checked={completed}/{total} analysed={len(analysed)} failures={len(reasons)}",
                    flush=True,
                )
    analysed.sort(key=lambda row: (row["brain_score"], row["quality"]), reverse=True)
    print(f"PREMATCH_PARALLEL scanned={len(fixtures)} workers={workers} analysed={len(analysed)} failures={len(reasons)}", flush=True)
    return analysed, reasons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="/tmp/gool_v4_shadow_xbet.json")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--one-tomorrow", action="store_true")
    parser.add_argument("--nations-tomorrow", action="store_true")
    parser.add_argument("--nations-today", action="store_true")
    parser.add_argument("--count-tomorrow", action="store_true")
    parser.add_argument("--count-today", action="store_true")
    parser.add_argument("--first-acca", action="store_true")
    args = parser.parse_args()
    fs = FlashscoreProvider()
    msk = timezone(timedelta(hours=3)); now_msk = datetime.now(msk); now_ts = datetime.now(timezone.utc).timestamp()
    if args.count_today or args.first_acca:
        live = []
        fs_daily = fs.parse_master_scheduled(fs._feed("f_1_0_3_en_1", timeout=12, max_hosts=1))
        if os.getenv("GOOL_FIXTURE_SOURCE", "").strip().lower() == "flashscore":
            source_counts = {"flashscore": len(fs_daily)}
            fixtures = fs_daily
        else:
            source_counts, fixtures = all_fixture_sources(now_msk.date().isoformat())
            source_counts["flashscore"] = len(fs_daily)
            from .providers.fixture_sources import dedupe
            fixtures = dedupe(fixtures + fs_daily)
        print("FIXTURE_SOURCES " + " ".join(f"{k}={v}" for k,v in source_counts.items()) + f" unique={len(fixtures)}", flush=True)
        if args.count_today and not args.first_acca:
            return
    else:
        live = fs.live_matches(); fixtures = fs.scheduled_matches()
    target_date = now_msk.date() + timedelta(days=1) if (args.one_tomorrow or args.nations_tomorrow or args.count_tomorrow) else now_msk.date()
    remaining = [m for m in fixtures if (m.meta or {}).get("scheduled_start_ts") and datetime.fromtimestamp(float((m.meta or {}).get("scheduled_start_ts")), tz=msk).date() == target_date and float((m.meta or {}).get("scheduled_start_ts")) > now_ts]
    if args.count_tomorrow:
        print(f"PREMATCH_COUNT date={target_date.isoformat()} not_started={len(remaining)} scheduled_total={len(fixtures)}", flush=True)
        return
    if args.nations_today:
        anchor = next((m for m in fixtures if pair_score(m.home, m.away, "Norway", "Portugal") >= 0.72), None)
        if anchor is not None:
            print(f"NATIONS_ANCHOR {anchor.home} — {anchor.away} | league={anchor.league!r} | FS={anchor.provider_match_id}", flush=True)
            remaining = [m for m in remaining if str(m.league or "").casefold() == str(anchor.league or "").casefold()]
        else:
            print("NATIONS_ANCHOR Norway — Portugal NOT FOUND", flush=True)
            remaining = []
    elif args.nations_tomorrow:
        nations = {("Georgia","Ukraine"),("Armenia","Montenegro"),("Latvia","Cyprus"),("Belgium","France"),("Türkiye","Italy"),("Turkey","Italy"),("Northern Ireland","Hungary"),("Romania","Bosnia and Herzegovina"),("Sweden","Poland")}
        remaining = [m for m in remaining if any(pair_score(m.home, m.away, h, a) >= 0.72 for h, a in nations)]
    elif args.one_tomorrow and remaining:
        remaining = remaining[:1]

    # Stage 1: GOOL brain analyses every Flashscore fixture with no bookmaker input.
    analysed, failures = _analyse_fixtures(fs, remaining)
    for row in analysed:
        m = row["match"]
        full = row["profile"].get("full_match") or {}
        print("ONE_DEBUG", m.home, "--", m.away, "sources=", row.get("sources"), "coverage=", row.get("source_coverage"), "sample=", row["sample"], "quality=", round(row["quality"], 2), "full=", full, "brain=", round(row["brain_score"], 3), flush=True)
    # Rank football evidence first. Do not let an arbitrary absolute threshold
    # starve the price stage: strong tendencies qualify directly; otherwise the
    # best evidence-backed fixtures form a small exploration floor.
    eligible = [row for row in analysed if row.get("trends")]
    strong = [row for row in eligible if row["brain_score"] >= 0.42]
    # Stage 2 must price the full evidence-backed field, not an arbitrary top 12.
    # Football quality remains the first gate; bookmaker value decides only after it.
    brain_candidates = eligible
    price_cap = int(os.getenv("GOOL_PREMATCH_PRICE_CAP", "250"))
    if price_cap > 0:
        brain_candidates = brain_candidates[:price_cap]

    selected_ids = {str(row["match"].provider_match_id) for row in brain_candidates}
    state = XBetPrematchCollector(Path(args.state)).collect_once(targets=[row["match"] for row in brain_candidates])
    report = build_market_report(state, args.limit, fixtures=fixtures, live=live, allowed_event_ids=selected_ids, target_date=target_date)
    print(f"PREMATCH_FUNNEL fs={len(remaining)} analysed={len(analysed)} evidence_eligible={len(eligible)} strong={len(strong)} brain_selected={len(brain_candidates)} odds_requested={len(brain_candidates)} price_cap={price_cap} profile_failures={len(failures)}", flush=True)
    for row in brain_candidates:
        m=row["match"]
        trend_text=",".join(f"{t['name']}:{t['probability']:.2f}" for t in row.get("trends",[])[:5])
        print(f"BRAIN {m.home} — {m.away} score={row['brain_score']:.3f} quality={row['quality']:.2f} sample={row['sample']} TREND={trend_text}", flush=True)
    print(report, flush=True)
    if args.first_acca:
        # Rebuild priced candidates for the selected football shortlist, then let the existing
        # V4 accumulator policy choose a 2-leg ticket. Never pad with an unqualified leg.
        priced = []
        xbet_rows = [r for r in (state.get("matches") or {}).values() if isinstance(r, dict)]
        analysed_by_id = {str(row["match"].provider_match_id): row for row in analysed}
        for row0 in brain_candidates:
            match = row0["match"]
            market, _ = _match_xbet(match, xbet_rows)
            if not market:
                try: market, _ = pinnacle_market_for_match(match)
                except Exception: market = None
            if not market: continue
            try:
                cached = analysed_by_id.get(str(match.provider_match_id)) or row0
                profile = cached["profile"]
                quality = float(cached["quality"])
                priced.extend(build_prematch_candidates(event_id=match.provider_match_id,home=match.home,away=match.away,profile=profile,market=market,data_quality=quality))
            except Exception: continue
        accas = build_accumulators(priced, legs=2)
        print("=== GOOL FIRST ACCA ===", flush=True)
        if not accas:
            print("NO QUALIFIED ACCUMULATOR", flush=True)
        else:
            a=accas[0]
            for i,p in enumerate(a["legs"],1):
                print(f"A{i}. {p.home} — {p.away} | {p.selection} @ {p.odds:.2f} | model={p.model_probability:.3f} edge={p.edge:+.3f} EV={p.expected_value:+.3f}", flush=True)
            print(f"ACCA combined_odds={a['combined_odds']:.2f} combined_probability={a['combined_probability']:.3f} EV={a['expected_value']:+.3f}", flush=True)


if __name__ == "__main__":
    main()