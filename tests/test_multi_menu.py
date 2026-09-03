from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.journal import save_signal_journal
from gool_bot2.multi_menu import analysis_text, report_text


def test_multi_report_counts_only_multi_entries(tmp_path: Path, monkeypatch):
    journal = tmp_path / "multi.json"
    analysis = tmp_path / "multi.jsonl"
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("GOOL_MULTI_ANALYSIS_PATH", str(analysis))
    now = datetime.now(timezone.utc).isoformat()
    save_signal_journal(
        journal,
        [
            {
                "created_at": now,
                "match_id": "a",
                "strategy": "another_goal",
                "result": "won",
                "profit_units": 0.80,
                "signal_source": "GOOL",
            },
            {
                "created_at": now,
                "match_id": "b",
                "strategy": "home_goal",
                "result": "lost",
                "profit_units": -1.0,
                "signal_source": "MARKET_OVERRIDE",
            },
        ],
    )
    text = report_text()
    assert "GOOL MULTI · ЖУРНАЛ" in text
    assert "P/L <b>-0.20u</b>" in text
    assert "проход <b>50.0%</b>" in text
    assert "Override-входов: <b>1</b>" in text


def test_multi_analysis_shows_model_prematch_live_and_market(tmp_path: Path, monkeypatch):
    journal = tmp_path / "multi.json"
    analysis = tmp_path / "multi.jsonl"
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("GOOL_MULTI_ANALYSIS_PATH", str(analysis))
    row = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "match_id": "abc",
        "home": "Home",
        "away": "Away",
        "minute": 64,
        "score": [1, 0],
        "experts": {
            "another_goal": {"probability": 0.66, "passed": False},
            "home_goal": {"probability": 0.72, "passed": False},
        },
        "context": {
            "prematch": {"available": True, "home_recent": 10, "away_recent": 10},
            "live": {"xg_total": 1.84, "shots_total": 19, "sot_total": 7},
        },
        "router": {
            "status": "BET",
            "reason": "override won ranking",
            "winner": {
                "label": "ИТБ1 1.5",
                "odd": 4.76,
                "rating": 84,
                "value_edge_pp": 12.4,
                "market_pressure_pp": 6.4,
                "market_override": True,
                "value_override": False,
            },
            "rejected": [],
        },
    }
    analysis.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    text = analysis_text()
    assert "MODEL + PREMATCH + LIVE + 1xBet" in text
    assert "AG 66×" in text
    assert "PRE 10/10" in text
    assert "LIVE xG 1.84" in text
    assert "ИТБ1 1.5 @ 4.76" in text
    assert "override: <b>1</b>" in text
