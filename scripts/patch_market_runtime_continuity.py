from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"missing text in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1))


# Runtime must receive every LIVE match, not just ordinary GOOL windows.
p = "src/gool_bot2/storage_live_collector.py"
replace_once(
    p,
    '''        active = [m for m in matches if int(m.minute or 0) < 90 and self._entry_window(int(m.minute or 0)) and not m.is_halftime]\n        halftime = [m for m in matches if bool(m.is_halftime)]\n        late_settlement = [m for m in matches if 76 <= int(m.minute or 0) < 90 and not m.is_halftime]\n        dead_first_half = [m for m in matches if 36 <= int(m.minute or 0) <= 45 and not m.is_halftime]\n''',
    '''        active = [m for m in matches if self._entry_window(int(m.minute or 0)) and not m.is_halftime]\n        active_ids = {str(m.provider_match_id) for m in active}\n        market_watch = [\n            m for m in matches\n            if int(m.minute or 0) > 0 and str(m.provider_match_id) not in active_ids\n        ]\n        halftime = [m for m in market_watch if bool(m.is_halftime)]\n        dead_first_half = [m for m in market_watch if 36 <= int(m.minute or 0) <= 45 and not m.is_halftime]\n''',
)
replace_once(
    p,
    '            "settlement_only": 0,\n',
    '            "settlement_only": 0,\n            "market_watch": len(market_watch),\n',
)
replace_once(
    p,
    '''        # Halftime and 76-89' are needed for settlement, but do not deserve expensive\n        # provider calls because no new ordinary GOOL entry can be created there.\n        for match in halftime + late_settlement:\n''',
    '''        # Every non-ordinary LIVE minute still gets a cheap snapshot. This keeps\n        # autonomous 1xBet STEAM and Matchbook MONEY FLOW alive at 36-45, HT,\n        # 76-89 and 90+ without spending expensive football-detail calls there.\n        for match in market_watch:\n''',
)
replace_once(
    p,
    '                print(f"LIVE_SETTLEMENT_SNAPSHOT_ERROR match={match.provider_match_id} error={type(exc).__name__}:{exc}", flush=True)\n',
    '                print(f"LIVE_MARKET_WATCH_SNAPSHOT_ERROR match={match.provider_match_id} error={type(exc).__name__}:{exc}", flush=True)\n',
)

# Correct stale runtime comment.
p = "src/gool_bot2/multi_runtime.py"
replace_once(
    p,
    '''    # Autonomous STEAM remains a separate exceptional layer, but the concept's\n    # global 75' entry deadline is applied immediately after it.\n''',
    '''    # Autonomous STEAM is a separate all-LIVE market hunter. The cutoff below\n    # applies only to ordinary GOOL; enforce_entry_cutoff explicitly bypasses STEAM.\n''',
)

# Rolling-restart compatibility: old Matchbook state may not yet have money_flow.
p = "src/gool_bot2/multi_money_flow.py"
replace_once(
    p,
    '''    exchange = record.get("matchbook_exchange") or {}\n    context = ((exchange.get("systems") or {}).get(strategy) or {})\n    period = str(context.get("period") or "FT")\n''',
    '''    exchange = record.get("matchbook_exchange") or {}\n    systems = exchange.get("systems") or {}\n    context = dict(systems.get(strategy) or {})\n    if not context:\n        minute = int((record.get("match") or {}).get("minute") or 0)\n        legacy_key = "goal_before_ht" if minute <= 45 else "another_goal"\n        context = dict(systems.get(legacy_key) or {})\n    period = str(context.get("period") or "FT")\n''',
)

