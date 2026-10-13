"""Regression: 1.70 is a public-singles floor in ALL sports and phases.

Lower-priced fully-verified parlay legs are a separate product, not affected
by this policy. No synthetic odds or underpriced telegram messages permitted.
"""
from types import SimpleNamespace

import pytest

from gool_bot2.single_price_policy import (
    minimum_single_odd, public_single_allowed, public_single_rejection,
)
from gool_bot2.multi_router import MarketCandidate, score_candidate, route_market
from gool_bot2.v4_prematch_engine import PrematchPick, signal_tier


@pytest.mark.parametrize("sport", ("football", "hockey", "basketball"))
@pytest.mark.parametrize("phase", ("LIVE", "PREMATCH"))
def test_public_single_min_170_for_all_sports_and_phases(sport, phase, monkeypatch):
    # Old configuration values must never allow 1.40/1.50 public signals.
    monkeypatch.setenv("GOOL_SINGLE_MIN_ODD", "1.40")
    monkeypatch.setenv("GOOL_LIVE_SINGLE_MIN_ODD", "1.40")
    monkeypatch.setenv("GOOL_PREMATCH_SINGLE_MIN_ODD", "1.40")
    monkeypatch.setenv("GOOL_MULTISPORT_SINGLE_MIN_ODD", "1.40")
    monkeypatch.setenv("GOOL_BASKETBALL_SINGLE_MIN_ODD", "1.40")
    monkeypatch.setenv("GOOL_HOCKEY_SINGLE_MIN_ODD", "1.40")
    assert minimum_single_odd(sport=sport, phase=phase) == 1.70
    for odd in (None, "", 0, 1.4, 1.5, 1.69, float("nan"), float("inf")):
        assert not public_single_allowed(odd, sport=sport, phase=phase)
        assert public_single_rejection(odd, sport=sport, phase=phase)
    assert public_single_allowed(1.70, sport=sport, phase=phase)
    assert public_single_allowed(2.10, sport=sport, phase=phase)


def _candidate(odd, key):
    return MarketCandidate(
        key=key, family="match_total", strategy="another_goal",
        label="ТБ 0.5", odd=odd, model_probability=.9,
        market_age_seconds=0.0, data_quality=1.0,
    )


def test_football_live_router_can_choose_second_valid_market_not_short_price():
    high_rank_short = _candidate(1.60, "short")
    high_rank_short.market_pressure_pp = 10.0
    valid = _candidate(1.75, "valid")
    decision = route_market([high_rank_short, valid], minute=40, score=(0, 0))
    assert decision.status == "BET"
    assert decision.winner.odd == 1.75
    assert any("live_single_odd_below_1.70" in c.blocks for c in decision.rejected)


def test_football_final_telegram_gate_blocks_unpriced_and_low_price(monkeypatch):
    from gool_bot2 import multi_telegram
    monkeypatch.setattr(multi_telegram, "is_multi_telegram_active", lambda: True)
    calls = []
    monkeypatch.setattr(multi_telegram.telegram, "broadcast", lambda *a, **k: calls.append("text") or 1)
    monkeypatch.setattr(multi_telegram.telegram, "broadcast_photo", lambda *a, **k: calls.append("photo") or 1)
    decision = SimpleNamespace(winner=SimpleNamespace(odd=1.60))
    for odd in (1.60, None):
        assert multi_telegram.emit_multi_signal({}, decision, {"odd": odd, "match_id": "A"}) == 0
    assert calls == []


@pytest.mark.parametrize("sport", ("hockey", "basketball"))
@pytest.mark.parametrize("phase", ("LIVE", "PREMATCH"))
def test_multisport_publication_checks_floor_before_journal_or_network(sport, phase, capsys):
    from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS

    class DuplicateStub:
        lookups = 0
        def _already_seen(self, *a, **kw):
            self.lookups += 1
            return True

    stub = DuplicateStub()
    row = {"event_id": "fixture", "phase": phase, "market_family": "match_total",
           "scope": "PERIOD_1", "home": "A", "away": "B"}
    for odd in (1.40, 1.59, 1.69, None):
        assert MultiSportSteamWorker._record_signal(
            stub, row, {"odd": odd, "direction": "over"}, SPORTS[sport],
        ) == (False, 0)
    assert stub.lookups == 0
    assert f"GOOL_{sport.upper()}_SINGLE_ODD_REJECT" in capsys.readouterr().out
    assert MultiSportSteamWorker._record_signal(
        stub, row, {"odd": 1.70, "direction": "over"}, SPORTS[sport],
    ) == (False, 0)
    assert stub.lookups == 1


def test_football_prematch_single_rejects_parlay_odds(monkeypatch):
    monkeypatch.setenv("GOOL_FOOTBALL_V5_ACTIVE", "1")
    low = PrematchPick("a", "A", "B", "goal_1h", "goal 1h", 1.5, .9, .67, .9)
    high = PrematchPick("a", "A", "B", "goal_1h", "goal 1h", 1.8, .9, .67, .9)
    assert signal_tier(low) is None
    assert signal_tier(high) is not None


def test_football_express_legs_still_can_use_149(monkeypatch):
    from gool_bot2.v4_prematch_engine import choose_delivery
    monkeypatch.setenv("GOOL_PREMATCH_SINGLE_MIN_ODD", "1.70")
    rows = [
        PrematchPick("a", "A", "B", "goal_1h", "goal 1h", 1.49, .91, .66, .95),
        PrematchPick("c", "C", "D", "goal_1h", "goal 1h", 1.49, .91, .66, .95),
    ]
    selection = choose_delivery(rows, max_singles=5, max_doubles=1, include_super=False)
    assert selection["singles"] == []
    assert selection["doubles"]
    assert all(p.odds == 1.49 for p in selection["doubles"][0]["legs"])
