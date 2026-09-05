from __future__ import annotations

from gool_bot2.prematch_goal_profile import apply_half_goal_prior, build_prematch_goal_profile


def _row(event_id: str, home: str, away: str, ht: tuple[int, int], ft: tuple[int, int]):
    return {
        "event_id": event_id,
        "home": home,
        "away": away,
        "home_score": ft[0],
        "away_score": ft[1],
        "halftime_home_score": ht[0],
        "halftime_away_score": ht[1],
        "second_half_home_score": ft[0] - ht[0],
        "second_half_away_score": ft[1] - ht[1],
        "timestamp": f"2026-08-{int(event_id[-1]) + 10:02d}T12:00:00+00:00",
        "source": "test",
    }


def _context():
    home_recent = [
        _row("a1", "A", "X1", (1, 0), (2, 1)),
        _row("a2", "X2", "A", (0, 1), (0, 2)),
        _row("a3", "A", "X3", (2, 0), (3, 0)),
        _row("a4", "X4", "A", (1, 1), (1, 2)),
        _row("a5", "A", "X5", (0, 0), (1, 1)),
        _row("a6", "X6", "A", (0, 1), (1, 2)),
    ]
    away_recent = [
        _row("b1", "B", "Y1", (1, 0), (2, 0)),
        _row("b2", "Y2", "B", (1, 1), (1, 2)),
        _row("b3", "B", "Y3", (0, 1), (1, 2)),
        _row("b4", "Y4", "B", (0, 1), (1, 1)),
        _row("b5", "B", "Y5", (1, 1), (2, 2)),
        _row("b6", "Y6", "B", (0, 0), (0, 1)),
    ]
    h2h = [
        _row("h1", "A", "B", (2, 0), (2, 1)),
        _row("h2", "B", "A", (1, 1), (2, 1)),
        _row("h3", "A", "B", (2, 0), (2, 1)),
    ]
    return {
        "home_recent": home_recent,
        "away_recent": away_recent,
        "home_at_home": [row for row in home_recent if row["home"] == "A"],
        "away_away": [row for row in away_recent if row["away"] == "B"],
        "h2h": h2h,
        "sources": ["365scores_recent_halves"],
        "has_trends": True,
    }


def _record(minute: int, score: tuple[int, int], *, halftime_score=None):
    providers = {"flashscore": {"meta": {"goal_timeline": []}}}
    if halftime_score is not None:
        providers["365scores"] = {"meta": {"halftime_score": list(halftime_score)}}
    return {
        "match": {
            "home": "A",
            "away": "B",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
        },
        "providers": providers,
        "prematch_context": _context(),
    }


def test_builds_separate_first_and_second_half_profiles_with_h2h():
    profile = build_prematch_goal_profile(_record(17, (1, 0)))
    first = profile["first_half"]
    second = profile["second_half"]

    assert first["available"] is True
    assert second["available"] is True
    assert first["pair_sample"] == 6
    assert first["h2h"]["matches"] == 3
    assert first["h2h_weight"] == 0.12
    assert first["h2h"]["over"]["1.5"] == 1.0
    assert first["expected_total"] != second["expected_total"]
    assert 0.0 < first["over"]["0.5"] < 1.0
    assert first["over"]["1.5"] > first["over"]["2.5"]


def test_first_half_live_target_is_next_concrete_total_line():
    profile = build_prematch_goal_profile(_record(17, (1, 0)))
    active = profile["active"]

    assert active["period"] == "1H"
    assert active["current_half_goals"] == 1
    assert active["next_total_line"] == 1.5
    assert active["remaining_minutes"] == 28.0
    assert 0.0 < active["one_more_probability"] < 1.0


def test_second_half_counts_only_goals_after_halftime():
    profile = build_prematch_goal_profile(_record(60, (2, 1), halftime_score=(1, 0)))
    active = profile["active"]

    assert active["period"] == "2H"
    assert active["current_half_goals"] == 2
    assert active["next_total_line"] == 2.5
    assert active["remaining_minutes"] == 30.0
    assert 0.0 < active["one_more_probability"] < 1.0


def test_history_prior_never_promotes_live_state_and_is_capped():
    record = _record(17, (1, 0))
    experts = {
        "goal_before_ht": {
            "probability": 0.70,
            "state": "BORDERLINE",
            "passed": False,
            "blocks": ["expert_borderline"],
            "diagnostics": {},
        }
    }
    before = experts["goal_before_ht"]["probability"]
    apply_half_goal_prior(record, experts)
    after = experts["goal_before_ht"]["probability"]

    assert experts["goal_before_ht"]["state"] == "BORDERLINE"
    assert experts["goal_before_ht"]["passed"] is False
    assert abs(after - before) <= 0.0601
    diag = experts["goal_before_ht"]["diagnostics"]["half_prematch_prior"]
    assert diag["period"] == "1H"
    assert diag["next_total_line"] == 1.5


def test_no_halftime_history_means_no_fabricated_half_prior():
    record = _record(20, (0, 0))
    for key in ("home_recent", "away_recent", "home_at_home", "away_away", "h2h"):
        for row in record["prematch_context"].get(key, []):
            row.pop("halftime_home_score", None)
            row.pop("halftime_away_score", None)
            row.pop("second_half_home_score", None)
            row.pop("second_half_away_score", None)
    profile = build_prematch_goal_profile(record)

    assert profile["first_half"]["available"] is False
    assert profile["second_half"]["available"] is False
    assert profile["active"]["available"] is False
