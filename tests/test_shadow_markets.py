from pathlib import Path

from gool_bot2.shadow_markets import analyze_btts_shadow, analyze_team_goal_shadow
from gool_bot2.shadow_market_worker import process_record


def _provider(stats):
    return {"stats": stats}


def _history(team, opponent, scores):
    out = []
    for i, (gf, ga) in enumerate(scores):
        out.append({
            "event_id": f"m{i}",
            "home": team,
            "away": opponent,
            "home_score": gf,
            "away_score": ga,
        })
    return out


def _record(score=(0, 0)):
    home_scores = [(2, 1), (1, 0), (3, 1), (2, 0), (1, 1), (2, 1)]
    away_scores = [(1, 1), (2, 1), (1, 0), (3, 1), (2, 0), (1, 1)]
    return {
        "match": {
            "flashscore_event_id": "evt1",
            "home": "Home",
            "away": "Away",
            "league": "Test League",
            "minute": 30,
            "home_score": score[0],
            "away_score": score[1],
            "is_finished": False,
        },
        "prematch_context": {
            "home_recent": _history("Home", "X", home_scores),
            "away_recent": _history("Away", "Y", away_scores),
        },
        "providers": {
            "flashscore": _provider({
                "shots": [10, 9],
                "shots_on_target": [4, 4],
                "shots_inside_box": [6, 5],
                "big_chances": [2, 2],
                "dangerous_attacks": [38, 36],
                "corners": [4, 4],
            })
        },
        "live_momentum": {
            "home_shots_last_5m": 3,
            "away_shots_last_5m": 3,
            "home_sot_last_5m": 2,
            "away_sot_last_5m": 2,
            "home_big_last_5m": 1,
            "away_big_last_5m": 1,
            "home_danger_last_5m": 9,
            "away_danger_last_5m": 9,
            "home_shots_last_10m": 5,
            "away_shots_last_10m": 5,
            "home_sot_last_10m": 3,
            "away_sot_last_10m": 3,
        },
    }


def test_shadow_btts_can_pass_at_zero_zero_with_two_sided_pressure():
    result = analyze_btts_shadow(_record((0, 0)))
    assert result["shadow_only"] is True
    assert result["passed"] is True


def test_shadow_team_goal_selects_a_team_without_emitting_active_signal():
    result = analyze_team_goal_shadow(_record((0, 0)))
    assert result["shadow_only"] is True
    assert result["passed"] is True
    assert result["selected_side"] in {"home", "away"}
    assert result["team"] in {"Home", "Away"}


def test_shadow_worker_uses_separate_journal_and_settles_team_goal(tmp_path: Path):
    journal = tmp_path / "shadow.json"
    analysis = tmp_path / "shadow.jsonl"
    cards = tmp_path / "cards"
    record = _record((0, 0))
    created = process_record(record, journal, analysis, cards)
    assert created >= 1
    assert journal.exists()
    assert analysis.exists()
    assert list(cards.glob("*.png"))
