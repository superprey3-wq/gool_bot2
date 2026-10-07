from __future__ import annotations

import html
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

from .multisport_journal import load_journal, normalize_entry, stat_line, stats
from .multisport_parlay import parlay_text
from .providers.flashscore import FlashscoreProvider
from .xbet_multisport_steam import multisport_scope_is_complete, parse_flashscore_events
from .providers.common import norm_team
from .xbet_multisport_markets import SCOPE_LABEL_RU, policy_text_ru


SPORT_META = {
    "hockey": ("🏒", "ХОККЕЙ"),
    "basketball": ("🏀", "БАСКЕТБОЛ"),
}


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


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


def _is_parlay_row(row: dict[str, Any]) -> bool:
    return (
        str(row.get("origin") or "") == "multisport_parlay"
        or str(row.get("market_family") or "") == "parlay"
        or str(row.get("signal_type") or "") == "prematch_parlay"
    )


def _sport_rows(sport: str, phase: str | None = None) -> list[dict[str, Any]]:
    rows = load_journal(journal_path())
    out = [row for row in rows if str(row.get("sport") or "") == sport]
    if phase:
        wanted = str(phase).upper()
        out = [row for row in out if _row_phase(row) == wanted]
    return out


def _record_text(rows: list[dict[str, Any]]) -> str:
    return stat_line(stats([normalize_entry(row) for row in rows]))


def _journal_day(row: dict[str, Any]) -> tuple[str, str]:
    """Group bets by the date of the match, not by signal creation time."""
    try:
        tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        tz = timezone.utc

    dt = None

    # PREMATCH can be calculated/sent the previous evening. The journal should
    # still show it under the date when the match is actually played. LIVE rows
    # also keep the match date when a game crosses midnight.
    try:
        ts = float(row.get("scheduled_start_ts") or row.get("start_ts") or 0.0)
        if ts > 0:
            dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz)
    except Exception:
        dt = None

    # Legacy rows may not contain match start time.
    if dt is None:
        created = str(row.get("created_at") or "").strip()
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(tz)
            except Exception:
                dt = None

    if dt is None:
        dt = datetime.now(tz)

    return dt.strftime("%Y-%m-%d"), dt.strftime("%d.%m.%Y")


