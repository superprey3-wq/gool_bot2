from __future__ import annotations

import re
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"missing expected text in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    p = Path(path)
    text = p.read_text()
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"expected one regex match in {path}, got {count}: {pattern!r}")
    p.write_text(updated)


# 1xBet STEAM: no minute window and no football-data-quality dependency.
steam = "src/gool_bot2/multi_autonomous_steam.py"
replace_once(
    steam,
    '    if minute < max(1, _i("XBET_AUTONOMOUS_STEAM_MIN_MINUTE", 10)):\n        return []\n    if data_quality < max(0.0, min(1.0, _f("XBET_AUTONOMOUS_STEAM_MIN_DATA_QUALITY", 0.45))):\n        return []\n',
    '    if minute <= 0:\n        return []\n',
)
replace_once(
    steam,
    '    if minute <= _i("XBET_AUTONOMOUS_STEAM_ANOTHER_GOAL_MAX_MINUTE", 85):\n',
    '    if minute > 0:\n',
)
replace_once(
    steam,
    '    if minute <= _i("XBET_AUTONOMOUS_STEAM_TWO_GOALS_MAX_MINUTE", 60):\n',
    '    if minute > 0:\n',
)
replace_once(
    steam,
    '    if minute <= _i("XBET_AUTONOMOUS_STEAM_TEAM_GOAL_MAX_MINUTE", 75):\n',
    '    if minute > 0:\n',
)
replace_once(
    steam,
    '    if minute <= _i("XBET_AUTONOMOUS_STEAM_BTTS_MAX_MINUTE", 75) and not (hs > 0 and aws > 0):\n',
    '    if minute > 0 and not (hs > 0 and aws > 0):\n',
)
replace_once(
    steam,
    '    if 0 < minute <= _i("XBET_AUTONOMOUS_STEAM_FIRST_HALF_MAX_MINUTE", 42) and not bool(match.get("is_halftime")):\n',
    '    if 0 < minute <= 45 and not bool(match.get("is_halftime")):\n',
)

# Matchbook context: keep ordinary contexts bounded, add an all-LIVE money-flow context.
matchbook = "src/gool_bot2/matchbook_exchange.py"
old_systems = '''    systems = {\n        "goal_before_ht": _target_context(event, "1H", total + 0.5) if 1 <= minute <= 35 else {"available": False},\n        "another_goal": _target_context(event, "FT", total + 0.5) if 46 <= minute <= 75 else {"available": False},\n    }\n'''
new_systems = '''    money_flow = {"available": False}\n    if minute > 0 and not bool(match.get("is_finished")) and bool(event.get("in_running")):\n        flow_period = "1H" if not bool(match.get("is_halftime")) and minute <= 45 else "FT"\n        money_flow = _target_context(event, flow_period, total + 0.5)\n        if flow_period == "1H" and not bool(money_flow.get("available")):\n            money_flow = _target_context(event, "FT", total + 0.5)\n\n    systems = {\n        "goal_before_ht": _target_context(event, "1H", total + 0.5) if 1 <= minute <= 35 else {"available": False},\n        "another_goal": _target_context(event, "FT", total + 0.5) if 46 <= minute <= 75 else {"available": False},\n        "money_flow": money_flow,\n    }\n'''
replace_once(matchbook, old_systems, new_systems)

# MONEY FLOW: all LIVE minutes; period/market comes from Matchbook context.
flow = "src/gool_bot2/multi_money_flow.py"
regex_once(
    flow,
    r'def _active_system\(record: dict\[str, Any\]\) -> tuple\[str, str, str\] \| None:\n.*?(?=\n\ndef _threshold)',
    '''def _active_system(record: dict[str, Any]) -> str | None:\n    match = record.get("match") or {}\n    minute = int(match.get("minute") or 0)\n    if minute <= 0 or bool(match.get("is_finished")):\n        return None\n    return "money_flow"\n''',
)
replace_once(
    flow,
    '''    active = _active_system(record)\n    if active is None:\n        return {"eligible": False, "reason": "outside_money_flow_window"}\n    strategy, period, family = active\n    exchange = record.get("matchbook_exchange") or {}\n    context = ((exchange.get("systems") or {}).get(strategy) or {})\n''',
    '''    strategy = _active_system(record)\n    if strategy is None:\n        return {"eligible": False, "reason": "money_flow_match_not_live"}\n    exchange = record.get("matchbook_exchange") or {}\n    context = ((exchange.get("systems") or {}).get(strategy) or {})\n    period = str(context.get("period") or "FT")\n    family = "first_half_total" if period == "1H" else "match_total"\n''',
)

