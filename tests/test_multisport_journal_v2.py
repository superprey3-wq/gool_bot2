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
