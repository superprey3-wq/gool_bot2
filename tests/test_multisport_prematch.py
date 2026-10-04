from gool_bot2.multisport_prematch import detect_prematch_signal
from gool_bot2.xbet_multisport_steam import SPORTS


def test_hockey_prematch_over_move_emits_signal(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", "30")
    cfg = SPORTS["hockey"]
    rows = [
        {"ts": 0, "metric": 5.50, "line": 5.5, "over": 1.92, "under": 1.92, "probability": .50},
        {"ts": 60, "metric": 5.94, "line": 6.0, "over": 1.78, "under": 2.05, "probability": .535},
    ]
    signal = detect_prematch_signal(rows, cfg, now=60)
    assert signal is not None
    assert signal["phase"] == "PREMATCH"
    assert signal["direction"] == "over"
    assert signal["line"] == 6.0
    assert signal["opening_line"] == 5.5


def test_basketball_prematch_under_move_emits_signal(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", "30")
    cfg = SPORTS["basketball"]
    rows = [
        {"ts": 0, "metric": 220.8, "line": 220.5, "over": 1.84, "under": 1.96, "probability": .5075},
        {"ts": 60, "metric": 217.7, "line": 218.5, "over": 2.05, "under": 1.76, "probability": .48},
    ]
    signal = detect_prematch_signal(rows, cfg, now=60)
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["odd"] == 1.76
    assert signal["probability_delta_pp"] > 0
