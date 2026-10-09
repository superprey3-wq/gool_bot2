"""Self-contained, evidence-first audit of GOOL day-report basketball singles.

Only journal values already captured at the moment of signal/settlement are
used. Never infer H2H coefficients or historical 8/10 from screenshots, never
regenerate past signals from today's lines, and never label synthetic sample
frequency as a calibrated winning probability.
"""
from __future__ import annotations

import html
import json
import math
from collections import Counter, defaultdict
from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _str(value: Any) -> str:
    return html.escape(str(value))


def _fmt(value: Any, decimals: int = 2) -> str:
    number = _num(value)
    return f"{number:.{decimals}f}" if number is not None else "нет данных"


def _score(value: Any) -> str:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return f"{_str(value[0])}:{_str(value[1])}"
    return "не записан"


def _scope(scope: Any) -> str:
    key = str(scope or "")
    options = {
        "FULL_MATCH": "Матч целиком",
        "FIRST_HALF": "1-я половина",
        "SECOND_HALF": "2-я половина",
        "QUARTER_1": "1-я четверть",
        "QUARTER_2": "2-я четверть",
        "QUARTER_3": "3-я четверть",
        "QUARTER_4": "4-я четверть",
    }
    return options.get(key, key or "область не записана")


def _phase(value: Any) -> str:
    return str(value or "?").upper()


def _direction(row: dict[str, Any]) -> str:
    # On total markets the user-visible selection is the settlement contract.
    # Some older journal rows had technical direction mismatching card text.
    selection = str(row.get("selection") or "").lower()
    family = str(row.get("market_family") or "")
    if family in {"match_total", "home_total", "away_total"}:
        if "итм" in selection or "тм" in selection or "under" in selection:
            return "under"
        if "итб" in selection or "тб" in selection or "over" in selection:
            return "over"
    direction = str(row.get("direction") or "").lower()
    if direction in {"over", "under", "home", "away"}:
        return direction
    return ""


def _pct(rows: list[dict[str, Any]]) -> str:
    wins = sum(str(r.get("result") or "") == "won" for r in rows)
    lost = sum(str(r.get("result") or "") == "lost" for r in rows)
    return f"{wins/(wins+lost)*100:.1f}%" if wins + lost else "—"


def _units(row: dict[str, Any]) -> float | None:
    result = str(row.get("result") or "pending").lower()
    if result not in {"won", "lost", "void", "push"}:
        return None
    stored = _num(row.get("profit_units"))
    if stored is not None:
        return stored
    if result in {"push", "void"}:
        return 0.0
    if result == "lost":
        return -1.0
    odd = _num(row.get("odd"))
    return odd-1.0 if odd is not None and odd > 1.0 else None


def _line_result(row: dict[str, Any]) -> dict[str, Any]:
    """Independent arithmetic consistency audit. Never overwrite bookmaker settlement.

    Scope-specific settled_score is authoritative for the *selection*. The
    optional settled_match_score is shown separately and NEVER used to grade
    a quarter or half market.
    """
    score = row.get("settled_score")
    if not isinstance(score, (tuple, list)) or len(score) < 2:
        return {"state": "NO_SETTLED_SCORE", "detail": "Для независимого расчёта не записан settled_score"}
    home, away = _num(score[0]), _num(score[1])
    line = _num(row.get("line"))
    if home is None or away is None or line is None:
        return {"state": "NO_SETTLED_SCORE", "detail": "Для независимого расчёта не хватает счёта или линии"}
    family = str(row.get("market_family") or "")
    side = str(row.get("selection_side") or _direction(row)).lower()
    direct = _direction(row)
    # The settlement method's own semantics, with explicit scope accounting.
    if family == "handicap":
        if side not in {"home", "away"}:
            return {"state": "NO_MARKET_SIDE", "detail": "Сторона форы отсутствует в записи"}
        diff = (home-away if side == "home" else away-home) + line
        estimated = "won" if diff > 0 else "lost" if diff < 0 else "void"
        calculation = f"Фора {side}: ({home:g}−{away:g})+{line:+g}={diff:+g}" if side == "home" else f"Фора away: ({away:g}−{home:g})+{line:+g}={diff:+g}"
    elif family == "moneyline":
        if side not in {"home", "away"}:
            return {"state": "NO_MARKET_SIDE", "detail": "Сторона исхода отсутствует в записи"}
        estimated = "void" if home == away else "won" if ((home > away) == (side == "home")) else "lost"
        calculation = f"Исход {side}: {home:g}:{away:g}"
    elif family in {"match_total", "home_total", "away_total"}:
        if direct not in {"over", "under"}:
            return {"state": "NO_MARKET_SIDE", "detail": "Направление тотала не записано"}
        value = home+away if family == "match_total" else home if family == "home_total" else away
        diff = value-line
        estimated = "void" if diff == 0 else "won" if ((diff > 0) == (direct == "over")) else "lost"
        calculation = f"{family} {direct}: {value:g} против линии {line:g}"
    else:
        return {"state": "NOT_SUPPORTED", "detail": "Рынок не проверяется простым расчётом"}
    reported = str(row.get("result") or "pending").lower()
    state = "MATCH" if reported == estimated else "MISMATCH" if reported in {"won","lost","void","push"} else "PENDING"
    return {"state": state, "estimated": estimated, "detail": calculation, "reported": reported}


