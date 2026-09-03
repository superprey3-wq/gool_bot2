from __future__ import annotations

import html
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import load_signal_journal
from .multi_journal import settle_multi_journal
from .providers.flashscore import (
    FINISHED_COARSE_STATUS,
    FIRST_HALF_STATUS,
    HALFTIME_STATUS,
    SECOND_HALF_STATUS,
    FlashscoreProvider,
)


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def journal_path() -> Path:
    raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multi_journal.json"


def analysis_path() -> Path:
    raw = os.getenv("GOOL_MULTI_ANALYSIS_PATH", "").strip()
    if raw:
        return Path(raw)
    legacy = os.getenv("GOOL_MULTI_SHADOW_PATH", "").strip()
    return Path(legacy) if legacy else _runtime() / "live" / "gool_multi_analysis.jsonl"


def _parse_dt(value: Any) -> datetime | None:
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


def _pct(wins: int, losses: int) -> str:
    total = wins + losses
    return "—" if total <= 0 else f"{wins / total * 100:.1f}%"


def _profit(rows: list[dict[str, Any]]) -> float:
    return sum(float(row.get("profit_units") or 0.0) for row in rows if str(row.get("result") or "") in {"won", "lost", "push", "void"})


def _roi(rows: list[dict[str, Any]]) -> str:
    settled = [row for row in rows if str(row.get("result") or "") in {"won", "lost"}]
    if not settled:
        return "—"
    return f"{_profit(settled) / len(settled) * 100:+.1f}%"


def _stats_line(rows: list[dict[str, Any]]) -> str:
    counts = Counter(str(row.get("result") or "pending").lower() for row in rows)
    return (
        f"✅ <b>{counts['won']}</b> · ❌ <b>{counts['lost']}</b> · "
        f"⏳ <b>{counts['pending']}</b> · ↩️ <b>{counts['void'] + counts['push']}</b> · "
        f"проход <b>{_pct(counts['won'], counts['lost'])}</b> · "
        f"P/L <b>{_profit(rows):+.2f}u</b> · ROI <b>{_roi(rows)}</b>"
    )


