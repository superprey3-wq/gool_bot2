from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gool_bot2.halftime_second_half import (
    choose_second_half_market,
    evaluate_halftime_second_half,
)
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.fusion import FootballDataFusion
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector, _half_goal_only
from gool_bot2 import xbet_market_pressure as xbet_market


POLL_SECONDS = int(os.getenv("GOOL_HT_POLL_SECONDS", "15"))
POLL_COUNT = int(os.getenv("GOOL_HT_POLL_COUNT", "5"))
MAX_MATCHES = int(os.getenv("GOOL_HT_MAX_MATCHES", "10"))


def _second_half_subgame(game: dict[str, Any]) -> dict[str, Any] | None:
    for subgame in game.get("SG") or []:
        if not isinstance(subgame, dict):
            continue
        period = str(subgame.get("P") or "").strip()
        name = str(subgame.get("PN") or "").strip().casefold()
        if period == "2" or name in {
            "2nd half", "second half", "2 half", "2nd half result", "second half result"
        }:
            return subgame
    return None


def _decode_as_second_half(game: dict[str, Any]) -> dict[str, Any]:
    decoded = xbet_market.decode_markets(game)
    return {
        "match_total": _half_goal_only(decoded.get("match_total")),
        "home_total": _half_goal_only(decoded.get("home_total")),
        "away_total": _half_goal_only(decoded.get("away_total")),
    }


