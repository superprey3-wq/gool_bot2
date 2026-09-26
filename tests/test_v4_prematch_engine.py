from gool_bot2.v4_prematch_engine import PrematchPick, build_accumulators, devig_two_way, qualified_pick, picks_from_goal_profile


def test_devig_two_way_removes_margin():
    over, under = devig_two_way(1.80, 2.00)
    assert abs((over + under) - 1.0) < 1e-9
    assert over > under


def test_weak_or_fake_value_is_rejected():
    weak = PrematchPick("a", "A", "B", "total", "over 2.5", 1.60, 0.64, 0.625, 0.9)
    assert qualified_pick(weak) is False


def test_accumulator_uses_distinct_matches_only():
    picks = [
        PrematchPick("a", "A", "B", "total", "over 1.5", 1.45, 0.76, 0.68, 0.9),
        PrematchPick("a", "A", "B", "1x2", "home", 1.55, 0.72, 0.64, 0.9),
        PrematchPick("b", "C", "D", "total", "over 1.5", 1.50, 0.74, 0.66, 0.9),
    ]
    rows = build_accumulators(picks, legs=2)
    assert rows
    assert all(len({leg.event_id for leg in row["legs"]}) == 2 for row in rows)


def test_goal_profile_builds_priced_prematch_totals():
    profile = {
        "first_half": {"available": True, "expected_total": 1.1},
        "second_half": {"available": True, "expected_total": 1.7},
    }
    market = {"match_totals": [{"line": 2.5, "over": 1.95, "under": 1.85}]}
    picks = picks_from_goal_profile(
        event_id="m1", home="A", away="B", profile=profile, market=market, data_quality=0.9
    )
    assert len(picks) == 2
    over = next(p for p in picks if p.selection == "over 2.5")
    under = next(p for p in picks if p.selection == "under 2.5")
    assert 0 < over.model_probability < 1
    assert abs((over.market_probability + under.market_probability) - 1.0) < 1e-9
    assert over.event_id == "m1"