# One football Brain: remove the duplicate numeric confidence gate and the later rating floor.
runtime = "src/gool_bot2/multi_runtime.py"
replace_once(runtime, "from .multi_confidence_gate import enforce_confidence_gate\n", "")
regex_once(
    runtime,
    r'\ndef _minimum_rating\(\) -> float:\n.*?(?=\ndef _ensure_any_goal_coverage_proxy)',
    "\n",
)
replace_once(
    runtime,
    '''    # Matchbook cannot manufacture a BET. It only adjusts the rating of an\n    # already-created ordinary candidate before the normal confidence gate.\n    decision = apply_matchbook_confirmation(decision, record)\n\n    # Strong 1xBet confirmation may support only the active ordinary concept\n    # candidate. Other football products are no longer exposed to the router.\n    decision = enforce_confidence_gate(decision, experts, market_row=market)\n''',
    '''    # Matchbook cannot manufacture an ordinary GOOL BET. It can only annotate\n    # or modestly adjust a candidate that the single football Brain already chose.\n    decision = apply_matchbook_confirmation(decision, record)\n''',
)
replace_once(runtime, "    decision = _enforce_min_rating(decision)\n", "")
replace_once(
    runtime,
    "    Every production entry, including STEAM, is finally closed after 75'.\n",
    "    Only ordinary GOOL uses the 35'/75' entry windows; autonomous market\n    systems remain live for the whole match while their markets are tradable.\n",
)

