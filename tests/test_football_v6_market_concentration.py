"""Football V6 public market risk and honest winner-evidence regression."""
from __future__ import annotations

from pathlib import Path

from gool_bot2.active_challenger_decisions import football_v6_active_picks
from gool_bot2.football_v6_consensus_shadow import evaluate_football_v6_prematch
from gool_bot2.prematch_scenario_active import recover_football_v6_prematch
from gool_bot2.v4_prematch_delivery import prematch_row_from_pick
from gool_bot2.v4_prematch_engine import (
    PrematchPick, rank_v6_prematch_for_delivery,
)


def cand(market="match_1x2", selection="away", *, odd=3.19,
         p=.376, fair=.309, ev=.199, disagreement=3):
    return {
        "state": "PASS_SHADOW", "market": market, "selection": selection,
        "odd": odd, "market_shrunk_probability_heuristic": p,
        "bookmaker_fair_probability": fair,
        "heuristic_ev_units": ev, "model_disagreement_pp": disagreement,
    }


def snap(*cs, coverage=.0):
    return {
        "status": "PASS_SHADOW", "data_quality": .83,
        "quote_source": "1xbet", "opponent_strength_coverage": coverage,
        "candidates": list(cs),
    }


def run(snapshot):
    return football_v6_active_picks(
        snapshot, event_id="m1", home="Newcastle", away="Aston Villa",
        league="ENGLAND: Premier League", kickoff_ts=1791702000,
    )


def test_1x2_at_37_percent_never_becomes_strong_or_bypasses_opponent_evidence():
    winner = cand()
    assert run(snap(winner, coverage=0.0))[0] == []
    picks, details = run(snap(winner, coverage=.40))
    assert len(picks) == 1 and picks[0].market == "match_1x2"
    assert details["winner_opponent_coverage"] == .40
    assert rank_v6_prematch_for_delivery(picks)[0][1] == "NORMAL"


def test_totals_take_priority_over_unsupported_winner_without_forced_quota():
    winner = cand()
    # This more likely 1.95 OVER has lower nominal EV than the longshot
    # but better value per unit of price risk.
    total = cand("match_total", "over 2.5", odd=1.95, p=.57, fair=.52, ev=.111, disagreement=2.0)
    picks, detail = run(snap(winner, total, coverage=.0))
    assert len(picks) == 1 and picks[0].market == "match_total"
    assert detail["v6_market_rejections"]["1x2_opponent_strength_unverified"] == 1
    assert detail["v6_eligible_markets"] == {"match_total": 1}
    picks, _ = run(snap(winner, total, coverage=.8))
    assert len(picks) == 1 and picks[0].market == "match_total"
    all_picks, _ = football_v6_active_picks(
        snap(winner, total, coverage=.8),
        event_id="m1", home="A", away="B", league="L", kickoff_ts=1791702000,
        return_all=True,
    )
    assert {p.market for p in all_picks} == {"match_total", "match_1x2"}


def test_missing_totals_are_not_made_up_and_odds_caps_apply_to_v6_primary():
    prices = snap(cand(odd=8.0, p=.20, fair=.13, ev=.60), coverage=.8)
    assert run(prices)[0] == []
    # A user with only 1X2 bookmaker prices must not get a fictitious FT total.
    small = snap(cand("match_1x2"), coverage=.8)
    assert run(small)[0][0].market == "match_1x2"


def test_v6_scenario_recovery_cannot_restore_unverified_winner():
    p = PrematchPick("m1", "A", "B", "match_1x2", "away", 2.71, .419, .363, .82, "L", 1791702000)
    scenarios = {"scenarios": [{
        "id": "F04", "status": "MATCH",
        "market_targets": [{"family": "match_1x2", "direction": "away", "scope": "FULL_MATCH"}],
    }]}
    picks, _ = recover_football_v6_prematch(
        scenarios, tournament_candidates=[p],
        shadow={"data_quality": .83, "quote_source": "1xbet", "opponent_strength_coverage": 0},
    )
    assert picks == []


def test_v6_quote_shadow_reports_all_market_family_options_not_only_12():
    forecast = {
        "status": "READY", "home_lambda": 1.4, "away_lambda": 1.2,
        "quality": .8, "opponent_strength_coverage": 0.0,
        "opponent_strength_confirmed": False,
    }
    profile = {"full_match": {
        "available": True, "home_expected_goals": 1.45, "away_expected_goals": 1.15,
    }}
    quote = {
        "match_totals": [{"line": x+.5, "over": 1.91, "under": 1.91} for x in range(10)],
        "home_totals": [{"line": x+.5, "over": 1.91, "under": 1.91} for x in range(3)],
        "match_1x2": {"home": 2.3, "draw": 3.3, "away": 3.6},
        "btts": {"yes": 1.9, "no": 1.9},
    }
    out = evaluate_football_v6_prematch(forecast, profile, quote, event_id="match")
    assert out["quoted_markets_checked"] > 12
    assert len(out["candidates"]) > 12
    assert "match_total" in out["quoted_by_market"]
    assert "btts" in out["quoted_by_market"]
    assert out["opponent_strength_coverage"] == 0.0


def test_new_public_cards_carry_actual_model_version():
    pick = PrematchPick("one", "A", "B", "match_total", "over 2.5", 1.95,
                        .58, .52, .8, "L", 1791702000)
    row = prematch_row_from_pick(pick, brain_version="V6")
    assert row["brain_version"] == "V6"
    card_src = (Path(__file__).resolve().parents[1] /
                "src/gool_bot2/v4_prematch_card.py").read_text("utf8")
    assert 'f"GOOL {brain}"' in card_src
    assert 'f"{brain} · PREMATCH RESULT"' in card_src
