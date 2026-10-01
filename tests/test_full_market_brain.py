from __future__ import annotations

from gool_bot2.full_market_brain import analyze_full_market
from gool_bot2.full_market_math import _asian_total_net, _asian_handicap_net


HOME = "H"
AWAY = "A"


def item(value, *, pid=None, selection=None, line=None, btts=None, score=None, winner=None, active=True):
    return {
        "eventParticipantId": pid,
        "value": str(value),
        "active": active,
        "selection": selection,
        "handicap": None if line is None else {"value": str(line)},
        "bothTeamsToScore": btts,
        "score": score,
        "winner": winner,
    }


def row(scope, typ, odds, bookmaker=1):
    return {
        "bettingScope": scope,
        "bettingType": typ,
        "bookmakerId": bookmaker,
        "odds": odds,
    }


def profile():
    return {
        "full_match": {
            "available": True,
            "home_expected_goals": 1.85,
            "away_expected_goals": 1.05,
            "expected_total": 2.90,
            "pair_sample": 10,
        },
        "first_half": {
            "available": True,
            "home_expected_goals": 0.78,
            "away_expected_goals": 0.42,
            "expected_total": 1.20,
            "pair_sample": 10,
        },
        "second_half": {
            "available": True,
            "home_expected_goals": 1.07,
            "away_expected_goals": 0.63,
            "expected_total": 1.70,
            "pair_sample": 10,
        },
    }


def market_data():
    return {
        "settings": {
            "bookmakers": [
                {"bookmaker": {"id": 1, "name": "Book A"}},
                {"bookmaker": {"id": 2, "name": "Book B"}},
            ]
        },
        "odds": [
            row("FULL_TIME", "HOME_DRAW_AWAY", [
                item(1.90, pid=HOME), item(4.10, pid=AWAY), item(3.60),
            ]),
            row("FULL_TIME", "DRAW_NO_BET", [
                item(1.36, pid=HOME), item(3.15, pid=AWAY),
            ]),
            row("FULL_TIME", "DOUBLE_CHANCE", [
                item(1.28), item(1.22, pid=HOME), item(1.92, pid=AWAY),
            ]),
            row("FULL_TIME", "OVER_UNDER", [
                item(1.80, selection="OVER", line=2.5),
                item(2.02, selection="UNDER", line=2.5),
                item(2.15, selection="OVER", line=3.0),
                item(1.72, selection="UNDER", line=3.0),
            ]),
            row("FIRST_HALF", "OVER_UNDER", [
                item(1.70, selection="OVER", line=0.5),
                item(2.15, selection="UNDER", line=0.5),
                item(2.45, selection="OVER", line=1.5),
                item(1.55, selection="UNDER", line=1.5),
            ]),
            row("SECOND_HALF", "OVER_UNDER", [
                item(1.55, selection="OVER", line=0.5),
                item(2.55, selection="UNDER", line=0.5),
                item(2.02, selection="OVER", line=1.5),
                item(1.78, selection="UNDER", line=1.5),
            ]),
            row("FULL_TIME", "BOTH_TEAMS_TO_SCORE", [
                item(1.86, btts=True), item(1.91, btts=False),
            ]),
            row("FIRST_HALF", "BOTH_TEAMS_TO_SCORE", [
                item(3.60, btts=True), item(1.27, btts=False),
            ]),
            row("FULL_TIME", "ODD_OR_EVEN", [
                item(1.90, selection="ODD"), item(1.90, selection="EVEN"),
            ]),
            row("FULL_TIME", "CORRECT_SCORE", [
                item(7.5, score="1:0"), item(8.5, score="2:0"),
                item(7.0, score="1:1"), item(11.0, score="2:1"),
            ]),
            row("FULL_TIME", "HALF_FULL_TIME", [
                item(2.8, winner="1/1"), item(5.5, winner="X/1"),
                item(10.0, winner="2/1"), item(12.0, winner="1/X"),
                item(5.8, winner="X/X"), item(18.0, winner="2/X"),
                item(25.0, winner="1/2"), item(12.0, winner="X/2"),
                item(16.0, winner="2/2"),
            ]),
            row("FULL_TIME", "ASIAN_HANDICAP", [
                item(1.92, pid=HOME, line=-0.5),
                item(1.92, pid=AWAY, line=0.5),
                item(2.18, pid=HOME, line=-0.75),
                item(1.70, pid=AWAY, line=0.75),
            ]),
            row("FULL_TIME", "EUROPEAN_HANDICAP", [
                item(2.25, pid=HOME, line=-1.0),
                item(3.40, line=-1.0),
                item(2.85, pid=AWAY, line=1.0),
            ]),
        ],
    }


