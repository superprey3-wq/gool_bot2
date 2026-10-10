"""Real-data GOOL Football V5 shadow weekend audit, no Telegram delivery.

Usage:
  python scripts/gool_football_v5_weekend.py --days 0,1 --max-total 1600
  python scripts/gool_football_v5_weekend.py --settle-jsonl artifacts/football_v5/weekend.jsonl --days -1,0
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gool_bot2.football_v5_shadow import append_first_snapshots, forecast_from_history, price_shadow, score_grid
from gool_bot2.football_model_challengers import poisson_profile_challenger
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_prematch_market import XBetPrematchCollector, find_prematch_market

MSK = ZoneInfo("Europe/Moscow")


def _fixture_date(match):
    return datetime.fromtimestamp(float((match.meta or {}).get("scheduled_start_ts") or 0), MSK).date()


def _finished_map(fs: FlashscoreProvider, offsets: list[int]):
    finished = {}
    for offset in offsets:
        raw = fs._feed(f"f_1_{offset}_3_en_1", timeout=12, max_hosts=1)
        if raw:
            for eid, state in fs._parse_master_states(raw).items():
                if state.get("is_finished"):
                    finished[eid] = state
    return finished


def settle(path: Path, days: list[int], output: Path) -> None:
    fs = FlashscoreProvider()
    actual = _finished_map(fs, days)
    evaluated, counts = [], Counter()
    for line in path.read_text("utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = str(row.get("event_id") or "")
        game = actual.get(eid)
        if not game:
            counts["not_finished_or_not_in_feed"] += 1
            continue
        score = [int(game["home_score"]), int(game["away_score"])]
        lam = row.get("v5") or {}
        chances = lam.get("probabilities") or {}
        if lam.get("status") != "READY":
            continue
        actual_result = ("home_win" if score[0] > score[1] else "away_win" if score[0] < score[1] else "draw")
        predictions = [(key, float(p), int((key == actual_result) if key in ("home_win", "away_win", "draw") else
                                     (sum(score) >= 2 if key == "over_1.5" else
                                      sum(score) >= 3 if key == "over_2.5" else
                                      sum(score) >= 4 if key == "over_3.5" else
                                      score[0] > 0 and score[1] > 0 if key == "btts_yes" else
                                      sum(score) > 0)))
                       for key, p in chances.items()]
        brier = {key: (p - won)**2 for key,p,won in predictions}
        baseline_probabilities = (row.get("poisson_baseline") or {}).get("probabilities") or {}
        baseline_brier = {
            key: (float(baseline_probabilities[key])-won)**2
            for key, _p, won in predictions if key in baseline_probabilities
        }
        selection = row.get("shadow_price_candidate")
        pnl = None
        if selection:
            over = selection["direction"] == "over"
            hit = sum(score) > float(selection["line"]) if over else sum(score) < float(selection["line"])
            pnl = round(float(selection["odd"]) - 1 if hit else -1, 5)
        evaluated.append({
            "event_id": eid, "score": score,
            "brier_by_market": brier,
            "baseline_brier_by_market": baseline_brier,
            "shadow_price_candidate": selection, "selection_profit_units": pnl,
        })
        counts["settled"] += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    rated = [r["selection_profit_units"] for r in evaluated if r["selection_profit_units"] is not None]
    metrics = {key: round(sum(r["brier_by_market"][key] for r in evaluated) / len(evaluated), 5) for key in (
        evaluated[0]["brier_by_market"] if evaluated else []
    )}
    baseline_metrics = {
        key: round(sum(r["baseline_brier_by_market"][key] for r in evaluated if key in r["baseline_brier_by_market"]) /
                   max(1, sum(key in r["baseline_brier_by_market"] for r in evaluated)), 5)
        for key in metrics
        if any(key in r["baseline_brier_by_market"] for r in evaluated)
    }
    summary = {
        "settled": len(evaluated),
        "not_finished_or_not_in_feed": counts["not_finished_or_not_in_feed"],
        "v5_brier_by_market": metrics,
        "existing_poisson_baseline_brier": baseline_metrics,
        "v5_brier_improvement_positive_is_better": {
            key: round(baseline_metrics[key] - metrics[key], 5)
            for key in metrics if key in baseline_metrics
        },
        "priced_selections_settled": len(rated),
        "priced_selections_profit_units": round(sum(rated), 4),
        "priced_selections_roi": round(sum(rated) / len(rated), 5) if rated else None,
        "note": "No chronological model retraining; selection probabilities were UNCALIBRATED",
    }
    output.write_text(json.dumps({"summary": summary, "events": evaluated}, ensure_ascii=False, indent=2), "utf-8")
    print("FOOTBALL_V5_SETTLEMENT", json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="0,1")
    ap.add_argument("--max-total", type=int, default=1600)
    ap.add_argument("--max-per-day", type=int, default=1100)
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--price-limit", type=int, default=80)
    ap.add_argument("--output", default="artifacts/football_v5/weekend.jsonl")
    ap.add_argument("--settle-jsonl", default="")
    args = ap.parse_args()
    offsets = [int(x) for x in args.days.split(",") if x.strip()]
    if args.settle_jsonl:
        settle(Path(args.settle_jsonl), offsets, Path(args.output).with_suffix(".settled.json"))
        return
    fs = FlashscoreProvider()
    now = time.time()
    current_msk = datetime.now(MSK).date()
    matches = []
    day_counts = {}
    # Scan every available Flashscore fixture from the requested days; the
    # 1600 figure is only a processing cap, not a claimed count of fixtures.
    for offset in offsets:
        matches_day = [
            m for m in fs.scheduled_matches_for_day(offset)
            if (m.meta or {}).get("scheduled_start_ts")
            and _fixture_date(m) == current_msk + timedelta(days=offset)
            and float(m.meta["scheduled_start_ts"]) > now
        ]
        day_counts[str(current_msk + timedelta(days=offset))] = len(matches_day)
        matches.extend(matches_day[:max(0, args.max_per_day)])
    unique = {str(m.provider_match_id):m for m in matches}
    matches = list(unique.values())[:max(0,args.max_total)]
    print("FOOTBALL_V5_WEEKEND_START", json.dumps({
        "remaining_by_day":day_counts, "selected_for_scan":len(matches),
        "workers":args.workers, "odds_price_limit":args.price_limit,
        "started_at_utc":datetime.now(timezone.utc).isoformat(),
    },ensure_ascii=False),flush=True)

    def one(m):
        kickoff = float((m.meta or {}).get("scheduled_start_ts") or 0)
        baseline = {}
        if kickoff <= time.time():
            pred = {"status":"WAIT_STARTED","model":"football_v5_shadow"}
        else:
            try:
                context = FlashscoreProvider().fetch_match_history(str(m.provider_match_id), m.home, m.away, limit=10)
                pred = forecast_from_history(home=m.home,away=m.away,kickoff=kickoff,context=context)
                prior = build_prematch_goal_profile({
                    "match":{"home":m.home,"away":m.away},"prematch_context":context,
                })
                forecast = poisson_profile_challenger(prior)
                if forecast is not None:
                    baseline = {
                        "model": forecast.name,
                        "home_lambda": forecast.home_lambda,
                        "away_lambda": forecast.away_lambda,
                        "probabilities": score_grid(forecast.home_lambda,forecast.away_lambda),
                    }
            except Exception as exc:
                pred = {"status":"WAIT_PROVIDER", "reason":type(exc).__name__, "model":"football_v5_shadow"}
        return {
            "event_id":str(m.provider_match_id),
            "home":m.home, "away":m.away, "league":m.league or "",
            "kickoff":kickoff, "shadow_created_utc":datetime.now(timezone.utc).isoformat(),
            "v5":pred, "poisson_baseline":baseline,
        }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    records = []
    workers = max(1,min(24,args.workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, m): m for m in matches}
        for future in as_completed(futures):
            m = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {"event_id":str(m.provider_match_id),"home":m.home,"away":m.away,
                       "kickoff":float((m.meta or {}).get("scheduled_start_ts") or 0),
                       "v5":{"status":"WAIT_EXCEPTION", "reason":type(exc).__name__}}
            counts[str(row["v5"].get("status") or "UNKNOWN")] += 1
            records.append(row)
            if len(records) % 100 == 0:
                print("FOOTBALL_V5_PROGRESS",len(records),"/",len(matches),dict(counts),flush=True)

    # Snapshot BEFORE any book prices and before kickoff; no selection on future
    # results. When prices exist, attach a first, immutable quote after screening.
    candidates = sorted(
        [r for r in records if r["v5"].get("status") == "READY" and r["kickoff"] > time.time() + 120],
        key=lambda r:(float(r["v5"].get("quality") or 0),float(r["v5"].get("opponent_strength_coverage") or 0)),
        reverse=True,
    )
    targets = [unique[r["event_id"]] for r in candidates[:max(0,args.price_limit)] if r["event_id"] in unique]
    if targets:
        try:
            book_path = output.parent / "xbet_snapshot.json"
            XBetPrematchCollector(book_path).collect_once(targets=targets)
            lookup = {r["event_id"]:r for r in records}
            for m in targets:
                entry = lookup.get(str(m.provider_match_id))
                if not entry:
                    continue
                quote = find_prematch_market(m.home,m.away,path=book_path)
                if not quote:
                    continue
                priced = price_shadow(entry["v5"], quote)
                if priced:
                    entry["shadow_price_candidate"] = priced[0]
                    counts["price_candidates"] += 1
                counts["book_market_matched"] += 1
        except Exception as exc:
            print("FOOTBALL_V5_ODDS_UNAVAILABLE",type(exc).__name__,str(exc)[:150],flush=True)
    new = append_first_snapshots(output, sorted(records,key=lambda r:(r["kickoff"],r["event_id"])))
    summary = {
        "fixture_counts_by_day":day_counts,
        "selected":len(matches), "processed":len(records),
        "states":dict(counts), "new_snapshots":new,
        "source":"Flashscore historical completed games and 1xBet when available",
        "note":"V5 shadow; all coefficients/probabilities provisional; no bets sent",
    }
    (output.parent/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),"utf-8")
    print("FOOTBALL_V5_WEEKEND_DONE",json.dumps(summary,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
