from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def patch(path: str, replacements: list[tuple[str, str]]) -> None:
    target = ROOT / path
    text = target.read_text("utf-8")
    original = text
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"expected text missing in {path}: {old!r}")
        text = text.replace(old, new)
    if text != original:
        target.write_text(text, "utf-8")
        print(f"patched {path}")


patch(
    "src/gool_bot2/multi_concept.py",
    [
        ("FIRST_HALF_MAX_MINUTE = 30", "FIRST_HALF_MAX_MINUTE = 35"),
        ("first half: goal before half-time only, entries through 30';", "first half: goal before half-time only, entries through 35';"),
    ],
)

patch(
    "src/gool_bot2/storage_live_collector.py",
    [
        ("return 1 <= int(minute) <= 30 or 46 <= int(minute) <= 75", "return 1 <= int(minute) <= 35 or 46 <= int(minute) <= 75"),
        ("dead_first_half = [m for m in matches if 31 <= int(m.minute or 0) <= 45 and not m.is_halftime]", "dead_first_half = [m for m in matches if 36 <= int(m.minute or 0) <= 45 and not m.is_halftime]"),
        ("Use the otherwise dead 31-45/HT window", "Use the otherwise dead 36-45/HT window"),
    ],
)

patch(
    "src/gool_bot2/multi_runtime.py",
    [
        (
            "from .xbet_market_pressure import live_1x2_context, load_market_state\n",
            "from .xbet_market_demand import request_live_market\nfrom .xbet_market_pressure import live_1x2_context, load_market_state\n",
        ),
        (
            "    if bool(match.get(\"is_halftime\")):\n        experts.pop(\"goal_before_ht\", None)\n\n    market = _market_row(record)\n",
            "    demand = request_live_market(record, experts)\n    if demand is not None:\n        print(\n            f\"XBET_DEMAND_REQUEST match={mid} minute={minute} strategy={demand.get('strategy')} \"\n            f\"state={demand.get('football_state')} p={float(demand.get('football_probability') or 0):.3f}\",\n            flush=True,\n        )\n\n    if bool(match.get(\"is_halftime\")):\n        experts.pop(\"goal_before_ht\", None)\n\n    market = _market_row(record)\n",
        ),
        (
            "active_strategy = \"goal_before_ht\" if 1 <= minute <= 30 else \"another_goal\" if 46 <= minute <= 75 else \"\"",
            "active_strategy = \"goal_before_ht\" if 1 <= minute <= 35 else \"another_goal\" if 46 <= minute <= 75 else \"\"",
        ),
    ],
)

patch(
    "src/gool_bot2/multi_match_intelligence.py",
    [
        (
            "    if 21 <= minute <= 30:\n        return {\"period\": \"1H\", \"bucket\": \"21-30\", \"factor\": 1.08}\n    if 46 <= minute <= 55:\n",
            "    if 21 <= minute <= 30:\n        return {\"period\": \"1H\", \"bucket\": \"21-30\", \"factor\": 1.08}\n    if 31 <= minute <= 35:\n        return {\"period\": \"1H\", \"bucket\": \"31-35\", \"factor\": 1.12}\n    if 46 <= minute <= 55:\n",
        ),
        (
            "active_strategy = \"goal_before_ht\" if 1 <= minute <= 30 else (\"another_goal\" if 46 <= minute <= 75 else None)",
            "active_strategy = \"goal_before_ht\" if 1 <= minute <= 35 else (\"another_goal\" if 46 <= minute <= 75 else None)",
        ),
    ],
)

patch(
    "src/gool_bot2/multi_money_flow.py",
    [("    if 1 <= minute <= 30:\n        return \"goal_before_ht\", \"1H\", \"first_half_total\"", "    if 1 <= minute <= 35:\n        return \"goal_before_ht\", \"1H\", \"first_half_total\"")],
)

patch(
    "src/gool_bot2/matchbook_exchange.py",
    [("if 1 <= minute <= 30 else {\"available\": False}", "if 1 <= minute <= 35 else {\"available\": False}")],
)

patch(
    "src/gool_bot2/goal_state_engine.py",
    [
        (
            "    elif minute < 10 or evidence < min_evidence or confidence is None:\n",
            "    elif evidence < min_evidence or confidence is None:\n",
        ),
        (
            "    elif minute < 10:\n        fh_state = NO_DATA\n        fh_strength = _blend([(any_strength, 0.65), (first_half_prior, 0.35)])\n        fh_blocks = [\"first_half_warmup\"]\n    else:\n",
            "    else:\n",
        ),
    ],
)

patch(
    "tests/test_multi_two_system_concept.py",
    [
        ("def test_first_half_routes_only_goal_before_ht_through_30() -> None:\n    for minute in (1, 15, 30):", "def test_first_half_routes_only_goal_before_ht_through_35() -> None:\n    for minute in (1, 15, 30, 35):"),
        ("def test_first_half_has_no_new_ordinary_entry_after_30() -> None:\n    for minute in (31, 40, 45):", "def test_first_half_has_no_new_ordinary_entry_after_35() -> None:\n    for minute in (36, 40, 45):"),
    ],
)

patch(
    "tests/test_money_flow_and_live_coverage.py",
    [
        (
            "    assert StorageLiveSnapshotCollector._entry_window(30)\n    assert not StorageLiveSnapshotCollector._entry_window(31)\n    assert not StorageLiveSnapshotCollector._entry_window(45)\n",
            "    assert StorageLiveSnapshotCollector._entry_window(30)\n    assert StorageLiveSnapshotCollector._entry_window(35)\n    assert not StorageLiveSnapshotCollector._entry_window(36)\n    assert not StorageLiveSnapshotCollector._entry_window(45)\n",
        ),
    ],
)

print("demand/window patch complete")
