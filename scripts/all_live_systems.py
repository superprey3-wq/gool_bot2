from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gool_bot2 import xbet_market_pressure as xbet_market
from gool_bot2.brain_v3_full_match import apply_full_match_brain_v3_to_experts, apply_full_match_strong_live
from gool_bot2.brain_v3_memory import build_brain_v3_memory
from gool_bot2.halftime_second_half import choose_second_half_market, evaluate_halftime_second_half
from gool_bot2.live_for_against_judge import evaluate_argument_judge
from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
from gool_bot2.live_momentum import LiveMomentumTracker
from gool_bot2.multi_runtime import _data_quality
from gool_bot2.prematch_goal_profile import build_prematch_goal_profile
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.fusion import FootballDataFusion
from gool_bot2.providers.prematch_fusion import PrematchDataFusion
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector, _half_goal_only


POLL_SECONDS = int(os.getenv("GOOL_ALL_LIVE_POLL_SECONDS", "50"))
POLL_COUNT = int(os.getenv("GOOL_ALL_LIVE_POLL_COUNT", "7"))
MAX_MATCHES = int(os.getenv("GOOL_ALL_LIVE_MAX_MATCHES", "80"))
WORKERS = int(os.getenv("GOOL_ALL_LIVE_WORKERS", "12"))


def _active_window(minute: int, halftime: bool) -> bool:
    if halftime:
        return False
    return 8 <= minute <= 43 or 46 <= minute <= 85


def _market_for_minute(minute: int) -> str | None:
    if 8 <= minute <= 43:
        return "GOAL_BEFORE_HT"
    if 46 <= minute <= 85:
        return "ANOTHER_GOAL"
    return None


def _phase(minute: int, halftime: bool) -> str:
    if halftime:
        return "HALFTIME"
    if minute < 8:
        return "EARLY_1H"
    if 8 <= minute <= 43:
        return "ACTIVE_1H"
    if minute <= 45:
        return "LATE_1H"
    if 46 <= minute <= 85:
        return "ACTIVE_2H"
    return "LATE_2H"


def _real_stats(record: dict[str, Any]) -> dict[str, Any]:
    core = ("xg", "xgot", "shots", "shots_on_target", "big_chances", "touches_box")
    providers = record.get("providers") or {}
    names = []
    keys = set()
    flashscore_keys = []
    for name, payload in providers.items():
        if not isinstance(payload, dict):
            continue
        stats = payload.get("stats") or {}
        present = []
        for key in core:
            value = stats.get(key)
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            try:
                float(value[0]); float(value[1])
            except (TypeError, ValueError):
                continue
            present.append(key)
        if present:
            names.append(str(name))
            keys.update(present)
            if str(name) == "flashscore":
                flashscore_keys = present
    return {
        "provider_count": len(names),
        "providers": names,
        "core_key_count": len(keys),
        "core_keys": sorted(keys),
        "flashscore_core_keys": sorted(set(flashscore_keys)),
        "sufficient_for_bet": len(names) >= 1 and len(keys) >= 2,
    }


def _enrich_many(fusion: FootballDataFusion, matches: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not matches:
        return out
    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(matches)))) as pool:
        futs = {pool.submit(fusion.enrich_flashscore_match, match): match for match in matches}
        for fut in as_completed(futs):
            match = futs[fut]
            try:
                out[str(match.provider_match_id)] = fut.result()
            except Exception as exc:
                print(
                    f"ALLLIVE_ENRICH_FAIL {match.home} - {match.away} {type(exc).__name__}:{exc}",
                    flush=True,
                )
    return out


def _second_half_subgame(game: dict[str, Any]) -> dict[str, Any] | None:
    for subgame in game.get("SG") or []:
        if not isinstance(subgame, dict):
            continue
        period = str(subgame.get("P") or "").strip()
        name = str(subgame.get("PN") or "").strip().casefold()
        if period == "2" or name in {"2nd half", "second half", "2 half", "2nd half result", "second half result"}:
            return subgame
    return None


