from __future__ import annotations

from pathlib import Path

import gool_bot2.brain_journal_results as bridge
import gool_bot2.multi_journal as journal
import gool_bot2.multi_menu as menu


def _entry(strategy: str = "another_goal") -> dict:
    return {
        "signal_key": f"m1:{strategy}",
        "created_at": "2026-09-10T00:00:00+00:00",
        "match_id": "m1",
        "home": "Home",
        "away": "Away",
        "minute": 60 if strategy == "another_goal" else 30,
        "score": [1, 0],
        "strategy": strategy,
        "head": strategy,
        "market": "ТБ 1.5" if strategy == "another_goal" else "1Т ТБ 1.5",
        "odd": 0.0,
        "probability": 0.82,
        "event_score": 82.0,
        "confidence_score": 82.0,
        "source": f"brain_primary:{strategy}",
        "signal_source": "GOOL_BRAIN",
    }


def _record(*, minute: int, hs: int, aws: int, finished: bool = False, halftime: bool = False) -> dict:
    return {
        "match": {
            "flashscore_event_id": "m1",
            "home": "Home",
            "away": "Away",
            "minute": minute,
            "home_score": hs,
            "away_score": aws,
            "is_finished": finished,
            "is_halftime": halftime,
            "status_code": "38" if halftime else "13" if minute >= 46 else "12",
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": []}}},
    }


def test_brain_journal_row_is_result_only_not_fake_bet():
    row = bridge._journal_row(_entry(), 1)
    assert row["result"] == "tracking"
    assert row["accounting_mode"] == "result_only"
    assert row["market_family"] == "match_total"
    assert row["market_key"] == "match_total:1.5"
    assert row["telegram_sent"] is True
    assert row["virtual_stake_rub"] == 0.0
    assert row["virtual_stake_pct"] == 0.0


def test_tracking_brain_signal_settles_won_without_fake_profit(monkeypatch):
    row = bridge._journal_row(_entry(), 1)
    monkeypatch.setattr(bridge, "_ORIGINAL_SETTLE_ENTRY", journal.settle_entry)
    monkeypatch.setattr(bridge, "_ORIGINAL_FINISH_ROW", journal._finish_row)
    monkeypatch.setattr(journal, "_finish_row", bridge._finish_result_only)
    changed = bridge._settle_tracking(row, _record(minute=66, hs=1, aws=1))
    assert changed is True
    assert row["result"] == "won"
    assert row["settled_score"] == [1, 1]
    assert row["profit_units"] == 0.0
    assert row["virtual_profit_rub"] == 0.0


def test_tracking_brain_signal_settles_lost_at_finish(monkeypatch):
    row = bridge._journal_row(_entry(), 1)
    monkeypatch.setattr(bridge, "_ORIGINAL_SETTLE_ENTRY", journal.settle_entry)
    monkeypatch.setattr(bridge, "_ORIGINAL_FINISH_ROW", journal._finish_row)
    monkeypatch.setattr(journal, "_finish_row", bridge._finish_result_only)
    changed = bridge._settle_tracking(row, _record(minute=90, hs=1, aws=0, finished=True))
    assert changed is True
    assert row["result"] == "lost"
    assert row["profit_units"] == 0.0


def test_tracking_counts_as_pending_but_does_not_pollute_roi(monkeypatch):
    tracking = bridge._journal_row(_entry(), 1)
    normal = {
        "result": "won",
        "profit_units": 1.0,
        "odd": 2.0,
        "created_at": "2026-09-10T00:00:00+00:00",
    }
    monkeypatch.setattr(bridge, "_ORIGINAL_STATS_LINE", menu._stats_line)
    monkeypatch.setattr(bridge, "_ORIGINAL_ROI", menu._roi)
    monkeypatch.setattr(bridge, "_ORIGINAL_PROFIT", menu._profit)
    monkeypatch.setattr(menu, "_roi", bridge._roi)
    monkeypatch.setattr(menu, "_profit", bridge._profit)
    text = bridge._stats_line([tracking, normal])
    assert "✅ <b>1</b>" in text
    assert "⏳ <b>1</b>" in text
    assert "ROI <b>+100.0%</b>" in text


def test_brain_result_card_uses_dash_without_required_price(tmp_path: Path, monkeypatch):
    row = bridge._journal_row(_entry(), 1)
    row.update({"result": "won", "settled_minute": 66, "settled_score": [1, 1], "result_notification_pending": True})
    seen = []

    def fake_render(render_row, record):
        seen.append(render_row.get("odd"))
        return b"brain-result-png"

    monkeypatch.setattr("gool_bot2.multi_card.render_multi_result_card", fake_render)
    monkeypatch.setattr("gool_bot2.telegram.broadcast_photo", lambda png: 1 if png == b"brain-result-png" else 0)
    monkeypatch.setattr("gool_bot2.telegram.broadcast", lambda text: 0)
    monkeypatch.setattr("gool_bot2.multi_delivery.finalize_result_delivery", lambda *args, **kwargs: True)
    monkeypatch.setattr(bridge, "_ORIGINAL_EMIT_RESULTS", lambda *args, **kwargs: 0)
    sent = bridge._emit_results(_record(minute=66, hs=1, aws=1), [row], journal_path=tmp_path / "journal.json")
    assert sent == 1
    assert seen == [None]