# Update old xBet test contract: all LIVE, demand only prioritises.
p = "tests/test_xbet_demand_driven.py"
text = Path(p).read_text()
start = text.index("def test_demand_collector_fetches_candidates_top_steam_and_rotating_watch")
end = text.index("\n\ndef test_first_half_can_pass_before_10", start)
new_test = '''def test_demand_collector_watches_every_live_match_and_prioritises_brain_demand(tmp_path, monkeypatch):\n    collector = xbet_worker.DemandDrivenXBetMarketCollector(\n        Path(tmp_path / "state.json"), Path(tmp_path / "history.jsonl")\n    )\n    monkeypatch.setattr(xbet_worker, "load_active_demands", lambda: {"demand": {"strategy": "another_goal"}})\n\n    matches = [\n        SimpleNamespace(provider_match_id="demand", minute=89, league="Regional League", is_finished=False),\n        SimpleNamespace(provider_match_id="m1", minute=3, league="League A", is_finished=False),\n        SimpleNamespace(provider_match_id="m2", minute=36, league="League B", is_finished=False),\n        SimpleNamespace(provider_match_id="m3", minute=76, league="League C", is_finished=False),\n        SimpleNamespace(provider_match_id="m4", minute=95, league="League D", is_finished=False),\n        SimpleNamespace(provider_match_id="done", minute=90, league="League E", is_finished=True),\n    ]\n    selected, stats = collector._select_matches(matches)\n    ids = [str(row.provider_match_id) for row in selected]\n    assert ids == ["demand", "m1", "m2", "m3", "m4"]\n    assert stats["demanded"] == 1\n    assert stats["live_watch"] == 5\n    assert stats["background"] == 4\n'''
Path(p).write_text(text[:start] + new_test + text[end:])

# New concept contract: only ordinary GOOL is cut off after 75.
p = "tests/test_multi_two_system_concept.py"
replace_once(
    p,
    '''def test_global_cutoff_also_blocks_autonomous_steam_after_75() -> None:\n    decision = enforce_entry_cutoff(_decision(76, source="1xbet:autonomous_steam"))\n    assert decision.status == "WAIT"\n    assert decision.winner is None\n    assert decision.rejected\n    assert decision.rejected[0].source == "1xbet:autonomous_steam"\n''',
    '''def test_global_cutoff_never_blocks_autonomous_steam_after_75() -> None:\n    for minute in (76, 89, 90, 95):\n        decision = enforce_entry_cutoff(_decision(minute, source="1xbet:autonomous_steam"))\n        assert decision.status == "BET"\n        assert decision.winner is not None\n        assert decision.winner.source == "1xbet:autonomous_steam"\n''',
)

# Remove the obsolete final rating-floor tests; keep epoch reset test.
p = Path("tests/test_multi_rating70_epoch.py")
text = p.read_text()
text = text.replace("from gool_bot2.multi_router import MarketCandidate, RouterDecision\nfrom gool_bot2.multi_runtime import _enforce_min_rating\n", "import gool_bot2.multi_runtime as multi_runtime\n")
start = text.index("def _decision")
end = text.index("def test_rating70_epoch_reset_clears_multi_tracking_only_once", start)
replacement = '''def test_runtime_has_no_second_final_rating_brain():\n    assert not hasattr(multi_runtime, "_enforce_min_rating")\n\n\n'''
p.write_text(text[:start] + replacement + text[end:])

# Strengthen new all-live test with the storage collector continuity contract.
p = Path("tests/test_all_live_market_watch.py")
text = p.read_text()
text = text.replace(
    "from gool_bot2.xbet_market_worker import DemandDrivenXBetMarketCollector\n",
    "from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector\nfrom gool_bot2.xbet_market_worker import DemandDrivenXBetMarketCollector\n",
)
text += '''\n\ndef test_storage_collector_keeps_ordinary_windows_separate_from_market_watch():\n    assert StorageLiveSnapshotCollector._entry_window(35)\n    assert not StorageLiveSnapshotCollector._entry_window(36)\n    assert StorageLiveSnapshotCollector._entry_window(46)\n    assert StorageLiveSnapshotCollector._entry_window(75)\n    assert not StorageLiveSnapshotCollector._entry_window(76)\n    # Market hunters are intentionally tested separately above at 89/90/95.\n'''
p.write_text(text)
