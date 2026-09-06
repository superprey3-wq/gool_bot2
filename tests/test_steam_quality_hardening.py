from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.multi_autonomous_steam import build_autonomous_steam_candidates
from gool_bot2.multi_public_metrics import STEAM_FORMULA, confidence_snapshot


def _record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "steam-quality",
            "home": "Home",
            "away": "Away",
            "minute": 26,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
        }
    }


def _market(delta: float, moves: int, related: list[float]) -> dict:
    pressure = {
        "match_total:0.5": {
            "prob_delta_pp": delta,
            "one_way_moves": moves,
            "old_odd": 1.81,
        }
    }
    keys = ("home_total:0.5", "away_total:0.5", "match_total:1.5")
    for key, related_delta in zip(keys, related):
        pressure[key] = {
            "prob_delta_pp": related_delta,
            "one_way_moves": 1,
            "old_odd": 2.00,
        }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "score_home": 0,
        "score_away": 0,
        "score_verified": True,
        "score_desync": False,
        "timeline_score_desync": False,
        "repricing_guard": False,
        "markets": {
            "match_total": [
                {"line": 0.5, "over": 1.62, "under": 2.30},
                {"line": 1.5, "over": 2.25, "under": 1.65},
            ],
            "home_total": [],
            "away_total": [],
            "first_half_total": [],
            "btts": {},
        },
        "pressure": pressure,
    }


def _target(rows):
    return next((row for row in rows if row.key == "match_total:0.5"), None)


def test_five_pp_two_move_signal_is_rejected_even_with_two_related_markets() -> None:
    rows = build_autonomous_steam_candidates(
        _record(),
        _market(5.0, 2, [2.4, 2.6]),
        data_quality=0.0,
    )
    assert _target(rows) is None


def test_high_delta_two_move_signal_survives_with_one_related_market() -> None:
    rows = build_autonomous_steam_candidates(
        _record(),
        _market(7.7, 2, [2.4]),
        data_quality=0.0,
    )
    target = _target(rows)
    assert target is not None
    assert target.market_pressure_pp == 7.7
    assert "steam_quality_gate" in target.reason_tags


def test_persistent_six_pp_signal_survives_with_broad_confirmation() -> None:
    rows = build_autonomous_steam_candidates(
        _record(),
        _market(6.3, 4, [2.2, 2.3, 2.5]),
        data_quality=0.0,
    )
    target = _target(rows)
    assert target is not None
    assert "steam_quality_gate" in target.reason_tags


def test_steam_public_confidence_is_market_strength_not_weak_football_blend() -> None:
    target = _target(
        build_autonomous_steam_candidates(
            _record(),
            _market(7.7, 2, [2.4]),
            data_quality=0.0,
        )
    )
    assert target is not None

    metrics = confidence_snapshot(_record(), target, {}, data_quality=0.0)

    assert metrics["layer"] == "STEAM"
    assert metrics["confidence_score"] == target.rating
    assert metrics["steam_score"] == target.rating
    assert metrics["formula"] == STEAM_FORMULA
    assert STEAM_FORMULA == "100% прогруз 1xBet · LIVE-футбол только справочно"