def _dated_sport_journal(
    rows: list[dict[str, Any]],
    *,
    heading: str,
    policy: str,
    phase: str | None = None,
    limit_days: int = 16,
) -> str:
    normalized = [normalize_entry(row) for row in rows]
    by_day: dict[str, dict[str, Any]] = {}
    for row in normalized:
        day_key, label = _journal_day(row)
        bucket = by_day.setdefault(day_key, {"label": label, "rows": []})
        bucket["rows"].append(row)

    day_keys = sorted(by_day)
    if limit_days > 0 and len(day_keys) > limit_days:
        day_keys = day_keys[-limit_days:]

    wanted_phase = str(phase or "").upper()
    blocks: list[str] = [heading, policy]

    if not day_keys:
        blocks.append("Пока нет записей.")
    else:
        for day_key in day_keys:
            bucket = by_day[day_key]
            day_rows = list(bucket["rows"])
            parlays = [row for row in day_rows if _is_parlay_row(row)]
            prematch = [
                row for row in day_rows
                if _row_phase(row) == "PREMATCH" and not _is_parlay_row(row)
            ]
            live = [
                row for row in day_rows
                if _row_phase(row) == "LIVE" and not _is_parlay_row(row)
            ]

            day_lines = [f"📅 <b>{bucket['label']}</b>"]
            if wanted_phase == "PREMATCH":
                day_lines.extend([
                    f"🟡 <b>PREMATCH</b>\n{_record_text(prematch)}",
                    f"🔗 <b>ЭКСПРЕССЫ</b>\n{_record_text(parlays)}",
                ])
            elif wanted_phase == "LIVE":
                day_lines.append(f"🔴 <b>LIVE</b>\n{_record_text(live)}")
            else:
                day_lines.extend([
                    f"🟡 <b>PREMATCH</b>\n{_record_text(prematch)}",
                    f"🔴 <b>LIVE</b>\n{_record_text(live)}",
                    f"🔗 <b>ЭКСПРЕССЫ</b>\n{_record_text(parlays)}",
                    f"📊 <b>ИТОГ ДНЯ</b>\n{_record_text(day_rows)}",
                ])
            blocks.append("\n\n".join(day_lines))

    all_parlays = [row for row in normalized if _is_parlay_row(row)]
    all_prematch = [
        row for row in normalized
        if _row_phase(row) == "PREMATCH" and not _is_parlay_row(row)
    ]
    all_live = [
        row for row in normalized
        if _row_phase(row) == "LIVE" and not _is_parlay_row(row)
    ]

    if wanted_phase == "PREMATCH":
        total = (
            "━━━━━━━━━━━━━━\n"
            "🏁 <b>ИТОГО · PREMATCH · ЗА ВСЁ ВРЕМЯ</b>\n"
            f"{_record_text(all_prematch)}\n\n"
            f"🔗 <b>ЭКСПРЕССЫ</b>\n{_record_text(all_parlays)}"
        )
    elif wanted_phase == "LIVE":
        total = (
            "━━━━━━━━━━━━━━\n"
            "🏁 <b>ИТОГО · LIVE · ЗА ВСЁ ВРЕМЯ</b>\n"
            f"{_record_text(all_live)}"
        )
    else:
        total = (
            "━━━━━━━━━━━━━━\n"
            "🏁 <b>ИТОГО · ЗА ВСЁ ВРЕМЯ</b>\n\n"
            f"🟡 <b>PREMATCH</b>\n{_record_text(all_prematch)}\n\n"
            f"🔴 <b>LIVE</b>\n{_record_text(all_live)}\n\n"
            f"🔗 <b>ЭКСПРЕССЫ</b>\n{_record_text(all_parlays)}\n\n"
            f"🌐 <b>ВСЕГО</b>\n{_record_text(normalized)}"
        )
    blocks.append(total)

    if limit_days > 0 and len(by_day) > limit_days:
        blocks.insert(2, f"ℹ️ Показаны последние {limit_days} дат; общий итог ниже включает весь журнал.")

    return "\n\n".join(blocks)


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
    captured = str((state or {}).get("captured_at") or "нет снимка")
    version = ""
    for candidate in (
        _runtime() / ".code_version",
        Path("/home/container/gool_bot2_data/.code_version"),
    ):
        try:
            raw = candidate.read_text("utf-8").strip()
        except Exception:
            continue
        if raw:
            version = raw[:12]
            break
    header = f"🏟 <b>GOOL MULTI · STATUS</b> · {mode}"
    if version:
        header += f" · {version}"
    lines = [header, f"🕐 snapshot: {captured}"]
    for key in ("hockey", "basketball"):
        icon, title = SPORT_META[key]
        row = sports.get(key) or {}
        if row.get("enabled") is False:
            lines.append(f"{icon} <b>{title}</b> · выключен")
            continue
        lines.append(
            f"{icon} <b>{title}</b>\n"
            f"├ PREMATCH · FS {int(row.get('flashscore_prematch') or 0)} · mapped {int(row.get('prematch_mapped') or 0)} · scan {int(row.get('prematch_scanned') or 0)} · decoded {int(row.get('prematch_decoded') or 0)} · signals {int(row.get('prematch_detected') or 0)}\n"
            f"└ LIVE · FS {int(row.get('flashscore_live') or 0)} · 1xBet {int(row.get('xbet_live') or 0)} · mapped {int(row.get('mapped') or 0)} · decoded {int(row.get('decoded') or 0)} · mismatch {int(row.get('score_mismatch') or 0)} · decode_fail {int(row.get('market_decode_failed') or 0)} · signals {int(row.get('detected') or 0)} · policy skip {int(row.get('policy_blocked') or 0)}"
        )

    super10 = (state or {}).get("global_super10") if isinstance(state, dict) else {}
    if isinstance(super10, dict):
        status = str(super10.get("status") or "нет данных")
        target = int(super10.get("target") or 10)
        available = int(super10.get("available") or 0)
        strict = int(super10.get("strict") or 0)
        reserve = int(super10.get("reserve_extra") or 0)
        by_sport = super10.get("available_by_sport") or {}
        missing = [str(x) for x in (super10.get("missing_sports") or []) if str(x)]
        detail = (
            f"🌐 <b>SUPER 10</b> · {status}\n"
            f"├ готово {available}/{target} · strict {strict} · reserve +{reserve}\n"
            f"└ ⚽ {int(by_sport.get('football') or 0)} · 🏒 {int(by_sport.get('hockey') or 0)} · "
            f"🏀 {int(by_sport.get('basketball') or 0)}"
        )
        if missing:
            detail += "\n⚠️ нет подходящих ног: " + ", ".join(missing)
        lines.append(detail)
    return "\n".join(lines)


def super10_text() -> str:
    """Interactive current GLOBAL SUPER 10 view."""
    try:
        from .global_super10 import readiness_snapshot, sent_path
        readiness = readiness_snapshot()
        sent = _load_json(sent_path(), {})
    except Exception as exc:
        return f"🌐 <b>SUPER 10</b>\n⚠️ Не удалось прочитать состояние: <code>{_h(type(exc).__name__)}</code>"

    target = int(readiness.get("target") or 10)
    available = int(readiness.get("available") or 0)
    strict = int(readiness.get("strict") or 0)
    reserve = int(readiness.get("reserve_extra") or 0)
    by_sport = readiness.get("available_by_sport") or {}
    missing = [str(x) for x in (readiness.get("missing_sports") or []) if str(x)]

    lines = [
        "🌐 <b>SUPER 10 · ФУТБОЛ + ХОККЕЙ + БАСКЕТБОЛ</b>",
        (
            f"Готово: <b>{available}/{target}</b> · strict <b>{strict}</b> · "
            f"reserve +<b>{reserve}</b>"
        ),
        (
            f"⚽ {int(by_sport.get('football') or 0)} · "
            f"🏒 {int(by_sport.get('hockey') or 0)} · "
            f"🏀 {int(by_sport.get('basketball') or 0)}"
        ),
    ]
    if missing:
        names = {"football": "футбол", "hockey": "хоккей", "basketball": "баскетбол"}
        lines.append("⚠️ Нет подходящих ног: " + ", ".join(names.get(x, x) for x in missing))
    need_more = int(readiness.get("need_more") or 0)
    if need_more > 0:
        lines.append(f"⏳ До сборки не хватает: <b>{need_more}</b>")
    else:
        lines.append("✅ Пул достаточный для сборки SUPER 10.")

    try:
        report_tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        report_tz = timezone.utc
    today = datetime.now(report_tz).strftime("%Y-%m-%d")
    sent_today = (
        isinstance(sent, dict)
        and sent.get("sent")
        and str(sent.get("day") or "") == today
        and isinstance(sent.get("ticket"), dict)
    )
    if sent_today:
        ticket = dict(sent["ticket"])
        lines.extend([
            "",
            f"✅ <b>СЕГОДНЯ SUPER 10 УЖЕ ОТПРАВЛЕН</b>",
            f"Общий кэф: <b>{float(ticket.get('combined_odds') or ticket.get('odd') or 0):.2f}</b>",
        ])
        for idx, leg in enumerate(ticket.get("legs") or [], 1):
            if not isinstance(leg, dict):
                continue
            icon = {"football": "⚽", "hockey": "🏒", "basketball": "🏀"}.get(str(leg.get("sport") or ""), "•")
            tier = str(leg.get("super_tier") or "strict")
            tier_mark = "S" if tier == "strict" else "R"
            lines.append(
                f"{idx}. {icon} {_h(leg.get('home') or '?')} — {_h(leg.get('away') or '?')}\n"
                f"   {_h(leg.get('selection') or '?')} @ {float(leg.get('odd') or 0):.2f} · {tier_mark}"
            )
    else:
        lines.extend([
            "",
            "📌 <i>Когда наберутся 10 допустимых разных матчей из всех трёх видов спорта, бот отправит карточку автоматически.</i>",
        ])
    return "\n".join(lines)


