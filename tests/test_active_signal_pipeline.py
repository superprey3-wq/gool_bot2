from gool_bot2.live_gool_analyzer import analyze_two_more_goals
from gool_bot2.match_context import xg_or_proxy_pair
from gool_bot2.signal_worker_all_cards import _prematch_confirmation


def _provider(stats):
    return {"stats": stats}


def _history(team, opponent, scores):
    rows = []
    for i, (gf, ga) in enumerate(scores):
        rows.append({
            "event_id": f"m{i}",
            "home": team,
            "away": opponent,
            "home_score": gf,
            "away_score": ga,
            "timestamp": 1000 + i,
        })
    return rows


def test_attack_proxy_is_used_when_real_xg_is_missing():
    record = {
        "providers": {
            "flashscore": _provider({
                "shots": [8, 6],
                "shots_on_target": [3, 2],
                "corners": [4, 3],
                "dangerous_attacks": [30, 26],
            })
        }
    }
    home, away, source, evidence = xg_or_proxy_pair(record)
    assert source == "attack_proxy"
    assert evidence >= 2
    assert home is not None and away is not None
    assert home > 0 and away > 0


def test_real_xg_has_priority_over_proxy():
    record = {
        "providers": {
            "flashscore": _provider({"shots": [20, 20], "shots_on_target": [8, 8]}),
            "fotmob": _provider({"xg": [0.72, 0.41]}),
        }
    }
    home, away, source, evidence = xg_or_proxy_pair(record)
    assert source == "provider_xg"
    assert evidence == 1
    assert round(home, 2) == 0.72
    assert round(away, 2) == 0.41


def test_two_more_requires_new_recent_live_evidence_after_epoch_reset():
    record = {
        "match": {"minute": 31, "home_score": 1, "away_score": 1, "is_finished": False},
        "providers": {
            "flashscore": _provider({
                "shots": [8, 7], "shots_on_target": [4, 3], "big_chances": [2, 1],
                "shots_inside_box": [5, 4], "corners": [4, 3], "dangerous_attacks": [35, 31],
            })
        },
        "live_momentum": {"minutes_in_epoch": 2.0},
    }
    result = analyze_two_more_goals(record)
    assert result["passed"] is False
    assert result["recent_ready"] is False


def test_two_more_can_use_proxy_plus_fresh_momentum():
    record = {
        "match": {"minute": 31, "home_score": 1, "away_score": 1, "is_finished": False},
        "providers": {
            "flashscore": _provider({
                "shots": [11, 9], "shots_on_target": [5, 4], "big_chances": [2, 2],
                "shots_inside_box": [7, 6], "high_xg_shots": [1, 1], "corners": [5, 4],
                "dangerous_attacks": [42, 38], "touches_box": [18, 16],
            })
        },
        "live_momentum": {
            "minutes_in_epoch": 10.0,
            "shots_total_last_5m": 5.0,
            "sot_total_last_5m": 3.0,
            "big_total_last_5m": 1.0,
            "danger_total_last_5m": 15.0,
            "shots_total_last_10m": 9.0,
            "sot_total_last_10m": 5.0,
        },
    }
    result = analyze_two_more_goals(record)
    assert result["xg_source"] == "attack_proxy"
    assert result["recent_ready"] is True
    assert result["recent_threat"] is True
    assert result["quality_threat"] is True


def test_prematch_strong_average_profile_is_not_volume_blocked():
    home_scores = [(1, 0), (4, 2), (2, 0), (1, 1), (3, 1), (2, 1), (1, 0), (2, 2), (3, 0), (1, 1)]
    away_scores = [(1, 1), (2, 0), (0, 2), (2, 1), (3, 1), (1, 0), (2, 2), (1, 1), (3, 0), (2, 1)]
    record = {
        "match": {"home": "Home", "away": "Away"},
        "prematch_context": {
            "source": "test",
            "home_recent": _history("Home", "X", home_scores),
            "away_recent": _history("Away", "Y", away_scores),
            "home_at_home": _history("Home", "X", home_scores[:5]),
            "away_away": [
                {**row, "home": "Y", "away": "Away", "home_score": row["away_score"], "away_score": row["home_score"]}
                for row in _history("Away", "Y", away_scores[:5])
            ],
            "h2h": [],
        },
    }
    result = _prematch_confirmation(record)
    assert result["enough_history"] is True
    assert result["home_form"]["avg_total"] >= 1.7
    assert result["away_form"]["avg_total"] >= 1.7
    assert result["goal_volume_pass"] is True
