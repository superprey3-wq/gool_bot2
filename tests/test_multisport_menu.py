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
    multisport_analysis_sections,
    sport_journal_text,
    sport_prematch_picks_sections,
    sport_overview_text,
    sport_phase_report_text,
    super10_history_text,
    super10_text,
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
    assert "GOOL MULTI · ЖУРНАЛ" in journal_text
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
    # Dedicated journals are aggregate scoreboards only. Individual pending
    # PREMATCH picks live in prematch views; active LIVE picks live in In Game.
    assert "SKA — CSKA" not in hockey
    assert "Denver — Utah" not in hockey
    assert "Denver — Utah" not in basket
    assert "SKA — CSKA" not in basket
    assert "PREMATCH" in hockey and "LIVE" in hockey
    assert "PREMATCH" in basket and "LIVE" in basket

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
    assert "GOOL MULTI · В ИГРЕ" in text
    assert "SKA — CSKA" in text
    assert "1:0" in text
    assert "2nd period" in text
    assert "ТБ 5.5 @ 1.85" in text
    assert "6+ шайб" in text

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
    assert "сейчас LIVE · 1:0" in text
    assert "Ф1 +1.5 @ 1.59" in text
    assert "может проиграть максимум в 1" in text


def test_multisport_in_game_recovers_legacy_pick_by_team_names(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    # Keep this unit test deterministic. The menu intentionally refreshes from
    # real Flashscore in production; without this stub a currently LIVE match
    # with the same teams can overwrite the fixture score during pytest.
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda _sport: [])

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


def test_multisport_in_game_fetches_fresh_flashscore_when_saved_state_is_empty(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {"matches": [], "flashscore_live_matches": []},
            "basketball": {"matches": [], "flashscore_live_matches": []},
        },
    })
    _write(journal, [{
        "sport": "basketball",
        "phase": "PREMATCH",
        "result": "pending",
        "flashscore_event_id": "FS-B1",
        "home": "Piratas de Bogota",
        "away": "Caimanes del Llano",
        "selection": "Ф2 +4.5",
        "market_family": "handicap",
        "selection_side": "away",
        "line": 4.5,
        "odd": 1.55,
        "strength": 100,
        "scope": "FULL_MATCH",
    }])

    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: [{
        "flashscore_event_id": "FS-B1",
        "home": "Piratas de Bogota",
        "away": "Caimanes del Llano",
        "score": [21, 24],
        "status_code": "Q2",
        "coarse_status": "2",
    }] if sport == "basketball" else [])

    text = "\n".join(menu.multisport_in_game_sections())
    assert "GOOL MULTI · В ИГРЕ" in text
    assert "Piratas de Bogota — Caimanes del Llano" in text
    assert "сейчас Q2 · 21:24" in text
    assert "Ф2 +4.5 @ 1.55" in text
    assert "100/100 · 🟡 PREMATCH" in text
    assert "может проиграть максимум в 4" in text


def test_multisport_analysis_includes_hockey_and_basketball_brain(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [{
                    "flashscore_event_id": "H1",
                    "home": "SKA", "away": "CSKA",
                    "score": [2, 1], "period": "2nd period",
                    "live_game_stats": {"segment_stats": {"shots_on_goal": [14, 8]}},
                    "signal": {"selection": "2-й период: ТБ 1.5", "odd": 1.82, "strength": 87},
                }]
            },
            "basketball": {
                "matches": [{
                    "flashscore_event_id": "B1",
                    "home": "Denver", "away": "Utah",
                    "score": [48, 44], "period": "2nd quarter",
                    "live_game_stats": {"segment_stats": {"rebounds": [18, 15], "turnovers": [4, 7]}},
                }]
            },
        },
    })

    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{"flashscore_event_id":"H1","home":"SKA","away":"CSKA","score":[2,1],"status_code":"2P"}]
        if sport == "hockey"
        else [{"flashscore_event_id":"B1","home":"Denver","away":"Utah","score":[48,44],"status_code":"Q2"}]
    ))

    text = "\n".join(multisport_analysis_sections())
    assert "ХОККЕЙ · АНАЛИЗ LIVE" in text
    assert "БАСКЕТБОЛ · АНАЛИЗ LIVE" in text
    assert "броски в створ 14:8" in text
    assert "SIGNAL" in text
    assert "2-й период: ТБ 1.5" in text
    assert "подборы 18:15" in text
    assert "WAIT" in text


