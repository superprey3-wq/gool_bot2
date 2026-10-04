from gool_bot2.xbet_multisport_steam import SPORTS, detect_live_segment_stats


def _row(ts, clock, total, line, over=1.82, under=1.98, probability=0.52, sport="basketball"):
    return {
        "ts": float(ts),
        "clock_seconds": float(clock),
        "score": [total // 2, total - total // 2],
        "line": float(line),
        "over": float(over),
        "under": float(under),
        "probability": float(probability),
        "scope": "QUARTER_2" if sport == "basketball" else "PERIOD_2",
        "market_family": "match_total",
        "league": "NBA" if sport == "basketball" else "KHL",
    }


def test_basket_live_brain_uses_quarter_pace_not_odds_movement(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_ELAPSED_SECONDS", "30")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_CLOCK_DELTA_SECONDS", "20")
    monkeypatch.setenv("GOOL_BASKETBALL_LIVE_SEGMENT_MIN_STAT_EDGE", "2")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MARKET_PRIOR_WEIGHT", "0.20")
    # NBA elapsed clock increasing: 2:00 -> 4:00, score pace projects well over 54.5.
    rows = [
        _row(100, 120, 12, 54.5, probability=0.50),
        _row(140, 160, 17, 54.5, probability=0.50),
        _row(180, 200, 22, 54.5, probability=0.50),
        _row(220, 240, 28, 54.5, probability=0.50),
    ]
    signal = detect_live_segment_stats(rows, SPORTS["basketball"], now=220, score_changed_at=None)
    assert signal is not None
    assert signal["brain_mode"] == "segment_stats"
    assert signal["direction"] == "over"
    assert signal["projected_total"] > 54.5


def test_hockey_live_brain_can_find_under_from_slow_period(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_ELAPSED_SECONDS", "60")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_CLOCK_DELTA_SECONDS", "20")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_SEGMENT_MIN_STAT_EDGE", "0.2")
    rows = [
        _row(100, 300, 0, 2.5, over=1.95, under=1.80, probability=0.48, sport="hockey"),
        _row(160, 360, 0, 2.5, over=1.95, under=1.80, probability=0.48, sport="hockey"),
        _row(220, 420, 0, 2.5, over=1.95, under=1.80, probability=0.48, sport="hockey"),
        _row(280, 480, 0, 2.5, over=1.95, under=1.80, probability=0.48, sport="hockey"),
    ]
    signal = detect_live_segment_stats(rows, SPORTS["hockey"], now=280, score_changed_at=None)
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["projected_total"] < 2.5


def test_live_stats_brain_vetoes_market_strongly_against_stat_side(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_ELAPSED_SECONDS", "30")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_CLOCK_DELTA_SECONDS", "20")
    monkeypatch.setenv("GOOL_BASKETBALL_LIVE_SEGMENT_MIN_STAT_EDGE", "2")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MARKET_OPPOSITION_FLOOR", "0.42")
    rows = [
        _row(100, 120, 12, 54.5, over=2.60, under=1.45, probability=0.36),
        _row(140, 160, 17, 54.5, over=2.60, under=1.45, probability=0.36),
        _row(180, 200, 22, 54.5, over=2.60, under=1.45, probability=0.36),
        _row(220, 240, 28, 54.5, over=2.60, under=1.45, probability=0.36),
    ]
    assert detect_live_segment_stats(rows, SPORTS["basketball"], now=220, score_changed_at=None) is None
