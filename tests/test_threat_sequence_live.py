from __future__ import annotations

from types import SimpleNamespace

from gool_bot2.threat_sequence_live import apply_threat_modifier, evaluate_live_threat


def rec(**m):
    base={
        "minutes_in_epoch":6,
        "xg_total_last_5m":0.0,
        "sot_total_last_5m":0.0,
        "shots_total_last_5m":0.0,
        "big_total_last_5m":0.0,
        "xg_total_last_10m":0.0,
        "sot_total_last_10m":0.0,
        "shots_total_last_10m":0.0,
    }
    base.update(m)
    return {"live_momentum":base}


def decision(status="BET", rating=65.0):
    winner=None if status!="BET" else SimpleNamespace(
        rating=rating,reason_tags=[],blocks=[],eligible=True
    )
    return SimpleNamespace(status=status,winner=winner,rejected=[],reason="brain")


def test_wait_never_becomes_bet():
    d=decision("WAIT")
    out=apply_threat_modifier(d,rec(xg_total_last_5m=.6,sot_total_last_5m=3,shots_total_last_5m=6,big_total_last_5m=2))
    assert out.status=="WAIT"
    assert out.winner is None


def test_surge_strengthens_existing_bet():
    d=decision("BET",66)
    out=apply_threat_modifier(d,rec(
        xg_total_last_5m=.6,sot_total_last_5m=3,shots_total_last_5m=6,big_total_last_5m=2,
        xg_total_last_10m=.8,sot_total_last_10m=4,shots_total_last_10m=8,
    ))
    assert out.status=="BET"
    assert out.winner.rating>66
    assert "threat_surge" in out.winner.reason_tags


def test_quiet_weakens_but_keeps_clear_bet():
    d=decision("BET",70)
    out=apply_threat_modifier(d,rec())
    assert out.status=="BET"
    assert out.winner.rating==67.0
    assert "threat_quiet" in out.winner.reason_tags


def test_quiet_vetoes_only_borderline_bet():
    d=decision("BET",64)
    out=apply_threat_modifier(d,rec())
    assert out.status=="WAIT"
    assert out.winner is None
    assert out.rejected
    assert "threat_sequence_borderline_veto" in out.rejected[0].blocks


def test_warm_state_from_moderate_recent_pressure():
    row=evaluate_live_threat(rec(
        xg_total_last_5m=.18,sot_total_last_5m=1,shots_total_last_5m=3,big_total_last_5m=0,
        xg_total_last_10m=.30,sot_total_last_10m=2,shots_total_last_10m=5,
    ))
    assert row["available"] is True
    assert row["state"] in {"WARM","BUILDING"}
