from __future__ import annotations

from types import SimpleNamespace

import pytest

from gool_bot2 import brain_v3_memory as memory
from gool_bot2.multi_money_flow_total_volume import enhance_money_flow_result
from gool_bot2.steam_quality_hardening import _trajectory_ok


def _memory_record(minute: int, *, score=(1, 0), shots=(4, 2), sot=(2, 1), xg=(0.5, 0.2)) -> dict:
    return {
        "captured_at": f"2026-09-06T12:{minute % 60:02d}:00+00:00",
        "match": {
            "flashscore_event_id": "epoch-test",
            "home": "Home",
            "away": "Away",
            "league": "Test League",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": list(shots),
                    "shots_on_target": list(sot),
                    "xg": list(xg),
                    "big_chances": [1, 0],
                    "dangerous_attacks": [20, 8],
                    "corners": [2, 1],
                }
            }
        },
    }


def test_v3_memory_never_carries_first_half_pressure_into_second_half() -> None:
    memory._HISTORY.clear()
    memory.build_brain_v3_memory(_memory_record(35, shots=(10, 4), sot=(5, 1), xg=(1.2, 0.3)), "epoch-test")
    second_half = memory.build_brain_v3_memory(
        _memory_record(46, shots=(11, 4), sot=(5, 1), xg=(1.25, 0.3)),
        "epoch-test",
    )

    assert second_half["current"]["period"] == "2H"
    assert second_half["score_epoch"]["period"] == "2H"
    assert second_half["score_epoch"]["start_minute"] == 46
    assert second_half["windows"] == {}

    later = memory.build_brain_v3_memory(
        _memory_record(51, shots=(14, 5), sot=(7, 1), xg=(1.55, 0.35)),
        "epoch-test",
    )
    assert later["windows"]["5m"]["home_shots"] == 3.0
    assert later["windows"]["5m"]["home_xg"] == pytest.approx(0.30)


def _steam_candidate() -> SimpleNamespace:
    return SimpleNamespace(
        strategy="steam_another_goal",
        key="match_total:2.5",
        market_pressure_pp=7.5,
        reason_tags=["market_breadth:2"],
    )


def test_steam_trajectory_rejects_move_that_is_reversing() -> None:
    row = {
        "pressure": {
            "match_total:2.5": {
                "one_way_moves": 3,
                "directional_consistency": 0.60,
                "down_moves": 2,
                "consecutive_up_moves": 0,
                "retrace_pp": 2.1,
                "latest_step_pp": -0.8,
            }
        }
    }
    assert _trajectory_ok(_steam_candidate(), row) is False


def test_steam_trajectory_accepts_persistent_non_reversing_move() -> None:
    row = {
        "pressure": {
            "match_total:2.5": {
                "one_way_moves": 4,
                "directional_consistency": 0.85,
                "down_moves": 1,
                "consecutive_up_moves": 3,
                "retrace_pp": 0.4,
                "latest_step_pp": 0.7,
            }
        }
    }
    assert _trajectory_ok(_steam_candidate(), row) is True


def _flow_record(*, fair_pp: float, relative_delta: float = 700.0) -> dict:
    market_volume = 4200.0
    return {
        "match": {
            "flashscore_event_id": "flow-test",
            "home": "Czech B",
            "away": "Czech C",
            "league": "Czech Republic 3. Liga",
            "minute": 68,
            "home_score": 1,
            "away_score": 0,
            "is_finished": False,
        },
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "mb-event"},
            "systems": {
                "money_flow": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 1.5,
                    "market_id": "mb-market",
                    "market_name": "Total Goals",
                    "volume": market_volume,
                    "fair_over": 0.61,
                    "over": {
                        "best_back": {"odds": 1.72},
                        "best_lay": {"odds": 1.76},
                    },
                    "flow": {
                        "window_ready_120s": True,
                        "volume_delta_120s": relative_delta,
                        "fair_over_delta_pp_120s": fair_pp,
                        "window_ready_300s": False,
                        "long_direction_consistency": 0.80,
                        "orderbook_ready": False,
                        "orderbook_confirmed": False,
                        "back_wom": 0.58,
                        "orderflow_imbalance": 0.20,
                        "orderbook_support_streak": 2,
                    },
                }
            },
        },
    }


def test_non_top_money_flow_can_signal_sustained_large_accumulation() -> None:
    base = {
        "eligible": False,
        "reason": "money_flow_threshold_not_reached",
        "market_volume": 4200.0,
        "flow": {},
    }
    out = enhance_money_flow_result(_flow_record(fair_pp=0.80), base)
    assert out["eligible"] is True
    assert out["level"] == "NON_TOP_ACCUMULATION"
    assert out["signal_basis"] == "non_top_long_accumulation"
    assert out["market_volume"] == 4200.0


def test_non_top_big_money_without_over_direction_stays_wait() -> None:
    base = {
        "eligible": False,
        "reason": "money_flow_threshold_not_reached",
        "market_volume": 4200.0,
        "flow": {},
    }
    out = enhance_money_flow_result(_flow_record(fair_pp=0.10), base)
    assert out["eligible"] is False
    assert out["reason"] == "money_flow_total_volume_wait_direction"
