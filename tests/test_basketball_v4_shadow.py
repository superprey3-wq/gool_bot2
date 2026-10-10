from __future__ import annotations

from gool_bot2 import basketball_v4_shadow
from gool_bot2.xbet_multisport_steam import basketball_v3_live_context_gate


def _brain():
    return {
        "scope": "QUARTER_2",
        "brain_state": "PASS",
        "segment_score_verified": True,
        "break_transition": False,
    }


def _lane():
    return {
        "scope": "QUARTER_2",
        "market_family": "match_total",
        "line": 42.5,
        "clock_seconds": 320.0,
        "over": 1.92,
        "under": 1.86,
    }


def test_v4_shadow_compares_dynamic_v2_with_v3_without_publishing(monkeypatch):
    calls = []

    def stub(brain, lane):
        calls.append((brain, lane))
        return {
            "direction": "over",
            "model_probability": .63,
            "market_probability": .52,
            "projected_total": 45.1,
            "possession_source": "flashscore_current_quarter",
            "projection_clock_source": "1xbet_segment_clock",
        }

    monkeypatch.setattr(basketball_v4_shadow, "basketball_v2_live_signal", stub)
    old = {"direction": "under", "model_probability": 0.77778}
    result = basketball_v4_shadow.assess_basketball_v4_live(_brain(), _lane(), old)
    assert len(calls) == 1
    assert result["state"] == "PASS"
    assert result["direction"] == "over"
    assert result["agreement_with_v3"] is False
    assert result["historical_probability_proxy"] == .77778
    assert result["model_probability_uncalibrated"] == .63
    # The challenger returns observations, not a send/selection instruction.
    assert "telegram_sent" not in result


def test_v4_shadow_refuses_unverified_quarter_and_break(monkeypatch):
    def impossible(*args):
        raise AssertionError("should not price on stale/invalid segment")
    monkeypatch.setattr(basketball_v4_shadow, "basketball_v2_live_signal", impossible)
    for brain, lane, reason in (
        ({**_brain(), "scope": "QUARTER_3"}, _lane(), "not_verified_current_quarter"),
        ({**_brain(), "segment_score_verified": False}, _lane(), "score_or_transition_unverified"),
        ({**_brain(), "break_transition": True}, _lane(), "score_or_transition_unverified"),
        ({**_brain(), "brain_state": "WAIT"}, _lane(), "dynamic_brain_not_ready"),
        (_brain(), {**_lane(), "clock_seconds": None}, "quote_or_clock_unavailable"),
    ):
        result = basketball_v4_shadow.assess_basketball_v4_live(brain, lane)
        assert result["state"] == "WAIT"
        assert result["reason"] == reason


def test_v4_shadow_waits_if_dynamic_model_not_qualified(monkeypatch):
    monkeypatch.setattr(basketball_v4_shadow, "basketball_v2_live_signal", lambda *args: None)
    result = basketball_v4_shadow.assess_basketball_v4_live(_brain(), _lane())
    assert result["state"] == "WAIT"
    assert result["reason"] == "dynamic_v2_gate_wait"


def test_wnba_live_quarter_is_ten_minutes_not_nba_twelve():
    # At 500s, a WNBA quarter has <120s remaining and must not be priced
    # with an extra 2 minutes accidentally inherited from substring 'nba'.
    old = {"line": 39.5, "direction": "over", "projected_total": 42.0}
    snapshot = {"scope": "QUARTER_2", "clock_seconds": 500, "score": [18, 12]}
    assert basketball_v3_live_context_gate(old, snapshot, "WNBA: Regular Season") is None
    assert basketball_v3_live_context_gate(old, snapshot, "NBA: Regular Season") is not None
