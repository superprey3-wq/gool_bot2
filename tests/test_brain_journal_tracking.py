from __future__ import annotations

from pathlib import Path

import gool_bot2.brain_journal_tracking as tracking
from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _brain_entry() -> dict:
    return {
        "signal_key": "fs1:another_goal",
        "created_at": "2026-09-10T00:00:00+00:00",
        "match_id": "fs1",
        "home": "Home",
        "away": "Away",
        "league": "League",
        "minute": 58,
        "score": [1, 0],
        "strategy": "another_goal",
        "head": "another_goal",
        "market": "ТБ 1.5",
        "odd": 0.0,
        "probability": 0.92,
        "event_score": 92.0,
        "confidence_score": 92.0,
        "source": "brain_primary:another_goal",
        "signal_source": "GOOL_BRAIN",
        "result": "signal_only",
    }


def _steam_decision() -> RouterDecision:
    candidate = MarketCandidate(
        key="match_total:1.5",
        family="match_total",
        label="ТБ 1.5",
        odd=1.80,
        model_probability=0.72,
        market_probability=0.60,
        goals_to_win=1,
        correlation_key="any_next_goal",
        strategy="steam_another_goal",
        source="1xbet:autonomous_steam",
        expert_passed=False,
        expert_blocks=[],
        market_pressure_pp=4.0,
        market_level="AUTONOMOUS_STEAM",
        market_override=True,
        value_override=False,
        market_age_seconds=2.0,
        data_quality=0.8,
        rating=88.0,
        expected_roi=0.0,
        value_edge_pp=0.0,
        eligible=True,
        reason_tags=["autonomous_steam"],
    )
    return RouterDecision("BET", 59, (1, 0), candidate, [], [], "STEAM")


def test_successful_brain_delivery_is_persisted_as_tracking_pending(tmp_path, monkeypatch):
    path = tmp_path / "multi.json"
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(path))
    monkeypatch.setattr(tracking, "_ORIGINAL_MARK_BRAIN_SENT", lambda entry, sent: None)

    entry = _brain_entry()
    tracking._mark_brain_sent_with_journal(entry, 1)

    rows = load_signal_journal(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "pending"
    assert row["tracking_only"] is True
    assert row["bank_tracking"] is False
    assert row["entry_key"] == "brain:fs1:another_goal"
    assert row["market_family"] == "match_total"
    assert row["market_key"] == "match_total:1.5"
    assert row["odd"] is None
    assert row["telegram_sent"] is True
    assert row["result_card_eligible"] is True


def test_tracking_result_never_invents_profit(monkeypatch):
    row = {
        "tracking_only": True,
        "odd": None,
        "result": "pending",
    }

    def fake_finish(target, *, result, minute, score, reason):
        target.update(
            {
                "result": result,
                "profit_units": -1.0,
                "settled_minute": minute,
                "settled_score": list(score),
                "settlement_source": reason,
                "virtual_stake_rub": 2000.0,
                "virtual_profit_rub": -2000.0,
            }
        )

    monkeypatch.setattr(tracking, "_ORIGINAL_FINISH_ROW", fake_finish)

    tracking._finish_row_without_fake_profit(
        row,
        result="won",
        minute=67,
        score=[2, 0],
        reason="flashscore_var_confirmed_live_score",
    )

    assert row["result"] == "won"
    assert row["profit_units"] is None
    assert "virtual_stake_rub" not in row
    assert "virtual_profit_rub" not in row


def test_tracking_brain_pending_does_not_block_separate_steam(tmp_path):
    path = tmp_path / "multi.json"
    save_signal_journal(
        path,
        [
            {
                **_brain_entry(),
                "mode": "active",
                "head": "multi",
                "entry_key": "brain:fs1:another_goal",
                "market_key": "match_total:1.5",
                "market_family": "match_total",
                "tracking_only": True,
                "result": "pending",
            }
        ],
    )
    record = {
        "match": {
            "flashscore_event_id": "fs1",
            "home": "Home",
            "away": "Away",
            "league": "League",
            "minute": 59,
            "home_score": 1,
            "away_score": 0,
        }
    }

    created = tracking._record_multi_entry_independent(
        record,
        _steam_decision(),
        {},
        path,
        data_quality=0.8,
    )

    assert created is not None
    rows = load_signal_journal(path)
    assert len(rows) == 2
    assert any(str(row.get("source") or "").startswith("1xbet:autonomous_steam") for row in rows)


def test_tracking_card_has_no_virtual_bank_strip(monkeypatch):
    monkeypatch.setattr(tracking, "_ORIGINAL_APPEND_BANK_STRIP", lambda png, entry, result=False: b"wrapped")
    assert tracking._append_bank_strip_without_tracking(b"png", {"tracking_only": True}, result=True) == b"png"
    assert tracking._append_bank_strip_without_tracking(b"png", {"tracking_only": False}, result=True) == b"wrapped"


def test_tracking_result_caption_never_shows_fake_zero_odd(monkeypatch):
    monkeypatch.setattr(tracking, "_ORIGINAL_RESULT_CAPTION", lambda row: "legacy")
    row = {
        "tracking_only": True,
        "result": "lost",
        "home": "Home",
        "away": "Away",
        "market": "ТБ 1.5",
        "odd": None,
        "settled_minute": 90,
        "settled_score": [1, 0],
    }
    text = tracking._result_caption_without_fake_odd(row)
    assert "НЕ ЗАШЁЛ" in text
    assert "0.00" not in text
