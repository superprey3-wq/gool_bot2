from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.multi_result_reconcile import reconcile_finalized_first_half
from gool_bot2.providers.flashscore import FlashscoreProvider


def test_report_recheck_corrects_old_wuhan_style_false_win(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "multi.json"
    now = datetime.now(timezone.utc).isoformat()
    save_signal_journal(
        path,
        [
            {
                "created_at": now,
                "mode": "active",
                "head": "multi",
                "match_id": "wuhan-qingdao",
                "home": "Wuhan Three Towns",
                "away": "Qingdao West Coast",
                "league": "China: Super League",
                "minute": 29,
                "score": [2, 1],
                "entry_key": "wuhan-qingdao:2-1:first_half_total:3.5",
                "market_key": "first_half_total:3.5",
                "market_family": "first_half_total",
                "market": "1Т ТБ 3.5",
                "strategy": "goal_before_ht",
                "odd": 1.97,
                "result": "won",
                "profit_units": 0.97,
                "settled_at": now,
                "settled_minute": 43,
                "settled_score": [2, 2],
                "settlement_source": "flashscore_first_half_timeline",
                "virtual_stake_rub": 2000.0,
                "virtual_profit_rub": 1940.0,
                "telegram_sent": True,
                "result_telegram_sent": True,
                "result_notification_pending": False,
            }
        ],
    )

    monkeypatch.setattr(
        FlashscoreProvider,
        "event_states",
        lambda self, ids: {
            "wuhan-qingdao": {
                "status_code": "38",
                "coarse_status": "2",
                "is_finished": False,
                "home_score": 2,
                "away_score": 1,
            }
        },
    )
    # Even a stale incident timeline must not override the authoritative HT 2:1.
    monkeypatch.setattr(
        FlashscoreProvider,
        "fetch_goal_timeline",
        lambda self, mid: [
            {"minute": 3, "score": [0, 1], "period": "1H", "event_type": "goal"},
            {"minute": 19, "score": [1, 1], "period": "1H", "event_type": "goal"},
            {"minute": 22, "score": [2, 1], "period": "1H", "event_type": "goal"},
            {"minute": 43, "score": [2, 2], "period": "1H", "event_type": "goal"},
        ],
    )

    assert reconcile_finalized_first_half(path) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "lost"
    assert row["settled_score"] == [2, 1]
    assert row["settled_minute"] == 45
    assert row["profit_units"] == -1.0
    assert row["virtual_profit_rub"] == -2000.0
    assert row["settlement_corrected"] is True
    assert row["settlement_correction"]["result"] == "won"
    assert row["result_notification_pending"] is True


def test_report_recheck_ignores_new_var_safe_settlement(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "multi.json"
    save_signal_journal(
        path,
        [
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "match_id": "safe",
                "market_family": "first_half_total",
                "market_key": "first_half_total:0.5",
                "result": "won",
                "settlement_source": "flashscore_halftime_master",
            }
        ],
    )

    called = {"state": False}

    def _states(self, ids):
        called["state"] = True
        return {}

    monkeypatch.setattr(FlashscoreProvider, "event_states", _states)
    assert reconcile_finalized_first_half(path) == 0
    assert called["state"] is False