def _decode_as_second_half(game: dict[str, Any]) -> dict[str, Any]:
    decoded = xbet_market.decode_markets(game)
    return {
        "match_total": _half_goal_only(decoded.get("match_total")),
        "home_total": _half_goal_only(decoded.get("home_total")),
        "away_total": _half_goal_only(decoded.get("away_total")),
    }


def _second_half_markets(collector: RobustXBetMarketCollector, event_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    game = collector._game(event_id)
    if not game:
        return None, {"reason": "xbet_game_unavailable"}
    subgame = _second_half_subgame(game)
    if not subgame:
        return None, {"reason": "second_half_subgame_not_found"}
    embedded = _decode_as_second_half(subgame)
    if embedded["match_total"] or embedded["home_total"] or embedded["away_total"]:
        return embedded, {"reason": "embedded_second_half_subgame", "subgame_id": str(subgame.get("I") or "")}
    child_id = str(subgame.get("I") or "").strip()
    if not child_id or child_id == event_id:
        return None, {"reason": "second_half_subgame_has_no_market"}
    child = collector._game(child_id)
    if not child:
        return None, {"reason": "second_half_child_unavailable", "subgame_id": child_id}
    decoded = _decode_as_second_half(child)
    if not any(decoded.values()):
        return None, {"reason": "second_half_child_no_totals", "subgame_id": child_id}
    return decoded, {"reason": "second_half_child", "subgame_id": child_id}


def _active_result(record: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    market = _market_for_minute(minute)
    quality = float(_data_quality(record))

    brain = apply_full_match_brain_v3_to_experts(record, {}, data_quality=quality, prematch_profile=None)
    brain = apply_full_match_strong_live(record, {}, brain)
    v4 = next((row for row in evaluate_live_goals(record) if row.market == market), None)
    judge = evaluate_argument_judge(record)
    evidence = _real_stats(record)

    v3_status = str(brain.get("status") or "WATCH")
    v4_status = "NONE" if v4 is None else str(v4.decision)
    judge_status = "NONE" if judge is None else str(judge.decision)

    votes = {
        "brain_v3": "BET" if v3_status == "BET" else "NO_BET",
        "live_brain_v4": "BET" if v4_status == "BET" else "NO_BET",
        "numeric_judge": "BET" if judge_status == "BET" else "NO_BET",
    }
    bet_votes = sum(value == "BET" for value in votes.values())

    return {
        "event_id": str(match.get("flashscore_event_id") or ""),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": minute,
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "phase": _phase(minute, bool(match.get("is_halftime"))),
        "market": market,
        "real_stats": evidence,
        "data_quality": round(quality, 3),
        "brain_v3": {
            "status": v3_status,
            "probability": brain.get("probability"),
            "state": brain.get("match_state"),
            "blocks": list(brain.get("blocks") or []),
            "xg5": (brain.get("recent") or {}).get("xg5"),
            "xg10": (brain.get("recent") or {}).get("xg10"),
        },
        "live_brain_v4": None if v4 is None else v4.to_dict(),
        "numeric_judge": None if judge is None else judge.to_dict(),
        "votes": votes,
        "bet_votes": bet_votes,
        "consensus": "BET" if bet_votes >= 2 and evidence["sufficient_for_bet"] else (
            "LOW_DATA" if not evidence["sufficient_for_bet"] else "NO_BET"
        ),
    }


def _halftime_result(match, record: dict[str, Any], xbet_index, collector: RobustXBetMarketCollector) -> dict[str, Any]:
    fs = FlashscoreProvider()
    try:
        context = PrematchDataFusion(fs).context(match, limit=8)
    except Exception:
        context = {}
    record["prematch_context"] = context
    profile = build_prematch_goal_profile(record)
    record["prematch_goal_profile"] = profile

    analysis = evaluate_halftime_second_half(record, prematch_profile=profile)
    xbet_event = collector._map(match, xbet_index) if xbet_index else None
    markets = None
    market_meta = {"reason": "xbet_match_not_mapped"}
    if xbet_event:
        try:
            markets, market_meta = _second_half_markets(collector, str(xbet_event))
        except Exception as exc:
            market_meta = {"reason": f"{type(exc).__name__}:{exc}"}
    pick = choose_second_half_market(analysis, markets)
    second = profile.get("second_half") or {}
    return {
        "event_id": str(match.provider_match_id),
        "home": match.home,
        "away": match.away,
        "league": match.league,
        "minute": 45,
        "score": [int(match.home_score or 0), int(match.away_score or 0)],
        "phase": "HALFTIME",
        "real_stats": _real_stats(record),
        "analysis": analysis.to_dict(),
        "prematch_second_half": {
            "expected_total": second.get("expected_total"),
            "home_expected_goals": second.get("home_expected_goals"),
            "away_expected_goals": second.get("away_expected_goals"),
            "pair_sample": second.get("pair_sample"),
        },
        "xbet": {
            "event_id": xbet_event,
            "meta": market_meta,
            "second_half_total": [] if not markets else list(markets.get("match_total") or []),
        },
        "pick": pick.to_dict(),
        "consensus": pick.decision,
    }


def main() -> None:
    fs = FlashscoreProvider()
    fusion = FootballDataFusion()
    momentum = LiveMomentumTracker(max_points=30)

    initial = fs.live_matches()[:MAX_MATCHES]
    tracked = {str(m.provider_match_id): m for m in initial}
    print(f"ALLLIVE_START count={len(tracked)} polls={POLL_COUNT} every={POLL_SECONDS}s", flush=True)
    for m in initial:
        print(
            f"ALLLIVE_MATCH {m.minute}' {'HT' if m.is_halftime else ''} "
            f"{m.home} - {m.away} {m.home_score}:{m.away_score} | {m.league}",
            flush=True,
        )

    latest_records: dict[str, dict] = {}
    halftime_records: dict[str, tuple[Any, dict]] = {}

    for poll in range(POLL_COUNT):
        current = {
            str(m.provider_match_id): m
            for m in fs.live_matches()
            if str(m.provider_match_id) in tracked
        }
        to_enrich = [
            m for m in current.values()
            if _active_window(int(m.minute or 0), bool(m.is_halftime))
            or bool(m.is_halftime)
        ]
        enriched = _enrich_many(fusion, to_enrich)
        print(
            f"ALLLIVE_POLL {poll + 1}/{POLL_COUNT} still_live={len(current)} enriched={len(enriched)}",
            flush=True,
        )
        for mid, record in enriched.items():
            match_obj = current.get(mid)
            if not match_obj:
                continue
            if bool(match_obj.is_halftime):
                halftime_records.setdefault(mid, (match_obj, record))
                continue
            record = momentum.attach(record)
            build_brain_v3_memory(record, mid)
            latest_records[mid] = record

        if poll < POLL_COUNT - 1:
            time.sleep(POLL_SECONDS)

    final_master = {
        str(m.provider_match_id): m
        for m in fs.live_matches()
        if str(m.provider_match_id) in tracked
    }

    # Capture any current halftime row not seen earlier.
    missing_ht = [m for mid, m in final_master.items() if bool(m.is_halftime) and mid not in halftime_records]
    for mid, rec in _enrich_many(fusion, missing_ht).items():
        if mid in final_master:
            halftime_records[mid] = (final_master[mid], rec)

    collector = RobustXBetMarketCollector(Path("/tmp/all_live_xbet.json"), Path("/tmp/all_live_xbet_history.jsonl"))
    try:
        _, xbet_index = collector._fetch_index()
    except Exception:
        xbet_index = []

    active_rows = []
    halftime_rows = []
    waiting_rows = []

    # Evaluate most recent enriched active records.
    for mid, record in latest_records.items():
        match = record.get("match") or {}
        minute = int(match.get("minute") or 0)
        if _market_for_minute(minute) is None:
            continue
        try:
            row = _active_result(record)
            active_rows.append(row)
            print(
                f"ALLLIVE_ACTIVE {row['minute']}' {row['home']} - {row['away']} {row['score'][0]}:{row['score'][1]} | "
                f"{row['market']} | V3={row['brain_v3']['status']}/{row['brain_v3']['probability']} "
                f"V4={None if row['live_brain_v4'] is None else row['live_brain_v4']['decision']}/"
                f"{None if row['live_brain_v4'] is None else row['live_brain_v4']['probability']} "
                f"J={None if row['numeric_judge'] is None else row['numeric_judge']['decision']}/"
                f"{None if row['numeric_judge'] is None else row['numeric_judge']['judge_score']} "
                f"real={row['real_stats']['providers']} => {row['consensus']}",
                flush=True,
            )
        except Exception as exc:
            print(f"ALLLIVE_ACTIVE_FAIL {mid} {type(exc).__name__}:{exc}", flush=True)

    # Analyse any match observed at halftime during this scan.
    for mid, (match_obj, record) in halftime_records.items():
        try:
            row = _halftime_result(match_obj, record, xbet_index, collector)
            halftime_rows.append(row)
            pick = row["pick"]
            print(
                f"ALLLIVE_HT {row['home']} - {row['away']} {row['score'][0]}:{row['score'][1]} | "
                f"Pgoal2H={row['analysis']['probability_goal_2h']} "
                f"Pover1.5={row['analysis']['probability_over_1_5_2h']} "
                f"real={row['real_stats']['providers']} | {pick['decision']} "
                f"{pick.get('selection')} {pick.get('line')} @{pick.get('odds')}",
                flush=True,
            )
        except Exception as exc:
            print(f"ALLLIVE_HT_FAIL {mid} {type(exc).__name__}:{exc}", flush=True)

    # Every initially live match gets a row, even if outside actionable windows or
    # finished while the diagnostic was collecting memory.
    covered = {row["event_id"] for row in active_rows} | {row["event_id"] for row in halftime_rows}
    for mid, initial_match in tracked.items():
        if mid in covered:
            continue
        cur = final_master.get(mid)
        match = cur or initial_match
        minute = int(match.minute or 0)
        phase = _phase(minute, bool(match.is_halftime))
        waiting_rows.append({
            "event_id": mid,
            "home": match.home,
            "away": match.away,
            "league": match.league,
            "minute": minute,
            "score": [int(match.home_score or 0), int(match.away_score or 0)],
            "phase": phase if cur else "FINISHED_OR_LEFT_LIVE",
            "decision": "WAIT" if cur else "ENDED_DURING_SCAN",
            "reason": "Outside configured live signal window." if cur else "Match left the live feed while sequential snapshots were collected.",
        })

    active_rows.sort(
        key=lambda row: (
            row["consensus"] == "BET",
            row["bet_votes"],
            0.0 if row["numeric_judge"] is None else float(row["numeric_judge"]["judge_score"]),
            float(row["brain_v3"].get("probability") or 0.0),
        ),
        reverse=True,
    )
    halftime_rows.sort(
        key=lambda row: (
            row["pick"]["decision"] == "BET",
            row["pick"]["decision"] == "LEAN",
            float(row["pick"].get("expected_value") or 0.0),
            float(row["analysis"].get("confidence") or 0.0),
        ),
        reverse=True,
    )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "initial_live_count": len(tracked),
        "poll_count": POLL_COUNT,
        "poll_seconds": POLL_SECONDS,
        "summary": {
            "active_analysed": len(active_rows),
            "halftime_analysed": len(halftime_rows),
            "waiting_or_finished": len(waiting_rows),
            "active_consensus_bets": sum(row["consensus"] == "BET" for row in active_rows),
            "active_low_data": sum(row["consensus"] == "LOW_DATA" for row in active_rows),
            "halftime_bets": sum(row["pick"]["decision"] == "BET" for row in halftime_rows),
            "halftime_leans": sum(row["pick"]["decision"] == "LEAN" for row in halftime_rows),
        },
        "active": active_rows,
        "halftime": halftime_rows,
        "waiting": waiting_rows,
    }
    Path("all_live_systems_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("=== ALL LIVE SYSTEMS RESULT ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
