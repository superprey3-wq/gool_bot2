from __future__ import annotations

from types import SimpleNamespace

import gool_bot2.betdaq_selection_lifecycle as life
import gool_bot2.betdaq_signal_menu as menu


def _alert(**overrides):
    row = {
        "level": "SELECTION_FLOW",
        "score": 89.0,
        "event_id": "bdq-event-1",
        "event_name": "Bolton Wanderers v West Ham",
        "home": "Bolton Wanderers",
        "away": "West Ham",
        "in_running": True,
        "market_id": "market-1",
        "selection_id": "sel-1",
        "family": "total",
        "selection": "TM",
        "label": "ТМ 1.5",
        "period": "FT",
        "line": 1.5,
        "window": "15s",
        "delta_for_gbp": 500.0,
    }
    row.update(overrides)
    return row


def _live_match(home="Bolton Wanderers", away="West Ham", hs=0, aws=0, minute=60):
    return SimpleNamespace(
        provider_match_id="fs123456",
        home=home,
        away=away,
        home_score=hs,
        away_score=aws,
        minute=minute,
        is_halftime=False,
    )


def test_total_push_is_recorded_and_settled_from_flashscore(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BETDAQ_SELECTION_PUSH_STATE_PATH", str(tmp_path / "betdaq-state.json"))
    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider.live_matches", lambda self: [_live_match()])
    monkeypatch.setattr(
        "gool_bot2.providers.flashscore.FlashscoreProvider.event_states",
        lambda self, ids: {
            "fs123456": {
                "event_id": "fs123456",
                "is_finished": True,
                "status_code": "100",
                "home_score": 0,
                "away_score": 1,
            }
        },
    )

    created = life.record_alerts([_alert()])
    assert len(created) == 1
    assert created[0]["result"] == "pending"
    assert created[0]["flashscore_event_id"] == "fs123456"

    settled = life.reconcile_pending(force=True)
    assert len(settled) == 1
    assert settled[0]["result"] == "won"
    assert settled[0]["settled_score"] == [0, 1]
    assert "ЗАШЁЛ" in life.result_text(settled[0])


def test_match_odds_push_can_lose(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BETDAQ_SELECTION_PUSH_STATE_PATH", str(tmp_path / "betdaq-p1.json"))
    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider.live_matches", lambda self: [_live_match()])
    monkeypatch.setattr(
        "gool_bot2.providers.flashscore.FlashscoreProvider.event_states",
        lambda self, ids: {
            "fs123456": {
                "event_id": "fs123456",
                "is_finished": True,
                "status_code": "100",
                "home_score": 1,
                "away_score": 2,
            }
        },
    )

    life.record_alerts(
        [
            _alert(
                family="match_odds",
                selection="P1",
                label="П1 Bolton Wanderers",
                line=None,
            )
        ]
    )
    settled = life.reconcile_pending(force=True)
    assert settled[0]["result"] == "lost"
    assert "НЕ ЗАШЁЛ" in life.result_text(settled[0])


def test_first_half_total_settles_after_second_half_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BETDAQ_SELECTION_PUSH_STATE_PATH", str(tmp_path / "betdaq-1h.json"))
    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider.live_matches", lambda self: [_live_match(minute=30)])
    monkeypatch.setattr(
        "gool_bot2.providers.flashscore.FlashscoreProvider.event_states",
        lambda self, ids: {
            "fs123456": {
                "event_id": "fs123456",
                "is_finished": False,
                "status_code": "13",
                "home_score": 1,
                "away_score": 1,
            }
        },
    )
    monkeypatch.setattr(
        "gool_bot2.providers.flashscore.FlashscoreProvider.fetch_goal_timeline",
        lambda self, event_id: [
            {"period": "1H", "score": [1, 0]},
            {"period": "2H", "score": [1, 1]},
        ],
    )

    life.record_alerts([_alert(period="1H", selection="TB", label="ТБ 0.5 · 1H", line=0.5)])
    settled = life.reconcile_pending(force=True)
    assert settled[0]["result"] == "won"
    assert settled[0]["settled_score"] == [1, 0]


def test_menu_counts_betdaq_results_separately(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BETDAQ_SELECTION_PUSH_STATE_PATH", str(tmp_path / "betdaq-menu.json"))
    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider.live_matches", lambda self: [])
    life.record_alerts([_alert(in_running=False)])

    menu._ORIGINAL_REPORT = lambda path, experiment_path=None: "BASE REPORT"
    menu._ORIGINAL_IN_GAME = lambda journal_path, analysis_path=None: ["BASE IN GAME"]

    report = menu.report_with_betdaq(tmp_path / "journal.json")
    assert "BETDAQ ПРОГРУЗ" in report
    assert "⏳ 1" in report

    sections = menu.in_game_with_betdaq(tmp_path / "journal.json")
    assert sections[0] == "BASE IN GAME"
    assert "BETDAQ ПРОГРУЗ · ОТКРЫТЫ · 1" in sections[-1]
