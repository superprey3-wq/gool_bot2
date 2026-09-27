from __future__ import annotations

from pathlib import Path

from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
from gool_bot2.multi_router import analyze_multi_match
from gool_bot2.multi_runtime import _data_quality
from gool_bot2.providers.fusion import FootballDataFusion
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


def main() -> None:
    collector = RobustXBetMarketCollector(
        Path("/tmp/gool_v4_e2e_market.json"),
        Path("/tmp/gool_v4_e2e_market_history.jsonl"),
    )
    state = collector.collect_once()
    markets = state.get("matches") or {}
    records = FootballDataFusion().live_records()
    print(
        f"LIVE_V4_E2E live_records={len(records)} xbet_mapped={len(markets)} "
        f"market_latency_ms={state.get('latency_ms')}",
        flush=True,
    )

    brain_bets = 0
    final_bets = 0
    priced = 0
    for record in records:
        match = record.get("match") or {}
        mid = str(match.get("flashscore_event_id") or "")
        minute = int(match.get("minute") or 0)
        if not mid or not (1 <= minute <= 75) or bool(match.get("is_finished")):
            continue

        # Production ordinary GOOL has two non-overlapping entry contours.
        active = "GOAL_BEFORE_HT" if 1 <= minute <= 35 else "ANOTHER_GOAL" if 46 <= minute <= 75 else ""
        if not active:
            continue

        decisions = {d.market: d for d in evaluate_live_goals(record)}
        d = decisions.get(active)
        if d is None or d.decision != "BET":
            continue
        brain_bets += 1

        key = "goal_before_ht" if active == "GOAL_BEFORE_HT" else "another_goal"
        experts = {
            key: {
                "probability": d.probability,
                "confidence": d.confidence,
                "passed": True,
                "source": "live_goal_brain_v4",
                "blocks": [],
            }
        }
        market = markets.get(mid)
        if market is not None:
            priced += 1
        route = analyze_multi_match(match, market, experts, data_quality=_data_quality(record))
        winner = route.winner
        if route.status == "BET" and winner is not None:
            final_bets += 1
            print(
                f"FINAL_BET {minute}' {match.get('home')} - {match.get('away')} "
                f"{match.get('home_score')}:{match.get('away_score')} | {active} "
                f"p={d.probability:.3f} conf={d.confidence:.3f} "
                f"market={winner.label} odd={winner.odd:.2f} "
                f"fair={float(winner.market_probability or 0):.3f} "
                f"edge_pp={winner.value_edge_pp:+.1f} roi={winner.expected_roi:+.3f} "
                f"rating={winner.rating:.1f} pressure_pp={winner.market_pressure_pp:+.1f}",
                flush=True,
            )
        else:
            rejected = route.rejected[0] if route.rejected else None
            print(
                f"FILTERED {minute}' {match.get('home')} - {match.get('away')} "
                f"{match.get('home_score')}:{match.get('away_score')} | {active} "
                f"p={d.probability:.3f} conf={d.confidence:.3f} "
                f"market={'yes' if market else 'no'} "
                f"blocks={[] if rejected is None else rejected.blocks} reason={route.reason}",
                flush=True,
            )

    print(
        f"LIVE_V4_E2E_SUMMARY brain_bets={brain_bets} priced={priced} final_bets={final_bets}",
        flush=True,
    )


if __name__ == "__main__":
    main()
