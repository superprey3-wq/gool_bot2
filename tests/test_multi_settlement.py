from __future__ import annotations

from gool_bot2.multi_router import MarketCandidate, RouterDecision
from gool_bot2.multi_settlement import settle_candidate, settle_decision


def _candidate(key: str, family: str, label: str, odd: float) -> MarketCandidate:
    return MarketCandidate(
        key=key,
        family=family,
        label=label,
        odd=odd,
        model_probability=0.6,
        correlation_key=key,
    )


def test_asian_total_push_is_not_counted_as_loss():
    candidate = _candidate("match_total:4", "asian_match_total", "ТБ 4", 1.60)

    settled = settle_candidate(candidate, (2, 2))

    assert settled.result == "push"
    assert settled.profit_units == 0.0


def test_shadow_settlement_can_detect_better_alternative():
    winner = _candidate("match_total:4.5", "match_total", "ТБ 4.5", 1.90)
    safer = _candidate("match_total:3.5", "match_total", "ТБ 3.5", 1.30)
    decision = RouterDecision(
        status="BET",
        minute=54,
        score=(1, 2),
        winner=winner,
        alternatives=[safer],
        rejected=[],
        reason="test",
    )

    settled = settle_decision(decision, (2, 2))

    assert settled["winner"]["result"] == "lost"
    assert settled["alternatives"][0]["result"] == "won"
    assert settled["router_picked_best_realized"] is False
    assert settled["best_realized"]["label"] == "ТБ 3.5"


def test_btts_settlement_uses_final_score():
    candidate = _candidate("btts_yes", "btts", "ОЗ — Да", 1.88)

    assert settle_candidate(candidate, (2, 1)).result == "won"
    assert settle_candidate(candidate, (2, 0)).result == "lost"
