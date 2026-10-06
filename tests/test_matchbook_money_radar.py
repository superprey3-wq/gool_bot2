from __future__ import annotations

from gool_bot2.matchbook_money_radar import build_money_radar


def _market(
    volume: float,
    *,
    over: float,
    under: float,
    fair_pp_300: float = 0.0,
    delta_300: float = 0.0,
    old_over: float = 1.80,
    new_over: float = 1.80,
    over_consistency: float = 0.5,
    under_consistency: float = 0.5,
):
    return {
        "period": "FT",
        "line": 2.5,
        "volume": volume,
        "over": {"volume": over, "best_back": {"odds": new_over}},
        "under": {"volume": under, "best_back": {"odds": 2.0}},
        "over_matched": over,
        "under_matched": under,
        "flow": {
            "window_ready_300s": True,
            "volume_delta_300s": delta_300,
            "fair_over_delta_pp_300s": fair_pp_300,
            "over_back_old_300s": old_over,
            "over_back_new": new_over,
            "long_over_consistency": over_consistency,
            "long_under_consistency": under_consistency,
        },
    }


def test_low_league_extreme_volume_is_lifted_to_top(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_RADAR_NON_TOP_VOLUME_GBP", "5000")
    monkeypatch.setenv("MATCHBOOK_RADAR_NON_TOP_EXTREME_GBP", "10000")
    state = {
        "available": True,
        "events": [
            {
                "event_id": "gr-1",
                "name": "Nestos Chrysoupoli FC vs Marko",
                "home": "Nestos Chrysoupoli FC",
                "away": "Marko",
                "country": "Greece",
                "league": "Super League 2",
                "in_running": True,
                "totals": {
                    "FT:2.5": _market(
                        61183.0,
                        over=26485.0,
                        under=34698.0,
                        fair_pp_300=-2.4,
                        delta_300=9200.0,
                        old_over=1.67,
                        new_over=1.82,
                        under_consistency=0.82,
                    )
                },
            }
        ],
    }

    rows = build_money_radar(state)

    assert len(rows) == 1
    row = rows[0]
    assert row["country"] == "Greece"
    assert row["league"] == "Super League 2"
    assert row["level"] == "EXTREME_VOLUME"
    assert row["direction"] == "TM"
    assert round(row["under_share_pct"]) == 57
    assert round(row["over_share_pct"]) == 43
    assert row["score"] >= 90.0


def test_top_league_small_volume_is_not_false_anomaly(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_RADAR_TOP_VOLUME_GBP", "25000")
    state = {
        "available": True,
        "events": [
            {
                "event_id": "eng-1",
                "name": "Alpha vs Beta",
                "country": "England",
                "league": "Premier League",
                "in_running": True,
                "totals": {
                    "FT:2.5": _market(
                        8500.0,
                        over=4500.0,
                        under=4000.0,
                        fair_pp_300=1.0,
                        delta_300=1500.0,
                        over_consistency=0.8,
                    )
                },
            }
        ],
    }

    assert build_money_radar(state) == []


def test_long_accumulation_can_trigger_before_absolute_threshold(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_RADAR_NON_TOP_VOLUME_GBP", "5000")
    monkeypatch.setenv("MATCHBOOK_RADAR_LONG_MIN_DELTA_GBP", "1000")
    monkeypatch.setenv("MATCHBOOK_RADAR_LONG_MIN_RELATIVE_PCT", "12")
    state = {
        "available": True,
        "events": [
            {
                "event_id": "cz-1",
                "name": "Reserve A vs Reserve B",
                "country": "Czech Republic",
                "league": "CFL",
                "in_running": True,
                "totals": {
                    "FT:2.5": _market(
                        4200.0,
                        over=2500.0,
                        under=1700.0,
                        fair_pp_300=1.2,
                        delta_300=1200.0,
                        old_over=1.90,
                        new_over=1.72,
                        over_consistency=0.8,
                    )
                },
            }
        ],
    }

    rows = build_money_radar(state)
    assert len(rows) == 1
    assert rows[0]["level"] == "ACCUMULATION"
    assert rows[0]["direction"] == "TB"
