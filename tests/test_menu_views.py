import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gool_bot2 import first_half_product
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


def test_in_game_merges_main_and_shadow_journals(monkeypatch, tmp_path: Path):
    main = tmp_path / "signal_journal.json"
    shadow = tmp_path / "gool_bot2_shadow_markets.json"
    main_analysis = tmp_path / "gool_bot2_analysis.jsonl"
    shadow_analysis = tmp_path / "gool_bot2_shadow_analysis.jsonl"

    _write_json(main, [
        {"match_id": "ag", "head": "another_goal", "minute": 30, "score": [0, 0], "result": "pending", "home": "AG Home", "away": "AG Away", "probability": 0.72, "created_at": "2026-09-03T12:00:00+00:00"},
        {"match_id": "fh", "head": "goal_before_ht", "minute": 35, "score": [1, 0], "result": "pending", "home": "FH Home", "away": "FH Away", "probability": 0.70, "created_at": "2026-09-03T12:01:00+00:00"},
        {"match_id": "p2", "head": "two_more_goals", "minute": 38, "score": [1, 2], "result": "pending", "home": "Plus Home", "away": "Plus Away", "probability": 0.78, "created_at": "2026-09-03T12:02:00+00:00"},
    ])
    _write_json(shadow, [
        {"match_id": "btts", "head": "both_teams_to_score", "minute": 38, "score": [1, 0], "result": "pending", "home": "BTTS Home", "away": "BTTS Away", "probability": 0.74, "created_at": "2026-09-03T12:03:00+00:00"},
        {"match_id": "team", "head": "team_to_score", "minute": 38, "score": [1, 2], "result": "pending", "home": "Team Home", "away": "Team Away", "probability": 0.71, "signal_source": "xbet_value_override", "created_at": "2026-09-03T12:04:00+00:00"},
    ])
    main_analysis.write_text("", encoding="utf-8")
    shadow_analysis.write_text("", encoding="utf-8")

    monkeypatch.setenv("SHADOW_MARKET_JOURNAL", str(shadow))
    monkeypatch.setenv("SHADOW_MARKET_ANALYSIS", str(shadow_analysis))

    def fake_states(self, ids):
        return {mid: {"is_live": True, "is_finished": False} for mid in ids}

    monkeypatch.setattr(first_half_product.FlashscoreProvider, "event_states", fake_states)

    sections = first_half_product._render_in_game(main, main_analysis)
    text = "\n".join(sections)

    assert "Активных сигналов: <b>5</b>" in text
    assert "ЕЩЁ ГОЛ" in text
    assert "ГОЛ ДО ПЕРЕРЫВА" in text
    assert "ЕЩЁ +2 ГОЛА" in text
    assert "ОБЕ ЗАБЬЮТ — ДА" in text
    assert "КОМАНДА ЗАБЬЁТ" in text
    assert "BTTS Home — BTTS Away" in text
    assert "Team Home — Team Away" in text
