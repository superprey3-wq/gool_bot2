import json
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.xbet_multisport_steam import (
    MultiSportSteamWorker,
    SPORTS,
    _infer_flashscore_scope,
    _flashscore_scoped_scores,
    detect_live_segment_stats,
    parse_flashscore_events,
    price_flashscore_live_candidate,
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

def test_hockey_flashscore_brain_uses_shots_before_xbet(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(
        worker,
        "_flashscore_live_stats",
        lambda fs, cfg, current_period="": {
            "source": "flashscore",
            "scope": "PERIOD_2",
            "available": True,
            "current_segment_available": True,
            "segment_stats": {
                "shots_on_goal": [4.0, 3.0],
                "blocked_shots": [2.0, 1.0],
                "penalties": [1.0, 1.0],
                "power_play_goals": [0.0, 0.0],
            },
        },
    )
    fs = {
        "flashscore_event_id": "HFSHOT01",
        "home": "Home",
        "away": "Away",
        "league": "AHL",
        "score": [1, 2],
        "score_parts": [[0, 1], [1, 1]],
        "status_code": "46",
        "period_start_ts": __import__("time").time() - 420,
    }

    first = worker._flashscore_live_brain(fs, SPORTS["hockey"])
    # Fresh-segment policy requires both enough samples and a genuine analysis
    # window. Age the first sample, then collect two more current-period reads.
    key = "hockey:HFSHOT01:PERIOD_2"
    worker._fs_brain_history[key][0]["ts"] -= 61.0
    second = worker._flashscore_live_brain(fs, SPORTS["hockey"])
    third = worker._flashscore_live_brain(fs, SPORTS["hockey"])

    assert first["scope"] == "PERIOD_2"
    assert first["brain_state"] == "WAIT"
    assert first["history_points"] == 1
    assert second["brain_state"] == "WAIT"
    assert second["history_points"] == 2
    assert third["brain_state"] in {"PASS", "BORDERLINE"}
    assert third["history_points"] == 3
    assert third["brain_score"] >= 50
    assert "броски 7" in third["brain_reason"]


def test_basketball_numeric_38_is_match_minute_not_halftime_enum(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(
        worker,
        "_flashscore_live_stats",
        lambda fs, cfg, current_period="": {
            "source": "flashscore",
            "scope": "QUARTER_4",
            "available": True,
            "current_segment_available": True,
            "segment_stats": {"rebounds": [6.0, 5.0], "turnovers": [2.0, 1.0]},
            "segment_attempts": {},
        },
    )
    fs = {
        "flashscore_event_id": "BQ400001",
        "home": "Home",
        "away": "Away",
        "league": "Chile",
        "score": [70, 67],
        "score_parts": [[20, 18], [18, 17], [17, 18], [15, 14]],
        "status_code": "38",
        "period_start_ts": __import__("time").time() - 300,
    }

    result = worker._flashscore_live_brain(fs, SPORTS["basketball"])

    assert result["period"] == "4-я четверть"
    assert result["scope"] == "QUARTER_4"

def test_basketball_halftime_status_cannot_skip_from_q2_to_q4(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(
        worker,
        "_flashscore_live_stats",
        lambda fs, cfg, current_period="": {
            "source": "flashscore",
            "scope": "QUARTER_3",
            "available": True,
            "current_segment_available": True,
            "segment_stats": {"rebounds": [0.0, 0.0], "turnovers": [0.0, 0.0]},
            "segment_attempts": {},
        },
    )
    fs = {
        "flashscore_event_id": "BHALF001",
        "home": "Besiktas",
        "away": "Trabzonspor",
        "league": "TURKEY: Super Lig",
        "score": [46, 46],
        "score_parts": [[24, 17], [22, 29]],
        # This is the problematic break snapshot: interpreting AC=38 as a
        # normal match minute would jump directly to Q4.
        "status_code": "38",
        "period_start_ts": __import__("time").time() - 30,
    }

    result = worker._flashscore_live_brain(fs, SPORTS["basketball"])

    assert result["scope"] == "QUARTER_3"
    assert result["period"] == "3-я четверть"
    assert result["break_transition"] is True
    assert result["elapsed_seconds"] == 0.0
    assert result["brain_state"] == "WAIT"


def test_basketball_halftime_q3_label_without_q3_score_part_is_still_break(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(
        worker,
        "_flashscore_live_stats",
        lambda fs, cfg, current_period="": {
            "source": "flashscore",
            "scope": "QUARTER_3",
            "available": True,
            "current_segment_available": True,
            "segment_stats": {"rebounds": [7.0, 8.0], "turnovers": [2.0, 2.0]},
            "segment_attempts": {},
        },
    )
    fs = {
        "flashscore_event_id": "BHALF002",
        "home": "Home",
        "away": "Away",
        "league": "ISRAEL: League Cup",
        "score": [33, 40],
        "score_parts": [[19, 27], [14, 13]],
        # Current Flashscore basketball feed uses AC=24 for Q3. There is
        # still no Q3 score-part row and the full score is exactly Q1+Q2.
        "status_code": "24",
        "period_start_ts": __import__("time").time() - 600,
    }

    result = worker._flashscore_live_brain(fs, SPORTS["basketball"])

    assert result["scope"] == "QUARTER_3"
    assert result["break_transition"] is True
    assert result["elapsed_seconds"] == 0.0
    assert result["brain_state"] == "WAIT"


def test_flashscore_brain_candidate_is_priced_without_waiting_for_odds_history(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_MIN_ODD", "1.45")
    monkeypatch.setenv("GOOL_MULTISPORT_MAX_ODD", "3.25")
    monkeypatch.setenv("GOOL_BASKETBALL_LIVE_SEGMENT_MIN_STAT_EDGE", "2.5")
    brain = {
        "brain_state": "PASS",
        "brain_score": 82.0,
        "scope": "QUARTER_2",
        # This pre-price value must not drive the bet; final projection uses
        # the reliable 1xBet segment clock only after Brain selected the game.
        "projected_total": 7.8,
        "current_segment_total": 31,
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_possessions_per_min": 2.4,
        "recent_score_rate": 5.0,
        "live_game_stats": {
            "current_segment_available": True,
            "stats_mode": "cumulative_through_current_segment",
            "segment_stats": {"rebounds": [8, 7]},
            "segment_attempts": {},
        },
        "brain_reason": "Flashscore pace and shooting pressure",
    }
    lane = {
        "scope": "QUARTER_2",
        "market_family": "match_total",
        "line": 53.5,
        "over": 1.88,
        "under": 1.88,
        "probability": 0.50,
        "clock_seconds": 310,
        "score": [15, 16],
        "league": "USA: NBA",
        "live_game_stats": {"segment_stats": {"rebounds": [8, 7]}},
    }

    signal = price_flashscore_live_candidate(brain, lane, SPORTS["basketball"])

    assert signal is not None
    assert signal["brain_mode"] == "basketball_live_v2"
    assert signal["direction"] == "over"
    assert signal["line"] == 53.5
    assert signal["odd"] == 1.88
    assert signal["projected_total"] > 53.5
    assert signal["projected_total"] != 7.8
    assert signal["elapsed_seconds"] == 310.0
    assert signal["projection_clock_source"] == "1xbet_after_flashscore_brain"
    assert signal["flashscore_brain_score"] == 82.0


def test_flashscore_brain_only_prices_current_segment():
    brain = {
        "brain_state": "PASS",
        "brain_score": 90.0,
        "scope": "PERIOD_2",
        "projected_total": 2.4,
    }
    wrong_period = {
        "scope": "PERIOD_3",
        "market_family": "match_total",
        "line": 1.5,
        "over": 1.85,
        "under": 1.95,
        "probability": 0.51,
    }
    full_match = {
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "line": 6.5,
        "over": 1.85,
        "under": 1.95,
        "probability": 0.51,
    }

    assert price_flashscore_live_candidate(brain, wrong_period, SPORTS["hockey"]) is None
    assert price_flashscore_live_candidate(brain, full_match, SPORTS["hockey"]) is None


def test_flashscore_parser_keeps_period_clock_for_stat_first_brain():
    body = (
        "ZA÷League~"
        "AA÷ABCDEFGH¬AE÷Home¬AF÷Away¬AB÷2¬AC÷38¬AD÷1000¬AO÷1450"
        "¬AG÷44¬AH÷32¬BA÷21¬BB÷12¬BC÷23¬BD÷20"
    )

    rows = parse_flashscore_events(body)

    assert len(rows) == 1
    assert rows[0]["match_start_ts"] == 1000
    assert rows[0]["period_start_ts"] == 1450
    assert rows[0]["score_parts"] == [[21, 12], [23, 20]]

def test_collect_once_does_not_query_xbet_when_flashscore_brain_has_no_candidates(tmp_path: Path, monkeypatch):
    from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker

    worker = MultiSportSteamWorker(tmp_path)
    empty_prepared = {
        "fs_today": [],
        "fs_live": [],
        "live_analysis": [],
        "live_candidates": [],
        "prematch_candidates": [],
    }
    monkeypatch.setattr(worker, "_prepare_flashscore_sport", lambda _cfg: dict(empty_prepared))
    monkeypatch.setattr(
        worker,
        "_xbet_index",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("LIVE 1xBet must not run before Brain candidate")),
    )
    monkeypatch.setattr(
        worker,
        "_xbet_prematch_index",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("PREMATCH 1xBet must not run before Brain shortlist")),
    )

    state = worker.collect_once()

    assert state["sports"]["hockey"]["xbet_live"] == 0
    assert state["sports"]["basketball"]["xbet_live"] == 0
    assert state["sports"]["hockey"]["prematch_brain_candidates"] == 0
    assert state["sports"]["basketball"]["prematch_brain_candidates"] == 0

def test_full_match_stats_are_used_cumulatively_through_current_hockey_period(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(worker._flashscore, "fetch_stats_detailed", lambda _event_id: {
        "sections": {
            "FULL_MATCH": {
                "stats": {
                    "shots_on_goal": {"home": 24, "away": 21},
                    "blocked_shots": {"home": 9, "away": 9},
                    "penalties": {"home": 4, "away": 2},
                }
            }
        }
    })
    fs = {
        "flashscore_event_id": "HCUM001",
        "home": "Home",
        "away": "Away",
        "league": "AHL",
        "score": [2, 1],
        "score_parts": [[1, 0], [1, 1]],
        "status_code": "15",
    }

    stats = worker._flashscore_live_stats(fs, SPORTS["hockey"], current_period="PERIOD_2")

    assert stats["current_segment_available"] is True
    assert stats["scope"] == "PERIOD_2"
    assert stats["stats_mode"] == "cumulative_through_current_segment"
    assert stats["segment_stats"]["shots_on_goal"] == [24.0, 21.0]
    assert stats["segment_stats"]["blocked_shots"] == [9.0, 9.0]
    assert stats["segment_stats"]["penalties"] == [4.0, 2.0]


def test_flashscore_numeric_status_is_match_minute_with_score_parts_crosscheck():
    assert _infer_flashscore_scope(
        {"status_code": "15", "score_parts": [[0, 1]], "league": "OHL"},
        SPORTS["hockey"],
    ) == "PERIOD_1"
    assert _infer_flashscore_scope(
        {"status_code": "46", "score_parts": [[0, 1], [1, 0], [0, 0]], "league": "AHL"},
        SPORTS["hockey"],
    ) == "PERIOD_3"
    assert _infer_flashscore_scope(
        {"status_code": "23", "score_parts": [[31, 29], [30, 30]], "league": "USA: NBA"},
        SPORTS["basketball"],
    ) == "QUARTER_2"
    assert _infer_flashscore_scope(
        {"status_code": "23", "score_parts": [[20, 18], [18, 17], [2, 3]], "league": "Chile"},
        SPORTS["basketball"],
    ) == "QUARTER_3"
    assert _infer_flashscore_scope(
        {"status_code": "38", "score_parts": [[20, 18], [18, 17], [17, 18], [8, 7]], "league": "Chile"},
        SPORTS["basketball"],
    ) == "QUARTER_4"

def test_priced_projection_ignores_absurd_flashscore_ao_age_for_realistic_nba_q3(monkeypatch):
    monkeypatch.setenv("GOOL_BASKETBALL_LIVE_SEGMENT_MIN_STAT_EDGE", "2.5")
    brain = {
        "brain_state": "PASS",
        "brain_score": 78.0,
        "scope": "QUARTER_3",
        "projected_total": 7.54,
        "current_segment_score": [2, 0],
        "current_segment_total": 2,
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_possessions_per_min": 1.5,
        "recent_score_rate": 1.0,
        "live_game_stats": {
            "current_segment_available": True,
            "stats_mode": "cumulative_through_current_segment",
            "segment_stats": {"rebounds": [1, 0]},
            "segment_attempts": {},
        },
        "brain_reason": "Flashscore stats selected this game first",
    }
    lane = {
        "scope": "QUARTER_3",
        "market_family": "match_total",
        "line": 57.5,
        "over": 1.90,
        "under": 1.92,
        "probability": 0.50,
        # Real audit showed the 1xBet local Q3 clock near 45s while AO age
        # was ~191s. The post-candidate projection must use 45s.
        "clock_seconds": 45,
        "score": [2, 0],
        "league": "USA: NBA - Pre-season",
        "live_game_stats": {"segment_stats": {"rebounds": [1, 0]}},
    }

    signal = price_flashscore_live_candidate(brain, lane, SPORTS["basketball"])

    assert signal is not None
    assert signal["direction"] == "under"
    assert 48.0 <= signal["projected_total"] <= 54.0
    assert signal["projected_total"] != 7.54
    assert signal["elapsed_seconds"] == 45.0
    assert signal["remaining_seconds"] == 675.0
    assert signal["projection_clock_source"] == "1xbet_after_flashscore_brain"

def test_live_scopes_accept_canonical_scope_names():
    from gool_bot2.xbet_multisport_markets import live_scopes_from_period

    assert live_scopes_from_period("hockey", "PERIOD_2") == {"PERIOD_2"}
    assert live_scopes_from_period("basketball", "QUARTER_3") == {"QUARTER_3"}

def test_flashscore_derives_new_segment_score_when_score_parts_lag():
    hockey = _flashscore_scoped_scores(
        {
            "status_code": "46",
            "league": "AHL",
            "score": [3, 2],
            "score_parts": [[1, 1], [2, 1]],
        },
        SPORTS["hockey"],
    )
    assert hockey["PERIOD_3"] == (0, 0)

    basket = _flashscore_scoped_scores(
        {
            # Current basketball stage-code feed: AC=24 means Q3. The Q3
            # score-part can lag one snapshot, so derive it from full score.
            "status_code": "24",
            "league": "Chile",
            "score": [45, 41],
            "score_parts": [[20, 18], [22, 19]],
        },
        SPORTS["basketball"],
    )
    assert basket["QUARTER_3"] == (3, 4)

def test_empty_current_period_section_uses_cumulative_full_match_immediately(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(worker._flashscore, "fetch_stats_detailed", lambda _event_id: {
        "sections": {
            "PERIOD_3": {"key": "PERIOD_3", "label": "3rd period", "stats": {}, "raw": []},
            "FULL_MATCH": {
                "key": "FULL_MATCH",
                "label": "Full match",
                "stats": {
                    "shots_on_goal": {"home": 30, "away": 27},
                    "blocked_shots": {"home": 13, "away": 9},
                    "penalties": {"home": 5, "away": 3},
                },
                "raw": [],
            },
        }
    })
    fs = {
        "flashscore_event_id": "EMPTY-P3",
        "home": "Anaheim Ducks",
        "away": "Florida Panthers",
        "league": "NHL",
        "score": [2, 1],
        "score_parts": [[1, 0], [1, 1], [0, 0]],
        "status_code": "46",
    }

    stats = worker._flashscore_live_stats(fs, SPORTS["hockey"], current_period="PERIOD_3")

    assert stats["stats_mode"] == "cumulative_through_current_segment"
    assert stats["scope"] == "PERIOD_3"
    assert stats["current_segment_available"] is True
    assert stats["segment_stats"]["shots_on_goal"] == [30.0, 27.0]
    assert stats["segment_stats"]["blocked_shots"] == [13.0, 9.0]
    assert stats["segment_stats"]["penalties"] == [5.0, 3.0]

def test_basketball_q3_uses_cumulative_q1_q2_q3_stats_when_only_full_match_exists(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(worker._flashscore, "fetch_stats_detailed", lambda _event_id: {
        "sections": {
            "QUARTER_3": {"key": "QUARTER_3", "label": "3rd quarter", "stats": {}, "raw": []},
            "FULL_MATCH": {
                "key": "FULL_MATCH",
                "label": "Full match",
                "stats": {
                    "rebounds": {"home": 26, "away": 21},
                    "turnovers": {"home": 7, "away": 8},
                    "field_goals": {
                        "home": 47.5,
                        "away": 44.2,
                        "home_attempts": 40,
                        "away_attempts": 43,
                    },
                },
                "raw": [],
            },
        }
    })
    fs = {
        "flashscore_event_id": "BCUMQ3",
        "home": "Home",
        "away": "Away",
        "league": "Chile",
        "score": [58, 54],
        "score_parts": [[20, 18], [19, 20], [19, 16]],
        "status_code": "23",
    }

    stats = worker._flashscore_live_stats(fs, SPORTS["basketball"], current_period="QUARTER_3")

    assert stats["scope"] == "QUARTER_3"
    assert stats["current_segment_available"] is True
    assert stats["stats_mode"] == "cumulative_through_current_segment"
    assert stats["segment_stats"]["rebounds"] == [26.0, 21.0]
    assert stats["segment_stats"]["turnovers"] == [7.0, 8.0]
    assert stats["segment_attempts"]["field_goals"] == [40.0, 43.0]

def test_hockey_late_third_period_under_is_blocked_when_empty_net_risk_is_high(monkeypatch):
    monkeypatch.setenv("GOOL_HOCKEY_EMPTY_NET_UNDER_GUARD_SECONDS", "300")
    monkeypatch.setenv("GOOL_HOCKEY_EMPTY_NET_UNDER_GUARD_MARGIN", "2")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_SEGMENT_MIN_STAT_EDGE", "0.2")
    brain = {
        "brain_state": "PASS",
        "brain_score": 78.0,
        "scope": "PERIOD_3",
        "score": [1, 2],
        "current_segment_score": [1, 0],
        "history_points": 3,
        "recent_shot_rate": 1.1,
        "live_game_stats": {
            "stats_mode": "cumulative_through_current_segment",
            "segment_stats": {"shots_on_goal": [22, 24]},
        },
    }
    lane = {
        "scope": "PERIOD_3",
        "market_family": "match_total",
        "line": 2.5,
        "over": 2.05,
        "under": 1.77,
        "probability": 0.47,
        "clock_seconds": 1050,  # 2:30 left in P3
        "score": [1, 0],
        "match_score": [1, 2],
        "league": "AHL",
        "live_game_stats": brain["live_game_stats"],
    }

    assert price_flashscore_live_candidate(brain, lane, SPORTS["hockey"]) is None


def test_hockey_late_third_period_under_guard_does_not_block_big_lead(monkeypatch):
    monkeypatch.setenv("GOOL_HOCKEY_EMPTY_NET_UNDER_GUARD_SECONDS", "300")
    monkeypatch.setenv("GOOL_HOCKEY_EMPTY_NET_UNDER_GUARD_MARGIN", "2")
    monkeypatch.setenv("GOOL_HOCKEY_LIVE_SEGMENT_MIN_STAT_EDGE", "0.2")
    brain = {
        "brain_state": "PASS",
        "brain_score": 78.0,
        "scope": "PERIOD_3",
        "score": [1, 4],
        "current_segment_score": [1, 0],
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_shot_rate": 0.75,
        "live_game_stats": {
            "stats_mode": "cumulative_through_current_segment",
            "segment_stats": {"shots_on_goal": [22, 24]},
        },
    }
    lane = {
        "scope": "PERIOD_3",
        "market_family": "match_total",
        "line": 2.5,
        "over": 2.05,
        "under": 1.77,
        "probability": 0.47,
        "clock_seconds": 1050,
        "score": [1, 0],
        "match_score": [1, 4],
        "league": "AHL",
        "live_game_stats": brain["live_game_stats"],
    }

    signal = price_flashscore_live_candidate(brain, lane, SPORTS["hockey"])
    assert signal is not None
    assert signal["direction"] == "under"



def test_worker_restores_recent_flashscore_brain_snapshots_after_restart(tmp_path: Path):
    history = tmp_path / "live" / "gool_multisport_history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "sports": {
            "basketball": {
                "flashscore_analysis_matches": [
                    {
                        "flashscore_event_id": "RESTB001",
                        "scope": "QUARTER_2",
                        "current_segment_score": [18, 17],
                        "live_game_stats": {
                            "current_segment_available": True,
                            "stats_mode": "direct_segment",
                            "segment_stats": {"rebounds": [8, 7]},
                        },
                    }
                ],
                "matches": [],
                "prematch_matches": [],
            }
        },
    }
    history.write_text(json.dumps(state) + "\n", encoding="utf-8")

    worker = MultiSportSteamWorker(tmp_path)

    restored = list(worker._fs_brain_history["basketball:RESTB001:QUARTER_2"])
    assert len(restored) == 1
    assert restored[0]["score"] == [18, 17]
    assert restored[0]["live_game_stats"]["current_segment_available"] is True


def test_basketball_direct_quarter_activity_overrides_false_break_heuristic(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(
        worker,
        "_flashscore_live_stats",
        lambda fs, cfg, current_period="": {
            "source": "flashscore",
            "scope": "QUARTER_2",
            "available": True,
            "current_segment_available": True,
            "stats_mode": "direct_segment",
            "segment_stats": {"field_goals": [2.0, 1.0], "rebounds": [1.0, 1.0]},
            "segment_attempts": {"field_goals": [4.0, 3.0]},
        },
    )
    fs = {
        "flashscore_event_id": "BPLAY002",
        "home": "Home",
        "away": "Away",
        "league": "WORLD: Club Friendly",
        "score": [24, 21],
        "score_parts": [[20, 18]],
        "status_code": "22",
        "coarse_status": "2",
        "period_start_ts": __import__("time").time() - 180,
    }

    result = worker._flashscore_live_brain(fs, SPORTS["basketball"])

    assert result["scope"] == "QUARTER_2"
    assert result["break_transition"] is False
    assert result["elapsed_seconds"] > 0


def test_basketball_flashscore_ac22_is_q1_stage_code_not_minute_22(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setattr(
        worker,
        "_flashscore_live_stats",
        lambda fs, cfg, current_period="": {
            "source": "flashscore",
            "scope": current_period,
            "available": True,
            "current_segment_available": True,
            "stats_mode": "direct_segment",
            "segment_stats": {
                "field_goals": [3.0, 2.0],
                "rebounds": [2.0, 1.0],
            },
            "segment_attempts": {"field_goals": [7.0, 6.0]},
        },
    )
    fs = {
        "flashscore_event_id": "BAC22001",
        "home": "Ibaraki Robots",
        "away": "Hiroshima D.",
        "league": "JAPAN: B.League Premier",
        "score": [7, 6],
        "score_parts": [[7, 6]],
        "status_code": "22",
        "period_start_ts": __import__("time").time() - 180,
    }

    result = worker._flashscore_live_brain(fs, SPORTS["basketball"])

    assert result["scope"] == "QUARTER_1"
    assert result["period"] == "1-я четверть"
    assert result["break_transition"] is False
    assert result["current_segment_score"] == [7, 6]


def test_basketball_flashscore_stage_codes_24_and_25_map_to_q3_q4():
    assert _infer_flashscore_scope(
        {
            "league": "PHILIPPINES: PBA",
            "status_code": "24",
            "score_parts": [[20, 18], [18, 20], [7, 4]],
        },
        SPORTS["basketball"],
    ) == "QUARTER_3"
    assert _infer_flashscore_scope(
        {
            "league": "AUSTRALIA: NBL",
            "status_code": "25",
            "score_parts": [[21, 31], [17, 25], [21, 20], [24, 12]],
        },
        SPORTS["basketball"],
    ) == "QUARTER_4"