def test_multisport_in_game_fuzzy_matches_and_deduplicates_legacy_rows(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(state, {"mode":"active","sports":{"hockey":{"matches":[]},"basketball":{"matches":[]}}})
    duplicate = {
        "sport":"basketball","phase":"PREMATCH","result":"pending",
        "home":"Piratas Bogota","away":"Caimanes Llano",
        "selection":"Ф2 +4.5","market_family":"handicap","selection_side":"away",
        "line":4.5,"odd":1.55,"strength":100,"scope":"FULL_MATCH",
    }
    _write(journal, [{**duplicate,"event_id":"old1"},{**duplicate,"event_id":"old2"}])
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{"flashscore_event_id":"LIVEB001","home":"Piratas de Bogota","away":"Caimanes del Llano","score":[22,24],"status_code":"Q2"}]
        if sport == "basketball" else []
    ))
    text = "\n".join(multisport_in_game_sections())
    assert text.count("Piratas Bogota — Caimanes Llano") == 1
    assert "22:24" in text

def test_multisport_analysis_shows_flashscore_brain_before_xbet_match(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [],
                "flashscore_analysis_matches": [{
                    "flashscore_event_id": "HFS001",
                    "home": "Oshawa Generals",
                    "away": "Sarnia Sting",
                    "score": [0, 1],
                    "period": "2-й период",
                    "brain_state": "BORDERLINE",
                    "brain_score": 64,
                    "brain_reason": "броски 21, темп бросков 2.3/мин, шайбы периода 1",
                    "live_game_stats": {"segment_stats": {"shots_on_goal": [11, 10]}},
                }],
            },
            "basketball": {"matches": [], "flashscore_analysis_matches": []},
        },
    })
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "HFS001",
            "home": "Oshawa Generals",
            "away": "Sarnia Sting",
            "score": [0, 1],
            "status_code": "46",
            "coarse_status": "2",
        }] if sport == "hockey" else []
    ))

    text = "\n".join(menu.multisport_analysis_sections())

    assert "Oshawa Generals — Sarnia Sting" in text
    assert "2-й период" in text
    assert "броски в створ 11:10" in text
    assert "BORDERLINE" in text
    assert "R64" in text
    assert "Brain уже выбрал матч" in text
    assert "рынок 1xBet ещё не синхронизирован" not in text

def test_multisport_analysis_shows_flashscore_brain_before_xbet_mapping(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [],
                "flashscore_analysis_matches": [{
                    "flashscore_event_id": "H-LIVE",
                    "home": "Oshawa Generals",
                    "away": "Sarnia Sting",
                    "score": [0, 1],
                    "period": "3-й период",
                    "scope": "PERIOD_3",
                    "brain_state": "PASS",
                    "brain_score": 78,
                    "projected_total": 2.4,
                    "brain_reason": "броски 19, темп бросков 2.8/мин, шайбы периода 1",
                    "live_game_stats": {
                        "segment_stats": {"shots_on_goal": [10, 9]}
                    },
                }],
            },
            "basketball": {"matches": [], "flashscore_analysis_matches": []},
        },
    })
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "H-LIVE",
            "home": "Oshawa Generals",
            "away": "Sarnia Sting",
            "score": [0, 1],
            "status_code": "46",
            "coarse_status": "2",
        }]
        if sport == "hockey" else []
    ))

    text = "\n".join(menu.multisport_analysis_sections())

    assert "Oshawa Generals — Sarnia Sting" in text
    assert "3-й период" in text
    assert "броски в створ 10:9" in text
    assert "PASS" in text
    assert "R78" in text
    assert "прогноз сегмента 2.4" in text
    assert "Brain уже выбрал матч" in text
    assert "46" not in text