def _second_half_markets(
    collector: RobustXBetMarketCollector,
    event_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    game = collector._game(event_id)
    if not game:
        return None, {"reason": "xbet_game_unavailable"}

    subgame = _second_half_subgame(game)
    if not subgame:
        return None, {"reason": "second_half_subgame_not_found"}

    embedded = _decode_as_second_half(subgame)
    if embedded["match_total"] or embedded["home_total"] or embedded["away_total"]:
        return embedded, {
            "reason": "embedded_second_half_subgame",
            "subgame_id": str(subgame.get("I") or ""),
        }

    subgame_id = str(subgame.get("I") or "").strip()
    if not subgame_id or subgame_id == event_id:
        return None, {"reason": "second_half_subgame_has_no_market"}

    child = collector._game(subgame_id)
    if not child:
        return None, {
            "reason": "second_half_child_unavailable",
            "subgame_id": subgame_id,
        }

    decoded = _decode_as_second_half(child)
    if not (decoded["match_total"] or decoded["home_total"] or decoded["away_total"]):
        return None, {
            "reason": "second_half_child_no_totals",
            "subgame_id": subgame_id,
        }
    return decoded, {
        "reason": "second_half_child",
        "subgame_id": subgame_id,
    }


def _enrich(match):
    fusion = FootballDataFusion()
    record = fusion.enrich_flashscore_match(match)
    fs = FlashscoreProvider()
    context = PrematchDataFusion(fs).context(match, limit=8)
    record["prematch_context"] = context
    profile = build_prematch_goal_profile(record)
    record["prematch_goal_profile"] = profile
    return record, profile


def _find_halftime_matches(fs: FlashscoreProvider):
    latest = []
    for attempt in range(1, POLL_COUNT + 1):
        live = fs.live_matches()
        latest = [
            m for m in live
            if bool(m.is_halftime)
        ]
        latest.sort(key=lambda m: (m.league or "", m.home, m.away))
        print(
            f"HT_SCAN attempt={attempt}/{POLL_COUNT} live={len(live)} halftime={len(latest)}",
            flush=True,
        )
        if latest:
            return latest[:MAX_MATCHES]
        if attempt < POLL_COUNT:
            time.sleep(POLL_SECONDS)
    return latest[:MAX_MATCHES]


def main() -> None:
    fs = FlashscoreProvider()
    halftime = _find_halftime_matches(fs)
    print(f"HT_FOUND count={len(halftime)}", flush=True)
    for m in halftime:
        print(
            f"HT_MATCH {m.home} - {m.away} | {m.league} | score={m.home_score}:{m.away_score}",
            flush=True,
        )

    if not halftime:
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "NO_HALFTIME_MATCHES",
            "matches": [],
        }
        Path("halftime_second_half_result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("HT_NO_MATCHES", flush=True)
        return

    enriched: dict[str, tuple[dict, dict]] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(halftime))) as pool:
        futures = {pool.submit(_enrich, match): match for match in halftime}
        for future in as_completed(futures):
            match = futures[future]
            try:
                enriched[str(match.provider_match_id)] = future.result()
            except Exception as exc:
                print(
                    f"HT_ENRICH_FAIL {match.home} - {match.away} {type(exc).__name__}:{exc}",
                    flush=True,
                )

    collector = RobustXBetMarketCollector(
        Path("/tmp/gool_ht_xbet.json"),
        Path("/tmp/gool_ht_xbet_history.jsonl"),
    )
    _, index = collector._fetch_index()
    print(f"HT_XBET_INDEX candidates={len(index)}", flush=True)

    rows = []
    for match in halftime:
        mid = str(match.provider_match_id)
        item = enriched.get(mid)
        if item is None:
            continue
        record, profile = item
        analysis = evaluate_halftime_second_half(record, prematch_profile=profile)

        xbet_event = collector._map(match, index) if index else None
        markets = None
        market_meta: dict[str, Any] = {"reason": "xbet_match_not_mapped"}
        if xbet_event:
            try:
                markets, market_meta = _second_half_markets(collector, str(xbet_event))
            except Exception as exc:
                market_meta = {"reason": f"{type(exc).__name__}:{exc}"}

        pick = choose_second_half_market(analysis, markets)
        second = (profile.get("second_half") or {}) if isinstance(profile, dict) else {}
        market_rows = [] if not markets else list(markets.get("match_total") or [])

        row = {
            "event_id": mid,
            "home": match.home,
            "away": match.away,
            "league": match.league,
            "halftime_score": [int(match.home_score or 0), int(match.away_score or 0)],
            "analysis": analysis.to_dict(),
            "prematch_second_half": {
                "expected_total": second.get("expected_total"),
                "home_expected_goals": second.get("home_expected_goals"),
                "away_expected_goals": second.get("away_expected_goals"),
                "pair_sample": second.get("pair_sample"),
                "over": second.get("over"),
            },
            "xbet": {
                "event_id": xbet_event,
                "meta": market_meta,
                "second_half_total": market_rows,
            },
            "pick": pick.to_dict(),
        }
        rows.append(row)

        price = (
            "-"
            if pick.odds is None
            else f"{pick.selection} {pick.line:g} @{pick.odds:.2f}"
        )
        likely_side = (
            match.home
            if analysis.home_score_probability_2h >= analysis.away_score_probability_2h
            else match.away
        )
        likely_side_p = max(
            analysis.home_score_probability_2h,
            analysis.away_score_probability_2h,
        )
        print(
            f"HT_ANALYSIS {match.home} - {match.away} {match.home_score}:{match.away_score} | "
            f"xg1={analysis.first_half_xg_or_proxy:.2f} shots={analysis.first_half_shots:.0f} "
            f"sot={analysis.first_half_sot:.0f} big={analysis.first_half_big_chances:.0f} | "
            f"2H_lambda={analysis.expected_goals_2h:.2f} "
            f"Pgoal2H={analysis.probability_goal_2h:.3f} "
            f"Pover1.5={analysis.probability_over_1_5_2h:.3f} "
            f"side={likely_side}:{likely_side_p:.3f} conf={analysis.confidence:.3f} | "
            f"{pick.decision} {price} reason={pick.reason}",
            flush=True,
        )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "OK",
        "matches": rows,
        "summary": {
            "halftime_matches": len(halftime),
            "analysed": len(rows),
            "real_bets": sum(row["pick"]["decision"] == "BET" for row in rows),
            "leans": sum(row["pick"]["decision"] == "LEAN" for row in rows),
            "skips": sum(row["pick"]["decision"] == "SKIP" for row in rows),
        },
    }
    Path("halftime_second_half_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("=== HALFTIME SECOND HALF RESULT ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
