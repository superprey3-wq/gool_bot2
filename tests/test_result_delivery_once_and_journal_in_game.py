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



def test_in_game_prematch_markets_are_human_readable(tmp_path):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    rows = [
        {
            "entry_id": "prematch:1",
            "entry_key": "prematch:1",
            "match_id": "fs-1",
            "home": "A",
            "away": "B",
            "origin": "prematch",
            "lifecycle": "in_game",
            "in_game": True,
            "mode": "active",
            "result": "pending",
            "telegram_sent": True,
            "minute": 0,
            "score": [0, 0],
            "market": "match_total",
            "selection": "over 2.5",
            "odd": 1.55,
        },
        {
            "entry_id": "prematch:2",
            "entry_key": "prematch:2",
            "match_id": "fs-2",
            "home": "C",
            "away": "D",
            "origin": "prematch",
            "lifecycle": "in_game",
            "in_game": True,
            "mode": "active",
            "result": "pending",
            "telegram_sent": True,
            "minute": 0,
            "score": [0, 0],
            "market": "home_total",
            "selection": "under 4.5",
            "odd": 2.12,
        },
        {
            "entry_id": "prematch:3",
            "entry_key": "prematch:3",
            "match_id": "fs-3",
            "home": "E",
            "away": "F",
            "origin": "prematch",
            "lifecycle": "in_game",
            "in_game": True,
            "mode": "active",
            "result": "pending",
            "telegram_sent": True,
            "minute": 0,
            "score": [0, 0],
            "market": "btts",
            "selection": "yes",
            "odd": 1.70,
        },
    ]
    journal.write_text(json.dumps(rows), "utf-8")
    analysis.write_text("", "utf-8")

    text = "\n".join(journal_in_game.journal_in_game_sections(journal, analysis))

    assert "ТБ 2.5 @ 1.55" in text
    assert "ИТМ1 4.5 @ 2.12" in text
    assert "Обе забьют — Да @ 1.70" in text
    assert "match_total" not in text
    assert "home_total" not in text


