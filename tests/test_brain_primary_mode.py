from __future__ import annotations

from pathlib import Path

import gool_bot2.brain_primary_mode as mode
import gool_bot2.multi_concept as concept


def _match(minute: int = 58, hs: int = 1, aws: int = 0) -> dict:
    return {
        "flashscore_event_id": "fs-brain-1",
        "home": "Home",
        "away": "Away",
        "league": "Test League",
        "minute": minute,
        "home_score": hs,
        "away_score": aws,
        "is_finished": False,
        "is_halftime": False,
    }


def _experts(probability: float = 0.72, state: str = "PASS") -> dict:
    return {
        "another_goal": {
            "probability": probability,
            "state": state,
            "passed": state == "PASS",
            "blocks": [],
        },
        "goal_before_ht": {
            "probability": probability,
            "state": state,
            "passed": state == "PASS",
            "blocks": [],
        },
    }


def test_main_brain_passes_without_any_xbet_market(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")
    decision = mode.analyze_brain_primary_match(
        _match(), None, {"another_goal": _experts()["another_goal"]}, data_quality=0.80
    )

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.source == "brain_primary:another_goal"
    assert decision.winner.label == "ТБ 1.5"
    assert decision.winner.odd == 0.0
    assert decision.winner.rating == 72.0
    assert "без обязательного кэфа" in decision.reason


def test_low_or_moving_xbet_odd_cannot_veto_main_brain(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")
    market = {
        "score_home": 1,
        "score_away": 0,
        "markets": {
            "match_total": [
                {"line": 1.5, "over": 1.20, "under": 4.80},
            ]
        },
        "pressure": {
            "match_total:1.5": {"prob_delta_pp": -12.0},
        },
    }
    decision = mode.analyze_brain_primary_match(
        _match(), market, {"another_goal": _experts()["another_goal"]}, data_quality=0.80
    )

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.odd == 1.20
    assert decision.winner.market_pressure_pp == -12.0
    assert decision.winner.market_override is False
    assert decision.winner.value_override is False


def test_brain_below_70_still_waits_even_without_xbet(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")
    decision = mode.analyze_brain_primary_match(
        _match(), None, {"another_goal": _experts(0.69)["another_goal"]}, data_quality=0.80
    )

    assert decision.status == "WAIT"
    assert decision.winner is None
    assert decision.rejected[0].rating == 69.0


def test_provider_quality_remains_a_football_data_guard(monkeypatch):
    monkeypatch.setenv("GOOL_MATCH_SUITABILITY_HARD_DATA_QUALITY", "0.45")
    decision = mode.analyze_brain_primary_match(
        _match(), None, {"another_goal": _experts(0.80)["another_goal"]}, data_quality=0.20
    )

    assert decision.status == "WAIT"
    assert "data_quality_too_low" in decision.rejected[0].blocks


def test_signal_only_dedupe_is_persisted_only_after_successful_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(tmp_path / "brain-signals.json"))
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    monkeypatch.setattr("gool_bot2.telegram.broadcast", lambda text: 1)
    monkeypatch.setattr("gool_bot2.multi_telegram.is_multi_telegram_active", lambda: True)

    record = {"match": _match()}
    decision = mode.analyze_brain_primary_match(
        record["match"], None, {"another_goal": _experts()["another_goal"]}, data_quality=0.80
    )
    _, entry = mode.sync_brain_or_market_journal(
        record,
        decision,
        _experts(),
        Path(tmp_path / "bet-journal.json"),
        data_quality=0.80,
    )
    assert entry is not None
    assert not (tmp_path / "bet-journal.json").exists()

    assert mode.emit_brain_or_market_signal(record, decision, entry) == 1

    _, repeated = mode.sync_brain_or_market_journal(
        record,
        decision,
        _experts(),
        Path(tmp_path / "bet-journal.json"),
        data_quality=0.80,
    )
    assert repeated is None


def test_routing_experts_installs_brain_primary_runtime(monkeypatch):
    called = []
    monkeypatch.setattr(concept, "install_runtime_patches", lambda: called.append(True))
    experts = _experts()

    routed = concept.routing_experts(_match(), experts)

    assert called == [True]
    assert set(routed) == {"another_goal"}
