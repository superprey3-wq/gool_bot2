from __future__ import annotations

from pathlib import Path

import gool_bot2.brain_card_restore as card_restore
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
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(tmp_path / "public-journal.json"))
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    monkeypatch.setattr("gool_bot2.telegram.broadcast", lambda text: 1)
    monkeypatch.setattr("gool_bot2.multi_telegram.is_multi_telegram_active", lambda: True)
    monkeypatch.setattr(
        card_restore,
        "_fresh_flashscore_state",
        lambda match_id: {
            "event_id": match_id,
            "coarse_status": "2",
            "is_live": True,
            "is_finished": False,
            "home_score": 1,
            "away_score": 0,
        },
    )
    monkeypatch.setattr("gool_bot2.multi_steam_card.render_multi_signal_card", lambda *args, **kwargs: b"brain-png")
    monkeypatch.setattr("gool_bot2.telegram.broadcast_photo", lambda png: 1 if png == b"brain-png" else 0)

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


def test_brain_signal_without_price_uses_normal_png_card(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(tmp_path / "brain-card-signals.json"))
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    monkeypatch.setattr("gool_bot2.multi_telegram.is_multi_telegram_active", lambda: True)
    monkeypatch.setattr(
        card_restore,
        "_fresh_flashscore_state",
        lambda match_id: {"home_score": 1, "away_score": 0, "is_finished": False},
    )

    record = {"match": _match()}
    decision = mode.analyze_brain_primary_match(
        record["match"], None, {"another_goal": _experts(0.92)["another_goal"]}, data_quality=0.80
    )
    _, entry = mode.sync_brain_or_market_journal(
        record,
        decision,
        _experts(0.92),
        Path(tmp_path / "bet-journal.json"),
        data_quality=0.80,
    )
    assert entry is not None
    assert decision.winner is not None
    assert decision.winner.odd == 0.0

    rendered = []

    def fake_render(record_arg, decision_arg, *, entry=None, market_row=None):
        assert decision_arg.winner is not None
        assert decision_arg.winner.odd is None
        rendered.append(True)
        return b"normal-gool-png"

    monkeypatch.setattr("gool_bot2.multi_steam_card.render_multi_signal_card", fake_render)
    monkeypatch.setattr("gool_bot2.telegram.broadcast_photo", lambda png: 1 if png == b"normal-gool-png" else 0)
    monkeypatch.setattr("gool_bot2.telegram.broadcast", lambda text: (_ for _ in ()).throw(AssertionError("text fallback should not run")))

    assert card_restore.emit_brain_card_signal(record, decision, entry) == 1
    assert rendered == [True]
    assert decision.winner.odd == 0.0
    assert (tmp_path / "brain-card-signals.json").exists()


def test_brain_card_is_dropped_if_score_changed_before_send(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(tmp_path / "stale-score-signals.json"))
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    monkeypatch.setattr("gool_bot2.multi_telegram.is_multi_telegram_active", lambda: True)
    monkeypatch.setattr(
        card_restore,
        "_fresh_flashscore_state",
        lambda match_id: {"home_score": 1, "away_score": 1, "is_finished": False},
    )

    record = {"match": _match(hs=1, aws=0)}
    decision = mode.analyze_brain_primary_match(
        record["match"], None, {"another_goal": _experts(0.92)["another_goal"]}, data_quality=0.80
    )
    _, entry = mode.sync_brain_or_market_journal(
        record,
        decision,
        _experts(0.92),
        Path(tmp_path / "bet-journal.json"),
        data_quality=0.80,
    )
    assert entry is not None
    assert decision.winner is not None
    assert decision.winner.label == "ТБ 1.5"

    monkeypatch.setattr(
        "gool_bot2.multi_steam_card.render_multi_signal_card",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stale card must not render")),
    )
    monkeypatch.setattr(
        "gool_bot2.telegram.broadcast_photo",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stale card must not send")),
    )
    monkeypatch.setattr(
        "gool_bot2.telegram.broadcast",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stale fallback must not send")),
    )

    assert card_restore.emit_brain_card_signal(record, decision, entry) == 0
    assert not (tmp_path / "stale-score-signals.json").exists()

    refreshed = mode.analyze_brain_primary_match(
        _match(hs=1, aws=1), None, {"another_goal": _experts(0.92)["another_goal"]}, data_quality=0.80
    )
    assert refreshed.status == "BET"
    assert refreshed.winner is not None
    assert refreshed.winner.label == "ТБ 2.5"


def test_routing_experts_installs_brain_primary_runtime_and_card(monkeypatch):
    called = []
    monkeypatch.setattr(concept, "install_runtime_patches", lambda: called.append("runtime"))
    monkeypatch.setattr(concept, "install_brain_card_patch", lambda: called.append("card"))
    experts = _experts()

    routed = concept.routing_experts(_match(), experts)

    assert called == ["runtime", "card"]
    assert set(routed) == {"another_goal"}
