from gool_bot2.v4_prematch_engine import PrematchPick, build_accumulators, devig_two_way, qualified_pick


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
