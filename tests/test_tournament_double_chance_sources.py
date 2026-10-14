"""Tournament coverage searches all bookmakers and real double-chance odds.

A full 9/9 coupon still requires actual, reliable pre-kickoff markets and
must never claim that market-derived reserves are proven +EV.
"""
import math

from gool_bot2 import football_v6_flashscore_quote as flashscore
from gool_bot2.tournament_coverage import bookmaker_coverage_picks, choose_quote_coverage
from gool_bot2.tournament_express import build_catalog


def _base_quote():
    return {
        "match_1x2": {"home": 5.0, "draw": 4.0, "away": 1.60},
        "double_chance": {
            "home_or_draw": 2.10, "draw_or_away": 1.31,
            "home_or_away": 1.24,
        },
        "match_totals": [],
        "home_totals": [], "away_totals": [], "btts": {},
    }


def _kw():
    return dict(event_id="A", home="Home", away="Away",
                league="EUROPE: Champions League - League phase",
                kickoff_ts=1792000000.0)


def test_real_double_chance_price_131_not_invented():
    picked = bookmaker_coverage_picks(_base_quote(), quote_source="flashscore", **_kw())
    dc = [p for p in picked if p.market == "double_chance"]
    assert [(x.selection, x.odds) for x in dc] == [("draw or away", 1.31), ("home or draw", 2.10)]
    assert all(0 < p.model_probability < 1 for p in dc)
    assert all(p.model_probability == p.market_probability for p in dc)


def test_double_chance_must_be_real_not_derived_from_1x2_only():
    quote = _base_quote()
    quote["double_chance"] = {}
    assert not any(p.market == "double_chance" for p in
                   bookmaker_coverage_picks(quote, quote_source="flashscore", **_kw()))
    quote["double_chance"] = {"draw_or_away": 1.31}
    quote["match_1x2"] = {}
    assert not any(p.market == "double_chance" for p in
                   bookmaker_coverage_picks(quote, quote_source="flashscore", **_kw()))


def test_real_safe_flashscore_double_chance_wins_over_unhelpful_1xbet_markets():
    # First feed offers only higher-risk 1X2 outcomes. Do not stop at it.
    old = {"match_1x2": {"home": 2.7, "draw": 3.5, "away": 2.8}}
    picks, source, book, diag = choose_quote_coverage(
        [(old, "1xbet"), ({**_base_quote(), "bookmaker": "Provider A"}, "flashscore")],
        **_kw(),
    )
    assert picks
    assert source == "flashscore"
    assert book == "Provider A"
    assert any(p.selection == "draw or away" and p.odds == 1.31 for p in picks)
    assert diag["books_checked"] == 2
    assert diag["safe_candidates"] >= 1


def test_no_offered_safe_odds_returns_empty_and_auditable_reasons():
    out, source, book, stats = choose_quote_coverage(
        [({"match_1x2": {"home": 2.4, "draw": 3.25, "away": 3.1}}, "1xbet")],
        **_kw(),
    )
    assert out == [] and source == "" and book == ""
    assert stats["books_checked"] == 1
    assert stats["safe_candidates"] == 0


def test_flashscore_double_chance_is_same_bookmaker_not_crossed(monkeypatch):
    monkeypatch.setattr(flashscore, "_find_participant_ids", lambda rows: ("h", "a"))
    offers = {}
    def add(typ, side, odd, book):
        offers.setdefault(("FULL_TIME", typ, side, None), []).append({
            "bookmaker": book, "odds": odd, "market_probability": .5,
        })
    for side, odd in (("HOME", 5.0), ("DRAW", 4.0), ("AWAY", 1.60)):
        add("HOME_DRAW_AWAY", side, odd, "A")
    add("DRAW_OR_AWAY", "DRAW_OR_AWAY", 1.31, "A")  # ignored unknown type
    add("DOUBLE_CHANCE", "DRAW_OR_AWAY", 1.31, "A")
    add("DOUBLE_CHANCE", "HOME_OR_DRAW", 2.12, "B")
    monkeypatch.setattr(flashscore, "_selection_observations", lambda *args: offers)
    all_books = flashscore.flashscore_fulltime_quotes({"odds": [{}]})
    a = next(x for x in all_books if x["bookmaker"] == "A")
    b = next(x for x in all_books if x["bookmaker"] == "B")
    assert a["double_chance"] == {"draw_or_away": 1.31}
    assert b["double_chance"] == {"home_or_draw": 2.12}
    assert flashscore.flashscore_fulltime_quote({"odds": [{}]})["bookmaker"] == "A"


