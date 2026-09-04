from gool_bot2.multi_entry_enrichment import _entry_timing_snapshot
from gool_bot2.multi_shadow import _timeline_context, context_snapshot


def _record():
    return {
        "captured_at": "2026-09-04T22:06:48+00:00",
        "match": {
            "flashscore_event_id": "8zbeorHG",
            "home": "Leones",
            "away": "Atletico F.C.",
            "minute": 78,
            "home_score": 0,
            "away_score": 2,
        },
        "providers": {
            "flashscore": {
                "stats": {"shots": (16.0, 6.0)},
                "meta": {
                    "goal_timeline_source": "flashscore",
                    "goal_timeline": [
                        {"minute": 3, "score": [0, 1]},
                        {"minute": 11, "score": [0, 2]},
                    ],
                    "goal_timeline_candidates": {
                        "flashscore": {"exact_score_match": True},
                        "fotmob": {"exact_score_match": True},
                        "365scores": {"exact_score_match": False},
                    },
                    "incident_timeline": [
                        {"minute": 11, "event_type": "goal", "side": "away"},
                        {"minute": 78, "event_type": "red_card", "side": "home"},
                    ],
                },
            },
            "fotmob": {"stats": {}, "meta": {}},
            "365scores": {"stats": {}, "meta": {}},
        },
        "provider_freshness": {
            "flashscore": {"endpoints": {"summary_incidents": {"observed_at": "2026-09-04T22:06:48+00:00"}}},
            "365scores": {"endpoints": {"game_stats": {"ttl_seconds": 10.0, "last_update_id": 5746578588}}},
        },
        "live_momentum": {},
        "prematch_context": {},
        "prefilter": {},
    }


def test_timeline_context_captures_last_goal_and_incident_without_changing_decision_inputs():
    row = _timeline_context(_record())
    assert row["source"] == "flashscore"
    assert row["last_goal_minute"] == 11
    assert row["minutes_since_last_goal"] == 67
    assert row["last_incident"] == {"minute": 78, "event_type": "red_card", "side": "home"}


def test_entry_timing_snapshot_persists_endpoint_freshness():
    row = _entry_timing_snapshot(_record())
    assert row["version"] == 1
    assert row["goal_timeline_source"] == "flashscore"
    assert row["provider_freshness"]["365scores"]["endpoints"]["game_stats"]["ttl_seconds"] == 10.0
    assert row["provider_freshness"]["365scores"]["endpoints"]["game_stats"]["last_update_id"] == 5746578588


def test_shadow_context_exposes_timing_as_observation_metadata():
    row = context_snapshot(_record())
    assert row["timeline"]["goal_count"] == 2
    assert row["provider_freshness"]["flashscore"]["endpoints"]["summary_incidents"]["observed_at"]
    # Existing live feature keys remain available and timing stays in separate
    # observation-only branches rather than altering the football metrics.
    assert "shots_total" in row["live"]
    assert "timeline" not in row["live"]
