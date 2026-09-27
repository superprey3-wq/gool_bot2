from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic

from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
from gool_bot2.multi_router import analyze_multi_match
from gool_bot2.multi_runtime import _data_quality
from gool_bot2.providers.fusion import FootballDataFusion
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


def _in_entry_window(minute: int) -> bool:
    return 1 <= minute <= 35 or 46 <= minute <= 75


def main() -> None:
    started = monotonic()
    fusion = FootballDataFusion()
    master = [m for m in fusion.flashscore.live_matches() if _in_entry_window(int(m.minute or 0))]
    print(f"FAST_LIVE master_candidates={len(master)}", flush=True)

    # Expensive multi-provider enrichment is candidate-only and parallel.
    records = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fusion.enrich_flashscore_match, m) for m in master]
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as exc:
                print(f"FAST_LIVE_ENRICH_FAIL {type(exc).__name__}", flush=True)

    brain_candidates = []
    for record in records:
        match = record.get("match") or {}
        minute = int(match.get("minute") or 0)
        active = "GOAL_BEFORE_HT" if 1 <= minute <= 35 else "ANOTHER_GOAL"
        decision = next((d for d in evaluate_live_goals(record) if d.market == active), None)
        if decision is not None and decision.decision == "BET":
            brain_candidates.append((record, decision, active))

    brain_ms = int((monotonic() - started) * 1000)
    print(f"FAST_LIVE brain_bets={len(brain_candidates)} brain_ms={brain_ms}", flush=True)

    # Build the 1xBet index once, then fetch markets ONLY for V4 BET candidates.
    collector = RobustXBetMarketCollector(
        Path("/tmp/gool_fast_market.json"),
        Path("/tmp/gool_fast_market_history.jsonl"),
    )
    _, index = collector._fetch_index()
    priced = final_bets = 0

    def price(item):
        record, decision, active = item
        match = record.get("match") or {}
        fs = next((m for m in master if str(m.provider_match_id) == str(match.get("flashscore_event_id"))), None)
        if fs is None:
            return item, None
        event_id = collector._map(fs, index)
        if not event_id:
            return item, None
        markets = collector._markets_for_event(
            event_id,
            int(fs.minute or 0),
            int(fs.home_score or 0) + int(fs.away_score or 0),
        )
        if not markets:
            return item, None
        import time
        from datetime import datetime, timezone
        score = (int(fs.home_score or 0), int(fs.away_score or 0))
        pressure = collector._pressure(str(fs.provider_match_id), score, time.time(), markets)
        row = {
            "flashscore_event_id": str(fs.provider_match_id),
            "xbet_event_id": event_id,
            "home": fs.home, "away": fs.away, "minute": int(fs.minute or 0),
            "score_home": score[0], "score_away": score[1],
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "markets": markets, "pressure": pressure, "line_move": False,
        }
        return item, row

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(price, item) for item in brain_candidates]
        for future in as_completed(futures):
            (record, d, active), market = future.result()
            match = record.get("match") or {}
            if market is None:
                print(f"FAST_FILTERED {match.get('home')} - {match.get('away')} | {active} no_live_market", flush=True)
                continue
            priced += 1
            key = "goal_before_ht" if active == "GOAL_BEFORE_HT" else "another_goal"
            experts = {key: {"probability": d.probability, "confidence": d.confidence, "passed": True, "source": "live_goal_brain_v4", "blocks": []}}
            route = analyze_multi_match(match, market, experts, data_quality=_data_quality(record))
            if route.status == "BET" and route.winner:
                final_bets += 1
                w = route.winner
                print(
                    f"FAST_FINAL_BET {match.get('minute')}' {match.get('home')} - {match.get('away')} "
                    f"{match.get('home_score')}:{match.get('away_score')} | {active} p={d.probability:.3f} "
                    f"odd={w.odd:.2f} fair={float(w.market_probability or 0):.3f} "
                    f"edge_pp={w.value_edge_pp:+.1f} roi={w.expected_roi:+.3f} rating={w.rating:.1f}",
                    flush=True,
                )
            else:
                blocks = route.rejected[0].blocks if route.rejected else []
                print(f"FAST_FILTERED {match.get('home')} - {match.get('away')} | {active} blocks={blocks} reason={route.reason}", flush=True)

    elapsed = int((monotonic() - started) * 1000)
    print(f"FAST_LIVE_SUMMARY master={len(master)} enriched={len(records)} brain_bets={len(brain_candidates)} priced={priced} final_bets={final_bets} elapsed_ms={elapsed}", flush=True)


if __name__ == "__main__":
    main()
