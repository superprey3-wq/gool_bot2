from __future__ import annotations

import pytest

from gool_bot2 import brain_v3_memory as memory


def _record(minute: int, *, score=(0, 0), shots=(0, 0), sot=(0, 0), xg=(0.0, 0.0), big=(0, 0), danger=(0, 0)) -> dict:
    return {
        "captured_at": "2026-09-06T12:00:00+00:00",
        "match": {
            "flashscore_event_id": "v3-test",
            "home": "Home",
            "away": "Away",
            "league": "Test League",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_finished": False,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": list(shots),
                    "shots_on_target": list(sot),
                    "xg": list(xg),
                    "big_chances": list(big),
                    "dangerous_attacks": list(danger),
                    "corners": [0, 0],
                }
            }
        },
    }


def test_v3_memory_builds_5_10_15_minute_pressure_windows() -> None:
    memory._HISTORY.clear()
    memory.build_brain_v3_memory(_record(10, shots=(1, 1), sot=(0, 0), xg=(0.05, 0.05)), "v3-test")
    memory.build_brain_v3_memory(_record(15, shots=(3, 1), sot=(1, 0), xg=(0.20, 0.05), danger=(8, 2)), "v3-test")
    memory.build_brain_v3_memory(_record(20, shots=(8, 2), sot=(4, 0), xg=(0.72, 0.08), big=(1, 0), danger=(30, 4)), "v3-test")
    out = memory.build_brain_v3_memory(
        _record(25, shots=(14, 3), sot=(7, 1), xg=(1.34, 0.12), big=(2, 0), danger=(56, 7)),
        "v3-test",
    )

    assert set(out["windows"]) >= {"5m", "10m", "15m"}
    assert out["windows"]["5m"]["home_shots"] == 6.0
    assert out["windows"]["10m"]["home_xg"] == pytest.approx(1.14)
    assert out["pressure"]["home"] is not None
    assert out["pressure"]["home"] > out["pressure"]["away"]
    assert out["pressure"]["state"] in {"HOME_PRESSURE", "HOME_SIEGE", "HOME_BUILDING"}


def test_v3_memory_never_uses_pre_goal_stats_as_post_goal_pressure() -> None:
    memory._HISTORY.clear()
    memory.build_brain_v3_memory(_record(60, score=(0, 0), shots=(10, 5), sot=(5, 2), xg=(1.20, 0.55)), "v3-test")
    memory.build_brain_v3_memory(_record(65, score=(0, 0), shots=(14, 6), sot=(7, 2), xg=(1.65, 0.60)), "v3-test")

    after_goal = memory.build_brain_v3_memory(
        _record(66, score=(1, 0), shots=(14, 6), sot=(7, 2), xg=(1.65, 0.60)),
        "v3-test",
    )
    assert after_goal["score_epoch"]["start_minute"] == 66
    assert after_goal["score_epoch"]["samples"] == 1
    assert after_goal["windows"] == {}
    assert after_goal["pressure"]["state"] == "POST_GOAL_RESET"

    later = memory.build_brain_v3_memory(
        _record(71, score=(1, 0), shots=(15, 7), sot=(7, 2), xg=(1.69, 0.63)),
        "v3-test",
    )
    assert later["windows"]["5m"]["home_xg"] == pytest.approx(0.04)
    assert later["windows"]["5m"]["home_shots"] == 1.0


def test_v3_duplicate_same_minute_replaces_snapshot_instead_of_fake_delta() -> None:
    memory._HISTORY.clear()
    memory.build_brain_v3_memory(_record(10, shots=(1, 0), xg=(0.05, 0.0)), "v3-test")
    memory.build_brain_v3_memory(_record(10, shots=(2, 0), xg=(0.10, 0.0)), "v3-test")
    out = memory.build_brain_v3_memory(_record(15, shots=(4, 0), xg=(0.25, 0.0)), "v3-test")

    assert out["snapshot_count"] == 2
    assert out["windows"]["5m"]["home_shots"] == 2.0
    assert out["windows"]["5m"]["home_xg"] == pytest.approx(0.15)