# Regression contract for no league priority/no market-minute caps/one Brain.
test = Path("tests/test_all_live_market_watch.py")
test.write_text('''from __future__ import annotations\n\nfrom datetime import datetime, timezone\n\nfrom gool_bot2.matchbook_exchange import matchbook_context\nfrom gool_bot2.multi_autonomous_steam import build_autonomous_steam_candidates\nfrom gool_bot2.multi_concept import enforce_entry_cutoff\nfrom gool_bot2.multi_money_flow import evaluate_money_flow\nfrom gool_bot2.multi_router import MarketCandidate, RouterDecision\nfrom gool_bot2.xbet_market_worker import DemandDrivenXBetMarketCollector\n\n\nclass _LiveMatch:\n    def __init__(self, match_id: str, minute: int, *, finished: bool = False, halftime: bool = False):\n        self.provider_match_id = match_id\n        self.minute = minute\n        self.is_finished = finished\n        self.is_halftime = halftime\n        self.league = "Any League"\n\n\ndef test_xbet_market_watch_has_no_minute_or_league_cap(monkeypatch):\n    matches = [\n        _LiveMatch("m1", 1),\n        _LiveMatch("m2", 44),\n        _LiveMatch("m3", 45, halftime=True),\n        _LiveMatch("m4", 76),\n        _LiveMatch("m5", 89),\n        _LiveMatch("m6", 90),\n        _LiveMatch("m7", 95),\n        _LiveMatch("done", 90, finished=True),\n    ]\n    monkeypatch.setattr("gool_bot2.xbet_market_worker.load_active_demands", lambda: {"m5": {}})\n    collector = object.__new__(DemandDrivenXBetMarketCollector)\n    selected, stats = collector._select_matches(matches)\n    assert [row.provider_match_id for row in selected] == ["m5", "m1", "m2", "m3", "m4", "m6", "m7"]\n    assert stats["live_watch"] == 7\n    assert stats["demanded"] == 1\n\n\ndef _steam_record(minute: int = 89):\n    return {\n        "match": {\n            "minute": minute,\n            "home_score": 1,\n            "away_score": 1,\n            "is_finished": False,\n            "is_halftime": False,\n        }\n    }\n\n\ndef _steam_market():\n    return {\n        "captured_at": datetime.now(timezone.utc).isoformat(),\n        "score_home": 1,\n        "score_away": 1,\n        "score_verified": True,\n        "markets": {\n            "match_total": [{"line": 2.5, "over": 1.70, "under": 2.20}],\n        },\n        "pressure": {\n            "match_total:2.5": {\n                "prob_delta_pp": 13.0,\n                "one_way_moves": 4,\n                "old_odd": 2.05,\n            }\n        },\n    }\n\n\ndef test_autonomous_steam_can_fire_at_89_without_football_quality_gate(monkeypatch):\n    monkeypatch.setenv("XBET_AUTONOMOUS_STEAM_MIN_RELATED_MARKETS", "1")\n    rows = build_autonomous_steam_candidates(_steam_record(89), _steam_market(), data_quality=0.0)\n    assert rows\n    assert rows[0].source == "1xbet:autonomous_steam"\n    assert rows[0].odd == 1.70\n\n\ndef _candidate(source: str) -> MarketCandidate:\n    return MarketCandidate(\n        key="match_total:2.5",\n        family="match_total",\n        label="ТБ 2.5",\n        odd=1.70,\n        model_probability=0.80,\n        strategy="another_goal",\n        source=source,\n        rating=80.0,\n    )\n\n\ndef test_ordinary_cutoff_does_not_block_autonomous_steam_after_75():\n    steam = RouterDecision("BET", 89, (1, 1), _candidate("1xbet:autonomous_steam"), [], [], "steam")\n    assert enforce_entry_cutoff(steam).status == "BET"\n\n    ordinary = RouterDecision("BET", 89, (1, 1), _candidate("gool"), [], [], "ordinary")\n    blocked = enforce_entry_cutoff(ordinary)\n    assert blocked.status == "WAIT"\n    assert blocked.winner is None\n\n\ndef _matchbook_state():\n    flow = {\n        "window_ready_30s": True,\n        "window_ready_60s": True,\n        "volume_delta_30s": 400.0,\n        "volume_delta_60s": 550.0,\n        "fair_over_delta_pp_30s": 2.8,\n        "fair_over_delta_pp_60s": 3.0,\n    }\n    total = {\n        "id": "tot25",\n        "name": "Total Goals 2.5",\n        "status": "open",\n        "in_running": True,\n        "volume": 2000.0,\n        "period": "FT",\n        "line": 2.5,\n        "fair_over": 0.62,\n        "over": {\n            "best_back": {"odds": 1.70, "available": 300.0},\n            "best_lay": {"odds": 1.72, "available": 250.0},\n        },\n        "under": {\n            "best_back": {"odds": 2.40, "available": 200.0},\n            "best_lay": {"odds": 2.44, "available": 200.0},\n        },\n        "flow": flow,\n    }\n    return {\n        "captured_at": datetime.now(timezone.utc).isoformat(),\n        "events": [{\n            "event_id": "mb1",\n            "name": "Arsenal vs Chelsea",\n            "home": "Arsenal",\n            "away": "Chelsea",\n            "status": "open",\n            "in_running": True,\n            "allow_live": True,\n            "volume": 5000.0,\n            "totals": {"FT:2.5": total},\n        }],\n    }\n\n\ndef test_money_flow_can_fire_at_89_and_90_plus():\n    for minute in (89, 90, 95):\n        record = {\n            "match": {\n                "flashscore_event_id": "fs1",\n                "home": "Arsenal",\n                "away": "Chelsea",\n                "minute": minute,\n                "home_score": 1,\n                "away_score": 1,\n                "is_finished": False,\n                "is_halftime": False,\n            },\n            "providers": {"flashscore": {"meta": {"goal_timeline": []}}},\n        }\n        record["matchbook_exchange"] = matchbook_context(record, _matchbook_state())\n        context = record["matchbook_exchange"]["systems"]["money_flow"]\n        assert context["available"] is True\n        assert context["period"] == "FT"\n        info = evaluate_money_flow(record)\n        assert info["eligible"] is True\n        assert info["odd"] == 1.70\n\n\ndef test_runtime_has_no_duplicate_confidence_brain():\n    text = __import__("pathlib").Path("src/gool_bot2/multi_runtime.py").read_text()\n    assert "enforce_confidence_gate" not in text\n    assert "_enforce_min_rating" not in text\n''')
