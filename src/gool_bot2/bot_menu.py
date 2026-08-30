from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

HEAD_LABELS = {
    "another_goal": "⚽ Ещё гол",
    "goal_before_ht": "⏱ Гол до перерыва",
    "over_2_5": "📈 ТБ 2.5",
    "both_teams_to_score": "🤝 Обе забьют",
}

MENU_KEYBOARD: dict[str, Any] = {
    "keyboard": [
        [{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}],
        [{"text": "🧠 Анализ"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _pct(won: int, lost: int) -> str:
    total = won + lost
    return "—" if not total else f"{won / total * 100:.1f}%"


def report_text(journal_path: Path) -> str:
    try:
        rows = json.loads(journal_path.read_text("utf-8")) if journal_path.exists() else []
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    lines = ["📊 <b>ОТЧЁТ GOOL Bot 2</b>", ""]
    total_won = total_lost = total_pending = 0
    for head, label in HEAD_LABELS.items():
        selected = [r for r in rows if str(r.get("head")) == head]
        counts = Counter(str(r.get("result") or "pending").lower() for r in selected)
        won, lost, pending = counts["won"], counts["lost"], counts["pending"]
        total_won += won; total_lost += lost; total_pending += pending
        lines.append(f"{label}: ✅ {won} · ❌ {lost} · ⏳ {pending} · {_pct(won, lost)}")
    lines += ["", f"Всего закрыто: <b>{total_won + total_lost}</b> · проход: <b>{_pct(total_won, total_lost)}</b>", f"Ожидают результата: <b>{total_pending}</b>"]
    return "\n".join(lines)


def in_game_text(journal_path: Path) -> str:
    try:
        rows = json.loads(journal_path.read_text("utf-8")) if journal_path.exists() else []
    except Exception:
        rows = []
    active = [r for r in rows if bool(r.get("in_game")) and str(r.get("result") or "pending").lower() == "pending"] if isinstance(rows, list) else []
    if not active:
        return "🟢 <b>В ИГРЕ</b>\n\nСейчас отмеченных активных входов нет."
    lines = ["🟢 <b>В ИГРЕ</b>", ""]
    for row in active[-12:]:
        score = row.get("score") or [0, 0]
        lines.append(f"{HEAD_LABELS.get(str(row.get('head')), str(row.get('head')))}\n{row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' · {score[0]}:{score[1]} · P {float(row.get('probability') or 0)*100:.1f}%")
    return "\n\n".join(lines)


def analysis_text(analysis_path: Path) -> str:
    if not analysis_path.exists():
        return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nДанные анализа ещё не накоплены."
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with analysis_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                key = (str(row.get("match_id") or ""), str(row.get("head") or ""))
                if key[0] and key[1]: latest[key] = row
    except Exception:
        return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nНе удалось прочитать текущий анализ."
    rows = sorted(latest.values(), key=lambda r: str(r.get("captured_at") or ""), reverse=True)[:12]
    if not rows:
        return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nПока нет LIVE-кандидатов."
    lines = ["🧠 <b>LIVE-АНАЛИЗ</b>", "Последние оценки моделей:", ""]
    for row in rows:
        score = row.get("score") or [0, 0]
        decision = "🔥 SIGNAL" if row.get("decision") == "SIGNAL" else "⏳ WAIT"
        lines.append(f"{HEAD_LABELS.get(str(row.get('head')), str(row.get('head')))} · {decision}\n{row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' · {score[0]}:{score[1]} · P {float(row.get('probability') or 0)*100:.1f}%")
    return "\n\n".join(lines)
