import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gool_bot2.bot_menu import analysis_text, report_text


def _write_json(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _append_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_report_has_today_and_all_time_for_all_four_strategies(tmp_path: Path):
    now = datetime.now(timezone.utc)
    main = tmp_path / "signal_journal.json"
    exp = tmp_path / "gool_bot2_shadow_markets.json"
    _write_json(main, [
        {"match_id": "m1", "head": "another_goal", "minute": 20, "score": [0, 0], "result": "won", "created_at": now.isoformat()},
        {"match_id": "m2", "head": "two_more_goals", "minute": 30, "score": [1, 0], "result": "lost", "created_at": now.isoformat()},
    ])
    _write_json(exp, [
        {"match_id": "m3", "head": "both_teams_to_score", "minute": 25, "score": [0, 0], "result": "won", "created_at": now.isoformat()},
        {"match_id": "m4", "head": "team_to_score", "minute": 35, "score": [0, 1], "result": "pending", "created_at": now.isoformat()},
    ])
    text = report_text(main)
    assert "СЕГОДНЯ" in text
    assert "ОБЩИЙ ЗА ВСЁ ВРЕМЯ" in text
    assert "⚽ Ещё гол" in text
    assert "🔥 Ещё +2 гола" in text
    assert "💜 Обе забьют — Да" in text
    assert "🔵 Команда забьёт" in text


def test_analysis_only_shows_fresh_online_rows_and_stays_under_telegram_limit(tmp_path: Path):
    now = datetime.now(timezone.utc)
    main = tmp_path / "gool_bot2_analysis.jsonl"
    exp = tmp_path / "gool_bot2_shadow_analysis.jsonl"
    fresh = []
    for i in range(20):
        fresh.append({
            "captured_at": now.isoformat(),
            "match_id": f"m{i}",
            "head": "another_goal" if i % 2 == 0 else "two_more_goals",
            "minute": 20 + (i % 20),
            "home": f"Home & {i}",
            "away": f"Away {i}",
            "score": [0, 0],
            "probability": 0.88 - i * 0.005,
            "decision": "WAIT",
            "blocks": ["gool_analyzer_rejected"],
            "gool_analyzer": {"details": {"prematch": {"blocks": []}, "live": {"blocks": ["cum=0.70<0.85"]}}},
        })
    stale = {
        "captured_at": (now - timedelta(minutes=30)).isoformat(),
        "match_id": "old",
        "head": "another_goal",
        "minute": 25,
        "home": "OLD MATCH",
        "away": "OLD AWAY",
        "score": [0, 0],
        "probability": 0.99,
        "decision": "WAIT",
    }
    _append_jsonl(main, fresh + [stale])
    _append_jsonl(exp, [
        {
            "captured_at": now.isoformat(),
            "match_id": "btts",
            "head": "both_teams_to_score",
            "minute": 31,
            "home": {"side": "home", "team": "A", "pressure_score": 0.7},
            "away": {"side": "away", "team": "B", "pressure_score": 0.8},
            "score": [0, 0],
            "confidence_score": 0.99,
            "decision": "SIGNAL",
            "blocks": [],
        },
        {"captured_at": now.isoformat(), "match_id": "team", "head": "team_to_score", "minute": 34, "home": "C", "away": "D", "team": "C", "score": [0, 0], "confidence_score": 0.83, "decision": "SIGNAL", "blocks": []},
    ])
    text = analysis_text(main)
    assert "АНАЛИЗ ОНЛАЙН · 4 СТРАТЕГИИ" in text
    assert "OLD MATCH" not in text
    assert "💜 Обе забьют — Да" in text
    assert "🔵 Команда забьёт" in text
    assert "Home &amp;" in text
    assert "A — B" in text
    assert "pressure_score" not in text
    assert len(text) < 4096