def _synthetic_record(row: dict[str, Any], state: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[str, Any]:
    status = str(state.get("status_code") or "")
    finished = bool(state.get("is_finished")) or str(state.get("coarse_status") or "") == FINISHED_COARSE_STATUS
    if finished:
        minute = 90
    elif status == HALFTIME_STATUS:
        minute = 45
    elif status == SECOND_HALF_STATUS:
        minute = 46
    elif status == FIRST_HALF_STATUS:
        minute = max(1, int(row.get("minute") or 1))
    else:
        minute = int(row.get("minute") or 0)
    return {
        "match": {
            "flashscore_event_id": str(row.get("match_id") or ""),
            "home": row.get("home"),
            "away": row.get("away"),
            "league": row.get("league"),
            "minute": minute,
            "home_score": int(state.get("home_score") or 0),
            "away_score": int(state.get("away_score") or 0),
            "is_halftime": status == HALFTIME_STATUS,
            "is_finished": finished,
            "status_code": status,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": timeline}}},
    }


def reconcile_pending() -> int:
    path = journal_path()
    rows = load_signal_journal(path)
    pending = [row for row in rows if str(row.get("result") or "pending").lower() == "pending"]
    ids = {str(row.get("match_id") or "") for row in pending if str(row.get("match_id") or "")}
    if not ids:
        return 0
    provider = FlashscoreProvider()
    try:
        states = provider.event_states(ids)
    except Exception as exc:
        print(f"GOOL_MULTI_MENU_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        return 0
    changed = 0
    for row in pending:
        mid = str(row.get("match_id") or "")
        state = states.get(mid)
        if not state:
            continue
        need_timeline = (
            str(row.get("market_family") or "") == "first_half_total"
            or bool(state.get("is_finished"))
            or int(state.get("home_score") or 0) + int(state.get("away_score") or 0) > sum(row.get("score") or [0, 0])
        )
        timeline: list[dict[str, Any]] = []
        if need_timeline:
            try:
                timeline = provider.fetch_goal_timeline(mid)
            except Exception:
                timeline = []
        changed += len(settle_multi_journal(_synthetic_record(row, state, timeline), path))
    return changed


def report_text(_: Path | None = None, experiment_path: Path | None = None) -> str:
    del experiment_path
    reconcile_pending()
    rows = load_signal_journal(journal_path())
    tz = _tz()
    today = datetime.now(tz).date()
    today_rows = [
        row for row in rows
        if (dt := _parse_dt(row.get("created_at"))) is not None and dt.astimezone(tz).date() == today
    ]
    parts = [
        "📊 <b>GOOL MULTI · ЖУРНАЛ</b>",
        "Один открытый BEST BET на матч; WAIT в статистику ставок не попадает.",
        "",
        f"📅 <b>СЕГОДНЯ · {today.strftime('%d.%m.%Y')}</b>",
        _stats_line(today_rows),
        "",
        "📚 <b>ВСЁ ВРЕМЯ</b>",
        _stats_line(rows),
    ]
    if rows:
        groups: list[str] = []
        labels = {
            "another_goal": "⚽ Ещё гол",
            "goal_before_ht": "🟡 Гол до перерыва",
            "two_more_goals": "🔥 Ещё +2",
            "home_goal": "🔵 ИТБ1",
            "away_goal": "🔵 ИТБ2",
            "both_teams_to_score": "💜 ОЗ — Да",
        }
        for strategy, label in labels.items():
            selected = [row for row in rows if str(row.get("strategy") or "") == strategy]
            if selected:
                groups.append(f"{label}: {_stats_line(selected)}")
        if groups:
            parts += ["", "<b>По выбранным рынкам:</b>", *groups]
        override = sum(1 for row in rows if str(row.get("signal_source") or "") in {"MARKET_OVERRIDE", "VALUE_OVERRIDE"})
        if override:
            parts.append(f"Override-входов: <b>{override}</b>")
    return "\n".join(parts)


def _latest_analysis() -> dict[str, dict[str, Any]]:
    path = analysis_path()
    if not path.exists():
        return {}
    max_age = float(os.getenv("ANALYSIS_ONLINE_MAX_AGE_MINUTES", "5"))
    now = datetime.now(timezone.utc)
    latest: dict[str, dict[str, Any]] = {}
    try:
        for line in path.open("r", encoding="utf-8"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            mid = str(row.get("match_id") or "")
            dt = _parse_dt(row.get("created_at") or row.get("captured_at"))
            minute = int(row.get("minute") or 0)
            if not mid or dt is None or not (0 < minute <= 85):
                continue
            age = (now - dt.astimezone(timezone.utc)).total_seconds() / 60.0
            if not (-1.0 <= age <= max_age):
                continue
            if mid not in latest or str(row.get("created_at") or "") >= str(latest[mid].get("created_at") or ""):
                latest[mid] = row
    except Exception:
        return {}
    return latest


def _source_label(row: dict[str, Any]) -> str:
    source = str(row.get("signal_source") or "GOOL")
    return {"MARKET_OVERRIDE": "1xBet OVERRIDE", "VALUE_OVERRIDE": "VALUE OVERRIDE"}.get(source, "GOOL")


def in_game_sections(_: Path | None = None, analysis_path_arg: Path | None = None) -> list[str]:
    del analysis_path_arg
    reconcile_pending()
    rows = [row for row in load_signal_journal(journal_path()) if str(row.get("result") or "pending").lower() == "pending"]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not rows:
        return ["🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\nОткрытых Multi-ставок сейчас нет."]
    states = _latest_analysis()
    parts = [f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\nОткрытых ставок: <b>{len(rows)}</b>"]
    for index, row in enumerate(rows, 1):
        live = states.get(str(row.get("match_id") or "")) or {}
        live_score = live.get("score") or row.get("score") or [0, 0]
        live_minute = int(live.get("minute") or row.get("minute") or 0)
        score = row.get("score") or [0, 0]
        block = (
            f"<b>{index}.</b> {_h(row.get('home'))} — {_h(row.get('away'))}\n"
            f"сейчас {live_minute}' · {live_score[0]}:{live_score[1]}\n"
            f"🎯 <b>{_h(row.get('market'))} @ {float(row.get('odd') or 0):.2f}</b> · рейтинг {float(row.get('rating') or 0):.0f}/100\n"
            f"P {float(row.get('probability') or 0) * 100:.1f}% · value {float(row.get('value_edge_pp') or 0):+.1f} п.п. · {_h(_source_label(row))}\n"
            f"↳ вход {row.get('minute', 0)}' · {score[0]}:{score[1]}"
        )
        if len("\n\n".join(parts + [block])) > 3800:
            break
        parts.append(block)
    return ["\n\n".join(parts)]


def _expert_text(experts: dict[str, Any]) -> str:
    labels = {
        "another_goal": "AG",
        "goal_before_ht": "1Т",
        "two_more_goals": "+2",
        "home_goal": "H",
        "away_goal": "A",
        "btts": "ОЗ",
    }
    out: list[str] = []
    for key, label in labels.items():
        row = experts.get(key) or {}
        value = row.get("probability")
        if value is None:
            continue
        mark = "✓" if bool(row.get("passed", True)) else "×"
        out.append(f"{label} {float(value) * 100:.0f}{mark}")
    return " · ".join(out) or "нет экспертных оценок"


def _context_text(context: dict[str, Any]) -> str:
    prematch = context.get("prematch") or {}
    live = context.get("live") or {}
    pre = (
        f"PRE {prematch.get('home_recent', 0)}/{prematch.get('away_recent', 0)}"
        if prematch.get("available") else "PRE —"
    )
    xg = live.get("xg_total")
    shots = live.get("shots_total")
    sot = live.get("sot_total")
    xg_text = "—" if xg is None else f"{float(xg):.2f}"
    shots_text = "—" if shots is None else f"{float(shots):.0f}"
    sot_text = "—" if sot is None else f"{float(sot):.0f}"
    return f"{pre} · LIVE xG {xg_text} · уд {shots_text} · в створ {sot_text}"


def analysis_text(_: Path | None = None, experiment_path: Path | None = None) -> str:
    del experiment_path
    states = list(_latest_analysis().values())
    if not states:
        return "🧠 <b>GOOL MULTI · АНАЛИЗ</b>\n\nСейчас нет свежих Multi-оценок в рабочем окне до 85'."
    states.sort(key=lambda row: (str((row.get("router") or {}).get("status") or "") == "BET", float(((row.get("router") or {}).get("winner") or {}).get("rating") or 0)), reverse=True)
    bets = sum(1 for row in states if str((row.get("router") or {}).get("status") or "") == "BET")
    overrides = sum(
        1 for row in states
        if bool((((row.get("router") or {}).get("winner") or {}).get("market_override")))
        or bool((((row.get("router") or {}).get("winner") or {}).get("value_override")))
    )
    parts = [
        "🧠 <b>GOOL MULTI · АНАЛИЗ ОНЛАЙН</b>",
        "MODEL + PREMATCH + LIVE + 1xBet → один BEST BET / WAIT",
        f"Матчей: <b>{len(states)}</b> · BET: <b>{bets}</b> · WAIT: <b>{len(states) - bets}</b> · override: <b>{overrides}</b>",
    ]
    shown = 0
    for row in states:
        router = row.get("router") or {}
        winner = router.get("winner") or {}
        status = str(router.get("status") or "WAIT")
        score = row.get("score") or [0, 0]
        decision = "🔥 BET" if status == "BET" else "⏳ WAIT"
        if winner:
            market = f"{winner.get('label')} @ {float(winner.get('odd') or 0):.2f} · R {float(winner.get('rating') or 0):.0f}"
            market += f" · value {float(winner.get('value_edge_pp') or 0):+.1f} п.п. · steam {float(winner.get('market_pressure_pp') or 0):+.1f}"
        else:
            rejected = list(router.get("rejected") or [])
            market = "нет проходящего рынка"
            if rejected:
                top = rejected[0]
                blocks = ", ".join(str(x) for x in (top.get("blocks") or [])[:2])
                market = f"ближе всего {top.get('label')} · {blocks or 'WAIT'}"
        block = (
            f"<b>{_h(row.get('home'))} — {_h(row.get('away'))}</b> · {row.get('minute', 0)}' · {score[0]}:{score[1]} · {decision}\n"
            f"{_h(_expert_text(row.get('experts') or {}))}\n"
            f"{_h(_context_text(row.get('context') or {}))}\n"
            f"1xBet: {_h(market)}\n"
            f"↳ {_h(router.get('reason') or '')}"
        )
        if len("\n\n".join(parts + [block])) > 3750:
            break
        parts.append(block)
        shown += 1
        if shown >= 7:
            break
    if shown < len(states):
        parts.append(f"<i>Показано {shown} из {len(states)} матчей.</i>")
    return "\n\n".join(parts)
