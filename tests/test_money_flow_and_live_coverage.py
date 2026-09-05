from __future__ import annotations

from gool_bot2 import matchbook_pagination
from gool_bot2.journal import save_signal_journal
from gool_bot2.multi_money_flow import (
    _apply_bank_settlement,
    _attach_bank_fields,
    _entry,
    evaluate_money_flow,
    money_flow_open_section,
    money_flow_report_line,
)
from gool_bot2.multi_money_flow_card import render_money_flow_result_card, render_money_flow_signal_card
from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector


def _record(*, minute: int = 60, last_goal: int | None = None, delta: float = 400.0, pp: float = 2.8):
    timeline = [] if last_goal is None else [{"minute": last_goal, "event_type": "goal", "score": [1, 1]}]
    return {
        "match": {
            "flashscore_event_id": "abc12345",
            "home": "Arsenal",
            "away": "Chelsea",
            "league": "Premier League",
            "minute": minute,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": timeline}}},
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "mb1"},
            "systems": {
                "another_goal": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 2.5,
                    "market_id": "m1",
                    "market_name": "Total Goals 2.5",
                    "market_status": "open",
                    "volume": 2000.0,
                    "fair_over": 0.62,
                    "over": {
                        "best_back": {"odds": 1.70, "available": 300.0},
                        "best_lay": {"odds": 1.72, "available": 250.0},
                    },
                    "under": {
                        "best_back": {"odds": 2.40, "available": 200.0},
                        "best_lay": {"odds": 2.44, "available": 200.0},
                    },
                    "flow": {
                        "window_ready_30s": True,
                        "window_ready_60s": True,
                        "volume_delta_30s": delta,
                        "volume_delta_60s": delta + 100.0,
                        "fair_over_delta_pp_30s": pp,
                        "fair_over_delta_pp_60s": pp + 0.2,
                    },
                }
            },
        },
    }


def test_heavy_money_flow_is_independent_candidate(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_BET_MIN_DELTA_30S", "250")
    monkeypatch.setenv("MATCHBOOK_FLOW_BET_MIN_RELATIVE_PCT", "8")
    info = evaluate_money_flow(_record(delta=400.0, pp=2.8))
    assert info["eligible"] is True
    assert info["strategy"] == "money_flow"
    assert info["window"] == "30s"
    assert info["level"] == "HEAVY_FLOW"
    assert info["odd"] == 1.70


def test_money_flow_does_not_chase_fresh_goal():
    info = evaluate_money_flow(_record(minute=60, last_goal=59, delta=900.0, pp=5.0))
    assert info["eligible"] is False
    assert info["reason"] == "post_goal_exchange_reset"


def test_money_flow_rejects_small_or_unready_move():
    weak = evaluate_money_flow(_record(delta=40.0, pp=0.5))
    assert weak["eligible"] is False
    record = _record(delta=900.0, pp=5.0)
    flow = record["matchbook_exchange"]["systems"]["another_goal"]["flow"]
    flow["window_ready_30s"] = False
    flow["window_ready_60s"] = False
    unready = evaluate_money_flow(record)
    assert unready["eligible"] is False


def test_money_flow_has_png_cards_and_independent_bank(tmp_path, monkeypatch):
    journal = tmp_path / "flow.json"
    bank_state = tmp_path / "flow_bank.json"
    monkeypatch.setenv("GOOL_MONEY_FLOW_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("GOOL_MONEY_FLOW_BANK_STATE_PATH", str(bank_state))
    monkeypatch.setenv("GOOL_MONEY_FLOW_BANK_INITIAL_RUB", "100000")
    monkeypatch.setenv("GOOL_MONEY_FLOW_BANK_STAKE_PCT", "0.02")

    record = _record(delta=900.0, pp=5.0)
    info = evaluate_money_flow(record)
    row = _entry(record, info)
    _attach_bank_fields(row, [])

    assert row["virtual_bank_before_rub"] == 100000.0
    assert row["virtual_stake_rub"] == 2000.0
    assert row["virtual_stake_pct"] == 0.02
    signal_png = render_money_flow_signal_card(record, row)
    assert signal_png.startswith(b"\x89PNG\r\n\x1a\n")

    row.update(
        {
            "result": "won",
            "profit_units": 0.70,
            "settled_at": "2026-09-05T20:00:00+00:00",
            "settled_minute": 66,
            "settled_score": [2, 1],
        }
    )
    assert _apply_bank_settlement(row) is True
    assert row["virtual_profit_rub"] == 1400.0
    result_png = render_money_flow_result_card(row, record)
    assert result_png.startswith(b"\x89PNG\r\n\x1a\n")

    save_signal_journal(journal, [row])
    report = money_flow_report_line(journal)
    assert "Matchbook MONEY FLOW" in report
    assert "101,400" in report


def test_money_flow_open_section_contains_pending_bet(tmp_path, monkeypatch):
    journal = tmp_path / "flow.json"
    bank_state = tmp_path / "flow_bank.json"
    monkeypatch.setenv("GOOL_MONEY_FLOW_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("GOOL_MONEY_FLOW_BANK_STATE_PATH", str(bank_state))

    record = _record(delta=900.0, pp=5.0)
    row = _entry(record, evaluate_money_flow(record))
    _attach_bank_fields(row, [])
    save_signal_journal(journal, [row])
    section = money_flow_open_section(journal)
    assert section is not None
    assert "MONEY FLOW · В ИГРЕ" in section
    assert "Arsenal" in section
    assert "2,000 ₽" in section


def test_production_detail_windows_and_top_leagues():
    assert StorageLiveSnapshotCollector._entry_window(1)
    assert StorageLiveSnapshotCollector._entry_window(30)
    assert StorageLiveSnapshotCollector._entry_window(35)
    assert not StorageLiveSnapshotCollector._entry_window(36)
    assert not StorageLiveSnapshotCollector._entry_window(45)
    assert StorageLiveSnapshotCollector._entry_window(46)
    assert StorageLiveSnapshotCollector._entry_window(75)
    assert not StorageLiveSnapshotCollector._entry_window(76)
    assert StorageLiveSnapshotCollector._top_league("England: Premier League")
    assert StorageLiveSnapshotCollector._top_league("UEFA Champions League")
    assert not StorageLiveSnapshotCollector._top_league("Regional League")


def test_matchbook_pagination_collects_beyond_first_100(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_EVENTS_PER_PAGE", "100")
    monkeypatch.setenv("MATCHBOOK_MAX_PAGES", "4")

    def fake_page(page: int, per_page: int):
        assert per_page == 100
        if page == 1:
            rows = [{"id": f"e{i}"} for i in range(100)]
        elif page == 2:
            rows = [{"id": f"e{i}"} for i in range(100, 120)]
        else:
            rows = []
        return {"events": rows}

    monkeypatch.setattr(matchbook_pagination, "_page_payload", fake_page)
    monkeypatch.setattr(
        matchbook_pagination,
        "decode_event",
        lambda row: {
            "event_id": row["id"],
            "home": "H",
            "away": "A",
            "start": None,
            "totals": {},
        },
    )
    rows = matchbook_pagination.fetch_events_paginated()
    assert len(rows) == 120
    assert rows[-1]["event_id"] == "e119"