from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import gool_bot2.journal_in_game as journal_in_game
import gool_bot2.result_delivery_guard as guard


def _row(key: str, *, result: str = "won") -> dict:
    return {
        "entry_key": key,
        "match_id": "fs-1",
        "home": "Home",
        "away": "Away",
        "minute": 20,
        "score": [0, 0],
        "market": "1Т ТБ 0.5",
        "strategy": "goal_before_ht",
        "result": result,
        "mode": "active",
        "telegram_sent": True,
        "rating": 88,
        "odd": 0.0,
    }


def test_same_result_can_only_reach_sender_once_across_parallel_attempts(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    journal.write_text("[]", "utf-8")
    calls: list[str] = []

    def fake_emit(record, rows, *, journal_path=None):
        calls.append(str(rows[0]["entry_key"]))
        return 1

    monkeypatch.setattr(guard, "_ORIGINAL_EMIT", fake_emit)
    row = _row("brain:fs-1:goal_before_ht")

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: guard._guarded_emit({}, [dict(row)], journal_path=journal), range(5)))

    assert sum(results) == 1
    assert calls == ["brain:fs-1:goal_before_ht"]


def test_failed_result_delivery_is_released_for_retry(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    journal.write_text("[]", "utf-8")
    outcomes = iter([0, 1])
    monkeypatch.setattr(guard, "_ORIGINAL_EMIT", lambda *args, **kwargs: next(outcomes))
    row = _row("steam:fs-1")

    assert guard._guarded_emit({}, [dict(row)], journal_path=journal) == 0
    assert guard._guarded_emit({}, [dict(row)], journal_path=journal) == 1


def test_in_game_membership_comes_from_delivered_unsettled_journal_and_renderer_is_read_only(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    rows = [
        {**_row("pending-1", result="pending"), "home": "Pending Home", "away": "Pending Away"},
        {**_row("tracking-1", result="tracking"), "home": "Brain Home", "away": "Brain Away", "accounting_mode": "result_only"},
        {**_row("won-1", result="won"), "home": "Won Home", "away": "Won Away"},
        {**_row("unsent-1", result="pending"), "home": "Never Sent", "telegram_sent": False},
    ]
    journal.write_text(json.dumps(rows), "utf-8")
    analysis.write_text("", "utf-8")
    monkeypatch.setattr(
        "gool_bot2.multi_menu.reconcile_pending",
        lambda: (_ for _ in ()).throw(AssertionError("In Game renderer must not settle journal")),
    )

    text = "\n".join(journal_in_game.journal_in_game_sections(journal, analysis))

    assert "Открыто: <b>2</b>" in text
    assert "Pending Home" in text
    assert "Brain Home" in text
    assert "Won Home" not in text
    assert "Never Sent" not in text
    assert "@ —" in text
