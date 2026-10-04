from __future__ import annotations

import json
from pathlib import Path

from gool_bot2.multisport_menu import (
    basketball_journal_text,
    hockey_journal_text,
    multisport_report_text,
    multisport_status_text,
    _pick_needed_text,
    multisport_in_game_sections,
    sport_journal_text,
    sport_overview_text,
    sport_phase_report_text,
)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_multisport_menu_reads_shared_state_and_journal(tmp_path: Path, monkeypatch):
    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "shadow",
        "sports": {
            "hockey": {
                "flashscore_prematch": 3,
                "prematch_decoded": 2,
                "prematch_detected": 1,
                "prematch_matches": [{
                    "home": "SKA",
                    "away": "CSKA",
                    "league": "KHL",
                    "start_ts": 1893456000,
                    "line": 5.5,
                    "over": 1.85,
                    "under": 1.95,
                }],
                "flashscore_live": 4,
                "xbet_live": 3,
                "mapped": 2,
                "decoded": 2,
                "detected": 1,
                "matches": [{
                    "home": "Boston",
                    "away": "Rangers",
                    "score": [2, 1],
                    "period": "3rd",
                    "line": 6.5,
                    "over": 1.82,
                    "under": 1.98,
                }],
            },
            "basketball": {
                "flashscore_prematch": 6,
                "prematch_decoded": 3,
                "prematch_detected": 0,
                "prematch_matches": [],
                "flashscore_live": 8,
                "xbet_live": 7,
                "mapped": 5,
                "decoded": 5,
                "detected": 0,
                "matches": [],
            },
        },
    })
    _write(journal, [
        {"sport": "hockey", "phase": "PREMATCH", "result": "won", "profit_units": 0.8},
        {"sport": "hockey", "phase": "LIVE", "result": "lost", "profit_units": -1.0},
        {"sport": "basketball", "phase": "PREMATCH", "result": "pending", "profit_units": 0.0},
    ])

    status = multisport_status_text()
    assert "ХОККЕЙ" in status
    assert "БАСКЕТБОЛ" in status
    assert "SHADOW" in status
    assert "1xBet 3" in status
    assert "mapped 2" in status
    assert "decoded 2" in status

    hockey = sport_overview_text("hockey")
    assert "Boston — Rangers" in hockey
    assert "SKA — CSKA" in hockey
    assert "Boston — Rangers" in hockey
    assert "ТБ 1.82 / ТМ 1.98" in hockey
    assert "PREMATCH журнал" in hockey
    assert "LIVE журнал" in hockey
    assert "РАЗДЕЛЕНИЕ РЫНКОВ" in hockey
    assert "Только ТБ/ТМ текущего периода" in hockey

    report = multisport_report_text()
    assert "P/L +0.80u" in report
    assert "P/L -1.00u" in report
    assert "PREMATCH и LIVE считаются отдельно" in report

    journal_text = sport_journal_text()
    assert "ЖУРНАЛ СИГНАЛОВ" in journal_text
    assert "PREMATCH" in journal_text
    assert "LIVE" in journal_text



def test_separate_hockey_and_basketball_journal_views(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(journal, [
        {
            "sport": "hockey", "phase": "PREMATCH", "event_id": "h1",
            "home": "SKA", "away": "CSKA", "scope": "PERIOD_2",
            "selection": "2-й период: ТБ 1.5", "odd": 1.85, "strength": 80,
        },
        {
            "sport": "basketball", "phase": "LIVE", "event_id": "b1",
            "home": "Denver", "away": "Utah", "scope": "QUARTER_3",
            "selection": "3-я четверть: ТБ 52.5", "odd": 1.90, "strength": 84,
            "score": [72, 70], "match_score": [72, 70], "period": "3rd quarter",
        },
    ])
    hockey = hockey_journal_text()
    basket = basketball_journal_text()
    assert "SKA — CSKA" in hockey
    assert "Denver — Utah" not in hockey
    assert "2-й период" in hockey
    assert "Denver — Utah" in basket
    assert "SKA — CSKA" not in basket
    assert "3-я четверть" in basket

    hreport = sport_phase_report_text("hockey")
    breport = sport_phase_report_text("basketball")
    assert "ОТДЕЛЬНЫЙ ОТЧЁТ" in hreport
    assert "Все рынки до матча" in hreport
    assert "Все рынки до матча" in breport


def test_started_multisport_prematch_moves_into_in_game_view(tmp_path: Path, monkeypatch):
    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [{
                    "flashscore_event_id": "FS-H1",
                    "home": "SKA",
                    "away": "CSKA",
                    "score": [1, 0],
                    "period": "2nd period",
                }]
            },
            "basketball": {"matches": []},
        },
    })
    _write(journal, [{
        "sport": "hockey",
        "phase": "PREMATCH",
        "result": "pending",
        "flashscore_event_id": "FS-H1",
        "home": "SKA",
        "away": "CSKA",
        "selection": "ТБ 5.5",
        "odd": 1.85,
    }])

    sections = multisport_in_game_sections()
    text = "\n".join(sections)
    assert "ХОККЕЙ / БАСКЕТБОЛ · В ИГРЕ" in text
    assert "SKA — CSKA" in text
    assert "1:0" in text
    assert "2nd period" in text
    assert "ТБ 5.5 @ 1.85" in text

    _write(journal, [{
        "sport": "hockey",
        "phase": "PREMATCH",
        "result": "won",
        "flashscore_event_id": "FS-H1",
        "home": "SKA",
        "away": "CSKA",
        "selection": "ТБ 5.5",
        "odd": 1.85,
    }])
    assert multisport_in_game_sections() == []