def test_full_nine_match_coupon_can_use_two_sources_and_dc_leg():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Europe/Moscow"))
    kick = datetime(2026, 10, 14, 23, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
    tournament = "EUROPE: Champions League - League phase"
    fixtures, reserves, notes = [], [], {}
    for i in range(9):
        eid = f"m{i}"
        name = f"Club {i}"
        kw = dict(event_id=eid, home=name, away="Rival", league=tournament, kickoff_ts=kick)
        source = "flashscore" if i in {1, 2, 8} else "1xbet"
        src_quote = {**_base_quote(), "bookmaker": "Book B" if source == "flashscore" else "1xBet"}
        p, o, book, diag = choose_quote_coverage([(src_quote, source)], **kw)
        assert p
        reserves.extend(p)
        notes[eid] = {"coverage_bookmaker": book, "coverage_diagnostic": diag}
        fixtures.append({"event_id": eid, "home": name, "away": "Rival",
                         "league": tournament, "kickoff_ts": kick})
    cat, tickets = build_catalog(fixtures, [], coverage_picks=reserves,
                                 fixture_diagnostics=notes, already_shrunk=True,
                                 now_ts=now.timestamp(), report_date="2026-10-14")
    assert cat["tournaments"][0]["qualified_count"] == 9
    assert len(tickets) == 1
    assert len(tickets[0]["legs"]) == 9
    assert tickets[0]["leg_bookmakers"]["m1"] == "Book B"


def test_coverage_checks_every_supported_scope_and_settleable_market():
    quote = {
        "bookmaker": "SameBook",
        "match_1x2": {"home": 1.55, "draw": 4.40, "away": 6.0},
        "double_chance": {"home_or_draw": 1.30},
        "draw_no_bet": {"home": 1.32, "away": 3.70},
        "match_totals": [{"line": 1.5, "over": 1.34, "under": 3.70}],
        "home_totals": [{"line": .5, "over": 1.34, "under": 3.70}],
        "away_totals": [{"line": .5, "over": 1.35, "under": 3.60}],
        "first_half_totals": [{"line": .5, "over": 1.65, "under": 2.28}],
        "second_half_totals": [{"line": .5, "over": 1.42, "under": 3.10}],
        "asian_handicaps": [{
            "home_line": +.5, "away_line": -.5,
            "home": 1.38, "away": 3.12,
        }],
        "btts": {"yes": 1.76, "no": 2.16},
    }
    picks = bookmaker_coverage_picks(quote, quote_source="flashscore", **_kw())
    markets = {p.market for p in picks}
    assert {
        "match_total", "home_total", "away_total", "match_1x2",
        "double_chance", "draw_no_bet", "asian_handicap", "btts",
        "1H_OVER_UNDER", "2H_OVER_UNDER",
    } <= markets
    assert any(p.selection == "home +0.5" for p in picks if p.market == "asian_handicap")
    assert all(p.odds >= 1.30 for p in picks)

    from gool_bot2.v4_prematch_settlement import settle_prematch_pick
    for market, selection, expected in (
        ("draw_no_bet", "home", "push"),
        ("asian_handicap", "home +0.5", "won"),
        ("1H_OVER_UNDER", "over 0.5", "won"),
        ("2H_OVER_UNDER", "under 0.5", "lost"),
        ("double_chance", "home or draw", "won"),
    ):
        row = {"market": market, "selection": selection}
        assert settle_prematch_pick(row, 1, 1, half_time_score=(1, 0)) == expected


def test_asian_quarter_and_whole_handicaps_excluded_until_push_fraction_settlement():
    quote = {"asian_handicaps": [
        {"home_line": .25, "away_line": -.25, "home": 1.60, "away": 2.20},
        {"home_line": 1.0, "away_line": -1.0, "home": 1.65, "away": 2.15},
        {"home_line": +.5, "away_line": -.5, "home": 1.38, "away": 3.12},
    ]}
    picks = bookmaker_coverage_picks(quote, quote_source="flashscore", **_kw())
    handi = [p for p in picks if p.market == "asian_handicap"]
    assert len(handi) == 2
    assert {p.selection for p in handi} == {"home +0.5", "away -0.5"}
