from __future__ import annotations

import json

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.fotmob import FotMobProvider
from gool_bot2.providers.scores365 import Scores365Provider
from gool_bot2.providers.secondary_live_guard import (
    parse_365_goal_timeline,
    parse_365_stats_payload,
    parse_fotmob_goal_timeline,
)


FLASH_ID = "8zbeorHG"
FOTMOB_ID = "1000013708"
SCORES365_ID = "4732151"
HOME_365_ID = 9829
AWAY_365_ID = 10362


def _scores(rows: list[dict]) -> list[list[int]]:
    return [list(row.get("score") or []) for row in rows]


def _keeps_original_two_goals(rows: list[dict]) -> bool:
    scores = _scores(rows)
    return len(scores) >= 2 and scores[:2] == [[0, 1], [0, 2]]


def main() -> None:
    report: dict[str, object] = {}

    flashscore = FlashscoreProvider()
    fs_stats = flashscore.fetch_stats(FLASH_ID)
    fs_goals = flashscore.fetch_goal_timeline(FLASH_ID)
    fs_incidents = flashscore.fetch_incident_timeline(FLASH_ID)
    report["flashscore"] = {
        "goals": fs_goals,
        "red_cards": fs_stats.get("red_cards"),
        "shots": fs_stats.get("shots"),
        "shots_on_target": fs_stats.get("shots_on_target"),
        "corners": fs_stats.get("corners"),
        "incidents": len(fs_incidents),
    }
    # The audited production bug was that these two goals disappeared because
    # IA=2 (away) was incorrectly treated as a non-goal event type. Later match
    # events are allowed and must not make this regression fixture brittle.
    assert _keeps_original_two_goals(fs_goals), report["flashscore"]
    assert tuple(fs_stats.get("red_cards") or ()) == (1.0, 0.0), report["flashscore"]

    fotmob = FotMobProvider()
    fm_detail = fotmob._detail(FOTMOB_ID)
    fm_goals = parse_fotmob_goal_timeline(fm_detail)
    fm_status = ((fm_detail.get("header") or {}).get("status") or {}) if isinstance(fm_detail, dict) else {}
    report["fotmob"] = {
        "goals": fm_goals,
        "score": fm_status.get("scoreStr"),
        "live_time": fm_status.get("liveTime"),
        "home_red_cards": fm_status.get("numberOfHomeRedCards"),
        "away_red_cards": fm_status.get("numberOfAwayRedCards"),
        "momentum": ((fm_detail.get("content") or {}).get("momentum") if isinstance(fm_detail, dict) else None),
        "lineup": ((fm_detail.get("content") or {}).get("lineup") if isinstance(fm_detail, dict) else None),
    }
    assert _keeps_original_two_goals(fm_goals), report["fotmob"]

    scores365 = Scores365Provider()
    sc_game = scores365._detail(SCORES365_ID)
    sc_payload, sc_meta = scores365._stats_payload(SCORES365_ID)
    sc_stats = parse_365_stats_payload(sc_payload, HOME_365_ID, AWAY_365_ID)
    sc_goals = parse_365_goal_timeline(sc_game, HOME_365_ID, AWAY_365_ID)
    report["365scores"] = {
        "goals": sc_goals,
        "shots": sc_stats.get("shots"),
        "shots_on_target": sc_stats.get("shots_on_target"),
        "corners": sc_stats.get("corners"),
        "red_cards": sc_stats.get("red_cards"),
        "yellow_cards": sc_stats.get("yellow_cards"),
        "attacks": sc_stats.get("attacks"),
        "stats_meta": sc_meta,
        "detail_events": len(sc_game.get("events") or []) if isinstance(sc_game, dict) else 0,
    }
    # /game/stats is the cumulative source we previously ignored. Exact shot
    # totals may legitimately change after the 78' audit snapshot, but the feed
    # must stay populated and retain the confirmed home red card.
    shots = tuple(sc_stats.get("shots") or ())
    assert len(shots) == 2 and sum(shots) > 0, report["365scores"]
    assert tuple(sc_stats.get("red_cards") or ()) == (1.0, 0.0), report["365scores"]
    assert _keeps_original_two_goals(sc_goals), report["365scores"]

    report["cross_source"] = {
        "flashscore_last_score": _scores(fs_goals)[-1] if fs_goals else None,
        "fotmob_last_score": _scores(fm_goals)[-1] if fm_goals else None,
        "365scores_last_score": _scores(sc_goals)[-1] if sc_goals else None,
        "flashscore_vs_365_shots": [fs_stats.get("shots"), sc_stats.get("shots")],
        "flashscore_vs_365_sot": [fs_stats.get("shots_on_target"), sc_stats.get("shots_on_target")],
        "flashscore_vs_365_corners": [fs_stats.get("corners"), sc_stats.get("corners")],
    }

    print("GOOL_PROVIDER_HARDENING_OK")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
