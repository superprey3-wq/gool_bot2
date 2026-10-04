from pathlib import Path

from gool_bot2.multisport_journal import append_unique, grouped_stats, load_journal


def test_legacy_rows_are_migrated_to_journal_v2_live(tmp_path: Path):
    path = tmp_path / "journal.json"
    path.write_text(
        '[{"sport":"hockey","event_id":"1","direction":"over","line":5.5,"odd":1.8,"result":"won","profit_units":0.8}]',
        encoding="utf-8",
    )
    rows = load_journal(path)
    assert rows[0]["journal_version"] == 2
    assert rows[0]["phase"] == "LIVE"
    assert rows[0]["signal_type"] == "live_total_movement"
    assert rows[0]["market_family"] == "match_total"
    assert rows[0]["selection"] == "ТБ 5.5"


def test_prematch_and_live_same_event_are_separate_entries(tmp_path: Path):
    path = tmp_path / "journal.json"
    base = {
        "sport": "basketball",
        "event_id": "22",
        "market_family": "match_total",
        "direction": "under",
        "line": 221.5,
        "odd": 1.85,
    }
    assert append_unique(path, {**base, "phase": "PREMATCH", "selection": "ТМ 221.5"})
    assert append_unique(path, {**base, "phase": "LIVE", "selection": "ТМ 221.5"})
    assert not append_unique(path, {**base, "phase": "LIVE", "selection": "ТМ 219.5", "line": 219.5})
    assert len(load_journal(path)) == 2


def test_grouped_stats_keep_prematch_and_live_separate(tmp_path: Path):
    path = tmp_path / "journal.json"
    append_unique(path, {
        "sport": "hockey", "phase": "PREMATCH", "event_id": "1",
        "direction": "over", "line": 5.5, "odd": 1.8,
        "result": "won", "profit_units": 0.8,
    })
    append_unique(path, {
        "sport": "hockey", "phase": "LIVE", "event_id": "1",
        "direction": "over", "line": 6.5, "odd": 1.9,
        "result": "lost", "profit_units": -1.0,
    })
    value = grouped_stats(load_journal(path))
    assert value["by_sport"]["hockey"]["by_phase"]["PREMATCH"]["won"] == 1
    assert value["by_sport"]["hockey"]["by_phase"]["LIVE"]["lost"] == 1
    assert round(value["all"]["profit_units"], 2) == -0.2



def test_same_event_same_phase_can_store_different_period_scopes(tmp_path: Path):
    path = tmp_path / "journal.json"
    base = {
        "sport": "hockey", "phase": "PREMATCH", "event_id": "77",
        "market_family": "match_total", "direction": "over", "line": 1.5, "odd": 1.8,
    }
    assert append_unique(path, {**base, "scope": "PERIOD_1", "selection": "1-й период: ТБ 1.5"})
    assert append_unique(path, {**base, "scope": "PERIOD_2", "selection": "2-й период: ТБ 1.5"})
    assert len(load_journal(path)) == 2


def test_same_flashscore_match_dedupes_rotating_xbet_event_ids(tmp_path: Path):
    path = tmp_path / "journal.json"
    first = {
        "sport": "basketball", "phase": "PREMATCH",
        "event_id": "xbet-111", "flashscore_event_id": "fsABC123",
        "scope": "FULL_MATCH", "market_family": "handicap",
        "selection": "Ф2 +4.5", "selection_side": "away",
        "line": 4.5, "odd": 1.55,
    }
    second = {
        **first,
        "event_id": "xbet-222",
        "odd": 1.57,
    }
    assert append_unique(path, first)
    assert not append_unique(path, second)
    rows = load_journal(path)
    assert len(rows) == 1
    assert rows[0]["flashscore_event_id"] == "fsABC123"


def test_load_journal_collapses_legacy_duplicate_rotating_xbet_ids(tmp_path: Path):
    path = tmp_path / "journal.json"
    path.write_text(
        """[
          {"sport":"basketball","phase":"PREMATCH","event_id":"a","flashscore_event_id":"fs1","scope":"FULL_MATCH","market_family":"handicap","selection":"Ф2 +4.5","line":4.5,"odd":1.55,"created_at":"2026-10-04T19:00:00+00:00"},
          {"sport":"basketball","phase":"PREMATCH","event_id":"b","flashscore_event_id":"fs1","scope":"FULL_MATCH","market_family":"handicap","selection":"Ф2 +4.5","line":4.5,"odd":1.55,"created_at":"2026-10-04T19:05:00+00:00"}
        ]""",
        encoding="utf-8",
    )
    rows = load_journal(path)
    assert len(rows) == 1
    assert rows[0]["event_id"] == "b"
