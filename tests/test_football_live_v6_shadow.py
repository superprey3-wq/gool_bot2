from __future__ import annotations

from dataclasses import dataclass

from gool_bot2 import football_live_v6_shadow as v6


@dataclass
class FakeDecision:
    market: str = "ANOTHER_GOAL"
    decision: str = "BET"
    probability: float = 0.72
    confidence: float = 0.82
    score: float = 0.59

    def to_dict(self):
        return {
            "market": self.market,
            "decision": self.decision,
            "probability": self.probability,
            "confidence": self.confidence,
        }


def _record(minute=60, halftime=False):
    return {
        "match": {
            "flashscore_event_id": "fixture",
            "minute": minute,
            "home_score": 1,
            "away_score": 1,
            "is_halftime": halftime,
            "is_finished": False,
        }
    }


def _decision(strategy="another_goal"):
    return {"active": True, "strategy": strategy, "status": "BET", "probability": 0.79}


def test_v6_live_observes_existing_decisions_without_changing_production(monkeypatch):
    calls = []
    def hazard(row, *, period):
        calls.append(period)
        return {"available": True, "passed": False, "probability": .46}
    monkeypatch.setattr(v6, "estimate_live_goal_hazard", hazard)
    monkeypatch.setattr(v6, "evaluate_live_goals", lambda _: [FakeDecision()])
    production = _decision()
    original = dict(production)
    result = v6.football_live_v6_shadow(_record(), production)
    assert production == original
    assert result["status"] == "COMPARED"
    assert result["brain_v3_bet"] is True
    assert result["live_v4_bet"] is True
    assert result["hazard_passed"] is False
    assert result["agreement_diagnostic_only"] is False
    assert result["correlated_sources_not_independent_votes"] is True
    assert result["expected_value_calculated"] is False
    assert result["quoted_odds_used"] is False
    assert calls == ["FT"]


def test_v6_live_first_half_scope_and_same_market(monkeypatch):
    monkeypatch.setattr(v6, "estimate_live_goal_hazard", lambda *a, **kw:
                        {"available": True, "passed": True, "probability": .58})
    monkeypatch.setattr(v6, "evaluate_live_goals", lambda _: [
        FakeDecision(market="GOAL_BEFORE_HT", probability=.55),
        FakeDecision(market="ANOTHER_GOAL", probability=.70),
    ])
    result = v6.football_live_v6_shadow(_record(33), _decision("goal_before_ht"))
    assert result["status"] == "COMPARED"
    assert result["live_v4"]["market"] == "GOAL_BEFORE_HT"
    assert result["hazard"]["probability"] == .58


def test_v6_live_waits_on_stoppages_and_outside_target(monkeypatch):
    monkeypatch.setattr(v6, "evaluate_live_goals", lambda _: (_ for _ in ()).throw(
        AssertionError("must not call provider in break")))
    for row, decision in (
        (_record(45, halftime=True), _decision("goal_before_ht")),
        (_record(45), _decision("another_goal")),
        (_record(48), _decision("goal_before_ht")),
        (_record(96), _decision("another_goal")),
    ):
        result = v6.football_live_v6_shadow(row, decision)
        assert result["status"] == "WAIT"


def test_v6_live_missing_xg_never_creates_public_bet(monkeypatch):
    monkeypatch.setattr(v6, "estimate_live_goal_hazard", lambda *a, **kw:
                        {"available": False, "passed": False, "probability": None})
    monkeypatch.setattr(v6, "evaluate_live_goals", lambda _: [])
    result = v6.football_live_v6_shadow(_record(), _decision())
    assert result["status"] == "WAIT"
    assert "telegram_sent" not in result
    assert result["agreement_diagnostic_only"] is False


def test_v6_live_hook_is_non_veto_audit_only():
    import inspect
    from gool_bot2.production_signal_throughput import _production_brain_audit
    source = inspect.getsource(_production_brain_audit)
    assert "football_live_v6_shadow(record, out)" in source
    assert 'record["brain_v3_decision"] = out' in source
    assert "out[\"football_live_v6_shadow\"] = v6_shadow" in source


def test_live_v6_is_persisted_in_match_journal_and_dayreport():
    import inspect
    from gool_bot2.multi_journal import entry_from_decision
    from gool_bot2.dayreport_brain_audit import _model_evidence

    assert '"football_live_v6_shadow"' in inspect.getsource(entry_from_decision)
    row = {
        "brain_mode": "brain_v3",
        "football_live_v6_shadow": {
            "status": "COMPARED",
            "brain_v3_bet": True,
            "live_v4_bet": False,
            "hazard_passed": False,
        },
    }
    evidence = " ".join(_model_evidence(row))
    assert "Football LIVE V6 SHADOW" in evidence
    assert "V3=True" in evidence
    assert "V4=False" in evidence
    assert "не калиброваны" in evidence
