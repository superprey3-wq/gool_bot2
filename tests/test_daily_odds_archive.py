from pathlib import Path

import pytest

from gool_bot2.daily_odds_archive import DailyOddsArchive, match_day, select_due_events


@pytest.fixture()
def archive(tmp_path: Path):
    instance = DailyOddsArchive(tmp_path / "daily.sqlite")
    yield instance
    instance.close()


@pytest.mark.parametrize("sport", ["football", "hockey", "basketball"])
def test_records_full_raw_market_tree_and_changes_across_restarts(archive, sport):
    now = 1791500000.0
    fs = {
        "flashscore_event_id": "FS01",
        "start_ts": now + 9 * 3600,
        "home": "A",
        "away": "B",
    }
    market = {
        "FULL_MATCH": {"raw": [
            {"G": 17, "GS": None, "T": 9, "P": 2.5, "C": 1.90},
            {"G": 17, "GS": None, "T": 10, "P": 2.5, "C": 1.95},
            {"G": 9999, "GS": 1, "T": 888, "P": None, "C": 2.10},
        ]},
        "PERIOD_1": {"raw": [
            {"G": 17, "GS": None, "T": 9, "P": 0.5, "C": 1.75},
        ]},
    }
    first = archive.record(sport=sport, event_id="B001", fs=fs, decoded=market, ts=now)
    assert first == {"quotes": 4, "changes": 0}
    day = match_day(fs["start_ts"])
    assert archive.coverage(sport, day) == {"archived_matches": 1, "latest_quotes": 4}
    assert archive.last_seen(sport, ["B001"]) == {"B001": now}

    second = archive.record(sport=sport, event_id="B001", fs=fs, decoded=market, ts=now + 100)
    assert second == {"quotes": 4, "changes": 0}
    assert archive.db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 4

    market["FULL_MATCH"]["raw"][0]["C"] = 1.72
    result = archive.record(sport=sport, event_id="B001", fs=fs, decoded=market, ts=now + 200)
    assert result == {"quotes": 4, "changes": 1}
    reasons = [row[0] for row in archive.db.execute("SELECT reason FROM price_history")]
    assert reasons.count("open") == 4
    assert reasons.count("change") == 1

    archive.record(sport=sport, event_id="B001", fs=fs, decoded=market, ts=now + 2200)
    assert archive.db.execute(
        "SELECT COUNT(*) FROM price_history WHERE reason='heartbeat'"
    ).fetchone()[0] == 4


def test_round_robin_not_nearest_only():
    now = 1791500000.0
    mapped = [
        ({"I": str(n)}, {"start_ts": now + (n + 1) * 3600}, False, 0.95)
        for n in range(20)
    ]
    earlier = {str(n): now - 100 for n in range(6)}
    selected = select_due_events(mapped, earlier, now=now, limit=5, refresh_seconds=200)
    assert [r[0]["I"] for r in selected] == ["6", "7", "8", "9", "10"]
    selected = select_due_events(mapped, earlier, now=now, limit=4, refresh_seconds=50)
    assert [r[0]["I"] for r in selected] == ["6", "7", "8", "9"]
    # Once all fixtures have been covered, stale ones become eligible again.
    all_known = {str(n): now - 300 - n for n in range(20)}
    again = select_due_events(mapped, all_known, now=now, limit=3, refresh_seconds=200)
    assert [r[0]["I"] for r in again] == ["19", "18", "17"]


def test_failed_empty_market_does_not_mark_scanned(archive):
    now = 1791500000.0
    fs = {"start_ts": now + 3600, "home": "A", "away": "B"}
    assert archive.record(sport="hockey", event_id="123", fs=fs, decoded={}, ts=now) == {
        "quotes": 0, "changes": 0
    }
    assert archive.last_seen("hockey", ["123"]) == {}

def test_super_waits_until_bookmaker_market_coverage_is_warmed(tmp_path, monkeypatch):
    import json

    from gool_bot2.global_super10 import day_market_readiness

    now = 1791500000.0
    day = match_day(now)
    live = tmp_path / "live"
    live.mkdir()
    multi_path = live / "gool_multisport_state.json"
    football_path = live / "xbet_prematch_archive_state.json"
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOL_MULTISPORT_STATE", str(multi_path))
    monkeypatch.setenv("GOOL_GLOBAL_SUPER10_MIN_ODDS_COVERAGE", "0.85")

    football_path.write_text(json.dumps({
        "archive_day": day,
        "archive_catalog_today": 10,
        "archive_coverage": {"archived_matches": 9},
    }))
    multi_path.write_text(json.dumps({"sports": {
        "hockey": {"daily_market_coverage": {"day": day, "matched_today": 10, "archived_matches": 8}},
        "basketball": {"daily_market_coverage": {"day": day, "matched_today": 10, "archived_matches": 9}},
    }}))
    before = day_market_readiness(now_ts=now)
    assert not before["ready"]
    assert not before["sports"]["hockey"]["ready"]
    payload = json.loads(multi_path.read_text())
    payload["sports"]["hockey"]["daily_market_coverage"]["archived_matches"] = 9
    multi_path.write_text(json.dumps(payload))
    after = day_market_readiness(now_ts=now)
    assert after["ready"]
    assert {key: row["ratio"] for key, row in after["sports"].items()} == {
        "football": 0.9, "hockey": 0.9, "basketball": 0.9
    }
