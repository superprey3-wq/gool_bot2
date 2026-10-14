"""Full tournament coupon coverage at real-bookmaker 1.30+ reserve prices."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from gool_bot2.tournament_coverage import bookmaker_coverage_picks
from gool_bot2.tournament_express import build_catalog, write_catalog
from gool_bot2.v4_prematch_engine import PrematchPick, signal_tier


def quote():
    return {
        "match_totals": [
            {"line": 1.5, "over": 1.30, "under": 3.90},
            {"line": 2.0, "over": 1.80, "under": 1.93},  # Asian settlement unsupported
        ],
        "home_totals": [{"line": .5, "over": 1.35, "under": 3.20}],
        "away_totals": [{"line": .5, "over": 1.55, "under": 2.45}],
        "match_1x2": {"home": 1.45, "draw": 3.90, "away": 6.10},
        "btts": {"yes": 1.85, "no": 1.93},
    }


def reserve(eid, name, kick, q=None, source="1xbet"):
    return bookmaker_coverage_picks(
        quote() if q is None else q, quote_source=source,
        event_id=eid, home=name, away=f"{name} away",
        league="EUROPE: Champions League - League phase",
        kickoff_ts=kick,
    )


def test_min_130_real_quote_and_no_fabricated_selections():
    opts = reserve("a", "A", 10000)
    assert opts
    assert any(p.odds == 1.30 and p.selection == "over 1.5" for p in opts)
    assert min(p.odds for p in opts) >= 1.30
    assert not any(p.selection.endswith("2") for p in opts)
    assert all(p.model_probability == p.market_probability for p in opts)
    assert opts == sorted(opts, key=lambda p: (-p.model_probability, p.odds, p.market, p.selection))


def test_reserve_needs_actual_bookmaker_source_and_paired_market():
    assert reserve("a", "A", 10000, source="unknown") == []
    assert reserve("a", "A", 10000, q={"match_totals": [{"line": 1.5, "over": 1.30}]}) == []
    assert reserve("a", "A", 10000, q={"match_1x2": {"home": 1.3, "draw": 3.5}}) == []
    assert reserve("a", "A", 10000, q={"btts": {"yes": 1.20, "no": 1.25}}) == []
    assert reserve("a", "A", 0) == []


def test_all_nine_fixtures_can_be_covered_with_real_bookmaker_reserves(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 14, 11, tzinfo=ZoneInfo("Europe/Moscow"))
    kick = (now + timedelta(hours=9)).timestamp()
    league = "EUROPE: Champions League - League phase"
    fixtures, backups, value = [], [], []
    for i in range(9):
        eid = f"e{i}"
        name = f"Team {i}"
        fixtures.append({
            "event_id": eid, "league": league,
            "home": name, "away": f"{name} away", "kickoff_ts": kick,
        })
        backups += reserve(eid, name, kick)
        if i in {0, 1, 2}:
            value.append(PrematchPick(eid, name, f"{name} away",
                                     "match_total", "over 1.5", 1.75,
                                     .75, .58, .95, league, kick))
    catalog, tickets = build_catalog(
        fixtures, value, coverage_picks=backups, already_shrunk=True,
        now_ts=now.timestamp(), report_date="2026-10-14",
    )
    t = catalog["tournaments"][0]
    assert t["qualified_count"] == 9
    assert t["coverage_only_count"] == 6
    assert t["verified_value_count"] == 3
    assert t["complete"]
    assert len(tickets) == 1
    assert len(tickets[0]["legs"]) == 9
    assert len(tickets[0]["coverage_only_legs"]) == 6
    assert all(x["odd"] >= 1.30 for x in (m["selection"] for m in t["matches"]))
    assert all(x["probability_source"] == "bookmaker_de_vig"
               for x in (m["selection"] for m in t["matches"] if m["selection"]["qualification"] == "COVERAGE_ONLY"))
    assert signal_tier(PrematchPick("a", "A", "B", "match_total", "over 1.5",
                                   1.30, .95, .72, .9)) is None


def test_missing_quote_never_creates_fabricated_full_tournament_coupon(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 14, 11, tzinfo=ZoneInfo("Europe/Moscow"))
    kick = (now + timedelta(hours=9)).timestamp()
    fixtures = [{"event_id": f"e{i}", "league": "Champions League",
                 "home": f"H{i}", "away": f"A{i}", "kickoff_ts": kick} for i in range(9)]
    reserves = []
    for i in range(8):
        reserves.extend(reserve(f"e{i}", f"H{i}", kick))
    cat, tickets = build_catalog(fixtures, [], coverage_picks=reserves,
                                 already_shrunk=True, now_ts=now.timestamp(), report_date="2026-10-14")
    assert cat["tournaments"][0]["qualified_count"] == 8
    assert cat["tournaments"][0]["coverage_only_count"] == 8
    assert tickets == []


def test_expired_or_started_quotes_do_not_create_retroactive_full_coupon(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_TOURNAMENT_EXPRESS_STATE", str(tmp_path / "state.json"))
    now = datetime(2026, 10, 14, 22, tzinfo=ZoneInfo("Europe/Moscow"))
    kick = (now - timedelta(hours=1)).timestamp()
    fixtures = [{"event_id": f"e{i}", "league": "Champions League",
                 "home": f"H{i}", "away": f"A{i}", "kickoff_ts": kick} for i in range(9)]
    reserves = sum((reserve(f"e{i}", f"H{i}", kick) for i in range(9)), [])
    cat, tickets = build_catalog(fixtures, [], coverage_picks=reserves,
                                 already_shrunk=True, now_ts=now.timestamp(), report_date="2026-10-14")
    assert cat["tournaments"][0]["qualified_count"] == 0
    assert tickets == []
