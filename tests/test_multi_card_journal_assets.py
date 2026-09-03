from __future__ import annotations

from gool_bot2.multi_journal import entry_from_decision, settle_multi_journal
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _decision() -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        strategy="another_goal",
        label="ТБ 0.5",
        odd=1.70,
        model_probability=0.75,
        goals_to_win=1,
        correlation_key="any_next_goal",
        rating=80.0,
        expected_roi=0.275,
        value_edge_pp=16.2,
        data_quality=0.9,
    )
    return RouterDecision(
        status="BET", minute=20, score=(0, 0), winner=winner,
        alternatives=[], rejected=[], reason="fixture",
    )


def _record(minute=20, hs=0, aws=0, finished=False):
    return {
        "match": {
            "flashscore_event_id": "asset-fixture",
            "home": "Home",
            "away": "Away",
            "league": "League",
            "minute": minute,
            "home_score": hs,
            "away_score": aws,
            "is_finished": finished,
        },
        "providers": {
            "flashscore": {
                "meta": {
                    "home_team_id": "home-id",
                    "away_team_id": "away-id",
                    "home_team_slug": "home",
                    "away_team_slug": "away",
                    "goal_timeline": ([{"minute": 40, "score": [1, 0]}] if hs else []),
                },
                "stats": {
                    "shots": [9, 4],
                    "shots_on_target": [4, 1],
                    "corners": [5, 2],
                    "dangerous_attacks": [31, 18],
                },
            }
        },
        "cards": {"home_red": 0, "away_red": 1 if minute >= 40 else 0},
    }


def test_entry_persists_card_assets_and_active_mode(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    row = entry_from_decision(_record(), _decision(), {"another_goal": {"probability": 0.75}}, data_quality=0.9)

    assert row is not None
    assert row["mode"] == "active"
    assert row["flashscore_meta"]["home_team_id"] == "home-id"
    assert row["stats_snapshot"]["shots"] == "9 : 4"
    assert row["cards"] == {"home_red": 0, "away_red": 0}


def test_settlement_persists_final_stats_and_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")
    from gool_bot2.journal import save_signal_journal

    row = entry_from_decision(_record(), _decision(), {"another_goal": {"probability": 0.75}}, data_quality=0.9)
    assert row is not None
    path = tmp_path / "multi.json"
    save_signal_journal(path, [row])

    settled = settle_multi_journal(_record(40, 1, 0), path)

    assert len(settled) == 1
    assert settled[0]["result"] == "won"
    assert settled[0]["settled_stats_snapshot"]["shots"] == "9 : 4"
    assert settled[0]["settled_cards"]["away_red"] == 1
    assert settled[0]["flashscore_meta"]["away_team_id"] == "away-id"
