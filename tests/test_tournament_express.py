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


def test_menu_shows_schedule_during_prematch_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    # The Telegram keyboard must work immediately, even with zero priced lines.
    monkeypatch.setattr("gool_bot2.tournament_express._now_ts",
                        lambda: datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
    monkeypatch.setattr("gool_bot2.tournament_express._today_state", lambda: {
        "date": "2026-10-13",
        "fixture_count": 2,
        "scan_status": "discovering",
        "tournaments": [{
            "key": league_key("UEFA Champions League"),
            "league": "UEFA Champions League",
            "fixture_count": 2, "qualified_count": 0, "complete": False,
            "matches": [
                {**event("a", "Team A"), "status": "NO_VERIFIED_MARKET", "selection": None},
                {**event("b", "Team B"), "status": "NO_VERIFIED_MARKET", "selection": None},
            ],
        }],
    })
    message, buttons = tournament_menu()
    assert "Расписание загружено" in message
    assert "0/2" in message
    assert buttons["inline_keyboard"][0][0]["callback_data"] == "tx:" + league_key("UEFA Champions League")
    assert "ещё проверяем линии" in tournament_details(league_key("UEFA Champions League"))


def test_catalog_tracks_progress_without_inventing_prices(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    fixtures = [event("a", "Team A"), event("b", "Team B")]
    catalog, tickets = build_catalog(fixtures, [], now_ts=now, report_date="2026-10-13",
                                     scan_status="pricing", processed_count=4, price_targets=20)
    assert catalog["scan_status"] == "pricing"
    assert catalog["processed_count"] == 4
    assert catalog["price_targets"] == 20
    assert catalog["fixture_count"] == 2
    assert tickets == []


def test_preshunk_v6_consensus_is_not_shrunk_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    # An already-market-shrunk candidate has enough edge. Shrinking again
    # erases its advantage. Preserve that evidence without inventing odds.
    fixture_list = [event("a", "Team A"), event("b", "Team B")]
    quoted_v6 = [
        PrematchPick(k, name, name + " away", "match_total", "over 1.5",
                     1.50, .70, .66, .90, "UEFA Champions League")
        for k, name in (("a", "Team A"), ("b", "Team B"))
    ]
    wrong, wrong_tickets = build_catalog(fixture_list, quoted_v6, now_ts=now,
                                        report_date="2026-10-13", already_shrunk=False)
    assert wrong["tournaments"][0]["qualified_count"] == 0
    assert wrong_tickets == []
    correct, tickets = build_catalog(fixture_list, quoted_v6, now_ts=now,
                                    report_date="2026-10-13", already_shrunk=True)
    assert correct["tournaments"][0]["qualified_count"] == 2
    assert len(tickets) == 1


def test_each_fixture_records_actual_rejection_cause(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    fixtures = [event("a", "Team A"), event("b", "Team B"),
                event("c", "Team C"), event("d", "Team D")]
    diag = {
        "a": {"reason": "not_priced"},
        "b": {"reason": "v5_history_unavailable"},
        "c": {"reason": "no_live_qualified_candidate", "quoted_markets_checked": 7, "qualified_active": 0},
        "d": {"reason": "no_live_qualified_candidate", "quoted_markets_checked": 12, "qualified_active": 3},
    }
    catalog, tickets = build_catalog(fixtures, [], now_ts=now,
                                     report_date="2026-10-13", fixture_diagnostics=diag)
    reasons = [m["diagnostics"]["display_reason"] for m in catalog["tournaments"][0]["matches"]]
    assert "начальный анализ" in reasons[0]
    assert "истории" in reasons[1]
    assert "7 проверено" in reasons[2]
    assert "3 вариантов V6" in reasons[3]
    assert tickets == []


def test_active_v6_exposes_all_qualified_tournament_alternatives():
    from gool_bot2.active_challenger_decisions import football_v6_active_picks
    shadow = {
        "status": "PASS_SHADOW", "data_quality": .9, "quote_source": "1xbet",
        "candidates": [
            {"state": "PASS_SHADOW", "market": "match_total", "selection": "over 0.5",
             "odd": 1.55, "market_shrunk_probability_heuristic": .78,
             "bookmaker_fair_probability": .66, "heuristic_ev_units": .209,
             "model_disagreement_pp": 3.0},
            {"state": "PASS_SHADOW", "market": "btts", "selection": "yes",
             "odd": 1.70, "market_shrunk_probability_heuristic": .71,
             "bookmaker_fair_probability": .59, "heuristic_ev_units": .207,
             "model_disagreement_pp": 4.0},
            {"state": "WAIT", "market": "match_total", "selection": "under 2.5",
             "odd": 1.65, "market_shrunk_probability_heuristic": .85,
             "bookmaker_fair_probability": .62, "heuristic_ev_units": .403,
             "model_disagreement_pp": 1.0},
        ],
    }
    kw = dict(event_id="a", home="Team A", away="Team B", league="UEFA Champions League", kickoff_ts=1234567)
    single, _ = football_v6_active_picks(shadow, **kw)
    all_markets, _ = football_v6_active_picks(shadow, return_all=True, **kw)
    assert len(single) == 1
    assert len(all_markets) == 2
    assert all(p.selection != "under 2.5" for p in all_markets)
