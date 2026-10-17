"""Regression for V6 real-data PREMATCH NO_BET despite qualified priced picks.

V6 outputs have ALREADY been market-shrunk. Legacy V4 delivery double-
shrunk them and then applied a 60% probability cutoff for ALL odds.
Synthetic fixtures here mirror the observed real CI funnel without
claiming historical profitability.
"""
from pathlib import Path

from gool_bot2.v4_prematch_engine import (
    PrematchPick, choose_delivery, rank_prematch_for_delivery,
    rank_v6_prematch_for_delivery,
)


def _v6(event, odd, probability, fair, quality=.83, market="match_total"):
    return PrematchPick(event, "Home", "Away", market, "over 2.5",
                        odd, probability, fair, quality, "League", 1791702000)


def test_v6_market_shrunk_price_relative_candidate_is_sent_not_doubly_shrunk():
    # Real audit: V6 found 14 priced selections but old 60% gate emitted NO_BET.
    pick = _v6("fixture1", 2.51, .435, .34)
    assert pick.edge >= .025 and pick.expected_value >= .07
    assert rank_prematch_for_delivery([pick]) == []
    actual = choose_delivery([pick], v6_active=True, include_super=False)
    assert actual["mode"] == "SINGLES"
    assert len(actual["singles"]) == 1
    published, tier = actual["singles"][0]
    assert published is pick  # Exactly one model-to-market shrink already happened.
    assert published.model_probability == .435
    assert tier in {"STRONG", "NORMAL"}


def test_v6_excludes_longshots_and_weak_value_even_if_legacy_probability_not_used():
    picks = [
        _v6("longshot", 7.40, .18, .11),
        _v6("low_edge", 2.50, .41, .39),
        _v6("poor_quality", 2.70, .44, .35, quality=.50),
        _v6("low_odd", 1.55, .80, .62),
        _v6("legitimate", 3.19, .376, .30),
        _v6("not_value", 2.52, .40, .36),
    ]
    selected = rank_v6_prematch_for_delivery(picks)
    assert [p.event_id for p, _ in selected] == ["legitimate"]


def test_v6_uses_one_public_single_per_game_with_independent_parlay_routing():
    picks = [
        _v6("a", 2.51, .435, .34),
        _v6("a", 2.63, .435, .33, market="home_total"),
        _v6("b", 3.19, .376, .30),
    ]
    delivery = choose_delivery(picks, v6_active=True, max_singles=8,
                               max_doubles=0, include_super=False)
    assert len(delivery["singles"]) == 2
    assert len({p.event_id for p, _ in delivery["singles"]}) == 2


def test_legacy_v5_and_v4_paths_remain_unchanged():
    pick = _v6("c", 2.51, .435, .34)
    assert choose_delivery([pick], include_super=False)["mode"] == "NO_BET"


def test_real_active_football_script_propagates_v6_delivery_flag():
    script = (Path(__file__).resolve().parents[1] /
              "scripts/gool_flashscore_today.py").read_text("utf-8")
    assert "v6_active=V6_ACTIVE" in script
    assert "if v6_active else rank_prematch_for_delivery" in (
        (Path(__file__).resolve().parents[1] /
         "src/gool_bot2/v4_prematch_engine.py").read_text("utf-8")
    )
