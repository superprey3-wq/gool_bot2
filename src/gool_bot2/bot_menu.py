from __future__ import annotations

import html
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
ACTIVE_HEADS = ALL_HEADS
MENU_KEYBOARD = {
    "keyboard": [[{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}], [{"text": "🧠 Анализ"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _h(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _team_text(value, fallback="?") -> str:
    if isinstance(value, dict):
        value = value.get("team") or value.get("name") or fallback
    return str(value or fallback)


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


def _dedupe(rows):
    latest = {}
    for row in rows:
        score = row.get("score") or [0, 0]
        try:
            home, away = int(score[0] or 0), int(score[1] or 0)
        except Exception:
            home = away = 0
        latest[(str(row.get("match_id") or ""), str(row.get("head") or ""), int(row.get("minute") or 0), home, away)] = row
    return list(latest.values())


def _half(minute):
    try:
        return "1Т" if int(minute or 0) <= 45 else "2Т"
    except Exception:
        return "?"


def _parse_dt(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _tz():
    try:
        return ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return timezone.utc


def _experiment_journal(main_path):
    env = os.getenv("SHADOW_MARKET_JOURNAL", "").strip()
    return Path(env) if env else main_path.with_name("gool_bot2_shadow_markets.json")


def _experiment_analysis(main_path):
    env = os.getenv("SHADOW_MARKET_ANALYSIS", "").strip()
    return Path(env) if env else main_path.with_name("gool_bot2_shadow_analysis.jsonl")


def _stats(rows):
    out = []
    total_w = total_l = total_p = 0
    for head in ALL_HEADS:
        counts = Counter(str(r.get("result") or "pending").lower() for r in rows if str(r.get("head") or "") == head)
        w, l, p = counts["won"], counts["lost"], counts["pending"]
        total_w += w
        total_l += l
        total_p += p
        out.append(f"{HEAD_LABELS[head]}: ✅ {w} · ❌ {l} · ⏳ {p} · <b>{_pct(w, l)}</b>")
    out += [
        f"Всего закрыто: <b>{total_w + total_l}</b> · проход: <b>{_pct(total_w, total_l)}</b>",
        f"Ожидают результата: <b>{total_p}</b>",
    ]
    return out


def report_text(path: Path, experiment_path: Path | None = None):
    experiment_path = experiment_path or _experiment_journal(path)
    rows = [r for r in _dedupe(_load_rows(path)) if str(r.get("head") or "") in MAIN_HEADS]
    rows += [r for r in _dedupe(_load_rows(experiment_path)) if str(r.get("head") or "") in EXPERIMENT_HEADS]
    tz = _tz()
    today = datetime.now(tz).date()
    today_rows = []
    for row in rows:
        dt = _parse_dt(row.get("created_at") or row.get("captured_at"))
        if dt and dt.astimezone(tz).date() == today:
            today_rows.append(row)
    lines = [
        "📊 <b>ОТЧЁТ GOOL Bot 2</b>",
        "",
        f"📅 <b>СЕГОДНЯ · {today.strftime('%d.%m.%Y')}</b>",
        *_stats(today_rows),
        "",
        "────────────",
        "",
        "📚 <b>ОБЩИЙ ЗА ВСЁ ВРЕМЯ</b>",
        *_stats(rows),
    ]
    return "\n".join(lines)


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


def _in_game_row(row, states):
    score = row.get("score") or [0, 0]
    live = states.get(str(row.get("match_id") or "")) or {}
    live_score = live.get("score") or score
    minute = int(live.get("minute") or row.get("minute") or 0)
    league = str(row.get("league") or "").strip()
    league_text = f" · {_h(league)}" if league else ""
    source = "GOOL" if str(row.get("signal_source") or "") == "gool_live_analyzer" else "MODEL"
    value = float(row.get("gool_signal_strength") or row.get("probability") or 0)
    metric = f"шанс <b>{value * 100:.0f}/100</b>" if source == "GOOL" else f"P <b>{value * 100:.1f}%</b>"
    return (
        f"{_h(row.get('home','?'))} — {_h(row.get('away','?'))}{league_text}\n"
        f"сейчас {minute}' ({_half(minute)}) · {live_score[0]}:{live_score[1]} · {source} {metric}\n"
        f"↳ вход: {row.get('minute',0)}' ({_half(row.get('minute'))}) · {score[0]}:{score[1]}"
    )


def in_game_sections(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    rows = _dedupe(_load_rows(journal_path))
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


def _short_block(value):
    x = str(value)
    if x.startswith("probability="):
        return "ниже порога модели"
    if x.startswith("gool_pressure="):
        return "GOOL давление ниже порога"
    if x.startswith("gool_strength="):
        return "шанс +2 ниже порога"
    if x.startswith("model_disagreement="):
        return "модели расходятся"
    if x.startswith("post_goal_cooldown_"):
        return "пауза после гола"
    if x.startswith("entry_window_closed_") or x == "window_closed":
        return "окно входа закрыто"
    if x.startswith("max_open=") or x.startswith("max_entries="):
        return "лимит входов"
    if x == "duplicate_pending_signal":
        return "уже есть сигнал"
    if x == "history":
        return "PREMATCH: мало истории"
    if x == "avg_goals":
        return "PREMATCH: низкий средний тотал"
    if x == "too_many_0_1_goal_games":
        return "PREMATCH: много матчей 0–1 гол"
    if x == "prematch_score":
        return "PREMATCH: общий балл ниже порога"
    if x.startswith("evidence="):
        return "LIVE: мало доступных показателей"
    if x.startswith("cum="):
        return "LIVE: общее давление ниже порога"
    if x.startswith("5m="):
        return "LIVE: слабые последние 5 минут"
    if x.startswith("10m="):
        return "LIVE: слабые последние 10 минут"
    if x == "no_recent_threat" or x.endswith(":side_no_recent_threat") or x == "side_no_recent_threat":
        return "LIVE: нет свежей угрозы"
    if x == "no_quality_threat" or x.endswith(":side_no_quality_threat") or x == "side_no_quality_threat":
        return "LIVE: нет качественной угрозы"
    if x == "no_team_passed":
        return "ни одна команда не прошла фильтр"
    if x.endswith(":side_pressure_low") or x == "side_pressure_low":
        return "давление команды ниже порога"
    if x.endswith(":side_evidence_low") or x == "side_evidence_low":
        return "мало статистики по команде"
    if x.endswith(":side_prematch_scoring_profile_low") or x == "side_prematch_scoring_profile_low":
        return "PREMATCH: команда редко забивает"
    if x == "warmup":
        return "ещё идёт LIVE-прогрев"
    if x == "btts_already_won":
        return "обе уже забили"
    return x


def _ag_details(row):
    if str(row.get("head") or "") != "another_goal":
        return []
    details = ((row.get("gool_analyzer") or {}).get("details") or {})
    raw = [str(x) for x in (details.get("prematch") or {}).get("blocks") or []]
    raw += [str(x) for x in (details.get("live") or {}).get("blocks") or []]
    out = []
    for item in raw:
        label = _short_block(item)
        if label not in out:
            out.append(label)
    return out


def _row_blocks(row):
    base = [_short_block(x) for x in row.get("blocks") or []]
    details = _ag_details(row)
    if "gool_analyzer_rejected" in base and details:
        base = [x for x in base if x != "gool_analyzer_rejected"] + details
    out = []
    for item in base:
        if item not in out:
            out.append(item)
    return out


def _fresh(row, now):
    dt = _parse_dt(row.get("captured_at"))
    max_age = float(os.getenv("ANALYSIS_ONLINE_MAX_AGE_MINUTES", "5"))
    minute = int(row.get("minute") or 0)
    if dt is None:
        return False
    age = (now - dt.astimezone(timezone.utc)).total_seconds() / 60.0
    return -1 <= age <= max_age and 0 < minute <= 75


def _read_analysis(path, heads, now):
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
            if head in EXPERIMENT_HEADS:
                row = dict(row)
                if isinstance(row.get("home"), dict):
                    row.setdefault("market_home", row.get("home"))
                    row["home"] = _team_text(row.get("home"))
                if isinstance(row.get("away"), dict):
                    row.setdefault("market_away", row.get("away"))
                    row["away"] = _team_text(row.get("away"))
            key = (mid, head)
            if key not in latest or str(row.get("captured_at") or "") >= str(latest[key].get("captured_at") or ""):
                latest[key] = row
    except Exception:
        return []
    return list(latest.values())


def _analysis_value(row):
    try:
        return float(row.get("probability") or row.get("gool_confidence") or row.get("confidence_score") or 0)
    except Exception:
        return 0.0


def analysis_text(path: Path, experiment_path: Path | None = None):
    experiment_path = experiment_path or _experiment_analysis(path)
    now = datetime.now(timezone.utc)
    rows = _read_analysis(path, MAIN_HEADS, now) + _read_analysis(experiment_path, EXPERIMENT_HEADS, now)
    rows.sort(key=lambda r: str(r.get("captured_at") or ""), reverse=True)
    if not rows:
        return "🧠 <b>АНАЛИЗ ОНЛАЙН</b>\n\nСейчас нет свежих онлайн-матчей в рабочем окне до 75'."

    block_counts = Counter()
    ag_counts = Counter()
    for row in rows:
        for item in _row_blocks(row):
            block_counts[item] += 1
        if str(row.get("head") or "") == "another_goal":
            for item in _ag_details(row):
                ag_counts[item] += 1

    online_matches = len({str(r.get("match_id") or "") for r in rows})
    ready = sum(1 for r in rows if str(r.get("decision") or "") == "SIGNAL")
    parts = [
        "🧠 <b>АНАЛИЗ ОНЛАЙН · 4 СТРАТЕГИИ</b>",
        "⚽ Ещё гол — MODEL + PREMATCH + LIVE\n🔥 Ещё +2 гола — GOOL LIVE\n💜 Обе забьют — Да\n🔵 Команда забьёт",
        f"Онлайн матчей: <b>{online_matches}</b> · текущих оценок: <b>{len(rows)}</b> · SIGNAL: <b>{ready}</b>",
    ]
    if block_counts:
        parts.append("<b>Основные блокировки сейчас:</b>\n" + "\n".join(f"• {_h(name)}: {count}" for name, count in block_counts.most_common(6)))
    if ag_counts:
        parts.append("<b>Почему блокируется ⚽ Ещё гол:</b>\n" + "\n".join(f"• {_h(name)}: {count}" for name, count in ag_counts.most_common(5)))
    parts.append("<b>Ближайшие входы онлайн:</b>")

    candidates = sorted(rows, key=_analysis_value, reverse=True)
    shown = 0
    for row in candidates:
        score = row.get("score") or [0, 0]
        head = str(row.get("head") or "")
        decision = "🔥 SIGNAL" if str(row.get("decision") or "") == "SIGNAL" else "⏳ WAIT"
        value = _analysis_value(row)
        value_text = f"{value * 100:.1f}%" if head == "another_goal" else f"{value * 100:.0f}/100"
        blocks = _row_blocks(row)
        block_text = ", ".join(_h(x) for x in blocks[:3]) if blocks else "готово"
        team = f" · {_h(row.get('team'))}" if head == "team_to_score" and row.get("team") else ""
        block = (
            f"{HEAD_LABELS.get(head, _h(head))} · {decision}{team}\n"
            f"{_h(_team_text(row.get('home','?')))} — {_h(_team_text(row.get('away','?')))} · {row.get('minute',0)}' ({_half(row.get('minute'))}) · "
            f"{score[0]}:{score[1]} · <b>{value_text}</b>\n"
            f"↳ {block_text}"
        )
        if len("\n\n".join(parts + [block])) > 3650:
            break
        parts.append(block)
        shown += 1
        if shown >= 8:
            break

    if shown < len(candidates):
        note = f"<i>Показано {shown} из {len(candidates)} текущих оценок — только самые близкие к входу.</i>"
        if len("\n\n".join(parts + [note])) <= 3850:
            parts.append(note)

    return "\n\n".join(parts)