def test_live_signal_goes_directly_to_in_game_and_journal_stays_summary_only(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [{
                    "flashscore_event_id": "H-LIVE-1",
                    "home": "Seattle Kraken",
                    "away": "Calgary Flames",
                    "score": [5, 0],
                    "period": "3-й период",
                }],
                "flashscore_live_matches": [{
                    "flashscore_event_id": "H-LIVE-1",
                    "home": "Seattle Kraken",
                    "away": "Calgary Flames",
                    "score": [5, 0],
                    "period": "3-й период",
                    "coarse_status": "2",
                }],
            },
            "basketball": {"matches": [], "flashscore_live_matches": []},
        },
    })
    _write(journal, [{
        "entry_id": "hockey:live:1:PERIOD_3:match_total",
        "sport": "hockey",
        "phase": "LIVE",
        "result": "pending",
        "flashscore_event_id": "H-LIVE-1",
        "home": "Seattle Kraken",
        "away": "Calgary Flames",
        "scope": "PERIOD_3",
        "market_family": "match_total",
        "selection": "3-й период: ТБ 0.5",
        "direction": "over",
        "line": 0.5,
        "odd": 1.85,
        "strength": 68,
        "period": "3-й период",
    }])
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "H-LIVE-1",
            "home": "Seattle Kraken",
            "away": "Calgary Flames",
            "score": [5, 0],
            "period": "3-й период",
            "coarse_status": "2",
        }] if sport == "hockey" else []
    ))

    in_game = "\n".join(menu.multisport_in_game_sections())
    journal_text = menu.hockey_journal_text()

    assert "Seattle Kraken — Calgary Flames" in in_game
    assert "3-й период: ТБ 0.5 @ 1.85" in in_game
    assert "🔴 LIVE" in in_game
    assert "Seattle Kraken — Calgary Flames" not in journal_text
    assert "🔴 <b>LIVE</b>" in journal_text


def test_finished_pick_is_not_listed_in_journal_or_in_game(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(state, {"mode":"active","sports":{"hockey":{"matches":[]},"basketball":{"matches":[]}}})
    _write(journal, [{
        "sport": "hockey",
        "phase": "LIVE",
        "result": "lost",
        "home": "Henderson Silver Knights",
        "away": "Bakersfield Condors",
        "selection": "3-й период: ТМ 1.5",
        "odd": 1.77,
        "profit_units": -1.0,
    }])
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda _sport: [])

    journal_text = menu.hockey_journal_text()
    assert "Henderson Silver Knights" not in journal_text
    assert "❌ 1" in journal_text
    assert menu.multisport_in_game_sections() == []

def test_sport_prematch_picks_only_issued_pending_future_and_sorted(tmp_path: Path, monkeypatch):
    import time

    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    now = time.time()
    _write(journal, [
        {
            "sport":"hockey","phase":"PREMATCH","result":"pending","event_id":"H-LATER",
            "home":"Later","away":"Away","league":"KHL","start_ts":now+7200,
            "selection":"Ф1 +1.5","odd":1.80,"strength":90,"scope":"FULL_MATCH",
        },
        {
            "sport":"hockey","phase":"PREMATCH","result":"pending","event_id":"H-SOONER",
            "home":"Sooner","away":"Away","league":"KHL","start_ts":now+1800,
            "selection":"ТБ 5.5","odd":1.60,"strength":85,"scope":"FULL_MATCH",
        },
        {
            "sport":"hockey","phase":"PREMATCH","result":"lost","event_id":"H-FINISHED",
            "home":"Finished","away":"Away","league":"KHL","start_ts":now+3600,
            "selection":"ТМ 5.5","odd":1.90,"strength":80,"scope":"FULL_MATCH",
        },
        {
            "sport":"hockey","phase":"PREMATCH","result":"pending","event_id":"H-STARTED",
            "home":"Already Started","away":"Away","league":"KHL","start_ts":now-60,
            "selection":"ТБ 4.5","odd":1.70,"strength":75,"scope":"FULL_MATCH",
        },
        {
            "sport":"basketball","phase":"PREMATCH","result":"pending","event_id":"B-OTHER",
            "home":"Other Sport","away":"Away","league":"NBA","start_ts":now+900,
            "selection":"ТБ 210.5","odd":1.75,"strength":88,"scope":"FULL_MATCH",
        },
    ])

    text = "\n".join(sport_prematch_picks_sections("hockey"))

    assert "Sooner — Away" in text
    assert "Later — Away" in text
    assert text.index("Sooner — Away") < text.index("Later — Away")
    assert "Finished" not in text
    assert "Already Started" not in text
    assert "Other Sport" not in text
    assert "ср. кэф <b>1.70</b>" in text