def test_value_prematch_enters_in_game_only_at_kickoff(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    analysis.write_text("", "utf-8")
    now = 1_800_000_000.0
    monkeypatch.setattr(journal_in_game.time, "time", lambda: now)

    future = {
        "entry_id": "value:future",
        "entry_key": "value:future",
        "match_id": "fs-value-future",
        "home": "Future",
        "away": "Match",
        "origin": "prematch_value",
        "product": "value_hunter",
        "lifecycle": "scheduled",
        "kickoff_ts": now + 600,
        "mode": "active",
        "result": "pending",
        "telegram_sent": True,
        "minute": 0,
        "score": [0, 0],
        "market": "match_total",
        "selection": "over 3.5",
        "odd": 3.10,
    }
    started = {
        **future,
        "entry_id": "value:started",
        "entry_key": "value:started",
        "match_id": "fs-value-started",
        "home": "Started",
        "kickoff_ts": now - 1,
    }
    journal.write_text(json.dumps([future, started]), "utf-8")

    text = "\n".join(journal_in_game.journal_in_game_sections(journal, analysis))

    assert "Future" not in text
    assert "Started" in text
    assert "ТБ 3.5 @ 3.10" in text


def test_regular_prematch_also_enters_in_game_by_kickoff_time(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    analysis.write_text("", "utf-8")
    now = 1_800_000_000.0
    monkeypatch.setattr(journal_in_game.time, "time", lambda: now)
    row = {
        "entry_id": "prematch:started",
        "entry_key": "prematch:started",
        "match_id": "fs-started",
        "home": "A",
        "away": "B",
        "origin": "prematch",
        "lifecycle": "scheduled",
        "kickoff_ts": now,
        "mode": "active",
        "result": "pending",
        "telegram_sent": True,
        "minute": 0,
        "score": [0, 0],
        "market": "match_total",
        "selection": "under 2.5",
        "odd": 1.90,
    }
    journal.write_text(json.dumps([row]), "utf-8")
    text = "\n".join(journal_in_game.journal_in_game_sections(journal, analysis))
    assert "A" in text
    assert "ТМ 2.5 @ 1.90" in text


def test_production_in_game_adapter_uses_strict_flashscore_live_filter(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    journal.write_text("[]", "utf-8")
    analysis.write_text("", "utf-8")

    monkeypatch.setattr("gool_bot2.multi_menu.journal_path", lambda: journal)
    monkeypatch.setattr("gool_bot2.multi_menu.analysis_path", lambda: analysis)

    calls = []
    def fake_strict(journal_path, analysis_path=None):
        calls.append((journal_path, analysis_path))
        return ["STRICT LIVE"]

    monkeypatch.setattr("gool_bot2.strict_in_game_live.strict_in_game_sections", fake_strict)
    monkeypatch.setattr("gool_bot2.multisport_menu.multisport_in_game_sections", lambda: [])

    assert journal_in_game.production_in_game_sections() == ["STRICT LIVE"]
    assert calls == [(journal, analysis)]


def test_production_in_game_adapter_includes_multisport_live_when_football_is_empty(tmp_path, monkeypatch):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    journal.write_text("[]", "utf-8")
    analysis.write_text("", "utf-8")

    monkeypatch.setattr("gool_bot2.multi_menu.journal_path", lambda: journal)
    monkeypatch.setattr("gool_bot2.multi_menu.analysis_path", lambda: analysis)
    monkeypatch.setattr(
        "gool_bot2.strict_in_game_live.strict_in_game_sections",
        lambda *_args, **_kwargs: [
            "🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\n"
            "Сейчас нет сигналов на матчах, подтверждённых Flashscore как LIVE."
        ],
    )
    monkeypatch.setattr(
        "gool_bot2.multisport_menu.multisport_in_game_sections",
        lambda: [
            "🟢 <b>GOOL MULTI · В ИГРЕ</b>\nМатчей: <b>1</b> · ставок: <b>3</b>",
            "<b>1. 🏀 Home — Away</b>\n"
            "🎯 ТБ 150.5 @ 1.80\n"
            "🎯 ИТБ1 75.5 @ 1.85\n"
            "🎯 3-я четверть: ТБ 40.5 @ 1.90",
        ],
    )

    sections = journal_in_game.production_in_game_sections()
    text = "\n".join(sections)

    assert "Матчей: <b>1</b> · ставок: <b>3</b>" in text
    assert "ТБ 150.5 @ 1.80" in text
    assert "ИТБ1 75.5 @ 1.85" in text
    assert "3-я четверть: ТБ 40.5 @ 1.90" in text
    assert "Сейчас нет сигналов" not in text


def test_production_in_game_adapter_combines_football_and_multisport(monkeypatch, tmp_path):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    journal.write_text("[]", "utf-8")
    analysis.write_text("", "utf-8")

    monkeypatch.setattr("gool_bot2.multi_menu.journal_path", lambda: journal)
    monkeypatch.setattr("gool_bot2.multi_menu.analysis_path", lambda: analysis)
    monkeypatch.setattr(
        "gool_bot2.strict_in_game_live.strict_in_game_sections",
        lambda *_args, **_kwargs: ["FOOTBALL LIVE"],
    )
    monkeypatch.setattr(
        "gool_bot2.multisport_menu.multisport_in_game_sections",
        lambda: ["BASKETBALL LIVE"],
    )

    assert journal_in_game.production_in_game_sections() == ["FOOTBALL LIVE", "BASKETBALL LIVE"]


def test_common_in_game_adapter_contains_multisport_only_once(monkeypatch, tmp_path):
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    journal.write_text("[]", "utf-8")
    analysis.write_text("", "utf-8")

    monkeypatch.setattr("gool_bot2.multi_menu.journal_path", lambda: journal)
    monkeypatch.setattr("gool_bot2.multi_menu.analysis_path", lambda: analysis)
    monkeypatch.setattr(
        "gool_bot2.strict_in_game_live.strict_in_game_sections",
        lambda *_args, **_kwargs: ["FOOTBALL LIVE"],
    )
    calls = {"multi": 0}

    def fake_multisport():
        calls["multi"] += 1
        return ["HOCKEY LIVE", "BASKETBALL LIVE"]

    monkeypatch.setattr(
        "gool_bot2.multisport_menu.multisport_in_game_sections",
        fake_multisport,
    )

    sections = journal_in_game.production_in_game_sections()

    assert sections == ["FOOTBALL LIVE", "HOCKEY LIVE", "BASKETBALL LIVE"]
    assert calls["multi"] == 1