def _source_brain(row: dict[str, Any]) -> str:
    return str(row.get("brain_mode") or row.get("signal_type") or "не записан")


def _model_evidence(row: dict[str, Any]) -> list[str]:
    lines = []
    brain = _source_brain(row)
    lines.append(f"Brain: {brain}")
    tier = row.get("historical_tier")
    hits_h = row.get("historical_home_hits")
    hits_a = row.get("historical_away_hits")
    if brain == "basketball_historical_v3" or (tier is not None and tier != ""):
        if hits_h is not None and hits_a is not None:
            lines.append(f"История 10+10: {hits_h}/10 и {hits_a}/10 · {tier or 'ступень не записана'}")
        else:
            lines.append("Исторические 7/10–8/10: НЕ ЗАПИСАНЫ (не приписывать задним числом)")
        coefficient = row.get("historical_coefficient_available")
        if coefficient is True:
            ch = row.get("historical_coefficient_home")
            ca = row.get("historical_coefficient_away")
            lines.append(f"Поправка на соперника: применена · K_A={ch if ch is not None else '?'} · K_B={ca if ca is not None else '?'}")
        elif coefficient is False:
            lines.append("Поправка на соперника: не применена (не хватило полной H2H-выборки)")
        else:
            lines.append("Поправка на соперника: наличие данных не записано")
        lines.append(f"Сдвиг прогнозного тотала: {_fmt(row.get('historical_correction_points'))} очка")
        if bool(row.get("historical_probability_uncalibrated")):
            lines.append("Вероятность V3: НЕ КАЛИБРОВАНА; 8/10 не означает 80% на будущие ставки")
    else:
        lines.append("Исторический фильтр V3: нет подтверждения в журнале; не приписывать эту ставку V3")
    for key, title in [
        ("projected_total", "Прогноз"),
        ("stat_edge", "Разница прогноза с линией"),
        ("strength", "Рейтинг (не вероятность)"),
        ("model_probability", "Модельная вероятность (как сохранено)"),
        ("market_probability", "Вероятность из рынка (как сохранено)"),
        ("opening_line", "Начальная линия"),
        ("opening_odd", "Начальный коэффициент"),
    ]:
        value = row.get(key)
        if value is not None:
            lines.append(f"{title}: {value}")
    if row.get("market_steam_agrees") is not None:
        lines.append("Движение 1xBet: " + ("совпадает" if row["market_steam_agrees"] else "против направления"))
    if row.get("matchbook_money_flow_agrees") is not None:
        lines.append("Matchbook: " + ("совпадает" if row["matchbook_money_flow_agrees"] else "против направления"))
    return lines


def _group_row(label: str, rows: list[dict[str, Any]]) -> str:
    settled = [r for r in rows if str(r.get("result") or "").lower() in {"won","lost","void","push"}]
    won = sum(str(r.get("result") or "").lower() == "won" for r in rows)
    lost = sum(str(r.get("result") or "").lower() == "lost" for r in rows)
    pending = len(rows)-len(settled)
    units = [_units(r) for r in settled]
    profit = sum(x for x in units if x is not None)
    graded = sum(str(r.get("result") or "").lower() in {"won","lost"} for r in rows)
    odds = [_num(r.get("odd")) for r in rows]
    odds = [v for v in odds if v is not None and v>1]
    avg = sum(odds)/len(odds) if odds else None
    roi = 100.0*profit/graded if graded else None
    return (
        "<tr>"
        f"<td>{_str(label)}</td><td>{len(rows)}</td><td>{won}</td><td>{lost}</td>"
        f"<td>{pending}</td><td>{_pct(rows)}</td>"
        f"<td>{f'{avg:.2f}' if avg is not None else '—'}</td>"
        f"<td>{profit:+.2f}u</td><td>{f'{roi:+.1f}%' if roi is not None else '—'}</td>"
        "</tr>"
    )


def _group_table(grouped: dict[str, list[dict[str, Any]]]) -> str:
    head = "<table><thead><tr><th>Сегмент</th><th>Всего</th><th>WIN</th><th>LOSS</th><th>Ждём</th><th>Проход</th><th>Ср. кэф</th><th>P/L</th><th>ROI</th></tr></thead><tbody>"
    return head + "".join(_group_row(k,v) for k,v in sorted(grouped.items())) + "</tbody></table>"


def _serialize(row: dict[str, Any]) -> str:
    # Raw snapshot is complete and shareable for downstream AI audit.
    value = json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True, default=str, allow_nan=True)
    return html.escape(value)


def raw_evidence_html(row: dict[str, Any]) -> str:
    """Full immutable (for this export) stored row, not a model reconstruction."""
    return "<details><summary>Все исходные поля журнала (JSON)</summary><pre class='raw-json'>" + _serialize(row) + "</pre></details>"