def test_journal_shows_average_odds_for_each_phase(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(journal, [
        {"sport":"basketball","phase":"PREMATCH","event_id":"B1","result":"won","odd":1.50,"profit_units":0.50},
        {"sport":"basketball","phase":"PREMATCH","event_id":"B2","result":"lost","odd":2.00,"profit_units":-1.00},
        {"sport":"basketball","phase":"LIVE","event_id":"B3","result":"pending","odd":1.80,"profit_units":0.0},
    ])

    text = basketball_journal_text()

    assert "ср. кэф 1.75" in text
    assert "ср. кэф 1.80" in text

def test_in_game_hides_pending_live_pick_after_its_period_is_over(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(state, {"mode":"active","sports":{"hockey":{"matches":[]},"basketball":{"matches":[]}}})
    _write(journal, [{
        "entry_id": "hockey:live:H1:PERIOD_1:match_total",
        "sport": "hockey",
        "phase": "LIVE",
        "result": "pending",
        "flashscore_event_id": "FSH1",
        "home": "Belye Medvedi",
        "away": "Omskie Yastreby",
        "scope": "PERIOD_1",
        "market_family": "match_total",
        "selection": "1-й период: ТБ 1.5",
        "direction": "over",
        "line": 1.5,
        "odd": 1.75,
        "strength": 68,
    }])
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id":"FSH1",
            "home":"Belye Medvedi",
            "away":"Omskie Yastreby",
            "league":"RUSSIA: MHL",
            "score":[1,1],
            "score_parts":[[0,1],[1,0]],
            "status_code":"27",
            "coarse_status":"2",
        }] if sport == "hockey" else []
    ))

    text = "\n".join(menu.multisport_in_game_sections())

    assert "1-й период: ТБ 1.5" not in text

def test_in_game_uses_brain_period_over_raw_numeric_status_and_drops_old_period_pick(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [],
                "flashscore_live_matches": [{
                    "flashscore_event_id": "FSMAG001",
                    "home": "Magnitogorsk",
                    "away": "Vladivostok",
                    "score": [0, 1],
                    "status_code": "15",
                    "coarse_status": "2",
                }],
                "flashscore_analysis_matches": [{
                    "flashscore_event_id": "FSMAG001",
                    "home": "Magnitogorsk",
                    "away": "Vladivostok",
                    "score": [0, 1],
                    "scope": "PERIOD_2",
                    "period": "2-й период",
                    "brain_state": "WAIT",
                    "brain_score": 32,
                }],
            },
            "basketball": {"matches": [], "flashscore_live_matches": [], "flashscore_analysis_matches": []},
        },
    })
    _write(journal, [
        {
            "entry_id": "hockey:live:H1:PERIOD_1:match_total",
            "sport": "hockey",
            "phase": "LIVE",
            "result": "pending",
            "flashscore_event_id": "FSMAG001",
            "home": "Magnitogorsk",
            "away": "Vladivostok",
            "scope": "PERIOD_1",
            "market_family": "match_total",
            "selection": "1-й период: ТБ 1",
            "direction": "over",
            "line": 1.0,
            "odd": 1.72,
            "strength": 66,
        },
        {
            "entry_id": "hockey:prematch:FSMAG001",
            "sport": "hockey",
            "phase": "PREMATCH",
            "result": "pending",
            "flashscore_event_id": "FSMAG001",
            "home": "Magnitogorsk",
            "away": "Vladivostok",
            "scope": "FULL_MATCH",
            "market_family": "handicap",
            "selection": "Ф2 +2",
            "selection_side": "away",
            "line": 2.0,
            "odd": 1.73,
            "strength": 100,
        },
    ])
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "FSMAG001",
            "home": "Magnitogorsk",
            "away": "Vladivostok",
            "score": [0, 1],
            "status_code": "15",
            "coarse_status": "2",
        }] if sport == "hockey" else []
    ))

    text = "\n".join(menu.multisport_in_game_sections())

    assert "сейчас 2-й период · 0:1" in text
    assert "сейчас 15" not in text
    assert "1-й период: ТБ 1 @ 1.72" not in text
    assert "Ф2 +2 @ 1.73" in text


