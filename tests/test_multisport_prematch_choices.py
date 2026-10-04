from gool_bot2.xbet_multisport_steam import SPORTS, detect_prematch_choice, settle_multisport_pick


def test_settle_handicap_and_moneyline():
    assert settle_multisport_pick({"market_family": "handicap", "selection_side": "home", "line": -1.5}, 5, 3) == "won"
    assert settle_multisport_pick({"market_family": "handicap", "selection_side": "away", "line": 1.5}, 5, 3) == "lost"
    assert settle_multisport_pick({"market_family": "moneyline", "selection_side": "away"}, 2, 4) == "won"


def test_prematch_choice_detector_requires_sustained_probability_support(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_CHOICE_MIN_FAIR_EDGE_PP", "2.0")
    rows = [
        {"ts": 100.0, "probability": 0.55, "odd": 1.85, "line": -1.5, "selection": "Ф1 -1.5", "selection_side": "home"},
        {"ts": 102.0, "probability": 0.565, "odd": 1.80, "line": -1.5, "selection": "Ф1 -1.5", "selection_side": "home"},
        {"ts": 104.0, "probability": 0.585, "odd": 1.74, "line": -1.5, "selection": "Ф1 -1.5", "selection_side": "home"},
    ]
    signal = detect_prematch_choice(rows, SPORTS["hockey"], now=104.0)
    assert signal is not None
    assert signal["selection"] == "Ф1 -1.5"
    assert signal["direction"] == "home"
    assert signal["probability_delta_pp"] >= 3.0



def test_choice_signal_start_uses_odd_field_not_team_name():
    cfg = SPORTS["hockey"]
    rows = [
        {"ts": 100.0, "probability": 0.55, "odd": 1.90, "line": 0.0, "selection": "П1", "selection_side": "home", "home": "Calgary Flames"},
        {"ts": 102.0, "probability": 0.565, "odd": 1.84, "line": 0.0, "selection": "П1", "selection_side": "home", "home": "Calgary Flames"},
        {"ts": 104.0, "probability": 0.59, "odd": 1.78, "line": 0.0, "selection": "П1", "selection_side": "home", "home": "Calgary Flames"},
    ]
    signal = detect_prematch_choice(rows, cfg, now=104.0)
    assert signal is not None
    assert signal["start"]["odd"] == 1.90
    assert signal["start"]["home"] == "Calgary Flames"
