from gool_bot2.multi_another_goal_guard import enforce_another_goal_context
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _decision(minute: int, score=(1, 1), pressure: float = 1.0) -> RouterDecision:
    winner = MarketCandidate(
        key=f"match_total:{sum(score) + 0.5:g}",
        family="match_total",
        strategy="another_goal",
        label=f"ТБ {sum(score) + 0.5:g}",
        odd=1.60,
        model_probability=0.80,
        rating=80.0,
        market_pressure_pp=pressure,
    )
    return RouterDecision(
        status="BET",
        minute=minute,
        score=score,
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="test",
    )


def _record(minute: int, score=(1, 1), last_goal: int | None = None, half_goals: int = 0, line_prob: float | None = None) -> dict:
    timeline = [] if last_goal is None else [{"minute": last_goal, "event_type": "goal", "score": list(score)}]
    over = {}
    if line_prob is not None:
        over[f"{half_goals + 0.5:.1f}"] = line_prob
    return {
        "match": {
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": timeline}}},
        "prematch_goal_profile": {
            "active": {"period": "2H", "current_half_goals": half_goals},
            "second_half": {"over": over},
        },
    }


def _market(draw_fair: float = 0.50, draw_delta: float = 4.0) -> dict:
    home_fair = (1.0 - draw_fair) / 2.0
    away_fair = home_fair
    return {
        "captured_at": "2026-09-05T16:00:00+00:00",
        "markets": {
            "match_1x2": {
                "home": 3.2,
                "draw": 2.0,
                "away": 3.2,
                "fair": {"home": home_fair, "draw": draw_fair, "away": away_fair},
                "overround": 0.05,
            }
        },
        "pressure": {
            "match_1x2:home": {"prob_delta_pp": -2.0},
            "match_1x2:draw": {"prob_delta_pp": draw_delta},
            "match_1x2:away": {"prob_delta_pp": -2.0},
        },
    }


def test_recent_goal_requires_three_minute_rebuild() -> None:
    decision = enforce_another_goal_context(
        _decision(62),
        _record(62, last_goal=60),
        {"another_goal": {"probability": 0.85}},
        _market(draw_delta=0.0),
    )
    assert decision.status == "WAIT"
    assert "another_goal_post_goal_rebuild" in decision.rejected[0].blocks


def test_after_rebuild_window_entry_can_continue() -> None:
    decision = enforce_another_goal_context(
        _decision(63),
        _record(63, last_goal=60),
        {"another_goal": {"probability": 0.85}},
        _market(draw_delta=0.0),
    )
    assert decision.status == "BET"


def test_tied_match_draw_repricing_blocks_marginal_another_goal() -> None:
    decision = enforce_another_goal_context(
        _decision(65, score=(2, 2), pressure=1.0),
        _record(65, score=(2, 2)),
        {"another_goal": {"probability": 0.78}},
        _market(draw_fair=0.50, draw_delta=4.0),
    )
    assert decision.status == "WAIT"
    assert "another_goal_1x2_draw_repricing" in decision.rejected[0].blocks


def test_tied_match_draw_repricing_can_be_overruled_only_by_strong_live_and_total() -> None:
    decision = enforce_another_goal_context(
        _decision(65, score=(2, 2), pressure=4.0),
        _record(65, score=(2, 2)),
        {"another_goal": {"probability": 0.84}},
        _market(draw_fair=0.50, draw_delta=4.0),
    )
    assert decision.status == "BET"


def test_second_half_saturation_blocks_without_double_confirmation() -> None:
    decision = enforce_another_goal_context(
        _decision(68, score=(2, 1), pressure=1.0),
        _record(68, score=(2, 1), half_goals=2, line_prob=0.24),
        {"another_goal": {"probability": 0.79}},
        _market(draw_delta=0.0),
    )
    assert decision.status == "WAIT"
    assert "another_goal_half_total_saturated" in decision.rejected[0].blocks


def test_leading_score_is_not_blocked_by_draw_repricing_rule() -> None:
    decision = enforce_another_goal_context(
        _decision(65, score=(2, 1), pressure=1.0),
        _record(65, score=(2, 1)),
        {"another_goal": {"probability": 0.80}},
        _market(draw_fair=0.55, draw_delta=5.0),
    )
    assert decision.status == "BET"
