from gool_bot2.multi_goal_coverage import enforce_goal_coverage
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _candidate(
    *,
    key: str,
    family: str,
    strategy: str,
    rating: float,
    roi: float,
    pressure: float = 0.0,
    probability: float = 0.66,
    data_quality: float = 1.0,
):
    return MarketCandidate(
        key=key,
        family=family,
        strategy=strategy,
        label=key,
        odd=2.0,
        model_probability=probability,
        correlation_key=key,
        source="test",
        market_pressure_pp=pressure,
        data_quality=data_quality,
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
    assert team.expected_roi == 0.0
    assert team.value_edge_pp == 0.0
    assert "confidence" in result.reason


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


def test_late_confidence_two_more_cannot_beat_passing_one_goal_market_on_fake_ev():
    two_more = _candidate(
        key="ТБ 3.5",
        family="match_total",
        strategy="two_more_goals",
        rating=82,
        roi=1.288,
        probability=0.729,
        data_quality=0.72,
    )
    two_more.value_edge_pp = 41.0
    two_more.value_override = True
    broad = _candidate(
        key="ТБ 2.5",
        family="match_total",
        strategy="another_goal",
        rating=68,
        roi=0.05,
        probability=0.73,
        data_quality=0.72,
    )
    decision = RouterDecision("BET", 59, (1, 1), two_more, [broad], [], "old +2 reason")
    experts = {
        "two_more_goals": {"metric": "confidence"},
        "another_goal": {"metric": "probability"},
    }

    result = enforce_goal_coverage(decision, experts)

    assert result.status == "BET"
    assert result.winner is broad
    assert result.winner.key == "ТБ 2.5"
    assert two_more.expected_roi == 0.0
    assert two_more.value_edge_pp == 0.0
    assert two_more.value_override is False
    assert "одного гола" in result.reason.lower()


def test_late_confidence_two_more_becomes_wait_when_safe_one_goal_does_not_pass():
    two_more = _candidate(
        key="ТБ 3.5",
        family="match_total",
        strategy="two_more_goals",
        rating=82,
        roi=1.288,
        probability=0.729,
        data_quality=0.72,
    )
    two_more.value_edge_pp = 41.0
    decision = RouterDecision("BET", 59, (1, 1), two_more, [], [], "old +2 reason")
    experts = {"two_more_goals": {"metric": "confidence"}}

    result = enforce_goal_coverage(decision, experts)

    assert result.status == "WAIT"
    assert result.winner is None
    assert "не берём +2 гола" in result.reason
    assert "late_two_more_requires_calibrated_probability" in two_more.blocks
