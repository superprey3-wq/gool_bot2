"""Only the UEFA Champions League and the five first-tier domestic leagues.

Also ensure the automatic full-fixture parlay never prefers long-priced
1.92/2.33 under-1.5 outcomes just to inflate the accumulator odds.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gool_bot2.elite_tournament_policy import elite_tournament_tier, is_elite_tournament
from gool_bot2.tournament_express import _priority, build_catalog, write_catalog
from gool_bot2.tournament_coverage import bookmaker_coverage_picks
from gool_bot2.v4_prematch_engine import PrematchPick


@pytest.mark.parametrize("league,tier", [
    ("EUROPE: Champions League - League phase", 0),
    ("UEFA Champions League", 0),
    ("ENGLAND: Premier League", 1),
    ("SPAIN: LaLiga EA Sports", 2),
    ("ITALY: Serie A", 3),
    ("GERMANY: Bundesliga", 4),
    ("FRANCE: Ligue 1 McDonald's", 5),
])
def test_allow_just_mens_ucl_and_top_five(league, tier):
    assert is_elite_tournament(league)
    assert elite_tournament_tier(league) == tier
    assert _priority(league) == tier


@pytest.mark.parametrize("league", [
    "EUROPE: Europa League",
    "EUROPE: Conference League",
    "EUROPE: Champions League Women",
    "EUROPE: Women's Champions League",
    "EUROPE: Champions League U19",
    "ASIA: AFC Champions League",
    "AFRICA: CAF Champions League",
    "ENGLAND: Championship",
    "ENGLAND: Premier League 2",
    "SPAIN: LaLiga2",
    "ITALY: Serie B",
    "ITALY: Serie A Women",
    "GERMANY: 2. Bundesliga",
    "FRANCE: Ligue 2",
    "FRANCE: National",
    "NETHERLANDS: Eredivisie",
    "TURKEY: Super Lig",
    "BRAZIL: Serie A",
    "WORLD: World Cup",
    "EUROPE: Nations League",
])
def test_prevent_all_other_flashscore_tournaments(league):
    assert not is_elite_tournament(league), league
    assert _priority(league) == 10


def fixtures(league, now, amount=2):
    return [
        {"event_id": f"{league}-{i}", "league": league,
         "home": f"Home{i}", "away": f"Away{i}", "kickoff_ts": (now+timedelta(hours=6)).timestamp()}
        for i in range(amount)
    ]


def test_menu_purges_stale_old_flashscore_leagues(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path/"daily.json"))
    now = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Europe/Moscow"))
    old, _ = build_catalog(
        fixtures("EUROPE: Champions League - League phase", now, 2),
        [], now_ts=now.timestamp(), report_date="2026-10-14")
    # Simulate pre-fix catalog accidentally including two irrelevant leagues.
    old["tournaments"].append({"league": "BRAZIL: Serie A",
        "matches": fixtures("BRAZIL: Serie A", now, 2)})
    write_catalog(old)
    new, tickets = build_catalog(
        fixtures("EUROPE: Champions League - League phase", now, 2) +
        fixtures("BRAZIL: Serie A", now, 2) +
        fixtures("GERMANY: Bundesliga", now, 2),
        [], now_ts=now.timestamp(), report_date="2026-10-14")
    assert [g["league"] for g in new["tournaments"]] == [
        "EUROPE: Champions League - League phase", "GERMANY: Bundesliga",
    ]
    assert new["fixture_count"] == 4
    assert tickets == []


def test_conservative_tournament_uses_130_reserve_not_risky_under_15(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path/"state.json"))
    now = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Europe/Moscow"))
    league = "EUROPE: Champions League - League phase"
    games = fixtures(league, now, 2)
    risky = [
        PrematchPick(g["event_id"], g["home"], g["away"], "match_total",
                     "under 1.5", odd, .84, .52, .90, league)
        for g, odd in zip(games, (2.33, 1.92))
    ]
    reserves = []
    for g in games:
        reserves += bookmaker_coverage_picks(
            {"match_totals": [{"line": 1.5, "over": 1.30, "under": 3.90}]},
            quote_source="1xbet", event_id=g["event_id"], home=g["home"],
            away=g["away"], league=league, kickoff_ts=g["kickoff_ts"],
        )
    catalog, tickets = build_catalog(games, risky, coverage_picks=reserves,
                                     already_shrunk=True, now_ts=now.timestamp(),
                                     report_date="2026-10-14")
    assert len(tickets) == 1
    assert [p.odds for p in tickets[0]["legs"]] == [1.30, 1.30]
    assert all(p.selection == "over 1.5" for p in tickets[0]["legs"])
    assert catalog["tournaments"][0]["coverage_only_count"] == 2


def test_only_long_prices_leaves_coupon_incomplete_instead_of_forced_risk(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path/"state.json"))
    now = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Europe/Moscow"))
    games = fixtures("EUROPE: Champions League - League phase", now, 2)
    value = [
        PrematchPick(g["event_id"], g["home"], g["away"], "match_total",
                     "under 1.5", 2.33, .70, .40, .91, g["league"])
        for g in games
    ]
    catalog, tickets = build_catalog(games, value, already_shrunk=True,
                                     now_ts=now.timestamp(), report_date="2026-10-14")
    assert tickets == []
    assert catalog["tournaments"][0]["qualified_count"] == 0


def test_market_reserve_does_not_accept_mismatched_total_ladders_or_invalid_vig():
    args = dict(quote_source="1xbet", event_id="id", home="Home",
                away="Away", league="Champions League", kickoff_ts=10000)
    contradictory = {
        "match_totals": [
            {"line": 1.5, "over": 2.30, "under": 1.50},
            {"line": 2.5, "over": 1.90, "under": 1.80},
        ],
    }
    assert bookmaker_coverage_picks(contradictory, **args) == []
    vig_invalid = {"match_totals": [{"line": 1.5, "over": 1.30, "under": 1.40}]}
    assert bookmaker_coverage_picks(vig_invalid, **args) == []


def test_selected_safe_market_rejects_implausibly_long_odd_even_with_model_ev(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path/"state.json"))
    now = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Europe/Moscow"))
    games = fixtures("EUROPE: Champions League - League phase", now, 2)
    value = [
        PrematchPick(g["event_id"], g["home"], g["away"], "match_total",
                     "over 1.5", 2.30, .80, .54, .93, g["league"])
        for g in games
    ]
    cat, tickets = build_catalog(games, value, already_shrunk=True, now_ts=now.timestamp(),
                                 report_date="2026-10-14")
    assert tickets == []
    assert cat["tournaments"][0]["qualified_count"] == 0