def test_multisport_in_game_needed_result_text_matches_settlement_rules():
    assert "6+ шайб" in _pick_needed_text({
        "sport":"hockey","market_family":"match_total","direction":"over","line":5.5,"scope":"FULL_MATCH",
    })
    text = _pick_needed_text({
        "sport":"basketball","market_family":"home_total","direction":"over","line":93.0,"scope":"FULL_MATCH",
    })
    assert "94+ очков" in text
    assert "ровно 93 — возврат" in text

    plus = _pick_needed_text({
        "sport":"hockey","market_family":"handicap","selection_side":"home","line":1.5,"scope":"FULL_MATCH",
    })
    assert "может проиграть максимум в 1" in plus

    minus = _pick_needed_text({
        "sport":"hockey","market_family":"handicap","selection_side":"home","line":-1.5,"scope":"FULL_MATCH",
    })
    assert "должна выиграть минимум в 2" in minus


def test_multisport_in_game_uses_raw_flashscore_live_even_when_xbet_mapping_is_zero(tmp_path: Path, monkeypatch):
    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "flashscore_live": 1,
                "xbet_live": 0,
                "mapped": 0,
                "matches": [],
                "flashscore_live_matches": [{
                    "flashscore_event_id": "FS-RAW-H1",
                    "home": "Krylya Sovetov",
                    "away": "Mikhaylov Academy U20",
                    "league": "MHL",
                    "score": [1, 0],
                    "coarse_status": "2",
                    "status_code": "LIVE",
                }],
            },
            "basketball": {
                "flashscore_live": 0,
                "xbet_live": 0,
                "mapped": 0,
                "matches": [],
                "flashscore_live_matches": [],
            },
        },
    })
    _write(journal, [{
        "sport": "hockey",
        "phase": "PREMATCH",
        "result": "pending",
        "flashscore_event_id": "FS-RAW-H1",
        "home": "Krylya Sovetov",
        "away": "Mikhaylov Academy U20",
        "selection": "Ф1 +1.5",
        "market_family": "handicap",
        "selection_side": "home",
        "line": 1.5,
        "odd": 1.59,
        "scope": "FULL_MATCH",
    }])

    text = "\n".join(multisport_in_game_sections())
    assert "Krylya Sovetov — Mikhaylov Academy U20" in text
    assert "1:0" in text
    assert "LIVE · Flashscore" in text
    assert "Ф1 +1.5 @ 1.59" in text
    assert "может проиграть максимум в 1" in text


def test_multisport_in_game_recovers_legacy_pick_by_team_names(tmp_path: Path, monkeypatch):
    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "flashscore_live_matches": [{
                    "flashscore_event_id": "LIVE1234",
                    "home": "Krylya Sovetov",
                    "away": "Mikhaylov Academy U20",
                    "league": "MHL",
                    "score": [1, 1],
                    "coarse_status": "2",
                }],
                "matches": [],
            },
            "basketball": {
                "flashscore_live_matches": [],
                "matches": [],
            },
        },
    })

    _write(journal, [{
        "sport": "hockey",
        "phase": "PREMATCH",
        "result": "pending",
        "flashscore_event_id": "OLD00000",
        "home": "Krylya Sovetov",
        "away": "Mikhaylov Academy U20",
        "selection": "Ф1 +1.5",
        "market_family": "handicap",
        "selection_side": "home",
        "line": 1.5,
        "odd": 1.59,
        "scope": "FULL_MATCH",
    }])

    text = "\n".join(multisport_in_game_sections())
    assert "Krylya Sovetov — Mikhaylov Academy U20" in text
    assert "1:1" in text
    assert "Ф1 +1.5 @ 1.59" in text