def super10_history_text(limit: int = 5) -> str:
    try:
        from .global_super10 import history_path
        rows = _load_json(history_path(), [])
    except Exception as exc:
        return f"🌐 <b>SUPER 10 · ИСТОРИЯ</b>\n⚠️ Ошибка: <code>{_h(type(exc).__name__)}</code>"
    rows = [dict(row) for row in rows if isinstance(row, dict)]
    if not rows:
        return "🌐 <b>SUPER 10 · ИСТОРИЯ</b>\nПока нет отправленных SUPER 10."
    out = ["🌐 <b>SUPER 10 · ИСТОРИЯ</b>"]
    for row in rows[-max(1, int(limit)):][::-1]:
        counts = row.get("sport_counts") or {}
        out.append(
            f"📅 <b>{_h(row.get('day') or '?')}</b> · кэф <b>{float(row.get('combined_odds') or row.get('odd') or 0):.2f}</b>\n"
            f"⚽ {int(counts.get('football') or 0)} · 🏒 {int(counts.get('hockey') or 0)} · "
            f"🏀 {int(counts.get('basketball') or 0)}"
        )
    return "\n\n".join(out)


def sport_overview_text(sport: str) -> str:
    if sport not in SPORT_META:
        return multisport_status_text()
    icon, title = SPORT_META[sport]
    state = _load_json(state_path(), {})
    sports = state.get("sports") if isinstance(state, dict) else {}
    current = ((sports or {}).get(sport) or {}) if isinstance(sports, dict) else {}
    sport_rows = _sport_rows(sport)
    parlay_rows = [row for row in sport_rows if _is_parlay_row(row)]
    prematch_rows = [row for row in sport_rows if _row_phase(row) == "PREMATCH" and not _is_parlay_row(row)]
    live_rows = [row for row in sport_rows if _row_phase(row) == "LIVE" and not _is_parlay_row(row)]
    mode = str((state or {}).get("mode") or os.getenv("GOOL_MULTISPORT_MODE", "shadow")).upper()

    prematch_policy, live_policy = policy_text_ru(sport)
    parts = [
        f"{icon} <b>GOOL MULTI · {title}</b> · {mode}",
        f"🧭 <b>РАЗДЕЛЕНИЕ РЫНКОВ</b>\n🟡 PREMATCH: {prematch_policy}\n🔴 LIVE: {live_policy}",
        f"🟡 <b>PREMATCH журнал</b>\n{_record_text(prematch_rows)}",
        f"🔴 <b>LIVE журнал</b>\n{_record_text(live_rows)}",
        f"🔗 <b>ЭКСПРЕССЫ журнал</b>\n{_record_text(parlay_rows)}",
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

def _pick_needed_text(row: dict[str, Any]) -> str:
    """Human-readable winning condition matching multisport settlement rules."""
    sport = str(row.get("sport") or "")
    family = str(row.get("market_family") or "match_total")
    side = str(row.get("selection_side") or row.get("direction") or "").casefold()
    direction = str(row.get("direction") or "over").casefold()
    scope = str(row.get("scope") or "FULL_MATCH")
    scope_label = SCOPE_LABEL_RU.get(scope, scope)
    unit = "шайб" if sport == "hockey" else "очков"

    try:
        raw_line = row.get("line")
        if raw_line is None or str(raw_line).strip() == "":
            match = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*$", str(row.get("selection") or ""))
            raw_line = match.group(1).replace(",", ".") if match else 0.0
        line = float(raw_line)
    except (TypeError, ValueError):
        line = 0.0

    prefix = "" if scope == "FULL_MATCH" else f"{scope_label}: "

    if family == "moneyline":
        team = "команды 1" if side == "home" else "команды 2"
        return f"{prefix}для захода нужна победа {team}"

    if family == "handicap":
        team = "Команда 1" if side != "away" else "Команда 2"
        abs_line = abs(line)
        is_integer = abs(line - round(line)) < 1e-9
        if line > 0:
            if is_integer:
                max_loss = max(0, int(round(line)) - 1)
                return (
                    f"{prefix}для захода: {team.lower()} может проиграть максимум в {max_loss}; "
                    f"поражение ровно в {int(round(line))} — возврат"
                )
            max_loss = max(0, int(math.floor(line)))
            return f"{prefix}для захода: {team.lower()} может проиграть максимум в {max_loss}"
        if line < 0:
            needed = int(math.floor(abs_line)) + 1 if is_integer else int(math.ceil(abs_line))
            if is_integer:
                return (
                    f"{prefix}для захода: {team.lower()} должна выиграть минимум в {needed}; "
                    f"победа ровно в {int(round(abs_line))} — возврат"
                )
            return f"{prefix}для захода: {team.lower()} должна выиграть минимум в {needed}"
        return f"{prefix}для захода нужна победа {team.lower()}"

    subject = (
        "команде 1"
        if family == "home_total"
        else "команде 2"
        if family == "away_total"
        else "суммарно"
    )
    is_integer = abs(line - round(line)) < 1e-9
    if direction == "under":
        if is_integer:
            win_max = int(round(line)) - 1
            return (
                f"{prefix}для захода: {subject} максимум {win_max} {unit}; "
                f"ровно {int(round(line))} — возврат"
            )
        win_max = int(math.floor(line))
        return f"{prefix}для захода: {subject} максимум {win_max} {unit}"

    if is_integer:
        win_min = int(round(line)) + 1
        return (
            f"{prefix}для захода: {subject} нужно {win_min}+ {unit}; "
            f"ровно {int(round(line))} — возврат"
        )
    win_min = int(math.floor(line)) + 1
    return f"{prefix}для захода: {subject} нужно {win_min}+ {unit}"


def _teams_match(left_home: str, left_away: str, right_home: str, right_away: str) -> bool:
    lh, la = norm_team(left_home), norm_team(left_away)
    rh, ra = norm_team(right_home), norm_team(right_away)
    if not lh or not la or not rh or not ra:
        return False
    if lh == rh and la == ra:
        return True
    return SequenceMatcher(None, lh, rh).ratio() >= 0.78 and SequenceMatcher(None, la, ra).ratio() >= 0.78


def _direct_flashscore_live(sport: str) -> list[dict[str, Any]]:
    """Fresh LIVE identity/score for the menu, independent of saved worker state."""
    sport_id = 4 if sport == "hockey" else 3
    provider = FlashscoreProvider()
    merged: dict[str, dict[str, Any]] = {}
    for path in (f"f_{sport_id}_0_3_en_1", f"f_{sport_id}_0_0_en_1"):
        try:
            body = provider._feed(path, timeout=3, max_hosts=1)
        except Exception:
            body = ""
        if not body:
            continue
        for row in parse_flashscore_events(body):
            if str(row.get("coarse_status") or "") == "2":
                merged[str(row.get("flashscore_event_id") or "")] = dict(row)
    return list(merged.values())


def multisport_in_game_sections() -> list[str]:
    """Pending multisport picks whose Flashscore match is currently LIVE."""
    state = _load_json(state_path(), {})
    sports_state = state.get("sports") if isinstance(state, dict) else {}
    sports_state = sports_state if isinstance(sports_state, dict) else {}
    rows = load_journal(journal_path())

    messages: list[str] = []
    total = 0
    match_total = 0
    sport_blocks: list[str] = []

    for sport in ("hockey", "basketball"):
        icon, title = SPORT_META[sport]
        current = (sports_state.get(sport) or {}) if isinstance(sports_state, dict) else {}
        # Flashscore is authoritative for "has the match started?".
        # current["matches"] contains only Flashscore+1xBet mapped games and may
        # lag behind even while the match is already live.
        fs_live_matches = [
            row for row in (current.get("flashscore_live_matches") or [])
            if isinstance(row, dict)
        ]
        analysis_by_id = {
            str(row.get("flashscore_event_id") or ""): dict(row)
            for row in (current.get("flashscore_analysis_matches") or [])
            if isinstance(row, dict) and str(row.get("flashscore_event_id") or "")
        }
        # The Telegram menu must not depend on the worker state refresh cadence.
        # Fetch fresh Flashscore LIVE rows on demand for score/status, then merge
        # the already-decoded Flashscore Brain scope/period on top. Raw AC alone
        # is not a safe period label for every hockey/basketball feed.
        fresh_live = _direct_flashscore_live(sport)
        fresh_by_id = {}
        for row in fresh_live:
            fs_id = str(row.get("flashscore_event_id") or "")
            if not fs_id:
                continue
            analysis = analysis_by_id.get(fs_id) or {}
            fresh_by_id[fs_id] = {**analysis, **dict(row)}
            if analysis.get("scope"):
                fresh_by_id[fs_id]["scope"] = analysis.get("scope")
            if analysis.get("period"):
                fresh_by_id[fs_id]["period"] = analysis.get("period")
        for row in fs_live_matches:
            fs_id = str(row.get("flashscore_event_id") or "")
            if not fs_id:
                continue
            analysis = analysis_by_id.get(fs_id) or {}
            saved = {**dict(row)}
            if analysis.get("scope"):
                saved["scope"] = analysis.get("scope")
            if analysis.get("period"):
                saved["period"] = analysis.get("period")
            if fs_id in fresh_by_id:
                # Fresh row wins for score/status; decoded Brain wins for
                # period/scope above.
                merged = {**saved, **fresh_by_id[fs_id]}
                if analysis.get("scope"):
                    merged["scope"] = analysis.get("scope")
                if analysis.get("period"):
                    merged["period"] = analysis.get("period")
                fresh_by_id[fs_id] = merged
            else:
                fresh_by_id[fs_id] = {**analysis, **saved}
        fs_live_matches = list(fresh_by_id.values())
        mapped_matches = [
            row for row in (current.get("matches") or [])
            if isinstance(row, dict)
        ]
        live_by_fs = {
            str(row.get("flashscore_event_id") or ""): dict(row)
            for row in fs_live_matches
            if str(row.get("flashscore_event_id") or "")
        }
        # Enrich authoritative Flashscore rows with decoded/mapped LIVE details
        # when they are available, but never require them for the menu.
        for mapped in mapped_matches:
            fs_id = str(mapped.get("flashscore_event_id") or "")
            if not fs_id:
                continue
            if fs_id in live_by_fs:
                live_by_fs[fs_id] = {**live_by_fs[fs_id], **mapped}
            else:
                live_by_fs[fs_id] = dict(mapped)
            analysis = analysis_by_id.get(fs_id) or {}
            if analysis.get("scope"):
                live_by_fs[fs_id]["scope"] = analysis.get("scope")
            if analysis.get("period"):
                live_by_fs[fs_id]["period"] = analysis.get("period")
        active: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            if str(row.get("sport") or "") != sport:
                continue
            if _is_parlay_row(row):
                continue
            if str(row.get("result") or "pending").lower() != "pending":
                continue
            phase_name = _row_phase(row)
            if phase_name not in {"PREMATCH", "LIVE"}:
                continue

            fs_id = str(row.get("flashscore_event_id") or "")
            live = live_by_fs.get(fs_id)

            # Legacy/already-sent PREMATCH rows can carry an empty/stale FS id.
            # Do not lose the pick after kickoff: recover by normalized teams
            # inside the same sport, using Flashscore as the LIVE authority.
            if live is None:
                wanted_home = norm_team(str(row.get("home") or ""))
                wanted_away = norm_team(str(row.get("away") or ""))
                for candidate in live_by_fs.values():
                    cand_home = norm_team(str(candidate.get("home") or ""))
                    cand_away = norm_team(str(candidate.get("away") or ""))
                    if wanted_home and wanted_away and _teams_match(
                        str(row.get("home") or ""),
                        str(row.get("away") or ""),
                        str(candidate.get("home") or ""),
                        str(candidate.get("away") or ""),
                    ):
                        live = candidate
                        break

            if live is not None:
                scope = str(row.get("scope") or "FULL_MATCH")
                if scope != "FULL_MATCH" and multisport_scope_is_complete(live, sport, scope):
                    continue
                active.append((row, live))

        if active:
            # Deduplicate individual bets first.
            unique_active: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
            for pick, live in active:
                key = str(pick.get("entry_id") or "")
                if not key:
                    key = ":".join([
                        str(pick.get("flashscore_event_id") or live.get("flashscore_event_id") or ""),
                        _row_phase(pick),
                        str(pick.get("scope") or ""),
                        str(pick.get("market_family") or ""),
                        str(pick.get("selection") or ""),
                    ])
                unique_active.setdefault(key, (pick, live))
            active = list(unique_active.values())
            active.sort(key=lambda pair: (0 if _row_phase(pair[0]) == "LIVE" else 1, str(pair[0].get("created_at") or "")))
        if not active:
            continue

        # One visual block per match. A started PREMATCH pick and a valid current
        # LIVE segment pick belong under the same scoreboard, not as duplicate
        # numbered matches.
        grouped: dict[str, dict[str, Any]] = {}
        for pick, live in active:
            fs_id = str(live.get("flashscore_event_id") or pick.get("flashscore_event_id") or "")
            group_key = fs_id or (
                norm_team(str(pick.get("home") or "")) + "|" + norm_team(str(pick.get("away") or ""))
            )
            group = grouped.setdefault(group_key, {"live": live, "picks": []})
            group["picks"].append(pick)
            # Prefer the row carrying decoded Brain period/scope.
            if str(live.get("period") or "") or str(live.get("scope") or ""):
                group["live"] = live

        total += len(active)
        match_total += len(grouped)
        lines = []
        for idx, group in enumerate(grouped.values(), 1):
            live = dict(group["live"])
            picks = list(group["picks"])
            picks.sort(key=lambda pick: (0 if _row_phase(pick) == "LIVE" else 1, str(pick.get("created_at") or "")))
            first = picks[0]
            score = list(live.get("score") or [0, 0])
            period = str(live.get("period") or live.get("status_code") or "LIVE")
            # Textual provider labels such as Q2/P2/LIVE are useful. Bare
            # numeric AC values such as "15" are not a human period label.
            if not period or period.strip().isdigit():
                period = "LIVE"
            block = [
                f"<b>{idx}. {icon} {first.get('home','?')} — {first.get('away','?')}</b>",
                f"сейчас {period} · {score[0]}:{score[1]}",
            ]
            for pick in picks:
                selection = str(pick.get("selection") or "?")
                needed = _pick_needed_text(pick)
                strength = float(pick.get("strength") or 0)
                phase_name = _row_phase(pick)
                phase_badge = "🔴 LIVE" if phase_name == "LIVE" else "🟡 PREMATCH"
                block.extend([
                    f"🎯 <b>{selection} @ {float(pick.get('odd') or 0):.2f}</b>",
                    f"🧠 {strength:.0f}/100 · {phase_badge}",
                    f"↳ {needed}",
                ])
            lines.append("\n".join(block))
        sport_blocks.append("\n\n".join(lines))

    if not sport_blocks:
        return []
    messages.append(
        f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\n"
        f"Матчей: <b>{match_total}</b> · ставок: <b>{total}</b>"
    )
    messages.extend(sport_blocks)
    return messages



def _stats_brief(stats_payload: dict[str, Any], sport: str) -> str:
    stats = dict(stats_payload.get("segment_stats") or {})
    if not stats:
        return "статистика сегмента пока недоступна"
    preferred = (
        ("shots_on_goal", "броски в створ"),
        ("shots", "броски"),
        ("powerplay_goals", "голы PP"),
        ("penalties_2m", "2 мин"),
    ) if sport == "hockey" else (
        ("field_goals", "FG"),
        ("three_point_field_goals", "3PT"),
        ("free_throws", "FT"),
        ("rebounds", "подборы"),
        ("turnovers", "потери"),
    )
    bits: list[str] = []
    for key, label in preferred:
        pair = stats.get(key)
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            a, b = pair[0], pair[1]
            bits.append(f"{label} {a:g}:{b:g}")
        if len(bits) >= 3:
            break
    return " · ".join(bits) if bits else "статистика сегмента получена"


def multisport_analysis_sections(limit_per_sport: int = 6) -> list[str]:
    """Current hockey+basketball Brain view for the common Analysis button."""
    state = _load_json(state_path(), {})
    sports_state = state.get("sports") if isinstance(state, dict) else {}
    sports_state = sports_state if isinstance(sports_state, dict) else {}
    provider = FlashscoreProvider()
    sections: list[str] = []

    for sport in ("hockey", "basketball"):
        icon, title = SPORT_META[sport]
        current = (sports_state.get(sport) or {}) if isinstance(sports_state, dict) else {}
        mapped = [dict(row) for row in (current.get("matches") or []) if isinstance(row, dict)]
        mapped_by_fs = {
            str(row.get("flashscore_event_id") or ""): row
            for row in mapped
            if str(row.get("flashscore_event_id") or "")
        }
        brain_rows = [
            dict(row) for row in (current.get("flashscore_analysis_matches") or [])
            if isinstance(row, dict)
        ]
        brain_by_fs = {
            str(row.get("flashscore_event_id") or ""): row
            for row in brain_rows
            if str(row.get("flashscore_event_id") or "")
        }
        live = _direct_flashscore_live(sport)
        if not live:
            continue
        lines = [f"{icon} <b>{title} · АНАЛИЗ LIVE</b> · {len(live)} матч."]
        for fs in live[:max(1, int(limit_per_sport))]:
            fs_id = str(fs.get("flashscore_event_id") or "")
            row = mapped_by_fs.get(fs_id)
            if row is None:
                row = next(
                    (
                        candidate for candidate in mapped
                        if _teams_match(
                            str(fs.get("home") or ""), str(fs.get("away") or ""),
                            str(candidate.get("home") or ""), str(candidate.get("away") or ""),
                        )
                    ),
                    None,
                )
            brain = brain_by_fs.get(fs_id)
            if brain is None:
                brain = next(
                    (
                        candidate for candidate in brain_rows
                        if _teams_match(
                            str(fs.get("home") or ""), str(fs.get("away") or ""),
                            str(candidate.get("home") or ""), str(candidate.get("away") or ""),
                        )
                    ),
                    None,
                )
            score = list(fs.get("score") or (brain or {}).get("score") or (row or {}).get("score") or [0, 0])
            period = str((brain or {}).get("period") or (row or {}).get("period") or fs.get("status_code") or "LIVE")
            stats_payload = dict((brain or {}).get("live_game_stats") or (row or {}).get("live_game_stats") or {})
            if not stats_payload and fs_id:
                try:
                    detailed = provider.fetch_stats_detailed(fs_id)
                    sections_raw = dict(detailed.get("sections") or {})
                    # For menu analysis, use the richest currently available section.
                    chosen = next(
                        (dict(v) for k, v in reversed(list(sections_raw.items())) if isinstance(v, dict) and (v.get("stats") or {})),
                        {},
                    )
                    segment_stats = {}
                    for key, item in (chosen.get("stats") or {}).items():
                        if not isinstance(item, dict):
                            continue
                        hv, av = item.get("home"), item.get("away")
                        if hv is not None and av is not None:
                            try:
                                segment_stats[str(key)] = [float(hv), float(av)]
                            except (TypeError, ValueError):
                                pass
                    stats_payload = {"segment_stats": segment_stats}
                except Exception:
                    stats_payload = {}

            signal = dict((row or {}).get("signal") or (row or {}).get("steam") or {})
            if signal:
                decision = (
                    f"🔥 <b>SIGNAL</b> · {signal.get('selection') or '?'} "
                    f"@ {float(signal.get('odd') or 0):.2f} · R{float(signal.get('strength') or 0):.0f}"
                )
            elif brain:
                brain_state = str(brain.get("brain_state") or "WAIT").upper()
                brain_score = float(brain.get("brain_score") or 0.0)
                brain_reason = str(brain.get("brain_reason") or "Flashscore статистика анализируется")
                projected = brain.get("projected_total")
                projection = (
                    f" · прогноз сегмента {float(projected):.1f}"
                    if projected is not None else ""
                )
                if row is None and brain_state in {"PASS", "BORDERLINE"}:
                    decision = (
                        f"🧠 <b>{brain_state}</b> · R{brain_score:.0f}{projection} · {brain_reason}\n"
                        f"💰 1xBet: Brain уже выбрал матч, теперь ищем цену текущего периода/четверти"
                    )
                elif row is not None and brain_state in {"PASS", "BORDERLINE"}:
                    decision = (
                        f"🧠 <b>{brain_state}</b> · R{brain_score:.0f}{projection} · {brain_reason}\n"
                        f"💰 1xBet найден, но edge/коэффициент не прошёл финальный фильтр"
                    )
                else:
                    decision = f"⏳ <b>WAIT</b> · Brain R{brain_score:.0f}{projection} · {brain_reason}"
            elif row is None:
                decision = "⏳ <b>WAIT</b> · Flashscore LIVE есть, Brain ещё прогревает статистику"
            else:
                decision = "⏳ <b>WAIT</b> · текущий период/четверть не прошёл пороги Brain"

            lines.append(
                f"<b>{fs.get('home','?')} — {fs.get('away','?')}</b> · {score[0]}:{score[1]} · {period}\n"
                f"📊 {_stats_brief(stats_payload, sport)}\n"
                f"{decision}"
            )
        sections.append("\n\n".join(lines))

    return sections


def multisport_report_text() -> str:
    lines = ["📊 <b>GOOL MULTI · ЖУРНАЛ</b>", "PREMATCH и LIVE считаются отдельно. ЭКСПРЕССЫ — отдельным разделом."]
    all_rows: list[dict[str, Any]] = []
    all_prematch: list[dict[str, Any]] = []
    all_live: list[dict[str, Any]] = []
    all_parlays: list[dict[str, Any]] = []

    for sport in ("hockey", "basketball"):
        sport_rows = _sport_rows(sport)
        parlays = [row for row in sport_rows if _is_parlay_row(row)]
        prematch = [
            row for row in sport_rows
            if _row_phase(row) == "PREMATCH" and not _is_parlay_row(row)
        ]
        live = [
            row for row in sport_rows
            if _row_phase(row) == "LIVE" and not _is_parlay_row(row)
        ]
        rows = [*prematch, *live, *parlays]
        all_rows.extend(rows)
        all_prematch.extend(prematch)
        all_live.extend(live)
        all_parlays.extend(parlays)
        icon, title = SPORT_META[sport]
        lines.append(
            f"{icon} <b>{title}</b>\n"
            f"🟡 PREMATCH · {_record_text(prematch)}\n"
            f"🔴 LIVE · {_record_text(live)}\n"
            f"🔗 ЭКСПРЕССЫ · {_record_text(parlays)}"
        )

    lines.append(
        f"🏟 <b>ИТОГО</b>\n"
        f"🟡 PREMATCH · {_record_text(all_prematch)}\n"
        f"🔴 LIVE · {_record_text(all_live)}\n"
        f"🔗 ЭКСПРЕССЫ · {_record_text(all_parlays)}\n"
        f"📚 ВСЕ · {_record_text(all_rows)}"
    )
    return "\n\n".join(lines)



def sport_prematch_picks_sections(sport: str, limit: int = 24) -> list[str]:
    """Only pending PREMATCH bets already issued by GOOL, nearest start first."""
    if sport not in SPORT_META:
        return ["🟡 <b>PREMATCH</b>\n\nНеизвестный вид спорта."]

    import datetime as _dt
    import time as _time

    icon, title = SPORT_META[sport]
    now = _time.time()
    rows: list[dict[str, Any]] = []
    for raw in _sport_rows(sport, "PREMATCH"):
        if _is_parlay_row(raw):
            continue
        row = normalize_entry(raw)
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        try:
            start_ts = float(row.get("scheduled_start_ts") or row.get("start_ts") or 0.0)
        except (TypeError, ValueError):
            start_ts = 0.0
        # Started matches belong to the common In Game view, not PREMATCH.
        if start_ts > 0 and start_ts <= now:
            continue
        row["_menu_start_ts"] = start_ts
        rows.append(row)

    rows.sort(key=lambda row: (
        float(row.get("_menu_start_ts") or 0.0) <= 0.0,
        float(row.get("_menu_start_ts") or 0.0) if float(row.get("_menu_start_ts") or 0.0) > 0 else float("inf"),
        str(row.get("home") or ""),
    ))
    rows = rows[: max(1, int(limit))]

    if not rows:
        return [f"🟡 <b>{icon} {title} · PREMATCH</b>\n\nСейчас нет выданных ботом ставок, которые ещё не начались."]

    try:
        tz = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        tz = _dt.timezone.utc

    valid_odds = []
    for row in rows:
        try:
            odd = float(row.get("odd") or 0.0)
        except (TypeError, ValueError):
            odd = 0.0
        if odd > 1.0:
            valid_odds.append(odd)
    avg_odd = (sum(valid_odds) / len(valid_odds)) if valid_odds else 0.0

    header = (
        f"🟡 <b>{icon} {title} · PREMATCH СТАВКИ</b>\n"
        f"Выдано и ещё не началось: <b>{len(rows)}</b> · ср. кэф <b>{avg_odd:.2f}</b>"
    )
    blocks: list[str] = [header]
    for idx, row in enumerate(rows, 1):
        start_ts = float(row.get("_menu_start_ts") or 0.0)
        start_label = (
            _dt.datetime.fromtimestamp(start_ts, tz).strftime("%d.%m %H:%M МСК")
            if start_ts > 0 else "время ?"
        )
        selection = str(row.get("selection") or "?")
        odd = float(row.get("odd") or 0.0)
        strength = float(row.get("strength") or 0.0)
        scope = str(row.get("scope") or "FULL_MATCH")
        scope_label = SCOPE_LABEL_RU.get(scope, scope)
        blocks.append(
            f"<b>{idx}. {row.get('home','?')} — {row.get('away','?')}</b>\n"
            f"🏆 {row.get('league') or '?'}\n"
            f"🕐 {start_label}\n"
            f"🎯 {scope_label} · <b>{selection} @ {odd:.2f}</b>\n"
            f"🧠 R{strength:.0f}"
        )

    messages: list[str] = []
    current = blocks[0]
    for block in blocks[1:]:
        candidate = current + "\n\n" + block
        if len(candidate) > 3800:
            messages.append(current)
            current = f"🟡 <b>{icon} {title} · PREMATCH · продолжение</b>\n\n{block}"
        else:
            current = candidate
    messages.append(current)
    return messages


def sport_journal_text(sport: str | None = None, limit: int = 14, phase: str | None = None) -> str:
    """Journal scoreboard grouped by signal date, with an all-time total."""
    rows = load_journal(journal_path())
    if sport in SPORT_META:
        rows = [row for row in rows if str(row.get("sport") or "") == sport]
        icon, title = SPORT_META[sport]
        heading = f"📒 <b>{icon} ЖУРНАЛ · {title}</b>"
        prematch_policy, live_policy = policy_text_ru(str(sport))
        policy = f"🟡 PREMATCH: {prematch_policy}\n🔴 LIVE: {live_policy}"
        return _dated_sport_journal(
            rows,
            heading=heading,
            policy=policy,
            phase=phase,
            limit_days=max(1, int(limit or 14)),
        )

    # Common multisport journal keeps the compact aggregate view.
    heading = "📒 <b>GOOL MULTI · ЖУРНАЛ</b>"
    policy = "🟡 PREMATCH · 🔴 LIVE · 🔗 ЭКСПРЕССЫ"
    parlays = [row for row in rows if _is_parlay_row(row)]
    prematch = [
        row for row in rows
        if _row_phase(row) == "PREMATCH" and not _is_parlay_row(row)
    ]
    live = [
        row for row in rows
        if _row_phase(row) == "LIVE" and not _is_parlay_row(row)
    ]
    wanted_phase = str(phase or "").upper()
    if wanted_phase == "PREMATCH":
        return (
            f"{heading} · PREMATCH\n\n{policy}\n\n"
            f"🟡 <b>PREMATCH</b>\n{_record_text(prematch)}\n\n"
            f"🔗 <b>ЭКСПРЕССЫ</b>\n{_record_text(parlays)}"
        )
    if wanted_phase == "LIVE":
        return f"{heading} · LIVE\n\n{policy}\n\n🔴 <b>LIVE</b>\n{_record_text(live)}"
    return (
        f"{heading}\n\n{policy}\n\n"
        f"🟡 <b>PREMATCH</b>\n{_record_text(prematch)}\n\n"
        f"🔴 <b>LIVE</b>\n{_record_text(live)}\n\n"
        f"🔗 <b>ЭКСПРЕССЫ</b>\n{_record_text(parlays)}"
    )

def hockey_journal_text(limit: int = 16, phase: str | None = None) -> str:
    return sport_journal_text("hockey", limit=limit, phase=phase)


def basketball_journal_text(limit: int = 16, phase: str | None = None) -> str:
    return sport_journal_text("basketball", limit=limit, phase=phase)


def sport_phase_report_text(sport: str) -> str:
    if sport not in SPORT_META:
        return multisport_report_text()
    icon, title = SPORT_META[sport]
    sport_rows = _sport_rows(sport)
    parlays = [row for row in sport_rows if _is_parlay_row(row)]
    prematch = [
        row for row in sport_rows
        if _row_phase(row) == "PREMATCH" and not _is_parlay_row(row)
    ]
    live = [
        row for row in sport_rows
        if _row_phase(row) == "LIVE" and not _is_parlay_row(row)
    ]
    prematch_policy, live_policy = policy_text_ru(sport)
    return (
        f"📊 <b>{icon} {title} · ОТДЕЛЬНЫЙ ОТЧЁТ</b>\n\n"
        f"🟡 <b>PREMATCH</b> · {_record_text(prematch)}\n"
        f"Рынки: {prematch_policy}\n\n"
        f"🔴 <b>LIVE</b> · {_record_text(live)}\n"
        f"Рынки: {live_policy}\n\n"
        f"🔗 <b>ЭКСПРЕССЫ</b> · {_record_text(parlays)}"
    )



def sport_parlay_text(sport: str) -> str:
    if sport not in SPORT_META:
        return "🔗 <b>GOOL MULTI · ЭКСПРЕССЫ</b>\n\nНеизвестный вид спорта."
    state = _load_json(state_path(), {})
    sports = state.get("sports") if isinstance(state, dict) else {}
    current = ((sports or {}).get(sport) or {}) if isinstance(sports, dict) else {}
    return parlay_text(list(current.get("prematch_parlays") or []), sport)
