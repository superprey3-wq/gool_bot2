import json
from datetime import datetime, timezone

from gool_bot2.live_multi_shadow import observe_live_multi_all_markets_shadow


def _fresh():
    return datetime.now(timezone.utc).isoformat()


def _record(minute=70, score=(1, 1), finished=False):
    return {
        "match": {
            "flashscore_event_id": "shadow-1",
            "home": "Home",
            "away": "Away",
            "league": "Test",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
            "is_finished": finished,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "xg": [0.45, 0.35],
                    "shots": [4, 3],
                    "shots_on_target": [1, 1],
                    "big_chances": [0, 0],
                    "shots_inside_box": [2, 2],
                }
            },
            "fotmob": {
                "stats": {
                    "xg": [0.45, 0.35],
                    "shots": [4, 3],
                    "shots_on_target": [1, 1],
                    "big_chances": [0, 0],
                    "shots_inside_box": [2, 2],
                }
            },
        },
        "live_momentum": {
            "minutes_in_epoch": 8,
            "xg_total_last_5m": 0.02,
            "shots_total_last_5m": 1,
            "sot_total_last_5m": 0,
            "big_total_last_5m": 0,
        },
    }


def _market():
    return {
        "score_home": 1,
        "score_away": 1,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 2.5, "over": 1.82, "under": 2.02}],
            "first_half_total": [],
            "home_total": [{"line": 1.5, "over": 2.55, "under": 1.48}],
            "away_total": [{"line": 1.5, "over": 2.65, "under": 1.45}],
            "btts": {"yes": 1.01, "no": 20.0},
            "match_1x2": {},
        },
        "pressure": {},
    }


def test_shadow_records_only_one_verdict_per_fixture_and_never_sends(monkeypatch, tmp_path):
    journal = tmp_path / "shadow.json"
    analysis = tmp_path / "analysis.jsonl"
    monkeypatch.setenv("GOOL_LIVE_MULTI_SHADOW_JOURNAL", str(journal))
    monkeypatch.setenv("GOOL_LIVE_MULTI_ALL_MARKETS_SHADOW", "1")

    first = observe_live_multi_all_markets_shadow(_record(), _market(), analysis)
    second = observe_live_multi_all_markets_shadow(_record(minute=71), _market(), analysis)

    assert first is not None and first.status == "BET"
    assert second is not None
    rows = json.loads(journal.read_text("utf-8"))
    assert len(rows) == 1
    assert rows[0]["telegram_sent"] is False
    assert rows[0]["shadow_only"] is True


def test_shadow_under_settles_won_at_finish(monkeypatch, tmp_path):
    journal = tmp_path / "shadow.json"
    analysis = tmp_path / "analysis.jsonl"
    monkeypatch.setenv("GOOL_LIVE_MULTI_SHADOW_JOURNAL", str(journal))
    monkeypatch.setenv("GOOL_LIVE_MULTI_ALL_MARKETS_SHADOW", "1")

    observe_live_multi_all_markets_shadow(_record(), _market(), analysis)
    observe_live_multi_all_markets_shadow(_record(minute=90, score=(1, 1), finished=True), None, analysis)

    rows = json.loads(journal.read_text("utf-8"))
    assert len(rows) == 1
    assert rows[0]["result"] == "won"
    assert rows[0]["profit_units"] > 0
