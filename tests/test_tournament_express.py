from datetime import datetime
from zoneinfo import ZoneInfo

from gool_bot2.v4_prematch_engine import PrematchPick, signal_tier
from gool_bot2.tournament_express import (
    build_catalog, league_key, read_catalog, tournament_details,
    tournament_menu, write_catalog,
)


def event(eid: str, home: str, minute: int = 0, league: str = "UEFA Champions League"):
    kickoff = datetime(2026, 10, 13, 20, minute, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    return {"event_id": eid, "league": league, "home": home, "away": home + " away", "kickoff_ts": kickoff}


def quoted(eid: str, home: str, odd: float = 1.50):
    return PrematchPick(eid, home, home + " away", "match_total", "over 1.5",
                        odd, .88, .65, .95, "UEFA Champions League")


def test_tournament_full_coupon_all_games_and_independent_from_singles(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    fixtures = [event("a", "Team A"), event("b", "Team B", 5), event("c", "Team C", 10)]
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    catalog, tickets = build_catalog(fixtures, [quoted("a", "Team A"), quoted("b", "Team B"), quoted("c", "Team C")],
                                     now_ts=now, report_date="2026-10-13")
    assert catalog["fixture_count"] == 3
    assert len(tickets) == 1
    assert len(tickets[0]["legs"]) == 3
    assert {p.event_id for p in tickets[0]["legs"]} == {"a", "b", "c"}
    assert catalog["tournaments"][0]["complete"] is True
    write_catalog(catalog)
    assert read_catalog()["ticket_count"] == 1


def test_tournament_does_not_drop_unpriced_fixtures(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    fixtures = [event("a", "Team A"), event("b", "Team B"), event("c", "Team C")]
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    result, tickets = build_catalog(fixtures, [quoted("a", "Team A"), quoted("b", "Team B")],
                                   now_ts=now, report_date="2026-10-13")
    assert tickets == []
    assert result["tournaments"][0]["fixture_count"] == 3
    assert result["tournaments"][0]["qualified_count"] == 2
    assert result["tournaments"][0]["complete"] is False
    write_catalog(result)
    current, buttons = tournament_menu()
    # The current-time menu is separately tested; historical fixtures remain
    # inspectable through the persistent catalog.
    assert read_catalog()["tournaments"][0]["matches"][-1]["status"] == "NO_VERIFIED_MARKET"


def test_started_fixture_blocks_full_coupon_even_if_it_had_a_valid_price(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 13, 20, 2, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    rows, tickets = build_catalog(
        [event("a", "Team A"), event("b", "Team B", 5)],
        [quoted("a", "Team A"), quoted("b", "Team B")],
        now_ts=now, report_date="2026-10-13",
    )
    assert tickets == []
    assert rows["tournaments"][0]["matches"][0]["status"] == "STARTED"


def test_later_scan_preserves_previous_universe(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    first, _ = build_catalog([event("a", "Team A"), event("b", "Team B")],
                             [quoted("a", "Team A")], now_ts=now, report_date="2026-10-13")
    write_catalog(first)
    after, tickets = build_catalog([event("b", "Team B")],
                                   [quoted("b", "Team B")], now_ts=now, report_date="2026-10-13")
    assert tickets == []
    assert after["tournaments"][0]["fixture_count"] == 2
    assert after["tournaments"][0]["qualified_count"] == 1


def test_unverified_and_low_quality_selections_never_force_full_coupon(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    bad = PrematchPick("b", "Team B", "Team B away", "btts", "yes", 1.5, .55, .55, .4, "UEFA Champions League")
    result, tickets = build_catalog([event("a", "Team A"), event("b", "Team B")],
                                   [quoted("a", "Team A"), bad], now_ts=now, report_date="2026-10-13")
    assert result["tournaments"][0]["qualified_count"] == 1
    assert tickets == []


def test_football_single_floor_is_distinct_from_parlay_floor(monkeypatch):
    monkeypatch.setenv("GOOL_FOOTBALL_V5_ACTIVE", "1")
    monkeypatch.setenv("GOOL_PREMATCH_SINGLE_MIN_ODD", "1.70")
    low = PrematchPick("a", "A", "B", "match_total", "over 1.5", 1.60, .89, .62, .9)
    high = PrematchPick("a", "A", "B", "match_total", "over 1.5", 1.75, .89, .62, .9)
    assert signal_tier(low) is None
    assert signal_tier(high) in {"STRONG", "NORMAL"}