def test_in_game_groups_live_and_prematch_bets_under_one_match(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {
                "matches": [],
                "flashscore_live_matches": [{
                    "flashscore_event_id": "FSKR001",
                    "home": "Kristall Saratov",
                    "away": "Metallurg Novokuznetsk",
                    "score": [0, 0],
                    "coarse_status": "2",
                }],
                "flashscore_analysis_matches": [{
                    "flashscore_event_id": "FSKR001",
                    "home": "Kristall Saratov",
                    "away": "Metallurg Novokuznetsk",
                    "score": [0, 0],
                    "scope": "PERIOD_1",
                    "period": "1-й период",
                }],
            },
            "basketball": {"matches": [], "flashscore_live_matches": [], "flashscore_analysis_matches": []},
        },
    })
    _write(journal, [
        {
            "entry_id": "hockey:live:K1:PERIOD_1:match_total",
            "sport": "hockey","phase": "LIVE","result": "pending",
            "flashscore_event_id": "FSKR001",
            "home": "Kristall Saratov","away": "Metallurg Novokuznetsk",
            "scope": "PERIOD_1","market_family": "match_total",
            "selection": "1-й период: ТБ 1","direction": "over","line": 1.0,
            "odd": 1.78,"strength": 67,
        },
        {
            "entry_id": "hockey:prematch:FSKR001",
            "sport": "hockey","phase": "PREMATCH","result": "pending",
            "flashscore_event_id": "FSKR001",
            "home": "Kristall Saratov","away": "Metallurg Novokuznetsk",
            "scope": "FULL_MATCH","market_family": "handicap",
            "selection": "Ф1 +1.5","selection_side": "home","line": 1.5,
            "odd": 1.61,"strength": 100,
        },
    ])
    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "FSKR001",
            "home": "Kristall Saratov",
            "away": "Metallurg Novokuznetsk",
            "score": [0, 0],
            "status_code": "15",
            "coarse_status": "2",
        }] if sport == "hockey" else []
    ))

    text = "\n".join(menu.multisport_in_game_sections())

    assert text.count("Kristall Saratov — Metallurg Novokuznetsk") == 1
    assert "🎯 <b>1-й период: ТБ 1 @ 1.78</b>" in text
    assert "🧠 67/100 · 🔴 LIVE" in text
    assert "🎯 <b>Ф1 +1.5 @ 1.61</b>" in text
    assert "🧠 100/100 · 🟡 PREMATCH" in text
    assert "Матчей: <b>1</b> · ставок: <b>2</b>" in text



