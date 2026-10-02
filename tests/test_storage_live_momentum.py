from datetime import datetime, timezone
from types import SimpleNamespace

from gool_bot2 import storage_live_collector as module
from gool_bot2.live_momentum import LiveMomentumTracker
from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector


class _FakeFlashscore:
    def __init__(self):
        self.rows = [
            {
                "shots": [2, 1],
                "shots_on_target": [1, 0],
                "xg": [0.20, 0.10],
                "big_chances": [0, 0],
                "dangerous_attacks": [10, 5],
            },
            {
                "shots": [5, 2],
                "shots_on_target": [2, 1],
                "xg": [0.50, 0.20],
                "big_chances": [1, 0],
                "dangerous_attacks": [18, 8],
            },
        ]

    def fetch_stats(self, _match_id):
        return self.rows.pop(0)

    def fetch_goal_timeline(self, _match_id):
        return []


def _collector(monkeypatch):
    collector = StorageLiveSnapshotCollector.__new__(StorageLiveSnapshotCollector)
    collector.flashscore = _FakeFlashscore()
    collector.prefilter_threshold = 50.0
    collector._momentum = LiveMomentumTracker()
    collector._prematch_context = {}
    collector._refresh_secondary = lambda match, minute: 0
    collector._cached_history = lambda match_id: {}
    collector._schedule_history = lambda match: None
    collector._attach_secondary_cache = lambda record, match_id: None
    collector._attach_flashscore_assets = lambda record, meta: None
    collector._prematch_store = SimpleNamespace(save=lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module._LEGACY,
        "football_prefilter",
        lambda *args, **kwargs: SimpleNamespace(score=0.0, candidate=False, reasons=[]),
    )
    return collector


def test_production_active_records_persist_real_5m_momentum(monkeypatch):
    collector = _collector(monkeypatch)
    match = SimpleNamespace(
        provider_match_id="m1",
        home="A",
        away="B",
        league="TEST",
        minute=10,
        home_score=0,
        away_score=0,
        is_halftime=False,
        meta={},
    )
    now = datetime.now(timezone.utc)

    first, _ = collector._active_record(match, now)
    assert first["live_momentum"]["minutes_in_epoch"] == 0

    match.minute = 15
    second, _ = collector._active_record(match, now)
    momentum = second["live_momentum"]

    assert momentum["minutes_in_epoch"] == 5
    assert momentum["shots_total_last_5m"] == 4.0
    assert momentum["sot_total_last_5m"] == 2.0
    assert momentum["xg_total_last_5m"] == 0.4
    assert momentum["big_total_last_5m"] == 1.0


def test_production_detail_windows_cover_full_live_match():
    assert StorageLiveSnapshotCollector._entry_window(1) is True
    assert StorageLiveSnapshotCollector._entry_window(35) is True
    assert StorageLiveSnapshotCollector._entry_window(42) is True
    assert StorageLiveSnapshotCollector._entry_window(43) is True
    assert StorageLiveSnapshotCollector._entry_window(45) is True
    assert StorageLiveSnapshotCollector._entry_window(46) is True
    assert StorageLiveSnapshotCollector._entry_window(75) is True
    assert StorageLiveSnapshotCollector._entry_window(82) is True
    assert StorageLiveSnapshotCollector._entry_window(95) is True
    assert StorageLiveSnapshotCollector._entry_window(96) is False
