from pathlib import Path

from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS


def _mapped(n):
    rows = []
    for i in range(n):
        rows.append(({"I": str(i)}, {"start_ts": 1000 + i}, False, 1.0))
    return rows


def test_prematch_batch_rotates_without_dropping_total_coverage(tmp_path: Path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_MAX_MAPPED_PER_SPORT", "10")
    monkeypatch.setenv("GOOL_MULTISPORT_PREMATCH_BATCH_SIZE", "4")
    cfg = SPORTS["hockey"]

    b1, total1 = worker._prematch_batch(cfg, _mapped(10))
    b2, total2 = worker._prematch_batch(cfg, _mapped(10))
    b3, total3 = worker._prematch_batch(cfg, _mapped(10))

    assert (total1, total2, total3) == (10, 10, 10)
    ids1 = {x[0]["I"] for x in b1}
    ids2 = {x[0]["I"] for x in b2}
    ids3 = {x[0]["I"] for x in b3}
    assert ids1 == {"0", "1", "2", "3"}
    assert ids2 == {"4", "5", "6", "7"}
    assert ids3 == {"8", "9", "0", "1"}
    assert ids1 | ids2 | ids3 == {str(i) for i in range(10)}


def test_super_pool_includes_more_than_80_fresh_priced_matches(tmp_path: Path):
    import time

    worker = MultiSportSteamWorker(tmp_path)
    cfg = SPORTS["hockey"]
    now = time.time()
    for i in range(85):
        worker._prematch_latest[cfg.key][str(i)] = {
            "event_id": str(i),
            "start_ts": now + 7200 + i,
            "ts": now - 30,
            "parlay_candidates": [{"event_id": str(i), "odd": 1.40}],
        }
    result = worker._scan_prematch(
        cfg,
        fs_today=[],
        xbet_prematch_prefetched=[],
        fs_price_candidates=[],
    )
    assert len(result["matches"]) == 80  # Telegram display only
    assert len(result["all_day_parlay_candidates"]) == 85  # SUPER full field

    worker._prematch_latest[cfg.key]["84"]["ts"] = now - 3600
    second = worker._scan_prematch(
        cfg,
        fs_today=[],
        xbet_prematch_prefetched=[],
        fs_price_candidates=[],
    )
    assert len(second["all_day_parlay_candidates"]) == 84  # stale prices excluded
