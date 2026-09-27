from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from .providers.flashscore import FlashscoreProvider
from .v4_shadow_report import _analyse_fixtures


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", " ", str(value or "").casefold()).strip()


def find_today_matches(query: str, *, limit: int = 6) -> list[Any]:
    queries = _search_forms(query)
    if not queries or max(map(len, queries)) < 2:
        return []
    fs = FlashscoreProvider()
    rows = fs.scheduled_matches_for_day(0) + fs.live_matches()
    found = []
    seen = set()
    for m in rows:
        if m.provider_match_id in seen:
            continue
        hay = _norm(f"{m.home} {m.away}")
        best = None
        for q in queries:
            tokens = [x for x in q.split() if len(x) >= 2]
            if q in hay:
                candidate = 100 + len(q)
            else:
                hits = sum(1 for t in tokens if t in hay)
                if not hits:
                    continue
                candidate = hits * 20 - abs(len(hay) - len(q)) * .02
            best = candidate if best is None else max(best, candidate)
        if best is None:
            continue
        score = best
        seen.add(m.provider_match_id)
        found.append((score, m))
    found.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in found[:max(1, limit)]]


def _team_form(rows: list[dict[str, Any]], team: str) -> str:
    if not rows:
        return "нет данных"
    w = d = l = gf = ga = 0
    fs = FlashscoreProvider()
    for r in rows[:10]:
        home = fs._same_team(str(r.get("home") or ""), team)
        hs, aws = int(r.get("home_score") or 0), int(r.get("away_score") or 0)
        a, b = (hs, aws) if home else (aws, hs)
        gf += a; ga += b
        if a > b: w += 1
        elif a == b: d += 1
        else: l += 1
    n = min(10, len(rows))
    return f"{w}В-{d}Н-{l}П · голы {gf}:{ga} · {gf/n:.2f}/{ga/n:.2f} за матч"


def analyse_match_text(match: Any) -> str:
    fs = FlashscoreProvider()
    analysed, failures = _analyse_fixtures(fs, [match])
    if not analysed:
        reason = failures.get(str(match.provider_match_id), "нет достаточных данных")
        return f"🧠 <b>GOOL V4 · РАЗБОР МАТЧА</b>\n\n{match.home} — {match.away}\n⚠️ Анализ недоступен: {reason}"
    row = analysed[0]
    history = fs.fetch_match_history(match.provider_match_id, match.home, match.away, limit=10) or {}
    primary = row.get("primary_trend") or {}
    trends = row.get("trends") or []
    ts = int((match.meta or {}).get("scheduled_start_ts") or 0)
    if ts:
        when = datetime.fromtimestamp(ts, ZoneInfo("Europe/Moscow")).strftime("%H:%M МСК")
    elif int(match.minute or 0):
        when = f"LIVE {int(match.minute or 0)}' · {int(match.home_score or 0)}:{int(match.away_score or 0)}"
    else:
        when = "сегодня"
    h2h = list(history.get("h2h") or [])
    h2h_text = "нет данных"
    if h2h:
        hg = sum(int(x.get("home_score") or 0) + int(x.get("away_score") or 0) for x in h2h)
        h2h_text = f"{len(h2h)} матч. · средний тотал {hg/len(h2h):.2f}"
    trend_lines = []
    for t in trends[:4]:
        trend_lines.append(f"• {t.get('name')}: {float(t.get('probability') or 0)*100:.0f}%")
    if primary:
        verdict = f"🎯 <b>Главный тренд: {primary.get('name')}</b> · {float(primary.get('probability') or 0)*100:.0f}%"
    else:
        verdict = "⏸ <b>NO BET:</b> нет достаточно однозначного главного тренда"
    return "\n".join([
        "🧠 <b>GOOL BOT 4 · V4 РАЗБОР МАТЧА</b>",
        f"⚽ <b>{match.home} — {match.away}</b>",
        f"🏆 {match.league or 'FOOTBALL'} · {when}",
        "",
        f"🏠 <b>{match.home}</b>: {_team_form(list(history.get('home_recent') or []), match.home)}",
        f"✈️ <b>{match.away}</b>: {_team_form(list(history.get('away_recent') or []), match.away)}",
        f"🤝 H2H: {h2h_text}",
        f"📚 Выборка: {int(row.get('sample') or 0)} · качество {float(row.get('quality') or 0)*100:.0f}%",
        "",
        "<b>Тренды V4:</b>",
        *(trend_lines or ["• сильных трендов не найдено"]),
        "",
        verdict,
        _manual_thought(history, match.home, match.away, primary, trends),
        "ℹ️ Ручной анализ показывает мнение V4 даже при NO BET; ставка появляется только при достаточном подтверждении.",
    ])


def match_choices(matches: list[Any]) -> dict[str, Any]:
    rows = []
    for m in matches[:6]:
        ts = int((m.meta or {}).get("scheduled_start_ts") or 0)
        when = datetime.fromtimestamp(ts, ZoneInfo("Europe/Moscow")).strftime("%H:%M") if ts else (f"{int(m.minute or 0)}'" if int(m.minute or 0) else "")
        label = f"{when} {m.home} — {m.away}".strip()[:62]
        rows.append([{"text": label, "callback_data": f"ma:{m.provider_match_id}"}])
    return {"inline_keyboard": rows}


def find_today_by_id(event_id: str) -> Any | None:
    fs = FlashscoreProvider()
    for m in fs.scheduled_matches_for_day(0) + fs.live_matches():
        if str(m.provider_match_id) == str(event_id):
            return m
    return None
