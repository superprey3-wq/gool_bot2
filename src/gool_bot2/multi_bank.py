from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import load_signal_journal, save_signal_journal


FINAL_RESULTS = {"won", "lost", "push", "void"}


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


def _initial_bank_env() -> float:
    try:
        return max(0.0, float(os.getenv("GOOL_MULTI_BANK_INITIAL_RUB", "100000")))
    except (TypeError, ValueError):
        return 100000.0


def stake_pct() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("GOOL_MULTI_BANK_STAKE_PCT", "0.02"))))
    except (TypeError, ValueError):
        return 0.02


def state_path(journal_path: Path) -> Path:
    raw = os.getenv("GOOL_MULTI_BANK_STATE_PATH", "").strip()
    return Path(raw) if raw else journal_path.with_name("gool_multi_bank_state.json")


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def ensure_state(journal_path: Path, *, started_at: datetime | None = None) -> dict[str, Any]:
    path = state_path(journal_path)
    state = _load_state(path)
    if state:
        return state
    started = started_at or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    state = {
        "version": 1,
        "started_at": started.astimezone(timezone.utc).isoformat(),
        "initial_bank_rub": round(_initial_bank_env(), 2),
        "last_daily_report_date": None,
    }
    _save_state(path, state)
    return state


def _started_at(state: dict[str, Any]) -> datetime:
    return _parse_dt(state.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc)


def _initial_bank(state: dict[str, Any]) -> float:
    try:
        return max(0.0, float(state.get("initial_bank_rub")))
    except (TypeError, ValueError):
        return _initial_bank_env()


def _eligible(row: dict[str, Any], state: dict[str, Any]) -> bool:
    created = _parse_dt(row.get("created_at"))
    return created is not None and created >= _started_at(state)


def _result(row: dict[str, Any]) -> str:
    return str(row.get("result") or "pending").lower()


