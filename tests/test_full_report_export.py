from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.full_report_export import build_full_report


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_full_report_contains_public_singles_parlays_results_and_super10(tmp_path: Path):
    football = tmp_path / "football.json"
    multisport = tmp_path / "multisport.json"
    super10 = tmp_path / "super10.json"
    created = "2026-10-06T15:00:00+00:00"

    _write(football, [
        {
            "entry_id": "f-live-1",
            "created_at": created,
            "telegram_sent": True,
            "mode": "active",
            "origin": "live",
            "strategy": "another_goal",
            "home": "Alpha",
            "away": "Beta",
            "league": "Test League",
            "market": "ТБ 2.5",
            "odd": 1.80,
            "result": "won",
            "profit_units": 0.80,
            "settled_score": [2, 1],
        },
        {
            "entry_id": "f-parlay-1",
            "created_at": created,
            "telegram_sent": True,
            "mode": "active",
            "origin": "prematch_parlay",
            "market_family": "parlay",
            "odd": 2.40,
            "result": "lost",
            "profit_units": -1.0,
            "legs": [
                {
                    "home": "Gamma",
                    "away": "Delta",
                    "selection": "ТБ 1.5",
                    "odd": 1.50,
                    "result": "won",
                },
                {
                    "home": "Epsilon",
                    "away": "Zeta",
                    "selection": "ТМ 3.5",
                    "odd": 1.60,
                    "result": "lost",
                },
            ],
        },
        {
            "entry_id": "f-hidden",
            "created_at": created,
            "telegram_sent": False,
            "mode": "active",
            "home": "Hidden",
            "away": "Football",
            "market": "ТБ 0.5",
            "odd": 1.10,
            "result": "won",
        },
    ])

    _write(multisport, [
        {
            "entry_id": "h-single",
            "created_at": created,
            "telegram_sent": True,
            "sport": "hockey",
            "phase": "PREMATCH",
            "home": "SKA",
            "away": "CSKA",
            "league": "KHL",
            "selection": "ТБ 5.5",
            "odd": 1.70,
            "result": "won",
            "profit_units": 0.70,
            "settled_score": [4, 2],
        },
        {
            "entry_id": "b-live",
            "created_at": created,
            "telegram_sent": True,
            "sport": "basketball",
            "phase": "LIVE",
            "home": "Denver",
            "away": "Utah",
            "league": "NBA",
            "selection": "4-я четверть: ТМ 52.5",
            "odd": 1.85,
            "result": "lost",
            "profit_units": -1.0,
            "settled_score": [30, 28],
        },
        {
            "entry_id": "h-parlay",
            "created_at": created,
            "telegram_sent": True,
            "sport": "hockey",
            "phase": "PREMATCH",
            "origin": "multisport_parlay",
            "market_family": "parlay",
            "selection": "Экспресс ×2",
            "odd": 2.55,
            "result": "pending",
            "legs": [
                {
                    "home": "Team One",
                    "away": "Team Two",
                    "selection": "ТМ 6.5",
                    "odd": 1.60,
                    "result": "pending",
                },
                {
                    "home": "Team Three",
                    "away": "Team Four",
                    "selection": "Ф1 +1.5",
                    "odd": 1.59,
                    "result": "pending",
                },
            ],
        },
        {
            "entry_id": "b-hidden",
            "created_at": created,
            "telegram_sent": False,
            "sport": "basketball",
            "phase": "PREMATCH",
            "home": "Hidden",
            "away": "Basket",
            "selection": "ТБ 200.5",
            "odd": 1.90,
            "result": "won",
        },
    ])

    _write(super10, [
        {
            "day": "2026-10-06",
            "kind": "GLOBAL_SUPER",
            "result": "pending",
            "combined_odds": 24.50,
            "legs": [
                {
                    "sport": "football",
                    "home": "Super A",
                    "away": "Super B",
                    "selection": "ТБ 1.5",
                    "odd": 1.45,
                    "super_tier": "strict",
                }
            ],
        }
    ])

    filename, payload, caption = build_full_report(
        football_path=football,
        multisport_path=multisport,
        super10_history_path=super10,
        now=datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc),
    )
    text = payload.decode("utf-8")

    assert filename.startswith("GOOL_FULL_REPORT_2026-10-06_")
    assert "GOOL BOT · ПОЛНЫЙ ОТЧЁТ" in text
    assert "Alpha — Beta" in text
    assert "SKA — CSKA" in text
    assert "Denver — Utah" in text
    assert "Gamma — Delta" in text
    assert "Team One — Team Two" in text
    assert "SUPER 10" in text
    assert "Super A" in text
    assert "✅ ЗАШЛО" in text
    assert "❌ НЕ ЗАШЛО" in text
    assert "Hidden" not in text
    assert "Ставок: <b>5</b>" in caption


def test_full_report_handles_empty_files(tmp_path: Path):
    football = tmp_path / "football.json"
    multisport = tmp_path / "multisport.json"
    super10 = tmp_path / "super10.json"
    _write(football, [])
    _write(multisport, [])
    _write(super10, [])

    filename, payload, caption = build_full_report(
        football_path=football,
        multisport_path=multisport,
        super10_history_path=super10,
        now=datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc),
    )

    assert filename.endswith(".html")
    text = payload.decode("utf-8")
    assert "Нет записей." in text
    assert "SUPER 10 пока не отправлялся." in text
    assert "Ставок: <b>0</b>" in caption
