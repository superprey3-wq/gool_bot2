from datetime import datetime, timezone

from gool_bot2.market_override_policy import can_override_another_goal, decorate_market_info


def _row():
    return {"captured_at": datetime.now(timezone.utc).isoformat()}


def _target(delta: float, moves: int, odd: float = 1.80):
    return {
        "prob_delta_pp": delta,
        "one_way_moves": moves,
        "weight": 1.0,
        "selection": {"odd": odd},
    }


def test_strong_steam_becomes_override():
    info = {
        "available": True,
        "confirmed": True,
        "level": "STRONG_STEAM",
        "score_pp": 7.0,
        "targets": [_target(7.2, 2, 1.80)],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is True
    assert out["override_price_ok"] is True
    assert can_override_another_goal(out, 0.60) is True


def test_low_odd_strong_steam_is_filtered():
    info = {
        "available": True,
        "confirmed": True,
        "level": "STRONG_STEAM",
        "score_pp": 9.0,
        "targets": [_target(9.0, 3, 1.20)],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is False
    assert out["override_price_ok"] is False
    assert out["override_primary_odd"] == 1.20
    assert "LOW_ODD" in out["reason"]


def test_pressure_is_not_override():
    info = {
        "available": True,
        "confirmed": True,
        "level": "PRESSURE",
        "score_pp": 4.0,
        "targets": [_target(4.0, 2, 1.80)],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is False


def test_strong_market_still_requires_model_sanity():
    info = {
        "available": True,
        "confirmed": True,
        "level": "MULTI_MARKET_STEAM",
        "score_pp": 8.0,
        "targets": [_target(8.0, 3, 1.80)],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is True
    assert can_override_another_goal(out, 0.40) is False
