from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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


def _sport_rows(sport: str) -> list[dict[str, Any]]:
    rows = _load_json(journal_path(), [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and str(row.get("sport") or "") == sport]


def _record_text(rows: list[dict[str, Any]]) -> str:
    settled = [row for row in rows if str(row.get("result") or "") in {"won", "lost", "void"}]
    won = sum(1 for row in settled if row.get("result") == "won")
    lost = sum(1 for row in settled if row.get("result") == "lost")
    void = sum(1 for row in settled if row.get("result") == "void")
    pending = sum(1 for row in rows if str(row.get("result") or "pending") == "pending")
    profit = sum(float(row.get("profit_units") or 0.0) for row in settled)
    graded = won + lost
    hit = won / graded * 100.0 if graded else 0.0
    roi = profit / len(settled) * 100.0 if settled else 0.0
    return f"✅ {won} · ❌ {lost} · ↩️ {void} · ⏳ {pending} · проход {hit:.1f}% · P/L {profit:+.2f}u · ROI {roi:+.1f}%"


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
            f"{icon} <b>{title}</b> · FS {int(row.get('flashscore_live') or 0)} · "
            f"1xBet {int(row.get('xbet_live') or 0)} · mapped {int(row.get('mapped') or 0)} · "
            f"decoded {int(row.get('decoded') or 0)} · signals {int(row.get('detected') or 0)}"
        )
    return "\n".join(lines)


def sport_overview_text(sport: str) -> str:
    if sport not in SPORT_META:
        return multisport_status_text()
    icon, title = SPORT_META[sport]
    state = _load_json(state_path(), {})
    sports = state.get("sports") if isinstance(state, dict) else {}
    current = ((sports or {}).get(sport) or {}) if isinstance(sports, dict) else {}
    rows = _sport_rows(sport)
    mode = str((state or {}).get("mode") or os.getenv("GOOL_MULTISPORT_MODE", "shadow")).upper()
    lines = [
        f"{icon} <b>GOOL MULTI · {title}</b> · {mode}",
        _record_text(rows),
        "",
        f"Сейчас LIVE: <b>{int(current.get('flashscore_live') or 0)}</b> · "
        f"синхронизировано: <b>{int(current.get('decoded') or 0)}</b>",
    ]
    matches = [row for row in (current.get("matches") or []) if isinstance(row, dict)]
    if not matches:
        lines.append("\nСейчас нет синхронизированных LIVE-матчей.")
        return "\n".join(lines)

    lines.append("")
    for row in matches[:6]:
        score = list(row.get("score") or [0, 0])
        period = str(row.get("period") or "LIVE")
        line = float(row.get("line") or 0.0)
        over = float(row.get("over") or 0.0)
        under = float(row.get("under") or 0.0)
        signal = row.get("signal") or row.get("steam") or {}
        signal_text = ""
        if signal:
            side = "ТБ" if str(signal.get("direction") or "over") == "over" else "ТМ"
            signal_text = f" · 🔥 {side} {float(signal.get('line') or line):g} R{float(signal.get('strength') or 0):.0f}"
        lines.append(
            f"<b>{row.get('home','?')} — {row.get('away','?')}</b> · {score[0]}:{score[1]} · {period}\n"
            f"↳ тотал {line:g} · ТБ {over:.2f} / ТМ {under:.2f}{signal_text}"
        )
    return "\n\n".join(lines)


def multisport_report_text() -> str:
    lines = ["📊 <b>GOOL MULTI · ХОККЕЙ + БАСКЕТБОЛ</b>"]
    all_rows: list[dict[str, Any]] = []
    for sport in ("hockey", "basketball"):
        rows = _sport_rows(sport)
        all_rows.extend(rows)
        icon, title = SPORT_META[sport]
        lines.append(f"{icon} <b>{title}</b>\n{_record_text(rows)}")

    lines.append(f"🏟 <b>ИТОГО</b>\n{_record_text(all_rows)}")
    return "\n\n".join(lines)
