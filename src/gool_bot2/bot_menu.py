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
    "prefilter": "🔎 Предфильтр",
    "model": "🧠 Модель",
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
    for head in ("another_goal", "goal_before_ht", "over_2_5", "both_teams_to_score"):
        label = HEAD_LABELS[head]
        selected = [r for r in rows if str(r.get("head")) == head]
        counts = Counter(str(r.get("result") or "pending").lower() for r in selected)
        won, lost, pending = counts["won"], counts["lost"], counts["pending"]
        total_won += won
        total_lost += lost
        total_pending += pending
        lines.append(f"{label}: ✅ {won} · ❌ {lost} · ⏳ {pending} · {_pct(won, lost)}")
    lines += [
        "",
        f"Всего закрыто: <b>{total_won + total_lost}</b> · проход: <b>{_pct(total_won, total_lost)}</b>",
        f"Ожидают результата: <b>{total_pending}</b>",
    ]
    return "\n".join(lines)


def in_game_text(journal_path: Path) -> str:
    try:
        rows = json.loads(journal_path.read_text("utf-8")) if journal_path.exists() else []
    except Exception:
        rows = []
    active = [
        r for r in rows
        if bool(r.get("in_game")) and str(r.get("result") or "pending").lower() == "pending"
    ] if isinstance(rows, list) else []
    if not active:
        return "🟢 <b>В ИГРЕ</b>\n\nСейчас отмеченных активных входов нет."
    lines = ["🟢 <b>В ИГРЕ</b>", ""]
    for row in active[-12:]:
        score = row.get("score") or [0, 0]
        lines.append(
            f"{HEAD_LABELS.get(str(row.get('head')), str(row.get('head')))}\n"
            f"{row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' · "
            f"{score[0]}:{score[1]} · P {float(row.get('probability') or 0)*100:.1f}%"
        )
    return "\n\n".join(lines)


def _short_block(reason: str) -> str:
    mapping = {
        "prefilter_rejected": "не прошёл предфильтр",
        "model_unavailable": "модель не загрузилась",
        "model_output_missing": "нет выхода модели",
        "warmup_until_10": "до 10'",
        "second_half_warmup_until_55": "до 55' во 2Т",
        "first_half_signal_window_closed_25": "окно 1Т закрыто",
        "collecting_until_15": "сбор до 15'",
        "halftime_model_requires_halftime": "только перерыв",
        "first_half_zero_zero_only": "1Т только 0:0",
        "over25_already_won": "ТБ2.5 уже сыграл",
        "btts_already_won": "ОЗ уже сыграл",
        "duplicate_pending_signal": "уже есть сигнал",
    }
    if reason in mapping:
        return mapping[reason]
    if reason.startswith("score=") or reason.startswith("probability="):
        return "ниже порога " + reason
    if reason.startswith("model_disagreement="):
        return "модели расходятся"
    if reason.startswith("post_goal_cooldown_"):
        return "пауза после гола"
    if reason.startswith("entry_window_closed_"):
        return "окно входа закрыто"
    if reason.startswith("max_open=") or reason.startswith("max_entries="):
        return "лимит входов"
    return reason


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
                if key[0] and key[1]:
                    latest[key] = row
    except Exception:
        return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nНе удалось прочитать текущий анализ."

    all_rows = sorted(latest.values(), key=lambda r: str(r.get("captured_at") or ""), reverse=True)
    if not all_rows:
        return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nПока нет LIVE-данных."

    funnel_rows = [r for r in all_rows if str(r.get("head")) == "prefilter"]
    live_matches = len({str(r.get("match_id")) for r in funnel_rows})
    prefilter_pass = sum(1 for r in funnel_rows if r.get("decision") == "PASS")
    model_rows = [r for r in all_rows if str(r.get("head")) in {"another_goal", "goal_before_ht", "over_2_5", "both_teams_to_score"}]
    signal_count = sum(1 for r in model_rows if r.get("decision") == "SIGNAL")

    blocker_counts: Counter[str] = Counter()
    for row in all_rows:
        for reason in row.get("blocks") or []:
            blocker_counts[_short_block(str(reason))] += 1

    top_rows = sorted(model_rows, key=lambda r: float(r.get("probability") or 0), reverse=True)[:8]
    lines = [
        "🧠 <b>LIVE-АНАЛИЗ</b>",
        f"LIVE матчей в воронке: <b>{live_matches}</b>",
        f"Прошли предфильтр: <b>{prefilter_pass}</b>",
        f"Модельных оценок: <b>{len(model_rows)}</b> · SIGNAL: <b>{signal_count}</b>",
    ]

    if blocker_counts:
        lines += ["", "<b>Что чаще всего блокирует:</b>"]
        for reason, count in blocker_counts.most_common(7):
            lines.append(f"• {reason}: {count}")

    if top_rows:
        lines += ["", "<b>Самые близкие к входу:</b>"]
        for row in top_rows:
            score = row.get("score") or [0, 0]
            decision = "🔥 SIGNAL" if row.get("decision") == "SIGNAL" else "⏳ WAIT"
            blocks = [_short_block(str(x)) for x in (row.get("blocks") or [])]
            block_text = ", ".join(blocks[:2]) if blocks else "нет блоков"
            lines.append(
                f"{HEAD_LABELS.get(str(row.get('head')), str(row.get('head')))} · {decision}\n"
                f"{row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' · "
                f"{score[0]}:{score[1]} · P <b>{float(row.get('probability') or 0)*100:.1f}%</b>\n"
                f"↳ {block_text}"
            )
    else:
        lines += ["", "До модельной оценки пока не дошёл ни один матч — смотри предфильтр выше."]

    return "\n\n".join(lines)
