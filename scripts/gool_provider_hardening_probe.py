from __future__ import annotations

import json

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.fotmob import FotMobProvider
from gool_bot2.providers.scores365 import Scores365Provider
from gool_bot2.providers.secondary_live_guard import parse_365_stats_payload, parse_fotmob_goal_timeline


FLASH_ID = "8zbeorHG"
FOTMOB_ID = "1000013708"
SCORES365_ID = "4732151"
HOME_365_ID = 9829
AWAY_365_ID = 10362


def _scores(rows: list[dict]) -> list[list[int]]:
    return [list(row.get("score") or []) for row in rows]


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
        "incidents": len(fs_incidents),
    }
    assert _scores(fs_goals) == [[0, 1], [0, 2]], report["flashscore"]
    assert tuple(fs_stats.get("red_cards") or ()) == (1.0, 0.0), report["flashscore"]

    fotmob = FotMobProvider()
    fm_detail = fotmob._detail(FOTMOB_ID)
    fm_goals = parse_fotmob_goal_timeline(fm_detail)
    fm_status = ((fm_detail.get("header") or {}).get("status") or {}) if isinstance(fm_detail, dict) else {}
    report["fotmob"] = {
        "goals": fm_goals,
        "score": fm_status.get("scoreStr"),
        "live_time": fm_status.get("liveTime"),
        "momentum": ((fm_detail.get("content") or {}).get("momentum") if isinstance(fm_detail, dict) else None),
        "lineup": ((fm_detail.get("content") or {}).get("lineup") if isinstance(fm_detail, dict) else None),
    }
    assert _scores(fm_goals) == [[0, 1], [0, 2]], report["fotmob"]

    scores365 = Scores365Provider()
    sc_game = scores365._detail(SCORES365_ID)
    sc_payload, sc_meta = scores365._stats_payload(SCORES365_ID)
    sc_stats = parse_365_stats_payload(sc_payload, HOME_365_ID, AWAY_365_ID)
    report["365scores"] = {
        "shots": sc_stats.get("shots"),
        "shots_on_target": sc_stats.get("shots_on_target"),
        "corners": sc_stats.get("corners"),
        "red_cards": sc_stats.get("red_cards"),
        "yellow_cards": sc_stats.get("yellow_cards"),
        "attacks": sc_stats.get("attacks"),
        "stats_meta": sc_meta,
        "detail_events": len(sc_game.get("events") or []) if isinstance(sc_game, dict) else 0,
    }
    assert tuple(sc_stats.get("shots") or ()) == (16.0, 6.0), report["365scores"]
    assert tuple(sc_stats.get("shots_on_target") or ()) == (7.0, 4.0), report["365scores"]
    assert tuple(sc_stats.get("red_cards") or ()) == (1.0, 0.0), report["365scores"]

    print("GOOL_PROVIDER_HARDENING_OK")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