def test_separate_sport_journal_is_grouped_by_date_with_all_time_total(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    monkeypatch.setenv("REPORT_TIMEZONE", "Europe/Moscow")
    _write(journal, [
        {
            "sport":"basketball","phase":"PREMATCH","event_id":"B-05-P",
            "created_at":"2026-10-05T10:00:00+00:00",
            "result":"won","odd":1.80,"profit_units":0.80,
        },
        {
            "sport":"basketball","phase":"LIVE","event_id":"B-05-L",
            "created_at":"2026-10-05T20:00:00+00:00",
            "result":"lost","odd":1.90,"profit_units":-1.00,
        },
        {
            "sport":"basketball","phase":"PREMATCH","event_id":"B-06-P",
            "created_at":"2026-10-06T02:30:00+00:00",
            "result":"won","odd":2.00,"profit_units":1.00,
        },
    ])

    text = basketball_journal_text()

    assert "05.10.2026" in text
    assert "06.10.2026" in text
    assert text.index("05.10.2026") < text.index("06.10.2026")
    assert text.count("ИТОГ ДНЯ") == 2
    assert "ИТОГО · ЗА ВСЁ ВРЕМЯ" in text
    assert "🌐 <b>ВСЕГО</b>" in text
    assert "✅ 2 · ❌ 1" in text


def test_phase_filtered_sport_journal_keeps_dates_and_phase_total(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    monkeypatch.setenv("REPORT_TIMEZONE", "Europe/Moscow")
    _write(journal, [
        {
            "sport":"hockey","phase":"PREMATCH","event_id":"H1",
            "created_at":"2026-10-05T12:00:00+00:00",
            "result":"won","odd":1.75,"profit_units":0.75,
        },
        {
            "sport":"hockey","phase":"LIVE","event_id":"H2",
            "created_at":"2026-10-06T12:00:00+00:00",
            "result":"lost","odd":1.70,"profit_units":-1.00,
        },
    ])

    text = hockey_journal_text(phase="LIVE")

    assert "05.10.2026" in text
    assert "06.10.2026" in text
    assert "ИТОГО · LIVE · ЗА ВСЁ ВРЕМЯ" in text
    assert "ИТОГ ДНЯ" not in text


def test_sport_journal_groups_prematch_by_match_date_not_creation_date(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    monkeypatch.setenv("REPORT_TIMEZONE", "Europe/Moscow")
    _write(journal, [
        {
            "sport":"basketball","phase":"PREMATCH","event_id":"B-NEXT-DAY",
            "created_at":"2026-10-05T20:00:00+00:00",
            "start_ts":1791266400,
            "scheduled_start_ts":1791266400,
            "result":"won","odd":1.80,"profit_units":0.80,
        }
    ])

    text = basketball_journal_text()

    assert "06.10.2026" in text
    assert "05.10.2026" not in text


def test_live_row_keeps_match_date_when_signal_is_after_midnight(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    monkeypatch.setenv("REPORT_TIMEZONE", "Europe/Moscow")
    _write(journal, [
        {
            "sport":"hockey","phase":"LIVE","event_id":"H-CROSS-MIDNIGHT",
            "created_at":"2026-10-06T22:30:00+00:00",
            "start_ts":1791309600,
            "scheduled_start_ts":1791309600,
            "result":"lost","odd":1.75,"profit_units":-1.00,
        }
    ])

    text = hockey_journal_text()

    assert "06.10.2026" in text



def test_hockey_and_basketball_journals_have_separate_express_sections(tmp_path: Path, monkeypatch):
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    _write(journal, [
        {
            "entry_id": "h-parlay-1",
            "sport": "hockey",
            "phase": "PREMATCH",
            "origin": "multisport_parlay",
            "signal_type": "prematch_parlay",
            "market_family": "parlay",
            "scope": "MULTI_MATCH",
            "created_at": "2026-10-06T10:00:00+00:00",
            "selection": "Экспресс ×2",
            "odd": 2.40,
            "result": "won",
            "profit_units": 1.40,
            "telegram_sent": True,
        },
        {
            "entry_id": "b-parlay-1",
            "sport": "basketball",
            "phase": "PREMATCH",
            "origin": "multisport_parlay",
            "signal_type": "prematch_parlay",
            "market_family": "parlay",
            "scope": "MULTI_MATCH",
            "created_at": "2026-10-06T11:00:00+00:00",
            "selection": "Экспресс ×2",
            "odd": 2.25,
            "result": "lost",
            "profit_units": -1.0,
            "telegram_sent": True,
        },
        {
            "sport": "hockey",
            "phase": "PREMATCH",
            "event_id": "H-SINGLE",
            "created_at": "2026-10-06T09:00:00+00:00",
            "result": "won",
            "odd": 1.70,
            "profit_units": 0.70,
        },
        {
            "sport": "basketball",
            "phase": "LIVE",
            "event_id": "B-LIVE",
            "created_at": "2026-10-06T12:00:00+00:00",
            "result": "won",
            "odd": 1.80,
            "profit_units": 0.80,
        },
    ])

    hockey = hockey_journal_text()
    basket = basketball_journal_text()

    assert "🔗 <b>ЭКСПРЕССЫ</b>" in hockey
    assert "🔗 <b>ЭКСПРЕССЫ</b>" in basket
    assert "✅ 1 · ❌ 0" in hockey
    assert "✅ 0 · ❌ 1" in basket

    # Express parents must not inflate the ordinary PREMATCH/LIVE buckets.
    hockey_day = hockey.split("📅 <b>06.10.2026</b>", 1)[1].split("━━━━━━━━━━━━━━", 1)[0]
    assert "🟡 <b>PREMATCH</b>\n✅ 1 · ❌ 0" in hockey_day
    basket_day = basket.split("📅 <b>06.10.2026</b>", 1)[1].split("━━━━━━━━━━━━━━", 1)[0]
    assert "🔴 <b>LIVE</b>\n✅ 1 · ❌ 0" in basket_day


def test_prematch_pick_list_does_not_show_parlay_parent(tmp_path: Path, monkeypatch):
    import time

    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    now = time.time()
    _write(journal, [
        {
            "entry_id": "h-parlay-parent",
            "sport": "hockey",
            "phase": "PREMATCH",
            "origin": "multisport_parlay",
            "signal_type": "prematch_parlay",
            "market_family": "parlay",
            "scope": "MULTI_MATCH",
            "result": "pending",
            "selection": "Экспресс ×2",
            "home": "ЭКСПРЕСС",
            "away": "HOCKEY",
            "start_ts": now + 3600,
            "odd": 2.40,
            "strength": 82,
        },
        {
            "sport": "hockey",
            "phase": "PREMATCH",
            "result": "pending",
            "event_id": "H1",
            "home": "SKA",
            "away": "CSKA",
            "start_ts": now + 1800,
            "selection": "ТМ 5.5",
            "odd": 1.65,
            "strength": 88,
            "scope": "FULL_MATCH",
        },
    ])

    text = "\n".join(sport_prematch_picks_sections("hockey"))

    assert "SKA — CSKA" in text
    assert "ЭКСПРЕСС — HOCKEY" not in text
    assert "Экспресс ×2" not in text



def test_multisport_status_shows_super10_readiness(tmp_path: Path, monkeypatch):
    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))
    _write(journal, [])
    _write(state, {
        "mode": "active",
        "global_super10": {
            "status": "not_ready",
            "target": 10,
            "strict": 5,
            "reserve_extra": 2,
            "available": 7,
            "need_more": 3,
            "available_by_sport": {"football": 3, "hockey": 2, "basketball": 2},
            "missing_sports": [],
        },
        "sports": {
            "hockey": {"enabled": True},
            "basketball": {"enabled": True},
        },
    })

    text = multisport_status_text()

    assert "SUPER 10" in text
    assert "not_ready" in text
    assert "готово 7/10" in text
    assert "strict 5" in text
    assert "reserve +2" in text
    assert "⚽ 3 · 🏒 2 · 🏀 2" in text



def test_super10_interactive_text_shows_readiness_and_sent_ticket(tmp_path: Path, monkeypatch):
    import gool_bot2.global_super10 as gs

    sent = tmp_path / "super10_sent.json"
    history = tmp_path / "super10_history.json"
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_SENT_PATH", str(sent))
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_HISTORY_PATH", str(history))
    monkeypatch.setattr(
        gs,
        "readiness_snapshot",
        lambda: {
            "target": 10,
            "strict": 6,
            "reserve_extra": 4,
            "available": 10,
            "available_by_sport": {"football": 4, "hockey": 3, "basketball": 3},
            "missing_sports": [],
            "need_more": 0,
        },
    )
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d")
    _write(sent, {
        "day": today,
        "sent": True,
        "ticket": {
            "combined_odds": 31.5,
            "legs": [
                {
                    "sport": "football",
                    "home": "A",
                    "away": "B",
                    "selection": "ТБ 1.5",
                    "odd": 1.50,
                    "super_tier": "strict",
                }
            ],
        },
    })
    _write(history, [
        {
            "day": "2026-10-06",
            "combined_odds": 31.5,
            "sport_counts": {"football": 4, "hockey": 3, "basketball": 3},
        }
    ])

    current = super10_text()
    archive = super10_history_text()

    assert "Готово: <b>10/10</b>" in current
    assert "СЕГОДНЯ SUPER 10 УЖЕ ОТПРАВЛЕН" in current
    assert "A — B" in current
    assert "ТБ 1.5 @ 1.50" in current
    assert "2026-10-06" in archive
    assert "31.50" in archive


def test_in_game_keeps_already_issued_basketball_live_rows_visible(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {"matches": [], "flashscore_live_matches": []},
            "basketball": {
                "matches": [],
                "flashscore_live_matches": [{
                    "flashscore_event_id": "FS-BASKET-1",
                    "home": "Murcia",
                    "away": "Varese",
                    "score": [79, 53],
                    "status_code": "Q4",
                    "coarse_status": "2",
                }],
                "flashscore_analysis_matches": [{
                    "flashscore_event_id": "FS-BASKET-1",
                    "home": "Murcia",
                    "away": "Varese",
                    "score": [79, 53],
                    "scope": "QUARTER_4",
                    "period": "4-я четверть",
                }],
            },
        },
    })

    _write(journal, [
        {
            "entry_id": "basketball:live:old-total",
            "sport": "basketball",
            "phase": "LIVE",
            "result": "pending",
            "flashscore_event_id": "FS-BASKET-1",
            "home": "Murcia",
            "away": "Varese",
            "scope": "FULL_MATCH",
            "market_family": "match_total",
            "selection": "ТМ 155.5",
            "odd": 1.88,
            "strength": 75,
        },
        {
            "entry_id": "basketball:live:old-team",
            "sport": "basketball",
            "phase": "LIVE",
            "result": "pending",
            "flashscore_event_id": "FS-BASKET-1",
            "home": "Murcia",
            "away": "Varese",
            "scope": "FULL_MATCH",
            "market_family": "home_total",
            "selection": "ИТМ1 84.5",
            "odd": 1.81,
            "strength": 85,
        },
        {
            "entry_id": "basketball:live:q4",
            "sport": "basketball",
            "phase": "LIVE",
            "result": "pending",
            "flashscore_event_id": "FS-BASKET-1",
            "home": "Murcia",
            "away": "Varese",
            "scope": "QUARTER_4",
            "market_family": "match_total",
            "selection": "4-я четверть: ТМ 42.5",
            "odd": 1.90,
            "strength": 82,
        },
    ])

    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "FS-BASKET-1",
            "home": "Murcia",
            "away": "Varese",
            "score": [79, 53],
            "status_code": "Q4",
            "coarse_status": "2",
        }]
        if sport == "basketball" else []
    ))

    text = "\n".join(menu.multisport_in_game_sections())

    assert "4-я четверть: ТМ 42.5 @ 1.90" in text
    assert "ТМ 155.5 @ 1.88" in text
    assert "ИТМ1 84.5 @ 1.81" in text
    assert "ставок: <b>3</b>" in text


