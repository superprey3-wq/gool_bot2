from gool_bot2.v4_prematch_engine import PrematchPick, build_accumulators, devig_two_way, qualified_pick, picks_from_goal_profile, devig_three_way, poisson_1x2, picks_from_1x2_profile, picks_from_btts_profile, build_prematch_candidates, blend_with_market, rank_prematch_singles, build_super_accumulator, choose_delivery


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


def test_three_way_devig_and_poisson_are_normalized():
    fair = devig_three_way(1.90, 3.60, 4.20)
    model = poisson_1x2(1.8, 0.9)
    assert abs(sum(fair) - 1.0) < 1e-9
    assert abs(sum(model) - 1.0) < 1e-6
    assert model[0] > model[2]


def test_1x2_profile_builds_three_priced_selections():
    profile = {
        "first_half": {"available": True, "home_expected_goals": 0.8, "away_expected_goals": 0.4},
        "second_half": {"available": True, "home_expected_goals": 1.0, "away_expected_goals": 0.5},
    }
    market = {"match_1x2": {"home": 1.90, "draw": 3.60, "away": 4.20}}
    picks = picks_from_1x2_profile(
        event_id="m2", home="A", away="B", profile=profile, market=market, data_quality=0.9
    )
    assert [p.selection for p in picks] == ["home", "draw", "away"]
    assert abs(sum(p.market_probability for p in picks) - 1.0) < 1e-9
    assert abs(sum(p.model_probability for p in picks) - 1.0) < 1e-6


def test_market_blend_shrinks_overconfident_model():
    pick = PrematchPick("m3", "A", "B", "match_total", "over 2.5", 2.0, 0.80, 0.50, 0.9)
    blended = blend_with_market(pick, model_weight=0.65)
    assert 0.50 < blended.model_probability < 0.80
    assert blended.edge < pick.edge


def test_rank_singles_dedupes_same_event_market():
    picks = [
        PrematchPick("m4", "A", "B", "match_total", "over 2.5", 1.8, 0.78, 0.55, 0.9),
        PrematchPick("m4", "A", "B", "match_total", "under 2.5", 2.0, 0.72, 0.45, 0.9),
    ]
    rows = rank_prematch_singles(picks, model_weight=1.0)
    assert len(rows) <= 1


def test_super_accumulator_requires_ten_distinct_low_price_legs():
    picks = [
        PrematchPick(str(i), f"H{i}", f"A{i}", "match_total", "over 1.5", 1.22, 0.86, 0.80, 0.9)
        for i in range(10)
    ]
    row = build_super_accumulator(picks)
    assert row is not None
    assert row["kind"] == "SUPER"
    assert len(row["legs"]) == 10
    assert len({leg.event_id for leg in row["legs"]}) == 10


def test_super_accumulator_never_pads_weak_or_expensive_legs():
    good = [
        PrematchPick(str(i), f"H{i}", f"A{i}", "match_total", "over 1.5", 1.22, 0.86, 0.80, 0.9)
        for i in range(9)
    ]
    bad = PrematchPick("bad", "X", "Y", "match_total", "over 1.5", 1.80, 0.86, 0.80, 0.9)
    assert build_super_accumulator([*good, bad]) is None


def test_delivery_never_reuses_single_event_in_parlay():
    singles = [
        PrematchPick("s1", "S1H", "S1A", "match_total", "over 2.5", 1.80, .82, .60, .9),
        PrematchPick("s2", "S2H", "S2A", "match_total", "under 2.5", 1.75, .81, .60, .9),
    ]
    low_price = [
        PrematchPick("p1", "P1H", "P1A", "goal_1h", "goal 1h", 1.55, .82, .68, .9),
        PrematchPick("p2", "P2H", "P2A", "goal_1h", "goal 1h", 1.58, .81, .67, .9),
    ]
    delivery = choose_delivery([*singles, *low_price], max_singles=2, max_doubles=1)
    single_ids = {p.event_id for p, _tier in delivery["singles"]}
    parlay_ids = {p.event_id for acc in delivery["doubles"] for p in acc["legs"]}
    assert delivery["doubles"]
    assert single_ids.isdisjoint(parlay_ids)


