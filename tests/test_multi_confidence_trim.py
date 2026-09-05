from __future__ import annotations

from gool_bot2 import multi_confidence_gate
from gool_bot2.multi_confidence_gate import enforce_confidence_gate
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _candidate(*, rating: float, football: float) -> tuple[RouterDecision, dict]:
    winner = MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        strategy="another_goal",
        label="Ещё гол",
        odd=1.70,
        model_probability=football,
        rating=rating,
        eligible=True,
        market_override=True,
        market_level="STRONG_STEAM",
        market_pressure_pp=8.0,
    )
    decision = RouterDecision(
        status="BET",
        minute=62,
        score=(1, 0),
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="test",
    )
    experts = {"another_goal": {"probability": football, "passed": True}}
    return decision, experts


def test_single_market_steam_cannot_rescue_marginal_rating(monkeypatch) -> None:
    monkeypatch.setattr(multi_confidence_gate, "_breadth_count", lambda *args, **kwargs: 0)
    decision, experts = _candidate(rating=76.0, football=0.76)

    result = enforce_confidence_gate(decision, experts, market_row={"pressure": {}})

    assert result.status == "WAIT"
    assert result.winner is None
    assert any("confidence_rating_below:76.0<77.0" in block for block in result.rejected[0].blocks)


def test_related_market_confirmation_can_relax_rating_only(monkeypatch) -> None:
    monkeypatch.setattr(multi_confidence_gate, "_breadth_count", lambda *args, **kwargs: 1)
    decision, experts = _candidate(rating=76.0, football=0.76)

    result = enforce_confidence_gate(decision, experts, market_row={"pressure": {}})

    assert result.status == "BET"
    assert result.winner is not None
    assert "multi_market_confirmation" in result.winner.reason_tags


def test_breadth_cannot_rescue_weak_football(monkeypatch) -> None:
    monkeypatch.setattr(multi_confidence_gate, "_breadth_count", lambda *args, **kwargs: 2)
    decision, experts = _candidate(rating=79.0, football=0.74)

    result = enforce_confidence_gate(decision, experts, market_row={"pressure": {}})

    assert result.status == "WAIT"
    assert result.winner is None
    assert any("confidence_football_below:74.0<75.0" in block for block in result.rejected[0].blocks)


def test_strong_football_and_rating_still_pass_without_breadth(monkeypatch) -> None:
    monkeypatch.setattr(multi_confidence_gate, "_breadth_count", lambda *args, **kwargs: 0)
    decision, experts = _candidate(rating=79.0, football=0.77)

    result = enforce_confidence_gate(decision, experts, market_row={"pressure": {}})

    assert result.status == "BET"
    assert result.winner is not None