def test_in_game_keeps_started_basketball_prematch_full_match_pick(tmp_path: Path, monkeypatch):
    import gool_bot2.multisport_menu as menu

    state = tmp_path / "state.json"
    journal = tmp_path / "journal.json"
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(state))
    monkeypatch.setenv("GOOL_MULTISPORT_JOURNAL", str(journal))

    _write(state, {
        "mode": "active",
        "sports": {
            "hockey": {"matches": [], "flashscore_live_matches": []},
            "basketball": {"matches": [], "flashscore_live_matches": []},
        },
    })
    _write(journal, [{
        "sport": "basketball",
        "phase": "PREMATCH",
        "result": "pending",
        "flashscore_event_id": "FS-BASKET-2",
        "home": "Paris",
        "away": "Lyon",
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "selection": "ТБ 170.5",
        "odd": 1.75,
        "strength": 88,
    }])

    monkeypatch.setattr(menu, "_direct_flashscore_live", lambda sport: (
        [{
            "flashscore_event_id": "FS-BASKET-2",
            "home": "Paris",
            "away": "Lyon",
            "score": [40, 38],
            "status_code": "Q2",
            "coarse_status": "2",
        }]
        if sport == "basketball" else []
    ))

    text = "\n".join(menu.multisport_in_game_sections())

    assert "Paris — Lyon" in text
    assert "ТБ 170.5 @ 1.75" in text
    assert "🟡 PREMATCH" in text
