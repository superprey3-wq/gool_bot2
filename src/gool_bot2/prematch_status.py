from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return Path(os.getenv("GOOL_PREMATCH_STATUS_PATH", str(runtime / "live" / "prematch_status.json")))


def update_prematch_status(**fields: Any) -> dict[str, Any]:
    path = _path()
    current: dict[str, Any] = {}
    try:
        raw = json.loads(path.read_text("utf-8"))
        if isinstance(raw, dict):
            current = raw
    except Exception:
        pass
    current.update(fields)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)
    return current


def _fmt(value: Any) -> str:
    return "—" if value in {None, ""} else str(value)


def prematch_status_text() -> str:
    path = _path()
    try:
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("status_not_object")
    except Exception:
        return (
            "🩺 <b>PREMATCH STATUS</b>\n\n"
            "Файл статуса ещё не создан. После следующего PREMATCH-прогона здесь появится диагностика."
        )

    running = bool(data.get("running"))
    exit_code = data.get("last_exit_code")
    mode = str(data.get("mode") or "—")
    lines = [
        "🩺 <b>PREMATCH STATUS</b>",
        f"Состояние: <b>{'RUNNING' if running else 'IDLE'}</b>",
        f"Этап: <b>{_fmt(data.get('stage'))}</b>",
        f"Последний старт: <code>{_fmt(data.get('last_cycle_started_at'))}</code>",
        f"Последнее завершение: <code>{_fmt(data.get('last_cycle_finished_at'))}</code>",
        f"Exit code: <b>{_fmt(exit_code)}</b>",
        "",
        "<b>Последний анализ</b>",
        f"матчей сегодня впереди: <b>{_fmt(data.get('fixtures'))}</b>",
        f"brain eligible: <b>{_fmt(data.get('brain_eligible'))}</b>",
        f"confident shortlist: <b>{_fmt(data.get('shortlist'))}</b> / cap <b>{_fmt(data.get('shortlist_cap'))}</b>",
        f"qualified before cap: <b>{_fmt(data.get('shortlist_qualified'))}</b>",
        f"priced candidates: <b>{_fmt(data.get('priced'))}</b>",
        f"режим: <b>{mode}</b>",
        f"ординары: <b>{_fmt(data.get('singles'))}</b>",
        f"экспрессы: <b>{_fmt(data.get('doubles'))}</b>",
        f"SUPER: <b>{'да' if data.get('super') else 'нет'}</b>",
        f"1xBet matched/stored: <b>{_fmt(data.get('xbet_matches'))}</b> · refreshed: <b>{_fmt(data.get('xbet_refreshed'))}</b>",
        "",
        "<b>Отправка</b>",
        f"cards: <b>{_fmt(data.get('delivered_cards'))}</b> · "
        f"singles: <b>{_fmt(data.get('delivered_entries'))}</b> · "
        f"parlays: <b>{_fmt(data.get('delivered_parlays'))}</b>",
    ]
    if data.get("last_error"):
        lines += ["", f"⚠️ Последняя ошибка: <code>{data.get('last_error')}</code>"]
    return "\n".join(lines)


__all__ = ["prematch_status_text", "update_prematch_status"]