def basketball_detail_html(rows: list[dict[str, Any]]) -> str:
    """Loss-analysis, model attribution, per-market settlement evidence and raw rows."""
    if not rows:
        return "<p class='muted'>Сегодня нет отправленных баскетбольных сигналов.</p>"
    by_model: dict[str,list[dict[str,Any]]] = defaultdict(list)
    by_phase: dict[str,list[dict[str,Any]]] = defaultdict(list)
    by_dir: dict[str,list[dict[str,Any]]] = defaultdict(list)
    by_scope: dict[str,list[dict[str,Any]]] = defaultdict(list)
    by_odds: dict[str,list[dict[str,Any]]] = defaultdict(list)
    for row in rows:
        by_model[_source_brain(row)].append(row)
        by_phase[_phase(row.get("phase"))].append(row)
        by_dir[_direction(row) or "не указано"].append(row)
        by_scope[_scope(row.get("scope"))].append(row)
        odd = _num(row.get("odd"))
        band = ("коэф не записан" if odd is None else "<1.40" if odd<1.40
                else "1.40–1.59" if odd<1.60 else "1.60–1.99" if odd<2.0
                else "2.00–2.99" if odd<3.0 else "≥3.00")
        by_odds[band].append(row)

    parts = [
        "<div class='audit'>",
        "<h3>🏀 БЕЗ ДОГАДОК: режим мозга, линия, история 10+10, коэффициенты и расчёт</h3>",
        "<p>Группы считаются ТОЛЬКО по отправленным ставкам текущего дня. "
        "У V3 частота 8/10 — показатель прошлых игр, а не гарантия выигрыша. "
        "Старые записи без диагностических полей отмечены как неполные; "
        "последующие результаты нельзя использовать для восстановления прогноза задним числом.</p>",
    ]
    for title, groups in [
        ("Версия Brain — отдельно V3 и V2", by_model),
        ("LIVE / PREMATCH", by_phase),
        ("Направления ТБ/ТМ/фора", by_dir),
        ("Четверть, половина, матч", by_scope),
        ("Коэффициенты", by_odds),
    ]:
        parts.append(f"<h3>{_str(title)}</h3>" + _group_table(groups))

    warnings = []
    for row in rows:
        result = _line_result(row)
        if result["state"] == "MISMATCH":
            warnings.append(
                f"<li><b>{_str(row.get('home') or '?')} — {_str(row.get('away') or '?')}</b>: "
                f"{_str(row.get('selection') or '?')} · журнал={_str(result.get('reported'))} "
                f"vs расчёт={_str(result.get('estimated'))} · {_str(result.get('detail'))}</li>"
            )
    if warnings:
        parts.append("<h3>⚠️ Возможные ошибки расчёта исходов (проверить линию и правила букмекера)</h3><ul>"+"".join(warnings)+"</ul>")
    else:
        parts.append("<p>Проверка счёта: явных расхождений среди доступных данных не обнаружено. "
                     "Это не подтверждает внешнюю линию букмекера или полноту источников.</p>")

    parts.append("<h3>Полная история каждого отправленного сигнала</h3>")
    for row in rows:
        score = _score(row.get("settled_score"))
        full_score = _score(row.get("settled_match_score"))
        check = _line_result(row)
        status = str(row.get("result") or "pending").upper()
        title = (
            f"{_str(row.get('home') or '?')} — {_str(row.get('away') or '?')} "
            f"({_str(_phase(row.get('phase')))} / {_str(_scope(row.get('scope')))})"
        )
        meta = _model_evidence(row)
        text = (
            f"<b>Выбор:</b> {_str(row.get('selection') or '?')} @ {_fmt(row.get('odd'))}; "
            f"<b>исход:</b> {_str(status)}; "
            f"<b>P/L:</b> {f'{_units(row):+.2f}u' if _units(row) is not None else 'ожидание'}"
        )
        parts.append(
            "<details class='ticket'><summary>" + title + " — " + _str(status) + "</summary>"
            f"<p>{text}</p>"
            f"<p><b>Счёт выбранного рынка:</b> {score}; "
            f"<b>счёт всего матча:</b> {full_score}. "
            "Для четверти учитывается её счёт, а не общий.</p>"
            f"<p><b>Независимая проверка журнала:</b> {_str(check['state'])}; {_str(check['detail'])}</p>"
            "<ul>" + "".join(f"<li>{_str(m)}</li>" for m in meta) + "</ul>"
            f"<p><b>1xBet ID:</b> {_str(row.get('event_id') or 'не записан')}; "
            f"<b>Flashscore ID:</b> {_str(row.get('flashscore_event_id') or 'не записан')}; "
            f"<b>время отправки:</b> {_str(row.get('created_at') or 'не записано')}</p>"
            + raw_evidence_html(row) + "</details>"
        )
    parts.append("</div>")
    return "".join(parts)


__all__ = ["basketball_detail_html", "raw_evidence_html", "_line_result", "_model_evidence"]
