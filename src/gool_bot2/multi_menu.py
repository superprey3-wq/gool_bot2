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
from .v4_prematch_settlement import settle_prematch_row
from .multi_public_metrics import source_label, strategy_bucket
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


def _unit_profit(row: dict[str, Any]) -> float | None:
    result = str(row.get("result") or "").lower()
    if result not in {"won", "lost", "push", "void"}:
        return None
    if row.get("profit_units") is not None:
        try:
            return float(row.get("profit_units"))
        except (TypeError, ValueError):
            pass
    if result == "lost":
        return -1.0
    if result in {"push", "void"}:
        return 0.0
    # Historical rows may predate profit_units. Reconstruct flat 1u P/L from
    # the actual taken price; parlay settlement stores effective_odd.
    try:
        odd = float(row.get("effective_odd") or row.get("odd") or 0.0)
    except (TypeError, ValueError):
        odd = 0.0
    return odd - 1.0 if odd > 1.0 else None


def _profit_info(rows: list[dict[str, Any]]) -> tuple[float, int]:
    profit = 0.0
    missing = 0
    for row in rows:
        if str(row.get("result") or "").lower() not in {"won", "lost", "push", "void"}:
            continue
        value = _unit_profit(row)
        if value is None:
            missing += 1
        else:
            profit += value
    return profit, missing


def _profit(rows: list[dict[str, Any]]) -> float:
    return _profit_info(rows)[0]


def _roi(rows: list[dict[str, Any]]) -> str:
    settled = [row for row in rows if str(row.get("result") or "").lower() in {"won", "lost"}]
    if not settled:
        return "—"
    profit, missing = _profit_info(settled)
    if missing:
        return f"{profit / max(1, len(settled) - missing) * 100:+.1f}%*"
    return f"{profit / len(settled) * 100:+.1f}%"


def _avg_odd(rows: list[dict[str, Any]]) -> str:
    odds = []
    for row in rows:
        try:
            odd = float(row.get("effective_odd") or row.get("odd") or 0.0)
        except (TypeError, ValueError):
            continue
        if odd > 1.0:
            odds.append(odd)
    return "—" if not odds else f"{sum(odds) / len(odds):.2f}"


