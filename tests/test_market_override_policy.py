from datetime import datetime, timezone

from gool_bot2.market_override_policy import can_override_another_goal, decorate_market_info


def _row():
    return {"captured_at": datetime.now(timezone.utc).isoformat()}


def test_strong_steam_becomes_override():
    info = {
        "available": True,
        "confirmed": True,
        "level": "STRONG_STEAM",
        "score_pp": 7.0,
        "targets": [{"prob_delta_pp": 7.2, "one_way_moves": 2}],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is True
    assert can_override_another_goal(out, 0.60) is True


def test_pressure_is_not_override():
    info = {
        "available": True,
        "confirmed": True,
        "level": "PRESSURE",
        "score_pp": 4.0,
        "targets": [{"prob_delta_pp": 4.0, "one_way_moves": 2}],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is False


def test_strong_market_still_requires_model_sanity():
    info = {
        "available": True,
        "confirmed": True,
        "level": "MULTI_MARKET_STEAM",
        "score_pp": 8.0,
        "targets": [{"prob_delta_pp": 8.0, "one_way_moves": 3}],
    }
    out = decorate_market_info(info, _row())
    assert out["override"] is True
    assert can_override_another_goal(out, 0.40) is False
