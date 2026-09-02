from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HEAD_LABELS = {
    "another_goal": "⚽ Ещё гол",
    "two_more_goals": "🔥 Ещё +2 гола",
    "both_teams_to_score": "💜 Обе забьют — Да",
    "team_to_score": "🔵 Команда забьёт",
    "prefilter": "🔎 Предфильтр",
    "model": "🧠 Модель",
}
MAIN_HEADS = ("another_goal", "two_more_goals")
EXPERIMENT_HEADS = ("both_teams_to_score", "team_to_score")
ALL_HEADS = MAIN_HEADS + EXPERIMENT_HEADS
MENU_KEYBOARD = {
    "keyboard": [[{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}], [{"text": "🧠 Анализ"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _pct(w, l):
    total = w + l
    return "—" if not total else f"{w / total * 100:.1f}%"


def _load_rows(path):
    if path is None:
        return []
    try:
        rows = json.loads(path.read_text("utf-8")) if path.exists() else []
    except Exception:
        rows = []
    return rows if isinstance(rows, list) else []


def _dedupe_signals(rows):
    latest = {}
    for row in rows:
        score = row.get("score") or [0, 0]
        try:
            home, away = int(score[0] or 0), int(score[1] or 0)
        except Exception:
            home = away = 0
        key = (
            str(row.get("match_id") or ""),
            str(row.get("head") or ""),
            int(row.get("minute") or 0),
            home,
            away,
        )
        latest[key] = row
    return list(latest.values())


def _latest_live_states(path):
    if path is None or not path.exists():
        return {}
    latest = {}
    try:
        for line in path.open("r", encoding="utf-8"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            mid = str(row.get("match_id") or "")
            if mid and (mid not in latest or str(row.get("captured_at") or "") >= str(latest[mid].get("captured_at") or "")):
                latest[mid] = row
    except Exception:
        return {}
    return latest


def _half(minute):
    try:
        return "1Т" if int(minute or 0) <= 45 else "2Т"
    except Exception:
        return "?"


def _report_timezone():
    name = os.getenv("REPORT_TIMEZONE", "Europe/Moscow")
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _parse_dt(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _is_today(row, today, tz):
    dt = _parse_dt(row.get("created_at") or row.get("captured_at"))
    return bool(dt and dt.astimezone(tz).date() == today)


def _stats_lines(rows, heads):
    lines = []
    total_w = total_l = total_p = 0
    for head in heads:
        selected = [r for r in rows if str(r.get("head") or "") == head]
        counts = Counter(str(r.get("result") or "pending").lower() for r in selected)
        w, l, p = counts["won"], counts["lost"], counts["pending"]
        total_w += w
        total_l += l
        total_p += p
        lines.append(f"{HEAD_LABELS[head]}: ✅ {w} · ❌ {l} · ⏳ {p} · <b>{_pct(w, l)}</b>")
    lines.append(f"Всего закрыто: <b>{total_w + total_l}</b> · проход: <b>{_pct(total_w, total_l)}</b>")
    lines.append(f"Ожидают результата: <b>{total_p}</b>")
    return lines


def report_text(path: Path, experiment_path: Path | None = None):
    main_rows = _dedupe_signals(_load_rows(path))
    exp_rows = _dedupe_signals(_load_rows(experiment_path))
    rows = [r for r in main_rows if str(r.get("head") or "") in MAIN_HEADS]
    rows += [r for r in exp_rows if str(r.get("head") or "") in EXPERIMENT_HEADS]

    tz = _report_timezone()
    today = datetime.now(tz).date()
    today_rows = [r for r in rows if _is_today(r, today, tz)]

    lines = [
        "📊 <b>ОТЧЁТ GOOL Bot 2</b>",
        "",
        f"📅 <b>СЕГОДНЯ · {today.strftime('%d.%m.%Y')}</b>",
        *_stats_lines(today_rows, ALL_HEADS),
        "",
        "────────────",
        "",
        "📚 <b>ОБЩИЙ ЗА ВСЁ ВРЕМЯ</b>",
        *_stats_lines(rows, ALL_HEADS),
        "",
        "<i>Статистика новых рынков считается отдельно, но показывается в общем отчёте.</i>",
    ]
    return "\n".join(lines)


def _in_game_row(r, states):
    ss = r.get("score") or [0, 0]
    live = states.get(str(r.get("match_id") or "")) or {}
    ls = live.get("score") or ss
    lm = int(live.get("minute") or r.get("minute") or 0)
    league = str(r.get("league") or "").strip()
    ll = f" · {league}" if league else ""
    src = "GOOL" if str(r.get("signal_source") or "") == "gool_live_analyzer" else "MODEL"
    value = float(r.get("gool_signal_strength") or r.get("probability") or 0)
    metric = f"шанс <b>{value * 100:.0f}/100</b>" if src == "GOOL" else f"P <b>{value * 100:.1f}%</b>"
    period = _half(r.get("minute"))
    current_half = _half(lm)
    return (
        f"{r.get('home','?')} — {r.get('away','?')}{ll}\n"
        f"сейчас {lm}' ({current_half}) · {ls[0]}:{ls[1]} · {src} {metric}\n"
        f"↳ вход: {r.get('minute',0)}' ({period}) · {ss[0]}:{ss[1]}"
    )


def in_game_sections(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    rows = _dedupe_signals(_load_rows(journal_path))
    states = _latest_live_states(analysis_path)
    pending = [
        r for r in rows
        if str(r.get("result") or "pending").lower() == "pending"
        and str(r.get("head") or "") in MAIN_HEADS
    ]
    pending.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    if not pending:
        return ["🟢 <b>В ИГРЕ</b>\n\nАктивных сигналов Ещё гол / Ещё +2 гола сейчас нет."]
    groups = (("another_goal", "⚽ <b>ЕЩЁ ГОЛ</b>"), ("two_more_goals", "🔥 <b>ЕЩЁ +2 ГОЛА</b>"))
    messages = [f"🟢 <b>В ИГРЕ</b>\nАктивных сигналов: <b>{len(pending)}</b>"]
    for head, title in groups:
        selected = [r for r in pending if str(r.get("head") or "") == head]
        if not selected:
            continue
        parts = [f"{title} · <b>{len(selected)}</b>"] + [f"<b>{i}.</b> {_in_game_row(r, states)}" for i, r in enumerate(selected, 1)]
        chunk = parts[0]
        for part in parts[1:]:
            candidate = chunk + "\n\n" + part
            if len(candidate) > 3800:
                messages.append(chunk)
                chunk = title + " · продолжение\n\n" + part
            else:
                chunk = candidate
        messages.append(chunk)
    return messages


def in_game_text(journal_path: Path, analysis_path: Path | None = None) -> str:
    return in_game_sections(journal_path, analysis_path)[0]


def _short_block(reason):
    if reason.startswith("probability="):
        return "ниже порога модели"
    if reason.startswith("gool_pressure="):
        return "GOOL давление ниже порога"
    if reason.startswith("gool_strength="):
        return "шанс +2 ниже порога"
    if reason.startswith("model_disagreement="):
        return "модели расходятся"
    if reason.startswith("post_goal_cooldown_"):
        return "пауза после гола"
    if reason.startswith("entry_window_closed_") or reason == "window_closed":
        return "окно входа закрыто"
    if reason.startswith("max_open=") or reason.startswith("max_entries="):
        return "лимит входов"
    if reason == "duplicate_pending_signal":
        return "уже есть сигнал"
    if reason == "history":
        return "PREMATCH: мало истории"
    if reason == "avg_goals":
        return "PREMATCH: низкий средний тотал"
    if reason == "too_many_0_1_goal_games":
        return "PREMATCH: много матчей 0–1 гол"
    if reason == "prematch_score":
        return "PREMATCH: общий балл ниже порога"
    if reason.startswith("evidence="):
        return "LIVE: мало доступных показателей"
    if reason.startswith("cum="):
        return "LIVE: общее давление ниже порога"
    if reason.startswith("5m="):
        return "LIVE: слабые последние 5 минут"
    if reason.startswith("10m="):
        return "LIVE: слабые последние 10 минут"
    if reason == "no_recent_threat" or reason.endswith(":side_no_recent_threat"):
        return "LIVE: нет свежей угрозы"
    if reason == "no_quality_threat" or reason.endswith(":side_no_quality_threat"):
        return "LIVE: нет качественной угрозы"
    if reason == "no_team_passed":
        return "ни одна команда не прошла фильтр"
    if reason.endswith(":side_pressure_low") or reason == "side_pressure_low":
        return "давление команды ниже порога"
    if reason.endswith(":side_evidence_low") or reason == "side_evidence_low":
        return "мало статистики по команде"
    if reason.endswith(":side_prematch_scoring_profile_low") or reason == "side_prematch_scoring_profile_low":
        return "PREMATCH: команда редко забивает"
    if reason == "warmup":
        return "ещё идёт LIVE-прогрев"
    if reason == "btts_already_won":
        return "обе уже забили"
    return reason


def _another_goal_detail_blocks(r):
    if str(r.get("head") or "") != "another_goal":
        return []
    analyzer = r.get("gool_analyzer") or {}
    details = analyzer.get("details") or {}
    pre = details.get("prematch") or {}
    live = details.get("live") or {}
    raw = [str(x) for x in pre.get("blocks") or []] + [str(x) for x in live.get("blocks") or []]
    seen = set()
    out = []
    for item in raw:
        label = _short_block(item)
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _row_blocks(r):
    base = [_short_block(str(x)) for x in r.get("blocks") or []]
    details = _another_goal_detail_blocks(r)
    if "gool_analyzer_rejected" in base and details:
        base = [x for x in base if x != "gool_analyzer_rejected"] + details
    seen = set()
    out = []
    for item in base:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _fresh(row, now):
    dt = _parse_dt(row.get("captured_at"))
    if dt is None:
        return False
    max_age = float(os.getenv("ANALYSIS_ONLINE_MAX_AGE_MINUTES", "5"))
    age = (now - dt.astimezone(timezone.utc)).total_seconds() / 60.0
    minute = int(row.get("minute") or 0)
    return -1.0 <= age <= max_age and 0 < minute <= 75


def _read_latest_analysis(path, heads, now):
    if path is None or not path.exists():
        return []
    latest = {}
    try:
        for line in path.open("r", encoding="utf-8"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            head = str(row.get("head") or "")
            mid = str(row.get("match_id") or "")
            if not mid or head not in heads or not _fresh(row, now):
                continue
            key = (mid, head)
            if key not in latest or str(row.get("captured_at") or "") >= str(latest[key].get("captured_at") or ""):
                latest[key] = row
    except Exception:
        return []
    return list(latest.values())


def analysis_text(path: Path, experiment_path: Path | None = None):
    now = datetime.now(timezone.utc)
    rows = _read_latest_analysis(path, MAIN_HEADS, now)
    rows += _read_latest_analysis(experiment_path, EXPERIMENT_HEADS, now)
    rows.sort(key=lambda r: str(r.get("captured_at") or ""), reverse=True)
    if not rows:
        return "🧠 <b>АНАЛИЗ ОНЛАЙН</b>\n\nСейчас нет свежих онлайн-матчей в рабочем окне до 75'."

    blocks = Counter()
    ag_blocks = Counter()
    for row in rows:
        for item in _row_blocks(row):
            blocks[item] += 1
        if str(row.get("head") or "") == "another_goal":
            for item in _another_goal_detail_blocks(row):
                ag_blocks[item] += 1

    online_matches = len({str(r.get("match_id") or "") for r in rows})
    ready = sum(1 for r in rows if str(r.get("decision") or "") == "SIGNAL")
    lines = [
        "🧠 <b>АНАЛИЗ ОНЛАЙН · 4 СТРАТЕГИИ</b>",
        "⚽ Ещё гол — MODEL + PREMATCH + LIVE\n🔥 Ещё +2 гола — GOOL LIVE\n💜 Обе забьют — Да\n🔵 Команда забьёт",
        f"Онлайн матчей: <b>{online_matches}</b> · текущих оценок: <b>{len(rows)}</b> · SIGNAL: <b>{ready}</b>",
    ]

    if blocks:
        lines += ["", "<b>Основные блокировки сейчас:</b>"] + [f"• {name}: {count}" for name, count in blocks.most_common(8)]
    if ag_blocks:
        lines += ["", "<b>Почему блокируется ⚽ Ещё гол:</b>"] + [f"• {name}: {count}" for name, count in ag_blocks.most_common(6)]

    def score_value(row):
        return float(row.get("probability") or row.get("gool_confidence") or row.get("confidence_score") or 0)

    top = sorted(rows, key=score_value, reverse=True)[:12]
    lines += ["", "<b>Ближайшие входы онлайн:</b>"]
    for row in top:
        score = row.get("score") or [0, 0]
        decision = "🔥 SIGNAL" if str(row.get("decision") or "") == "SIGNAL" else "⏳ WAIT"
        value = score_value(row)
        value_text = f"{value * 100:.1f}%" if str(row.get("head") or "") == "another_goal" else f"{value * 100:.0f}/100"
        block_text = ", ".join(_row_blocks(row)[:3]) if _row_blocks(row) else "готово"
        period = _half(row.get("minute"))
        head = str(row.get("head") or "")
        team_line = f" · {row.get('team')}" if head == "team_to_score" and row.get("team") else ""
        lines.append(
            f"{HEAD_LABELS.get(head, head)} · {decision}{team_line}\n"
            f"{row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' ({period}) · {score[0]}:{score[1]} · <b>{value_text}</b>\n"
            f"↳ {block_text}"
        )
    return "\n\n".join(lines)