def _stats_line(rows: list[dict[str, Any]]) -> str:
    counts = Counter(str(row.get("result") or "pending").lower() for row in rows)
    profit, missing = _profit_info(rows)
    pl = f"{profit:+.2f}u" + (f"*" if missing else "")
    return (
        f"✅ <b>{counts['won']}</b> · ❌ <b>{counts['lost']}</b> · "
        f"⏳ <b>{counts['pending']}</b> · ↩️ <b>{counts['void'] + counts['push']}</b> · "
        f"проход <b>{_pct(counts['won'], counts['lost'])}</b> · "
        f"ср.кэф <b>{_avg_odd(rows)}</b> · "
        f"P/L <b>{pl}</b> · ROI <b>{_roi(rows)}</b>"
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
        record = _synthetic_record(row, state, timeline)
        if str(row.get("origin") or "").casefold() == "prematch":
            # Prematch markets must NEVER use live early-win settlement.
            # They are settled only from an authoritative finished match.
            if not bool((record.get("match") or {}).get("is_finished")):
                continue
            fresh_rows = load_signal_journal(path)
            target = next((x for x in fresh_rows if str(x.get("entry_id") or x.get("entry_key") or "") == str(row.get("entry_id") or row.get("entry_key") or "")), None)
            if target is not None and settle_prematch_row(target, record):
                from .journal import save_signal_journal
                save_signal_journal(path, fresh_rows)
                changed += 1
        elif str(row.get("origin") or "").casefold() != "prematch_parlay":
            changed += len(settle_multi_journal(record, path))
    return changed


def _layer(row: dict[str, Any]) -> str:
    source = str(row.get("signal_source") or row.get("source") or "GOOL")
    return "STEAM" if "STEAM" in source.upper() else "GOOL"


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

    goal_before_ht = [
        row for row in rows
        if strategy_bucket(row.get("strategy")) == "goal_before_ht"
    ]
    another_goal = [
        row for row in rows
        if strategy_bucket(row.get("strategy")) == "another_goal"
    ]
    steam = [row for row in rows if _layer(row) == "STEAM"]

    parts = [
        "📊 <b>GOOL MULTI · ЖУРНАЛ</b>",
        "Только реально отправленные BEST BET. WAIT в статистику не попадает.",
        "",
        f"📅 <b>СЕГОДНЯ · {today.strftime('%d.%m.%Y')}</b>",
        _stats_line(today_rows),
        "",
        "📚 <b>ВСЯ НОВАЯ ЭПОХА</b>",
        _stats_line(rows),
        "",
        "<b>Две основные системы:</b>",
        f"🟡 Гол в 1-м тайме: {_stats_line(goal_before_ht)}",
        f"⚽ Ещё гол: {_stats_line(another_goal)}",
        "",
        "<b>Отдельная система прогруза:</b>",
        f"🔥 1xBet STEAM: {_stats_line(steam)}",
    ]

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


def _confidence(row: dict[str, Any]) -> float:
    try:
        return float(row.get("confidence_score") or row.get("rating") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_score(row: dict[str, Any]) -> float:
    try:
        value = row.get("event_score")
        if value is not None:
            return float(value)
        p = float(row.get("probability") or 0.0)
        return p * 100.0 if p <= 1.0 else p
    except (TypeError, ValueError):
        return 0.0


def in_game_sections(_: Path | None = None, analysis_path_arg: Path | None = None) -> list[str]:
    del analysis_path_arg
    reconcile_pending()
    rows = [
        row for row in load_signal_journal(journal_path())
        if str(row.get("result") or "pending").lower() == "pending"
    ]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not rows:
        return ["🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\nОткрытых ставок сейчас нет."]

    states = _latest_analysis()
    parts = [f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\nОткрыто: <b>{len(rows)}</b>"]
    for index, row in enumerate(rows, 1):
        live = states.get(str(row.get("match_id") or "")) or {}
        is_prematch = str(row.get("origin") or "").lower() == "prematch"
        live_score = live.get("score") or row.get("current_score") or row.get("score") or [0, 0]
        live_minute = int(live.get("minute") or row.get("current_minute") or row.get("minute") or 0)
        entry_score = row.get("score") or [0, 0]
        pressure = float(row.get("market_pressure_pp") or 0.0)
        reason = str(row.get("selection_reason") or row.get("reason") or "")
        entry_label = "ставка до матча" if is_prematch else f"вход {row.get('minute', 0)}' {entry_score[0]}:{entry_score[1]}"
        block = (
            f"<b>{index}. {_h(row.get('home'))} — {_h(row.get('away'))}</b>\n"
            f"сейчас <b>{live_minute}' · {live_score[0]}:{live_score[1]}</b>\n"
            f"🎯 <b>{_h(row.get('market'))} @ {float(row.get('odd') or 0):.2f}</b>\n"
            f"🧠 событие <b>{_event_score(row):.0f}/100</b> · уверенность <b>{_confidence(row):.0f}/100</b> · {_h('PREMATCH' if is_prematch else source_label(row.get('signal_source')))}\n"
            f"📈 1xBet {pressure:+.1f} п.п. · {_h(entry_label)}\n"
            f"↳ {_h(reason)}"
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
        state = str(row.get("state") or ("PASS" if row.get("passed") else "WAIT"))
        out.append(f"{label} {float(value) * 100:.0f} {state}")
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
        return "🧠 <b>GOOL MULTI · АНАЛИЗ</b>\n\nСейчас нет свежих оценок в рабочем окне до 85'."

    states.sort(
        key=lambda row: (
            str((row.get("router") or {}).get("status") or "") == "BET",
            float(((row.get("router") or {}).get("winner") or {}).get("rating") or 0),
        ),
        reverse=True,
    )
    bets = sum(1 for row in states if str((row.get("router") or {}).get("status") or "") == "BET")
    parts = [
        "🧠 <b>GOOL GOAL STATE · АНАЛИЗ ОНЛАЙН</b>",
        "PREMATCH + LIVE + momentum → футбольный сценарий → реальный рынок 1xBet",
        f"Матчей: <b>{len(states)}</b> · BET: <b>{bets}</b> · WAIT: <b>{len(states) - bets}</b>",
    ]

    shown = 0
    for row in states:
        router = row.get("router") or {}
        winner = router.get("winner") or {}
        status = str(router.get("status") or "WAIT")
        score = row.get("score") or [0, 0]
        decision = "🔥 BET" if status == "BET" else "⏳ WAIT"
        if winner:
            market = (
                f"{winner.get('label')} @ {float(winner.get('odd') or 0):.2f} · "
                f"R {float(winner.get('rating') or 0):.0f} · "
                f"1xBet {float(winner.get('market_pressure_pp') or 0):+.1f}"
            )
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
