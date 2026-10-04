from types import SimpleNamespace

from gool_bot2 import v4_shadow_report as report


def _stage1_profile():
    empty_team = {
        "scored_rate": .20,
        "conceded_rate": .20,
        "over": {"0.5": .20, "1.5": .20},
    }
    return {
        "first_half": {
            "available": True,
            "pair_sample": 8,
            "home": dict(empty_team),
            "away": dict(empty_team),
            "over": {"0.5": .20, "1.5": .20},
            "h2h": {"matches": 0},
        },
        "second_half": {
            "available": True,
            "pair_sample": 8,
            "home": dict(empty_team),
            "away": dict(empty_team),
            "over": {"0.5": .20, "1.5": .20},
            "h2h": {"matches": 0},
        },
        "full_match": {
            "available": True,
            "pair_sample": 8,
            "expected_total": 3.6,
            "home_expected_goals": 1.8,
            "away_expected_goals": 1.1,
        },
        "sources": ["flashscore_h2h"],
    }


def test_stage_one_uses_sample_quality_before_fusion(monkeypatch):
    class FakeFS:
        def fetch_match_history(self, *args, **kwargs):
            return {
                "home_recent": [],
                "away_recent": [],
                "home_at_home": [],
                "away_away": [],
                "h2h": [],
                "sources": ["flashscore_h2h"],
                "source_coverage": {},
            }

    monkeypatch.setattr(report, "FlashscoreProvider", FakeFS)
    monkeypatch.setattr(report, "build_prematch_goal_profile", lambda _record: _stage1_profile())

    fixture = SimpleNamespace(
        provider_match_id="TEST0001",
        home="Home",
        away="Away",
        league="Test",
        meta={},
    )
    rows, failures = report._analyse_fixtures(FakeFS(), [fixture])

    assert failures == {}
    assert len(rows) == 1
    assert rows[0]["quality"] == 1.0
    assert rows[0]["primary_trend"] is not None
    assert rows[0]["primary_trend"]["name"] == "FT_OVER_2.5"


def test_low_evidence_quality_does_not_erase_valid_trend():
    trends = report._trend_signals(_stage1_profile(), .61)
    assert any(t["name"] == "FT_OVER_2.5" for t in trends)
