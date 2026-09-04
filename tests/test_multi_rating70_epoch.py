from __future__ import annotations

import importlib.util
from pathlib import Path

from gool_bot2.multi_router import MarketCandidate, RouterDecision
from gool_bot2.multi_runtime import _enforce_min_rating


_SPEC = importlib.util.spec_from_file_location(
    "monkey_start",
    Path(__file__).resolve().parents[1] / "monkey_start.py",
)
assert _SPEC is not None and _SPEC.loader is not None
monkey_start = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(monkey_start)


def _decision(rating: float) -> RouterDecision:
    winner = MarketCandidate(
        key="home_total:1.5",
        family="team_total",
        strategy="home_goal",
        label="ИТБ1 1.5",
        odd=2.10,
        model_probability=0.61,
        rating=rating,
        eligible=True,
    )
    return RouterDecision(
        status="BET",
        minute=57,
        score=(1, 0),
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="test",
    )


def test_final_multi_rating_floor_rejects_69_9(monkeypatch):
    monkeypatch.delenv("GOOL_MULTI_MIN_RATING", raising=False)
    decision = _decision(69.9)

    result = _enforce_min_rating(decision)

    assert result.status == "WAIT"
    assert result.winner is None
    assert result.alternatives == []
    assert result.rejected
    assert "production_rating_floor" in result.rejected[0].reason_tags
    assert "70/100" in result.reason


def test_final_multi_rating_floor_accepts_70(monkeypatch):
    monkeypatch.delenv("GOOL_MULTI_MIN_RATING", raising=False)
    decision = _decision(70.0)

    result = _enforce_min_rating(decision)

    assert result.status == "BET"
    assert result.winner is not None
    assert result.winner.rating == 70.0


def test_rating70_epoch_reset_clears_multi_tracking_only_once(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    live = runtime / "live"
    live.mkdir(parents=True)
    journal = live / "gool_multi_journal.json"
    bank = live / "gool_multi_bank_state.json"
    journal.write_text('[{"result":"lost"}]', "utf-8")
    bank.write_text('{"current_bank_rub":88824}', "utf-8")

    monkeypatch.delenv("GOOL_MULTI_JOURNAL_PATH", raising=False)
    monkeypatch.delenv("GOOL_MULTI_BANK_STATE_PATH", raising=False)
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")

    monkey_start._reset_multi_tracking_once(runtime)

    assert not journal.exists()
    assert not bank.exists()
    marker = live / f".gool_multi_reset_{monkey_start.MULTI_RESET_ID}"
    assert marker.exists()

    # A normal restart after the cutover must keep the new epoch's statistics.
    journal.write_text("[]", "utf-8")
    bank.write_text('{"current_bank_rub":100000}', "utf-8")

    monkey_start._reset_multi_tracking_once(runtime)

    assert journal.exists()
    assert bank.exists()
