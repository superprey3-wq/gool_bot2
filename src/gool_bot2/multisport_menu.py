from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .multisport_journal import grouped_stats, load_journal, normalize_entry, stat_line


SPORT_META = {
    "hockey": ("🏒", "ХОККЕЙ"),
    "basketball": ("🏀", "БАСКЕТБОЛ"),
}
RESULT_ICON = {"won": "✅", "lost": "❌", "void": "↩️", "pending": "⏳"}


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def state_path() -> Path:
    raw = os.getenv("GOOL_MULTISPORT_STATE", "").strip() or os.getenv("XBET_MULTISPORT_STATE", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multisport_state.json"


def prematch_state_path() -> Path:
    raw = os.getenv("GOOL_MULTISPORT_PREMATCH_STATE", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multisport_prematch_state.json"


def journal_path() -> Path:
    raw = os.getenv("GOOL_MULTISPORT_JOURNAL", "").strip() or os.getenv("XBET_MULTISPORT_JOURNAL", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multisport_signals.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def _sport_rows(sport: str) -> list[dict[str, Any]]:
    return [row for row in load_journal(journal_path()) if str(row.get("sport") or "") == sport]


def _fmt_start(stamp: Any) -> str:
    try:
        ts = float(stamp or 0)
    except (TypeError, ValueError):
        return "—"
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m %H:%M UTC")


def _selection(row: dict[str, Any]) -> str:
    row = normalize_entry(row)
    return str(row.get("selection") or "—")


def multisport_status_text() -> str:
    live_state = _load_json(state_path(), {})
    pre_state = _load_json(prematch_state_path(), {})
    live_sports = live_state.get("sports") if isinstance(live_state, dict) else {}
    pre_sports = pre_state.get("sports") if isinstance(pre_state, dict) else {}
    live_sports = live_sports if isinstance(live_sports, dict) else {}
    pre_sports = pre_sports if isinstance(pre_sports, dict) else {}
    mode = str((live_state or {}).get("mode") or os.getenv("GOOL_MULTISPORT_MODE", "shadow")).upper()
    lines = [f"🏟 <b>GOOL MULTI · STATUS</b> · {mode}"]
    for key in ("hockey", "basketball"):
        icon, title = SPORT_META[key]
        live = live_sports.get(key) or {}
        pre = pre_sports.get(key) or {}
        if live.get("enabled") is False and pre.get("enabled") is False:
            lines.append(f"{icon} <b>{title}</b> · выключен")
            continue
        lines.append(
            f"{icon} <b>{title}</b>\n"
            f"├ PREMATCH: FS {int(pre.get('flashscore_scheduled') or 0)} · mapped {int(pre.get('mapped') or 0)} · signals {int(pre.get('detected') or 0)}\n"
            f"└ LIVE: FS {int(live.get('flashscore_live') or 0)} · mapped {int(live.get('mapped') or 0)} · signals {int(live.get('detected') or 0)}"
        )
    return "\n\n".join(lines)


def _prematch_section(sport: str, state: dict[str, Any]) -> str:
    row = (((state.get("sports") or {}).get(sport) or {}) if isinstance(state, dict) else {})
    matches = [item for item in (row.get("matches") or []) if isinstance(item, dict)]
    lines = [f"🟣 <b>PREMATCH</b> · найдено {int(row.get('flashscore_scheduled') or 0)} · синхр. {int(row.get('decoded') or 0)}"]
    if not matches:
        lines.append("Сейчас нет синхронизированных матчей с prematch-линией.")
        return "\n".join(lines)
    for item in sorted(matches, key=lambda x: float(x.get("scheduled_start_ts") or 9e18))[:5]:
        line = float(item.get("line") or 0)
        over = float(item.get("over") or 0)
        under = float(item.get("under") or 0)
        signal = item.get("signal") or {}
        sig = ""
        if signal:
            side = "ТБ" if str(signal.get("direction") or "over") == "over" else "ТМ"
            sig = f" · 🔥 {side} {float(signal.get('line') or line):g} R{float(signal.get('strength') or 0):.0f}"
        lines.append(
            f"<b>{item.get('home','?')} — {item.get('away','?')}</b> · {_fmt_start(item.get('scheduled_start_ts'))}\n"
            f"↳ тотал {line:g} · ТБ {over:.2f} / ТМ {under:.2f}{sig}"
        )
    return "\n\n".join(lines)


def _live_section(sport: str, state: dict[str, Any]) -> str:
    row = (((state.get("sports") or {}).get(sport) or {}) if isinstance(state, dict) else {})
    matches = [item for item in (row.get("matches") or []) if isinstance(item, dict)]
    lines = [f"🔴 <b>LIVE</b> · сейчас {int(row.get('flashscore_live') or 0)} · синхр. {int(row.get('decoded') or 0)}"]
    if not matches:
        lines.append("Сейчас нет синхронизированных LIVE-матчей.")
        return "\n".join(lines)
    for item in matches[:5]:
        score = list(item.get("score") or [0, 0])
        period = str(item.get("period") or "LIVE")
        line = float(item.get("line") or 0)
        over = float(item.get("over") or 0)
        under = float(item.get("under") or 0)
        signal = item.get("signal") or item.get("steam") or {}
        sig = ""
        if signal:
            side = "ТБ" if str(signal.get("direction") or "over") == "over" else "ТМ"
            sig = f" · 🔥 {side} {float(signal.get('line') or line):g} R{float(signal.get('strength') or 0):.0f}"
        lines.append(
            f"<b>{item.get('home','?')} — {item.get('away','?')}</b> · {score[0]}:{score[1]} · {period}\n"
            f"↳ тотал {line:g} · ТБ {over:.2f} / ТМ {under:.2f}{sig}"
        )
    return "\n\n".join(lines)


def sport_overview_text(sport: str) -> str:
    if sport not in SPORT_META:
        return multisport_status_text()
    icon, title = SPORT_META[sport]
    live_state = _load_json(state_path(), {})
    pre_state = _load_json(prematch_state_path(), {})
    rows = _sport_rows(sport)
    stats = grouped_stats(rows)["by_sport"][sport]
    mode = str((live_state or {}).get("mode") or os.getenv("GOOL_MULTISPORT_MODE", "shadow")).upper()
    return "\n\n".join(
        [
            f"{icon} <b>GOOL MULTI · {title}</b> · {mode}",
            f"🟣 PREMATCH · {stat_line(stats['by_phase']['PREMATCH'])}",
            f"🔴 LIVE · {stat_line(stats['by_phase']['LIVE'])}",
            _prematch_section(sport, pre_state),
            _live_section(sport, live_state),
        ]
    )


def multisport_report_text() -> str:
    rows = load_journal(journal_path())
    stats = grouped_stats(rows)
    lines = ["📊 <b>GOOL MULTI · ОТЧЁТ</b>"]
    for sport in ("hockey", "basketball"):
        icon, title = SPORT_META[sport]
        value = stats["by_sport"][sport]
        lines.append(
            f"{icon} <b>{title}</b>\n"
            f"🟣 PREMATCH: {stat_line(value['by_phase']['PREMATCH'])}\n"
            f"🔴 LIVE: {stat_line(value['by_phase']['LIVE'])}\n"
            f"Σ ВСЕ: {stat_line(value['all'])}"
        )
    lines.append(
        f"🏟 <b>ИТОГО</b>\n"
        f"🟣 PREMATCH: {stat_line(stats['by_phase']['PREMATCH'])}\n"
        f"🔴 LIVE: {stat_line(stats['by_phase']['LIVE'])}\n"
        f"Σ ВСЕ: {stat_line(stats['all'])}"
    )
    return "\n\n".join(lines)


def sport_journal_text(sport: str | None = None, limit: int = 14) -> str:
    rows = load_journal(journal_path())
    if sport in SPORT_META:
        rows = [row for row in rows if row.get("sport") == sport]
        icon, title = SPORT_META[sport]
        heading = f"📒 <b>{icon} ЖУРНАЛ · {title}</b>"
    else:
        heading = "📒 <b>GOOL MULTI · ЖУРНАЛ</b>"
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    lines = [heading]
    if not rows:
        return heading + "\n\nПока сигналов нет."
    for row in rows[:max(1, limit)]:
        phase = str(row.get("phase") or "LIVE").upper()
        phase_icon = "🟣" if phase == "PREMATCH" else "🔴"
        sport_icon = SPORT_META.get(str(row.get("sport") or ""), ("🏟", ""))[0]
        result = str(row.get("result") or "pending")
        result_icon = RESULT_ICON.get(result, "⏳")
        context = (
            f"старт {_fmt_start(row.get('scheduled_start_ts'))}"
            if phase == "PREMATCH"
            else f"счёт {':'.join(map(str, row.get('score') or [0, 0]))} · {row.get('period') or 'LIVE'}"
        )
        lines.append(
            f"{result_icon} {phase_icon}{sport_icon} <b>{row.get('home','?')} — {row.get('away','?')}</b>\n"
            f"↳ {_selection(row)} @ {float(row.get('odd') or 0):.2f} · R{float(row.get('strength') or 0):.0f} · {context}"
        )
    return "\n\n".join(lines)