def test_parlay_pool_rejects_expensive_non_single_legs():
    picks = [
        PrematchPick("s1", "A", "B", "match_total", "over 2.5", 1.80, .84, .60, .9),
        PrematchPick("p1", "C", "D", "goal_1h", "goal 1h", 1.55, .83, .68, .9),
        PrematchPick("p2", "E", "F", "goal_1h", "goal 1h", 1.58, .82, .67, .9),
        PrematchPick("x", "G", "H", "match_total", "over 3.5", 2.10, .80, .55, .9),
    ]
    delivery = choose_delivery(picks, max_singles=1, max_doubles=1)
    legs = [p for acc in delivery["doubles"] for p in acc["legs"]]
    assert legs
    assert all(p.odds <= 1.70 for p in legs)


def test_btts_profile_builds_yes_and_no_from_team_goal_rates():
    profile = {
        "first_half": {"available": True, "home_expected_goals": 0.7, "away_expected_goals": 0.5},
        "second_half": {"available": True, "home_expected_goals": 0.9, "away_expected_goals": 0.6},
    }
    market = {"btts": {"yes": 1.85, "no": 1.95}}
    picks = picks_from_btts_profile(event_id="b1", home="A", away="B", profile=profile, market=market, data_quality=.9)
    assert [p.selection for p in picks] == ["yes", "no"]
    assert abs(sum(p.model_probability for p in picks) - 1.0) < 1e-9
    assert abs(sum(p.market_probability for p in picks) - 1.0) < 1e-9


def test_goal_profile_compares_multiple_half_goal_total_lines():
    profile = {"full_match": {"available": True, "expected_total": 2.8}}
    market = {"match_totals": [
        {"line": 1.5, "over": 1.25, "under": 3.8},
        {"line": 2.5, "over": 1.85, "under": 1.95},
        {"line": 3.0, "over": 2.2, "under": 1.65},
        {"line": 3.5, "over": 2.6, "under": 1.45},
    ]}
    picks = picks_from_goal_profile(event_id="t1", home="A", away="B", profile=profile, market=market)
    assert {p.selection for p in picks} == {"over 1.5", "under 1.5", "over 2.5", "under 2.5", "over 3.5", "under 3.5"}
    assert all("3" not in p.selection or "3.5" in p.selection for p in picks)


def test_team_totals_are_priced_from_team_lambdas():
    from gool_bot2.v4_prematch_engine import picks_from_team_totals_profile
    profile = {"full_match": {"available": True, "home_expected_goals": 1.8, "away_expected_goals": 0.7}}
    market = {
        "home_totals": [{"line": 0.5, "over": 1.30, "under": 3.50}, {"line": 1.5, "over": 1.95, "under": 1.85}],
        "away_totals": [{"line": 0.5, "over": 1.85, "under": 1.95}],
    }
    picks = picks_from_team_totals_profile(event_id="tt1", home="A", away="B", profile=profile, market=market, data_quality=.9)
    labels = {p.selection for p in picks}
    assert {"ИТБ1 0.5", "ИТБ1 1.5", "ИТБ2 0.5"} <= labels
    home_goal = next(p for p in picks if p.selection == "ИТБ1 0.5")
    away_goal = next(p for p in picks if p.selection == "ИТБ2 0.5")
    assert home_goal.model_probability > away_goal.model_probability


def test_build_candidates_compares_team_totals_with_match_markets():
    profile = {"full_match": {"available": True, "expected_total": 2.5, "home_expected_goals": 1.8, "away_expected_goals": 0.7}}
    market = {
        "match_totals": [{"line": 2.5, "over": 1.95, "under": 1.85}],
        "home_totals": [{"line": 0.5, "over": 1.30, "under": 3.50}],
        "away_totals": [{"line": 0.5, "over": 1.85, "under": 1.95}],
    }
    picks = build_prematch_candidates(event_id="all1", home="A", away="B", profile=profile, market=market, data_quality=.9)
    markets = {p.market for p in picks}
    assert "match_total" in markets
    assert "home_total" in markets
    assert "away_total" in markets
