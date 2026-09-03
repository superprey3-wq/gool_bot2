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


def test_classic_total_has_no_asian_push_path():
    candidate = _candidate("match_total:3.5", "match_total", "ТБ 3.5", 1.60)

    assert settle_candidate(candidate, (2, 2)).result == "won"
    assert settle_candidate(candidate, (2, 1)).result == "lost"


def test_first_half_total_uses_half_time_score_not_final_score():
    candidate = _candidate("first_half_total:0.5", "first_half_total", "1Т ТБ 0.5", 1.90)

    assert settle_candidate(candidate, (2, 0), half_time_score=(0, 0)).result == "lost"
    assert settle_candidate(candidate, (2, 0), half_time_score=(1, 0)).result == "won"
    assert settle_candidate(candidate, (2, 0)).result == "void"


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
