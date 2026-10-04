from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .multisport_journal import load_journal, normalize_entry, stat_line, stats
from .xbet_multisport_markets import SCOPE_LABEL_RU, policy_text_ru


SPORT_META = {
    "hockey": ("🏒", "ХОККЕЙ"),
    "basketball": ("🏀", "БАСКЕТБОЛ"),
}


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def state_path() -> Path:
    raw = os.getenv("GOOL_MULTISPORT_STATE", "").strip() or os.getenv("XBET_MULTISPORT_STATE", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multisport_state.json"


def journal_path() -> Path:
    raw = os.getenv("GOOL_MULTISPORT_JOURNAL", "").strip() or os.getenv("XBET_MULTISPORT_JOURNAL", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multisport_signals.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text("utf-8"))
        return value
    except Exception:
        return default


def _row_phase(row: dict[str, Any]) -> str:
    raw = str(row.get("phase") or "").upper()
    if raw in {"PREMATCH", "LIVE"}:
        return raw
    return "PREMATCH" if str(row.get("origin") or "") == "multisport_prematch" else "LIVE"


def _sport_rows(sport: str, phase: str | None = None) -> list[dict[str, Any]]:
    rows = load_journal(journal_path())
    out = [row for row in rows if str(row.get("sport") or "") == sport]
    if phase:
        wanted = str(phase).upper()
        out = [row for row in out if _row_phase(row) == wanted]
    return out


def _record_text(rows: list[dict[str, Any]]) -> str:
    return stat_line(stats([normalize_entry(row) for row in rows]))


def _coverage_summary(matches: list[dict[str, Any]]) -> str:
    scopes: dict[str, dict[str, int]] = {}
    unknown: set[tuple[Any, Any, Any]] = set()
    failed: set[str] = set()
    for row in matches:
        coverage = row.get("market_coverage") or {}
        if isinstance(coverage, dict):
            for scope, value in coverage.items():
                if not isinstance(value, dict):
                    continue
                target = scopes.setdefault(str(scope), {"T": 0, "IT1": 0, "IT2": 0, "F": 0, "ML": 0})
                target["T"] += int(int(value.get("match_total_lines") or 0) > 0)
                target["IT1"] += int(int(value.get("home_total_lines") or 0) > 0)
                target["IT2"] += int(int(value.get("away_total_lines") or 0) > 0)
                target["F"] += int(int(value.get("handicap_lines") or 0) > 0)
                target["ML"] += int(bool(value.get("moneyline")))
                if str(value.get("fetch") or "") == "failed":
                    failed.add(str(scope))
        for item in row.get("unknown_market_catalog") or []:
            if isinstance(item, dict):
                unknown.add((item.get("G"), item.get("GS"), item.get("T")))
    if not scopes:
        return "Рынки: данных пока нет."
    lines = ["<b>Покрытие рынков</b>"]
    for scope in sorted(scopes, key=lambda s: (s != "FULL_MATCH", s)):
        value = scopes[scope]
        label = SCOPE_LABEL_RU.get(scope, scope)
        offered = []
        if value["T"]: offered.append(f"ТБ/ТМ×{value['T']}")
        if value["IT1"]: offered.append(f"ИТ1×{value['IT1']}")
        if value["IT2"]: offered.append(f"ИТ2×{value['IT2']}")
        if value["F"]: offered.append(f"фора×{value['F']}")
        if value["ML"]: offered.append(f"исход×{value['ML']}")
        lines.append(f"• {label}: " + (" · ".join(offered) if offered else "рынки не декодированы"))
    if unknown:
        lines.append(f"• raw неизвестных G/GS/T: <b>{len(unknown)}</b> (сохраняются для обучения декодера)")
    if failed:
        lines.append("• ⚠️ sub-game fetch failed: " + ", ".join(SCOPE_LABEL_RU.get(x, x) for x in sorted(failed)))
    return "\n".join(lines)


def multisport_status_text() -> str:
    state = _load_json(state_path(), {})
    sports = state.get("sports") if isinstance(state, dict) else {}
    sports = sports if isinstance(sports, dict) else {}
    mode = str((state or {}).get("mode") or os.getenv("GOOL_MULTISPORT_MODE", "shadow")).upper()
    lines = [f"🏟 <b>GOOL MULTI · STATUS</b> · {mode}"]
    for key in ("hockey", "basketball"):
        icon, title = SPORT_META[key]
        row = sports.get(key) or {}
        if row.get("enabled") is False:
            lines.append(f"{icon} <b>{title}</b> · выключен")
            continue
        lines.append(
            f"{icon} <b>{title}</b>\n"
            f"├ PREMATCH · FS {int(row.get('flashscore_prematch') or 0)} · decoded {int(row.get('prematch_decoded') or 0)} · signals {int(row.get('prematch_detected') or 0)} · policy skip {int(row.get('prematch_policy_blocked') or 0)}\n"
            f"└ LIVE · FS {int(row.get('flashscore_live') or 0)} · decoded {int(row.get('decoded') or 0)} · signals {int(row.get('detected') or 0)} · policy skip {int(row.get('policy_blocked') or 0)}"
        )
    return "\n".join(lines)


def sport_overview_text(sport: str) -> str:
    if sport not in SPORT_META:
        return multisport_status_text()
    icon, title = SPORT_META[sport]
    state = _load_json(state_path(), {})
    sports = state.get("sports") if isinstance(state, dict) else {}
    current = ((sports or {}).get(sport) or {}) if isinstance(sports, dict) else {}
    prematch_rows = _sport_rows(sport, "PREMATCH")
    live_rows = _sport_rows(sport, "LIVE")
    mode = str((state or {}).get("mode") or os.getenv("GOOL_MULTISPORT_MODE", "shadow")).upper()

    prematch_policy, live_policy = policy_text_ru(sport)
    parts = [
        f"{icon} <b>GOOL MULTI · {title}</b> · {mode}",
        f"🧭 <b>РАЗДЕЛЕНИЕ РЫНКОВ</b>\n🟡 PREMATCH: {prematch_policy}\n🔴 LIVE: {live_policy}",
        f"🟡 <b>PREMATCH журнал</b>\n{_record_text(prematch_rows)}",
        f"🔴 <b>LIVE журнал</b>\n{_record_text(live_rows)}",
    ]

    prematch_matches = [row for row in (current.get("prematch_matches") or []) if isinstance(row, dict)]
    if prematch_matches:
        lines = [f"🟡 <b>PREMATCH · БЛИЖАЙШИЕ</b> · {len(prematch_matches)}"]
        for row in prematch_matches[:6]:
            start_ts = float(row.get("start_ts") or 0.0)
            import datetime as _dt
            try:
                _tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
            except Exception:
                _tz = _dt.timezone.utc
            start_label = _dt.datetime.fromtimestamp(start_ts, _tz).strftime("%d.%m %H:%M МСК") if start_ts else "время ?"
            line = float(row.get("line") or 0.0)
            over = float(row.get("over") or 0.0)
            under = float(row.get("under") or 0.0)
            signal = row.get("signal") or {}
            signal_text = ""
            if signal:
                label = str(signal.get("selection") or ("ТБ" if str(signal.get("direction") or "over") == "over" else "ТМ") + f" {float(signal.get('line') or line):g}")
                signal_text = f" · 🔥 {label} R{float(signal.get('strength') or 0):.0f}"
            lines.append(
                f"<b>{row.get('home','?')} — {row.get('away','?')}</b>\n"
                f"🏆 {row.get('league') or '?'} · 🕐 {start_label}\n"
                f"↳ тотал {line:g} · ТБ {over:.2f} / ТМ {under:.2f}{signal_text}"
            )
        parts.append("\n\n".join(lines))
        parts.append(_coverage_summary(prematch_matches))
    else:
        parts.append("🟡 <b>PREMATCH</b>\nВ ближайшем окне пока нет синхронизированных матчей.")

    live_matches = [row for row in (current.get("matches") or []) if isinstance(row, dict)]
    if live_matches:
        lines = [f"🔴 <b>LIVE · СЕЙЧАС</b> · {len(live_matches)}"]
        for row in live_matches[:6]:
            score = list(row.get("score") or [0, 0])
            period = str(row.get("period") or "LIVE")
            line = float(row.get("line") or 0.0)
            over = float(row.get("over") or 0.0)
            under = float(row.get("under") or 0.0)
            signal = row.get("signal") or row.get("steam") or {}
            signal_text = ""
            if signal:
                label = str(signal.get("selection") or ("ТБ" if str(signal.get("direction") or "over") == "over" else "ТМ") + f" {float(signal.get('line') or line):g}")
                signal_text = f" · 🔥 {label} R{float(signal.get('strength') or 0):.0f}"
            lines.append(
                f"<b>{row.get('home','?')} — {row.get('away','?')}</b> · {score[0]}:{score[1]} · {period}\n"
                f"↳ тотал {line:g} · ТБ {over:.2f} / ТМ {under:.2f}{signal_text}"
            )
        parts.append("\n\n".join(lines))
        parts.append(_coverage_summary(live_matches))
    else:
        parts.append("🔴 <b>LIVE</b>\nСейчас нет синхронизированных матчей.")

    return "\n\n────────────\n\n".join(parts)

def multisport_report_text() -> str:
    lines = ["📊 <b>GOOL MULTI · ЖУРНАЛ</b>", "PREMATCH и LIVE считаются отдельно."]
    all_rows: list[dict[str, Any]] = []
    all_prematch: list[dict[str, Any]] = []
    all_live: list[dict[str, Any]] = []

    for sport in ("hockey", "basketball"):
        prematch = _sport_rows(sport, "PREMATCH")
        live = _sport_rows(sport, "LIVE")
        rows = [*prematch, *live]
        all_rows.extend(rows)
        all_prematch.extend(prematch)
        all_live.extend(live)
        icon, title = SPORT_META[sport]
        lines.append(
            f"{icon} <b>{title}</b>\n"
            f"🟡 PREMATCH · {_record_text(prematch)}\n"
            f"🔴 LIVE · {_record_text(live)}"
        )

    lines.append(
        f"🏟 <b>ИТОГО</b>\n"
        f"🟡 PREMATCH · {_record_text(all_prematch)}\n"
        f"🔴 LIVE · {_record_text(all_live)}\n"
        f"📚 ВСЕ · {_record_text(all_rows)}"
    )
    return "\n\n".join(lines)



def sport_journal_text(sport: str | None = None, limit: int = 14, phase: str | None = None) -> str:
    rows = load_journal(journal_path())
    wanted_phase = str(phase or "").upper()
    if sport in SPORT_META:
        rows = [row for row in rows if str(row.get("sport") or "") == sport]
        icon, title = SPORT_META[sport]
        heading = f"📒 <b>{icon} ЖУРНАЛ · {title}</b>"
    else:
        heading = "📒 <b>GOOL MULTI · ЖУРНАЛ СИГНАЛОВ</b>"
    if wanted_phase in {"PREMATCH", "LIVE"}:
        rows = [row for row in rows if _row_phase(row) == wanted_phase]
        heading += f" · {wanted_phase}"
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not rows:
        return heading + "\n\nПока сигналов нет."

    result_icon = {"won": "✅", "lost": "❌", "void": "↩️", "pending": "⏳"}
    lines = [heading]
    if sport in SPORT_META:
        prematch_policy, live_policy = policy_text_ru(str(sport))
        lines.append(f"🟡 PREMATCH: {prematch_policy}\n🔴 LIVE: {live_policy}")
    else:
        lines.append("🟡 PREMATCH · 🔴 LIVE")

    for row in rows[:max(1, int(limit))]:
        item = normalize_entry(row)
        phase_name = str(item.get("phase") or "LIVE")
        picon = "🟡" if phase_name == "PREMATCH" else "🔴"
        sicon = SPORT_META.get(str(item.get("sport") or ""), ("🏟", ""))[0]
        ricon = result_icon.get(str(item.get("result") or "pending"), "⏳")
        scope = str(item.get("scope") or "FULL_MATCH")
        scope_label = SCOPE_LABEL_RU.get(scope, scope)
        if phase_name == "PREMATCH":
            try:
                import datetime as _dt
                stamp = float(item.get("scheduled_start_ts") or item.get("start_ts") or 0)
                tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
                context = _dt.datetime.fromtimestamp(stamp, tz).strftime("%d.%m %H:%M МСК") if stamp else "до матча"
            except Exception:
                context = "до матча"
        else:
            score = item.get("match_score") or item.get("score") or [0, 0]
            context = f"{score[0]}:{score[1]} · {item.get('period') or 'LIVE'}"
        lines.append(
            f"{ricon} {picon}{sicon} <b>{item.get('home','?')} — {item.get('away','?')}</b>\n"
            f"↳ <b>{scope_label}</b> · {item.get('selection') or '?'} @ {float(item.get('odd') or 0):.2f}\n"
            f"↳ R{float(item.get('strength') or 0):.0f} · {context}"
        )
    return "\n\n".join(lines)


def hockey_journal_text(limit: int = 16, phase: str | None = None) -> str:
    return sport_journal_text("hockey", limit=limit, phase=phase)


def basketball_journal_text(limit: int = 16, phase: str | None = None) -> str:
    return sport_journal_text("basketball", limit=limit, phase=phase)


def sport_phase_report_text(sport: str) -> str:
    if sport not in SPORT_META:
        return multisport_report_text()
    icon, title = SPORT_META[sport]
    prematch = _sport_rows(sport, "PREMATCH")
    live = _sport_rows(sport, "LIVE")
    prematch_policy, live_policy = policy_text_ru(sport)
    return (
        f"📊 <b>{icon} {title} · ОТДЕЛЬНЫЙ ОТЧЁТ</b>\n\n"
        f"🟡 <b>PREMATCH</b> · {_record_text(prematch)}\n"
        f"Рынки: {prematch_policy}\n\n"
        f"🔴 <b>LIVE</b> · {_record_text(live)}\n"
        f"Рынки: {live_policy}"
    )
