from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from gool_bot2.betdaq_production import ProductionBetdaqExchangeCollector, event_hierarchy_fields


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_event_hierarchy_uses_persistent_then_snapshot_modes() -> None:
    persistent = event_hierarchy_fields(301, fetch_only=False)
    snapshot = event_hierarchy_fields(302, fetch_only=True)
    assert persistent[0] == 301
    assert persistent[5] is False
    assert snapshot[0] == 302
    assert snapshot[5] is True
    assert persistent[2] == snapshot[2] == 100003


def test_production_discovery_uses_rolling_window_across_midnight(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BETDAQ_DISCOVERY_LOOKBACK_HOURS", "6")
    monkeypatch.setenv("BETDAQ_DISCOVERY_LOOKAHEAD_HOURS", "36")
    collector = ProductionBetdaqExchangeCollector(tmp_path / "betdaq.json")
    now = datetime.now(timezone.utc)
    event_id = 15150001
    collector._topics = {
        f"/E/E_{event_id}/EL/en": {"1": "Home FC v Away FC"},
        f"/E/E_{event_id}/EEI": {"3": _iso(now + timedelta(hours=30))},
    }
    rows = collector._events_from_topics()
    assert len(rows) == 1
    assert rows[0]["event_id"] == event_id
    assert rows[0]["home"] == "Home FC"
    assert rows[0]["away"] == "Away FC"


def test_production_discovery_keeps_recent_live_game(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BETDAQ_DISCOVERY_LOOKBACK_HOURS", "6")
    monkeypatch.setenv("BETDAQ_DISCOVERY_LOOKAHEAD_HOURS", "36")
    collector = ProductionBetdaqExchangeCollector(tmp_path / "betdaq.json")
    now = datetime.now(timezone.utc)
    event_id = 15150002
    collector._topics = {
        f"/E/E_{event_id}/EL/en": {"1": "Alpha v Beta"},
        f"/E/E_{event_id}/EEI": {"3": _iso(now - timedelta(hours=2))},
    }
    rows = collector._events_from_topics()
    assert [row["event_id"] for row in rows] == [event_id]


def test_production_discovery_excludes_stale_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BETDAQ_DISCOVERY_LOOKBACK_HOURS", "6")
    monkeypatch.setenv("BETDAQ_DISCOVERY_LOOKAHEAD_HOURS", "36")
    collector = ProductionBetdaqExchangeCollector(tmp_path / "betdaq.json")
    now = datetime.now(timezone.utc)
    event_id = 15150003
    collector._topics = {
        f"/E/E_{event_id}/EL/en": {"1": "Old Home v Old Away"},
        f"/E/E_{event_id}/EEI": {"3": _iso(now - timedelta(hours=8))},
    }
    assert collector._events_from_topics() == []