def test_full_market_brain_decodes_core_market_families():
    result = analyze_full_market(market_data(), profile(), quality=1.0)
    assert result["participant_ids"] == {"home": HOME, "away": AWAY}

    covered = {(x["scope"], x["type"]) for x in result["modeled_market_types"]}
    expected = {
        ("FULL_TIME", "HOME_DRAW_AWAY"),
        ("FULL_TIME", "DRAW_NO_BET"),
        ("FULL_TIME", "DOUBLE_CHANCE"),
        ("FULL_TIME", "OVER_UNDER"),
        ("FIRST_HALF", "OVER_UNDER"),
        ("SECOND_HALF", "OVER_UNDER"),
        ("FULL_TIME", "BOTH_TEAMS_TO_SCORE"),
        ("FIRST_HALF", "BOTH_TEAMS_TO_SCORE"),
        ("FULL_TIME", "ODD_OR_EVEN"),
        ("FULL_TIME", "CORRECT_SCORE"),
        ("FULL_TIME", "HALF_FULL_TIME"),
        ("FULL_TIME", "ASIAN_HANDICAP"),
        ("FULL_TIME", "EUROPEAN_HANDICAP"),
    }
    assert expected <= covered

    candidates = result["candidates"]
    assert any(x["market_type"] == "HOME_DRAW_AWAY" and x["selection"] == "HOME" for x in candidates)
    assert any(x["market_type"] == "DOUBLE_CHANCE" and x["selection"] == "HOME_OR_DRAW" for x in candidates)
    assert any(x["market_type"] == "DRAW_NO_BET" and x["selection"] == "HOME" for x in candidates)
    assert any(x["scope"] == "FIRST_HALF" and x["market_type"] == "OVER_UNDER" for x in candidates)
    assert any(x["scope"] == "SECOND_HALF" and x["market_type"] == "OVER_UNDER" for x in candidates)
    assert any(x["market_type"] == "BOTH_TEAMS_TO_SCORE" for x in candidates)


def test_exotic_longshots_are_modeled_but_not_auto_promoted():
    result = analyze_full_market(market_data(), profile(), quality=1.0)
    exotic = [
        x for x in result["candidates"]
        if x["market_type"] in {"CORRECT_SCORE", "HALF_FULL_TIME"}
    ]
    assert exotic
    assert all(x["raw_model_probability"] is not None for x in exotic)
    assert all(x["status"] == "SKIP" for x in exotic if x["odds"] > 3.25)


def test_asian_settlement_math_handles_push_and_half_outcomes():
    assert _asian_total_net(3, 3.0, True, 2.0) == 0.0
    assert _asian_total_net(3, 2.75, True, 2.0) == 0.5
    assert _asian_total_net(2, 2.25, True, 2.0) == -0.5

    assert _asian_handicap_net(1, -1.0, 2.0) == 0.0
    assert _asian_handicap_net(1, -0.75, 2.0) == 0.5
    assert _asian_handicap_net(0, -0.25, 2.0) == -0.5


def test_unmodeled_scope_is_reported_not_given_fake_probability():
    data = market_data()
    data["odds"].append(
        row("FULL_TIME_OVER_TIME", "ODD_OR_EVEN", [
            item(1.90, selection="ODD"), item(1.90, selection="EVEN"),
        ])
    )
    result = analyze_full_market(data, profile(), quality=1.0)
    unmodeled = {(x["scope"], x["type"]) for x in result["unmodeled_market_types"]}
    assert ("FULL_TIME_OVER_TIME", "ODD_OR_EVEN") in unmodeled
    assert not any(
        x["scope"] == "FULL_TIME_OVER_TIME"
        for x in result["candidates"]
    )


def test_half_full_time_uses_derived_period_split_when_history_missing():
    p = {
        "full_match": {
            "available": True,
            "home_expected_goals": 1.85,
            "away_expected_goals": 1.05,
            "expected_total": 2.90,
            "pair_sample": 10,
        },
        "first_half": {"available": False, "pair_sample": 0},
        "second_half": {"available": False, "pair_sample": 0},
    }
    result = analyze_full_market(market_data(), p, quality=1.0)
    covered = {(x["scope"], x["type"]) for x in result["modeled_market_types"]}
    assert ("FULL_TIME", "HALF_FULL_TIME") in covered
    assert not any(
        x["scope"] == "FULL_TIME" and x["type"] == "HALF_FULL_TIME"
        for x in result["unmodeled_market_types"]
    )


def test_market_assisted_half_scope_cannot_be_full_bet():
    p = {
        "full_match": {
            "available": True,
            "home_expected_goals": 2.3,
            "away_expected_goals": 0.8,
            "expected_total": 3.1,
            "pair_sample": 10,
        },
        "first_half": {"available": False, "pair_sample": 0},
        "second_half": {"available": False, "pair_sample": 0},
    }
    result = analyze_full_market(market_data(), p, quality=1.0)
    assisted = [
        x for x in result["candidates"]
        if x["scope"] in {"FIRST_HALF", "SECOND_HALF"}
        and x.get("scope_source") == "ft_model_with_market_period_split"
    ]
    assert assisted
    assert all(x["status"] != "BET" for x in assisted)


def test_confidence_layer_selects_exactly_one_best_market():
    result = analyze_full_market(market_data(), profile(), quality=1.0)
    best = result.get("best_pick")
    assert best is not None
    assert 0.0 <= float(best["confidence_score"]) <= 100.0
    assert best["confidence_grade"] in {"VERY_HIGH", "HIGH", "MEDIUM", "LOW", "WEAK", "NO_DATA"}
    assert best["decision"] in {"BET", "LEAN", "SKIP"}

    actionable = [
        x for x in result["candidates"]
        if x["status"] in {"BET", "LEAN"}
        and x["model_probability"] is not None
        and x["expected_value"] is not None
        and 1.40 <= float(x["odds"]) <= 3.25
        and float(x["expected_value"]) > 0
        and (x["edge"] is None or float(x["edge"]) > 0)
    ]
    if actionable:
        assert float(best["confidence_score"]) == max(float(x["confidence_score"]) for x in actionable)


def test_confidence_is_not_just_hit_probability():
    result = analyze_full_market(market_data(), profile(), quality=1.0)
    rows = [
        x for x in result["candidates"]
        if x["model_probability"] is not None and x["confidence_score"] is not None
    ]
    assert rows
    # The score has independent value/reliability components; it must not be
    # a disguised p*100 field.
    assert any(
        abs(float(x["confidence_score"]) - 100.0 * float(x["model_probability"])) > 1.0
        for x in rows
    )