def _unit_profit(row: dict[str, Any]) -> float:
    if row.get("profit_units") is not None:
        try:
            return float(row.get("profit_units") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    result = _result(row)
    odd = float(row.get("odd") or 0.0)
    if result == "won":
        return odd - 1.0
    if result == "lost":
        return -1.0
    return 0.0


def apply_settlement_fields(row: dict[str, Any]) -> bool:
    if _result(row) not in FINAL_RESULTS:
        return False
    try:
        stake = float(row.get("virtual_stake_rub"))
    except (TypeError, ValueError):
        return False
    profit = round(stake * _unit_profit(row), 2)
    if row.get("virtual_profit_rub") == profit:
        return False
    row["virtual_profit_rub"] = profit
    return True


def _profit_rub(row: dict[str, Any]) -> float:
    if _result(row) not in FINAL_RESULTS:
        return 0.0
    try:
        if row.get("virtual_profit_rub") is not None:
            return float(row.get("virtual_profit_rub") or 0.0)
        return float(row.get("virtual_stake_rub") or 0.0) * _unit_profit(row)
    except (TypeError, ValueError):
        return 0.0


def _settled_before(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if _result(row) not in FINAL_RESULTS:
        return False
    if cutoff is None:
        return True
    settled = _parse_dt(row.get("settled_at"))
    return settled is not None and settled < cutoff


def bank_at(rows: list[dict[str, Any]], state: dict[str, Any], cutoff: datetime | None = None) -> float:
    bank = _initial_bank(state)
    for row in rows:
        if not _eligible(row, state) or not _settled_before(row, cutoff):
            continue
        bank += _profit_rub(row)
    return round(bank, 2)


def ensure_bank_fields(rows: list[dict[str, Any]], journal_path: Path) -> bool:
    """Backfill bankroll fields for rows created after the virtual bank started.

    Stake sizing is based on realized bankroll at entry time. Pending exposure is
    not deducted from bankroll equity, so two simultaneous entries are sized from
    the same realized bank until one of them settles.
    """
    state = ensure_state(journal_path)
    active = [row for row in rows if _eligible(row, state)]
    active.sort(key=lambda row: str(row.get("created_at") or ""))
    processed: list[dict[str, Any]] = []
    changed = False
    pct = stake_pct()

    for row in active:
        created = _parse_dt(row.get("created_at")) or datetime.now(timezone.utc)
        if row.get("virtual_stake_rub") is None:
            before = _initial_bank(state)
            for prior in processed:
                if _settled_before(prior, created):
                    before += _profit_rub(prior)
            before = round(max(0.0, before), 2)
            stake = round(min(before, before * pct), 2)
            row["virtual_bank_before_rub"] = before
            row["virtual_stake_rub"] = stake
            row["virtual_stake_pct"] = round(pct, 6)
            changed = True
        elif row.get("virtual_bank_before_rub") is None:
            before = _initial_bank(state)
            for prior in processed:
                if _settled_before(prior, created):
                    before += _profit_rub(prior)
            row["virtual_bank_before_rub"] = round(max(0.0, before), 2)
            changed = True
        if apply_settlement_fields(row):
            changed = True
        processed.append(row)
    return changed


def attach_entry_fields(row: dict[str, Any], rows: list[dict[str, Any]], journal_path: Path) -> None:
    created = _parse_dt(row.get("created_at")) or datetime.now(timezone.utc)
    state = ensure_state(journal_path, started_at=created)
    ensure_bank_fields(rows, journal_path)
    before = max(0.0, bank_at(rows, state, created))
    pct = stake_pct()
    row["virtual_bank_before_rub"] = round(before, 2)
    row["virtual_stake_rub"] = round(min(before, before * pct), 2)
    row["virtual_stake_pct"] = round(pct, 6)
    row["virtual_profit_rub"] = None


def sync_bank_fields(journal_path: Path) -> list[dict[str, Any]]:
    rows = load_signal_journal(journal_path)
    if ensure_bank_fields(rows, journal_path):
        save_signal_journal(journal_path, rows)
    return rows


def _money(value: float, *, signed: bool = False) -> str:
    rounded = int(round(value))
    if signed:
        sign = "+" if rounded > 0 else ("−" if rounded < 0 else "")
        rounded = abs(rounded)
    else:
        sign = ""
    return f"{sign}{rounded:,}".replace(",", " ") + " ₽"


def _pct_text(value: float) -> str:
    return f"{value:+.2f}%" if value else "0.00%"


def _local_range(day: date, tz) -> tuple[datetime, datetime]:
    start = datetime.combine(day, dt_time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _in_range(value: Any, start: datetime, end: datetime) -> bool:
    dt = _parse_dt(value)
    return dt is not None and start <= dt < end


def _status_at(row: dict[str, Any], cutoff: datetime) -> str:
    result = _result(row)
    if result not in FINAL_RESULTS:
        return "pending"
    settled = _parse_dt(row.get("settled_at"))
    return result if settled is not None and settled < cutoff else "pending"


def _period_stats(rows: list[dict[str, Any]], state: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    opened = [row for row in rows if _eligible(row, state) and _in_range(row.get("created_at"), start, end)]
    settled = [row for row in rows if _eligible(row, state) and _in_range(row.get("settled_at"), start, end)]
    counts = Counter(_result(row) for row in settled)
    turnover = sum(float(row.get("virtual_stake_rub") or 0.0) for row in opened)
    settled_risk = sum(
        float(row.get("virtual_stake_rub") or 0.0)
        for row in settled
        if _result(row) in {"won", "lost"}
    )
    profit = sum(_profit_rub(row) for row in settled)
    pending_at_end = sum(1 for row in opened if _status_at(row, end) == "pending")
    roi = 0.0 if settled_risk <= 0 else profit / settled_risk * 100.0
    return {
        "opened": len(opened),
        "won": counts["won"],
        "lost": counts["lost"],
        "void": counts["void"] + counts["push"],
        "settled": len(settled),
        "pending_at_end": pending_at_end,
        "turnover": turnover,
        "profit": profit,
        "roi": roi,
    }


def current_bank_summary(journal_path: Path, *, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = sync_bank_fields(journal_path)
    state = ensure_state(journal_path)
    bank = bank_at(rows, state, now.astimezone(timezone.utc) + timedelta(microseconds=1))
    initial = _initial_bank(state)
    pnl = bank - initial
    pct = 0.0 if initial <= 0 else pnl / initial * 100.0
    pending = [row for row in rows if _eligible(row, state) and _result(row) == "pending"]
    pending_stake = sum(float(row.get("virtual_stake_rub") or 0.0) for row in pending)
    return [
        f"💰 <b>Виртуальный банк: {_money(bank)}</b> · P/L {_money(pnl, signed=True)} ({_pct_text(pct)})",
        f"Старт: {_money(initial)} · риск на новый BET: <b>{stake_pct() * 100:.1f}%</b> · открыто: {len(pending)} на {_money(pending_stake)}",
    ]


def render_daily_bank_report(
    journal_path: Path,
    *,
    report_date: date | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz = _tz()
    local_now = now.astimezone(tz)
    day = report_date or local_now.date()
    day_start, day_end = _local_range(day, tz)
    cutoff = min(now.astimezone(timezone.utc) + timedelta(microseconds=1), day_end)

    rows = sync_bank_fields(journal_path)
    state = ensure_state(journal_path)
    day_stats = _period_stats(rows, state, day_start, cutoff)
    bank_start = bank_at(rows, state, day_start)
    bank_end = bank_at(rows, state, cutoff)
    day_pnl = bank_end - bank_start
    day_growth = 0.0 if bank_start <= 0 else day_pnl / bank_start * 100.0

    month_day = day.replace(day=1)
    month_start, _ = _local_range(month_day, tz)
    month_stats = _period_stats(rows, state, month_start, cutoff)
    month_bank_start = bank_at(rows, state, month_start)
    month_bank_end = bank_at(rows, state, cutoff)
    month_pnl = month_bank_end - month_bank_start
    month_growth = 0.0 if month_bank_start <= 0 else month_pnl / month_bank_start * 100.0

    tomorrow = day + timedelta(days=1)
    month_final = tomorrow.month != day.month
    month_title = "🏁 <b>ИТОГ МЕСЯЦА</b>" if month_final else f"📆 <b>{day.strftime('%m.%Y')} · МЕСЯЦ</b>"
    tz_label = getattr(tz, "key", None) or "local"

    parts = [
        "💰 <b>GOOL MULTI · ВИРТУАЛЬНЫЙ БАНК</b>",
        f"📅 <b>{day.strftime('%d.%m.%Y')}</b> · 00:00–23:59 · {tz_label}",
        f"Риск: <b>{stake_pct() * 100:.1f}% от реализованного банка на каждый BEST BET</b>",
        "",
        f"Банк 00:00: <b>{_money(bank_start)}</b>",
        f"Ставок открыто: <b>{day_stats['opened']}</b> · оборот {_money(day_stats['turnover'])}",
        f"Рассчитано: <b>{day_stats['settled']}</b> · ✅ {day_stats['won']} · ❌ {day_stats['lost']} · ↩️ {day_stats['void']} · ⏳ {day_stats['pending_at_end']}",
        f"P/L дня: <b>{_money(day_pnl, signed=True)}</b> · ROI {day_stats['roi']:+.2f}%",
        f"Банк 23:59: <b>{_money(bank_end)}</b> · {_pct_text(day_growth)} за день",
        "",
        month_title,
        f"Старт месяца: <b>{_money(month_bank_start)}</b>",
        f"Ставок: <b>{month_stats['opened']}</b> · ✅ {month_stats['won']} · ❌ {month_stats['lost']} · ↩️ {month_stats['void']}",
        f"P/L месяца: <b>{_money(month_pnl, signed=True)}</b> · ROI {month_stats['roi']:+.2f}%",
        f"Банк: <b>{_money(month_bank_end)}</b> · {_pct_text(month_growth)} за месяц",
    ]
    return "\n".join(parts)


def daily_report_due_date(journal_path: Path, *, now: datetime | None = None) -> date | None:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state = ensure_state(journal_path)
    tz = _tz()
    local = now.astimezone(tz)
    today = local.date()
    last_raw = str(state.get("last_daily_report_date") or "").strip()
    try:
        last = date.fromisoformat(last_raw) if last_raw else None
    except ValueError:
        last = None

    report_hour = max(0, min(23, int(os.getenv("GOOL_MULTI_BANK_REPORT_HOUR", "23"))))
    report_minute = max(0, min(59, int(os.getenv("GOOL_MULTI_BANK_REPORT_MINUTE", "59"))))
    due_time = dt_time(report_hour, report_minute)
    if local.time() >= due_time and last != today:
        return today

    yesterday = today - timedelta(days=1)
    started_local = _started_at(state).astimezone(tz).date()
    if started_local <= yesterday and (last is None or last < yesterday):
        return yesterday
    return None


def mark_daily_report_sent(journal_path: Path, day: date, *, sent_at: datetime | None = None) -> None:
    path = state_path(journal_path)
    state = ensure_state(journal_path)
    sent = sent_at or datetime.now(timezone.utc)
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    state["last_daily_report_date"] = day.isoformat()
    state["last_daily_report_sent_at"] = sent.astimezone(timezone.utc).isoformat()
    _save_state(path, state)
