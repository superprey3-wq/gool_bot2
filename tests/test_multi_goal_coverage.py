from gool_bot2.multi_goal_coverage import enforce_goal_coverage
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _candidate(*, key: str, family: str, strategy: str, rating: float, roi: float, pressure: float = 0.0):
    return MarketCandidate(
        key=key,
        family=family,
        strategy=strategy,
        label=key,
        odd=2.0,
        model_probability=0.66,
        correlation_key=key,
        source="test",
        market_pressure_pp=pressure,
        rating=rating,
        expected_roi=roi,
        eligible=True,
    )


def test_heuristic_team_goal_cannot_displace_eligible_another_goal():
    team = _candidate(key="ИТБ2 3.5", family="team_total", strategy="away_goal", rating=79, roi=0.347, pressure=-2.2)
    broad = _candidate(key="ТБ 5.5", family="match_total", strategy="another_goal", rating=73, roi=0.12, pressure=0.0)
    decision = RouterDecision("BET", 75, (2, 3), team, [broad], [], "old team reason")
    experts = {"away_goal": {"metric": "confidence"}, "another_goal": {"metric": "probability"}}

    result = enforce_goal_coverage(decision, experts)

    assert result.status == "BET"
    assert result.winner is broad
    assert result.alternatives[0] is team
    assert "любой команды" in result.reason


def test_calibrated_team_goal_needs_material_advantage_to_beat_another_goal():
    team = _candidate(key="ИТБ2 3.5", family="team_total", strategy="away_goal", rating=76, roi=0.20)
    broad = _candidate(key="ТБ 5.5", family="match_total", strategy="another_goal", rating=73, roi=0.14)
    decision = RouterDecision("BET", 70, (2, 3), team, [broad], [], "old team reason")
    experts = {"away_goal": {"metric": "probability"}}

    result = enforce_goal_coverage(decision, experts)

    assert result.winner is broad
    assert "потерю покрытия" in result.reason


def test_calibrated_team_goal_can_win_with_large_rating_and_roi_advantage():
    team = _candidate(key="ИТБ2 3.5", family="team_total", strategy="away_goal", rating=82, roi=0.30)
    broad = _candidate(key="ТБ 5.5", family="match_total", strategy="another_goal", rating=73, roi=0.14)
    decision = RouterDecision("BET", 70, (2, 3), team, [broad], [], "team genuinely stronger")
    experts = {"away_goal": {"metric": "probability"}}

    result = enforce_goal_coverage(decision, experts)

    assert result.winner is team
    assert result.reason == "team genuinely stronger"


def test_negative_1xbet_move_blocks_heuristic_team_when_no_another_goal_is_eligible():
    team = _candidate(key="ИТБ2 3.5", family="team_total", strategy="away_goal", rating=79, roi=0.347, pressure=-2.2)
    decision = RouterDecision("BET", 75, (2, 3), team, [], [], "old team reason")
    experts = {"away_goal": {"metric": "confidence"}}

    result = enforce_goal_coverage(decision, experts)

    assert result.status == "WAIT"
    assert result.winner is None
    assert "1xBet движется против" in result.reason
    assert "heuristic_team_market_opposed" in team.blocks
