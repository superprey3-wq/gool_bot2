from gool_bot2.prematch_value_hunter import qualify_value_pick, select_best_value_pick, diagnose_value_rows
from gool_bot2.v4_prematch_engine import PrematchPick


def pick(odds, p, market_p, quality=.90, event="x"):
    return PrematchPick(
        event, "Home", "Away", "match_total", "under 2.5",
        odds, p, market_p, quality, "Test", 1791000000.0,
    )


def meta(low, sample=10):
    return {
        "probability_low": low,
        "probability_before_regime": None,
        "profile_sample": sample,
        "bookmaker": "Test",
    }


def test_value_hunter_accepts_real_high_odds_value():
    p = pick(3.20, .40, .30)
    ok, info = qualify_value_pick(p, meta(.33))
    assert ok is True
    assert p.edge >= .08
    assert p.expected_value >= .15
    assert info["value_score"] > 0


def test_value_hunter_rejects_big_odds_without_robust_lower_bound():
    p = pick(3.20, .40, .30)
    ok, info = qualify_value_pick(p, meta(.295))
    assert ok is False
    assert "lower_bound_not_above_market" in info["reasons"]


def test_four_plus_price_requires_stronger_value():
    p = pick(4.50, .28, .19)
    ok, info = qualify_value_pick(p, meta(.205))
    assert ok is False
    assert "edge" in info["reasons"] or "ev" in info["reasons"]


def test_selects_best_value_not_highest_price():
    a = pick(2.70, .48, .37, event="a")
    b = pick(4.20, .35, .23, event="b")
    best = select_best_value_pick([
        (a, meta(.39)),
        (b, meta(.25)),
    ])
    assert best is not None
    assert best[0].event_id in {"a", "b"}
    assert best[1]["value_score"] > 0


def test_value_hunter_diagnostics_count_high_odds_and_rejections():
    good = pick(3.20, .40, .30, event="good")
    low_edge = pick(2.60, .37, .31, event="bad")
    diag = diagnose_value_rows([
        (good, meta(.33)),
        (low_edge, meta(.32)),
    ])
    assert diag["modeled_markets"] == 2
    assert diag["high_odds_markets"] == 2
    assert diag["qualified"] == 1
    assert diag["rejects"]["edge"] >= 1


def test_valuehunter_report_shows_prematch_progress(tmp_path, monkeypatch):
    from gool_bot2 import v4_value_hunter_delivery as delivery
    monkeypatch.setattr(delivery, "prematch_status_data", lambda: {
        "running": True,
        "stage": "pricing",
        "value_hunter_stage": "waiting_for_full_market",
        "last_cycle_started_at": "2026-10-04T07:30:00+00:00",
        "last_cycle_finished_at": None,
        "value_hunter_scanned_matches": 0,
        "value_hunter_modeled_markets": 0,
        "value_hunter_high_odds_markets": 0,
        "value_hunter_qualified": 0,
        "value_hunter_candidates": 0,
        "value_hunter_sent": 0,
        "value_hunter_rejects": {},
    })
    journal = tmp_path / "journal.json"
    journal.write_text("[]", encoding="utf-8")
    text = delivery.value_hunter_report_text(journal)
    assert "RUNNING" in text
    assert "pricing" in text
    assert "waiting_for_full_market" in text


def test_value_hunter_accepts_quality_v2_level():
    p = pick(3.20, .40, .30, quality=.71)
    ok, info = qualify_value_pick(p, meta(.33))
    assert ok is True
    assert "quality" not in info["reasons"]
