from gool_bot2.xbet_multisport_steam import (
    MultiSportSteamWorker,
    SPORTS,
    _infer_flashscore_scope,
    detect_live_segment_stats,
)


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



def test_hockey_shots_and_special_teams_feed_projection(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_SEGMENT_ELAPSED_SECONDS", "30")
    monkeypatch.setenv("GOOL_MULTISPORT_LIVE_MIN_CLOCK_DELTA_SECONDS", "20")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_SEGMENT_MIN_STAT_EDGE", "0.2")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_SHOTS_PROJECTION_WEIGHT", "0.60")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_GOAL_PER_SHOT", "0.08")
    rows = [
        {**_row(100, 300, 0, 2.5, over=1.82, under=1.98, probability=0.52, sport="hockey"),
         "live_game_stats": {"shots_on_goal": [5, 4], "penalties_2m": [0, 0], "powerplay_goals": [0, 0]}},
        {**_row(160, 360, 0, 2.5, over=1.82, under=1.98, probability=0.52, sport="hockey"),
         "live_game_stats": {"shots_on_goal": [8, 7], "penalties_2m": [1, 0], "powerplay_goals": [0, 0]}},
        {**_row(220, 420, 1, 2.5, over=1.82, under=1.98, probability=0.52, sport="hockey"),
         "live_game_stats": {"shots_on_goal": [11, 10], "penalties_2m": [1, 1], "powerplay_goals": [1, 0]}},
        {**_row(280, 480, 1, 2.5, over=1.82, under=1.98, probability=0.52, sport="hockey"),
         "live_game_stats": {"shots_on_goal": [14, 13], "penalties_2m": [2, 1], "powerplay_goals": [1, 0]}},
    ]
    signal = detect_live_segment_stats(rows, SPORTS["hockey"], now=280, score_changed_at=None)
    assert signal is not None
    assert signal["direction"] == "over"
    assert signal["hockey_pressure"]["shots_on_goal"] == [14, 13]
    assert signal["hockey_pressure"]["recent_shots_per_min"] > 0
    assert signal["hockey_pressure"]["special_teams_boost"] > 0

def test_flashscore_scope_is_inferred_without_1xbet_period():
    assert _infer_flashscore_scope(
        {"score_parts": [[20, 18], [14, 12]]},
        SPORTS["basketball"],
        ["FULL_MATCH", "QUARTER_1", "QUARTER_2"],
    ) == "QUARTER_2"
    assert _infer_flashscore_scope(
        {"score_parts": [[0, 1], [1, 0]]},
        SPORTS["hockey"],
        ["FULL_MATCH", "PERIOD_1", "PERIOD_2"],
    ) == "PERIOD_2"


def test_collect_once_runs_flashscore_brain_before_bookmaker_pricing(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_ENABLED", "1")
    worker = MultiSportSteamWorker(tmp_path)
    order = []

    def fake_today(cfg):
        if cfg.key != "hockey":
            return []
        return [
            {
                "flashscore_event_id": "LIVEH001",
                "home": "Live Home",
                "away": "Live Away",
                "league": "League",
                "score": [1, 1],
                "score_parts": [[0, 1], [1, 0]],
                "status_code": "46",
                "coarse_status": "2",
                "start_ts": 1,
            },
            {
                "flashscore_event_id": "PREH0001",
                "home": "Pre Home",
                "away": "Pre Away",
                "league": "League",
                "score": [0, 0],
                "score_parts": [],
                "status_code": "",
                "coarse_status": "1",
                "start_ts": __import__("time").time() + 3600,
            },
        ]

    def fake_live_analysis(rows, cfg):
        if rows:
            order.append("live_brain")
            return [{
                "flashscore_event_id": "LIVEH001",
                "home": "Live Home",
                "away": "Live Away",
                "score": [1, 1],
                "period": "2-й период",
                "brain_state": "PASS",
                "brain_score": 82,
                "brain_reason": "pressure",
                "live_game_stats": {"segment_stats": {"shots_on_goal": [12, 10]}},
            }]
        return []

    def fake_pre_shortlist(rows, cfg):
        if rows:
            order.append("prematch_brain")
            return [{**rows[0], "prematch_brain": {"state": "PASS", "score": 80}}]
        return []

    def fake_live_index(cfg):
        order.append("live_xbet")
        return []

    def fake_pre_index(cfg):
        order.append("prematch_xbet")
        return []

    monkeypatch.setattr(worker, "_flashscore_today", fake_today)
    monkeypatch.setattr(worker, "_flashscore_live_analysis", fake_live_analysis)
    monkeypatch.setattr(worker, "_flashscore_prematch_shortlist", fake_pre_shortlist)
    monkeypatch.setattr(worker, "_xbet_index", fake_live_index)
    monkeypatch.setattr(worker, "_xbet_prematch_index", fake_pre_index)

    state = worker.collect_once()

    assert order.index("live_brain") < order.index("live_xbet")
    assert order.index("prematch_brain") < order.index("prematch_xbet")
    hockey = state["sports"]["hockey"]
    assert hockey["flashscore_live"] == 1
    assert hockey["live_brain_candidates"] == 1
    assert hockey["mapped"] == 0
    assert hockey["flashscore_analysis_matches"][0]["brain_score"] == 82
    assert hockey["prematch_brain_candidates"] == 1

