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
from .multi_delivery import was_publicly_sent
from .multisport_journal import load_journal as load_multisport_journal


FINAL_RESULTS = {"won", "lost", "push", "void"}


def _tz():
    try:
        return ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return timezone.utc


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:+.2f}u"


def _odd(row: dict[str, Any]) -> float:
    try:
        return float(row.get("effective_odd") or row.get("settled_odd") or row.get("odd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _profit(row: dict[str, Any]) -> float | None:
    result = str(row.get("result") or "pending").lower()
    if result not in FINAL_RESULTS:
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
    odd = _odd(row)
    return odd - 1.0 if odd > 1.0 else None


def _result_label(row: dict[str, Any]) -> str:
    result = str(row.get("result") or "pending").lower()
    return {
        "won": "✅ ЗАШЛО",
        "lost": "❌ НЕ ЗАШЛО",
        "push": "↩️ ВОЗВРАТ",
        "void": "↩️ ВОЗВРАТ",
        "pending": "⏳ ЖДЁМ",
    }.get(result, result.upper() or "⏳ ЖДЁМ")


def _sport(row: dict[str, Any], fallback: str = "football") -> str:
    value = str(row.get("sport") or "").lower()
    return value if value in {"football", "hockey", "basketball"} else fallback


def _is_parlay(row: dict[str, Any]) -> bool:
    origin = str(row.get("origin") or "").lower()
    family = str(row.get("market_family") or "").lower()
    signal_type = str(row.get("signal_type") or "").lower()
    return (
        origin in {"prematch_parlay", "parlay", "multisport_parlay"}
        or family == "parlay"
        or signal_type == "prematch_parlay"
    )


def _phase(row: dict[str, Any]) -> str:
    phase = str(row.get("phase") or "").upper()
    if phase in {"PREMATCH", "LIVE"}:
        return phase
    origin = str(row.get("origin") or "").lower()
    return "PREMATCH" if origin.startswith("prematch") or _is_parlay(row) else "LIVE"


def _type_label(row: dict[str, Any]) -> str:
    if _is_parlay(row):
        return "Экспресс"
    origin = str(row.get("origin") or "").lower()
    if origin == "prematch_value":
        return "VALUE HUNTER"
    if _phase(row) == "PREMATCH":
        return "Ординар PREMATCH"
    strategy = str(row.get("strategy") or row.get("head") or "").lower()
    if strategy in {"goal_before_ht", "first_half_goal"}:
        return "Гол в 1-м тайме"
    if strategy in {"another_goal", "two_more_goals"}:
        return "LIVE"
    return "LIVE"


def _market_text(row: dict[str, Any]) -> str:
    if _is_parlay(row):
        legs = [leg for leg in (row.get("legs") or []) if isinstance(leg, dict)]
        if not legs:
            return str(row.get("selection") or "Экспресс")
        parts: list[str] = []
        for index, leg in enumerate(legs, 1):
            teams = f"{leg.get('home') or '?'} — {leg.get('away') or '?'}"
            selection = str(leg.get("selection") or leg.get("market") or "?")
            odd = _odd(leg)
            result = _result_label(leg)
            parts.append(f"{index}. {teams}: {selection} @ {odd:.2f} · {result}")
        return "\n".join(parts)
    return str(
        row.get("selection")
        or row.get("market")
        or row.get("strategy")
        or row.get("head")
        or "?"
    )


def _match_text(row: dict[str, Any]) -> str:
    if _is_parlay(row):
        legs = [leg for leg in (row.get("legs") or []) if isinstance(leg, dict)]
        return f"Экспресс ×{len(legs)}" if legs else "Экспресс"
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    return f"{home} — {away}"


def _score_text(row: dict[str, Any]) -> str:
    score = row.get("settled_score") or row.get("final_score")
    if isinstance(score, (list, tuple)) and len(score) >= 2:
        return f"{score[0]}:{score[1]}"
    return "—"


def _created_label(row: dict[str, Any], tz: Any) -> str:
    dt = _parse_dt(row.get("created_at") or row.get("captured_at"))
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M") if dt is not None else "—"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("result") or "pending").lower() for row in rows)
    settled = [row for row in rows if str(row.get("result") or "").lower() in FINAL_RESULTS]
    profits = [value for row in settled if (value := _profit(row)) is not None]
    odds = [_odd(row) for row in rows if _odd(row) > 1.0]
    risked = len([row for row in settled if str(row.get("result") or "").lower() in {"won", "lost"}])
    wins = counts["won"]
    losses = counts["lost"]
    return {
        "total": len(rows),
        "won": wins,
        "lost": losses,
        "pending": counts["pending"],
        "void": counts["void"] + counts["push"],
        "hit_rate": (wins / (wins + losses) * 100.0) if wins + losses else None,
        "avg_odd": (sum(odds) / len(odds)) if odds else None,
        "profit": sum(profits),
        "roi": (sum(profits) / risked * 100.0) if risked else None,
    }


def _stat_html(title: str, rows: list[dict[str, Any]]) -> str:
    s = _summary(rows)
    hit = "—" if s["hit_rate"] is None else f"{s['hit_rate']:.1f}%"
    avg = "—" if s["avg_odd"] is None else f"{s['avg_odd']:.2f}"
    roi = "—" if s["roi"] is None else f"{s['roi']:+.1f}%"
    return (
        '<div class="stat">'
        f"<h3>{html.escape(title)}</h3>"
        f"<div><b>{s['total']}</b> ставок</div>"
        f"<div>✅ {s['won']} · ❌ {s['lost']} · ⏳ {s['pending']} · ↩️ {s['void']}</div>"
        f"<div>Проход: <b>{hit}</b> · ср.кэф: <b>{avg}</b></div>"
        f"<div>P/L: <b>{s['profit']:+.2f}u</b> · ROI: <b>{roi}</b></div>"
        "</div>"
    )


def _table(rows: list[dict[str, Any]], tz: Any) -> str:
    if not rows:
        return "<p class='muted'>Нет записей.</p>"
    out = [
        "<table><thead><tr>",
        "<th>Дата</th><th>Спорт</th><th>Фаза</th><th>Тип</th><th>Матч / экспресс</th>",
        "<th>Турнир</th><th>Ставка</th><th>Кэф</th><th>Результат</th><th>Счёт</th><th>P/L</th>",
        "</tr></thead><tbody>",
    ]
    icons = {"football": "⚽", "hockey": "🏒", "basketball": "🏀"}
    for row in rows:
        sport = _sport(row)
        profit = _profit(row)
        market = html.escape(_market_text(row)).replace("\n", "<br>")
        out.append(
            "<tr>"
            f"<td>{html.escape(_created_label(row, tz))}</td>"
            f"<td>{icons.get(sport, '')} {html.escape(sport)}</td>"
            f"<td>{html.escape(_phase(row))}</td>"
            f"<td>{html.escape(_type_label(row))}</td>"
            f"<td>{html.escape(_match_text(row))}</td>"
            f"<td>{html.escape(str(row.get('league') or '—'))}</td>"
            f"<td>{market}</td>"
            f"<td>{_odd(row):.2f}</td>"
            f"<td>{html.escape(_result_label(row))}</td>"
            f"<td>{html.escape(_score_text(row))}</td>"
            f"<td>{'—' if profit is None else f'{profit:+.2f}u'}</td>"
            "</tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def _load_super10(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return []
    return [dict(row) for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _super10_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='muted'>SUPER 10 пока не отправлялся.</p>"
    out: list[str] = []
    for ticket in rows[::-1]:
        odd = float(ticket.get("combined_odds") or ticket.get("odd") or 0.0)
        result = str(ticket.get("result") or "pending").lower()
        result_label = {
            "won": "✅ ЗАШЛО",
            "lost": "❌ НЕ ЗАШЛО",
            "pending": "⏳ ЖДЁМ",
            "void": "↩️ ВОЗВРАТ",
            "push": "↩️ ВОЗВРАТ",
        }.get(result, result.upper())
        out.append(
            f"<div class='ticket'><h3>🌐 SUPER 10 · {html.escape(str(ticket.get('day') or '?'))}"
            f" · @ {odd:.2f} · {html.escape(result_label)}</h3><ol>"
        )
        for leg in ticket.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            sport = str(leg.get("sport") or "")
            icon = {"football": "⚽", "hockey": "🏒", "basketball": "🏀"}.get(sport, "•")
            out.append(
                "<li>"
                f"{icon} <b>{html.escape(str(leg.get('home') or '?'))} — {html.escape(str(leg.get('away') or '?'))}</b>"
                f"<br>{html.escape(str(leg.get('selection') or '?'))} @ {float(leg.get('odd') or 0.0):.2f}"
                f" · {html.escape(str(leg.get('super_tier') or 'strict'))}"
                "</li>"
            )
        out.append("</ol></div>")
    return "".join(out)


def build_full_report(
    *,
    football_path: Path | None = None,
    multisport_path: Path | None = None,
    super10_history_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[str, bytes, str]:
    from . import global_super10, multi_menu, multisport_menu

    tz = _tz()
    generated = now or datetime.now(tz)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=tz)
    else:
        generated = generated.astimezone(tz)

    football_path = football_path or multi_menu.journal_path()
    multisport_path = multisport_path or multisport_menu.journal_path()
    super10_history_path = super10_history_path or global_super10.history_path()

    football = [
        {**dict(row), "sport": "football"}
        for row in load_signal_journal(football_path)
        if was_publicly_sent(row) and not bool(row.get("public_duplicate"))
    ]
    multisport = [
        dict(row)
        for row in load_multisport_journal(multisport_path)
        if bool(row.get("telegram_sent"))
    ]
    rows = [*football, *multisport]
    rows.sort(
        key=lambda row: (_parse_dt(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
        reverse=True,
    )

    today = generated.date()
    today_rows = [
        row for row in rows
        if (dt := _parse_dt(row.get("created_at"))) is not None
        and dt.astimezone(tz).date() == today
    ]
    football_rows = [row for row in rows if _sport(row) == "football"]
    hockey_rows = [row for row in rows if _sport(row) == "hockey"]
    basketball_rows = [row for row in rows if _sport(row) == "basketball"]
    parlay_rows = [row for row in rows if _is_parlay(row)]
    singles_rows = [row for row in rows if not _is_parlay(row)]
    super10_rows = _load_super10(super10_history_path)

    css = """
    body{font-family:Arial,sans-serif;background:#f5f6f8;color:#16181d;margin:0;padding:24px}
    h1,h2,h3{margin:0 0 10px}.wrap{max-width:1500px;margin:auto}
    .meta,.muted{color:#6a717c}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:18px 0}
    .stat,.ticket{background:white;border:1px solid #dde1e7;border-radius:12px;padding:14px;margin:10px 0}
    table{width:100%;border-collapse:collapse;background:white;font-size:13px;margin:12px 0 28px}
    th,td{border:1px solid #dde1e7;padding:8px;vertical-align:top;text-align:left}
    th{background:#eef1f5;position:sticky;top:0}.section{margin-top:32px;overflow-x:auto}
    ol{margin-bottom:0}li{margin:6px 0}
    """
    html_doc = [
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<style>{css}</style><title>GOOL Full Report</title></head><body><div class='wrap'>",
        "<h1>📄 GOOL BOT · ПОЛНЫЙ ОТЧЁТ</h1>",
        f"<p class='meta'>Сформирован: {generated.strftime('%d.%m.%Y %H:%M')} · только реально отправленные ставки.</p>",
        "<h2>Сегодня</h2><div class='grid'>",
        _stat_html("Все ставки сегодня", today_rows),
        _stat_html("Футбол сегодня", [row for row in today_rows if _sport(row) == "football"]),
        _stat_html("Хоккей сегодня", [row for row in today_rows if _sport(row) == "hockey"]),
        _stat_html("Баскетбол сегодня", [row for row in today_rows if _sport(row) == "basketball"]),
        "</div>",
        "<h2>За всё время</h2><div class='grid'>",
        _stat_html("Все ставки", rows),
        _stat_html("Футбол", football_rows),
        _stat_html("Хоккей", hockey_rows),
        _stat_html("Баскетбол", basketball_rows),
        _stat_html("Ординары", singles_rows),
        _stat_html("Экспрессы", parlay_rows),
        "</div>",
        "<div class='section'><h2>Все ставки: ординары + LIVE</h2>",
        _table(singles_rows, tz),
        "</div>",
        "<div class='section'><h2>Экспрессы</h2>",
        _table(parlay_rows, tz),
        "</div>",
        "<div class='section'><h2>SUPER 10</h2>",
        _super10_html(super10_rows),
        "</div>",
        "</div></body></html>",
    ]
    payload = "".join(html_doc).encode("utf-8")
    filename = f"GOOL_FULL_REPORT_{generated.strftime('%Y-%m-%d_%H-%M')}.html"
    summary = _summary(rows)
    caption = (
        f"📄 <b>GOOL · ПОЛНЫЙ ОТЧЁТ</b>\n"
        f"Ставок: <b>{summary['total']}</b> · ✅ {summary['won']} · ❌ {summary['lost']} · "
        f"⏳ {summary['pending']} · P/L <b>{summary['profit']:+.2f}u</b>"
    )
    return filename, payload, caption


__all__ = ["build_full_report"]
