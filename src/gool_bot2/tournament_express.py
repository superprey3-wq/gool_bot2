"""Daily tournament-wide PREMATCH coupons with transparent full-fixture coverage.

No invented odds, no synthetic probabilities, no silent omission of matches.
The daily calendar is the universe; only confirmed model+bookmaker selections
are eligible. A tournament ticket exists only if EVERY calendar fixture passes.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .v4_prematch_engine import PrematchPick, blend_with_market


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _tz() -> ZoneInfo:
    return ZoneInfo("Europe/Moscow")


def state_path() -> Path:
    raw = os.getenv("GOOL_TOURNAMENT_EXPRESS_STATE", "").strip()
    return Path(raw) if raw else Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "tournament_express_today.json"


def league_key(league: str) -> str:
    return hashlib.sha256(league.strip().casefold().encode("utf-8")).hexdigest()[:12]


def _priority(league: str) -> int:
    n = league.casefold()
    if any(q in n for q in ("champions league", "лига чемпионов")):
        return 0
    if any(q in n for q in ("europa league", "лига европы")):
        return 1
    if any(q in n for q in ("conference league", "лига конференций")):
        return 2
    if any(q in n for q in ("world cup", "чемпионат мира", "euro ", "евро ")):
        return 3
    return 10


def _match_item(match: Any) -> dict[str, Any] | None:
    if isinstance(match, dict):
        m = match
        event_id = str(m.get("event_id") or m.get("provider_match_id") or "").strip()
        league = str(m.get("league") or "").strip()
        ts = float(m.get("kickoff_ts") or m.get("scheduled_start_ts") or 0)
        home, away = str(m.get("home") or ""), str(m.get("away") or "")
    else:
        event_id = str(getattr(match, "provider_match_id", "") or "").strip()
        league = str(getattr(match, "league", "") or "").strip()
        ts = float((getattr(match, "meta", None) or {}).get("scheduled_start_ts") or 0)
        home, away = str(getattr(match, "home", "") or ""), str(getattr(match, "away", "") or "")
    if not event_id or not league or not ts or not home or not away:
        return None
    return {"event_id": event_id, "league": league, "home": home, "away": away, "kickoff_ts": ts}


def _leg(pick: PrematchPick) -> dict[str, Any] | None:
    try:
        raw = blend_with_market(pick, model_weight=_f("GOOL_TOURNAMENT_MODEL_WEIGHT", .55))
        price = float(raw.odds)
        probability = float(raw.model_probability)
        min_odd = _f("GOOL_TOURNAMENT_LEG_MIN_ODD", 1.35)
        max_odd = _f("GOOL_TOURNAMENT_LEG_MAX_ODD", 1.85)
        min_quality = _f("GOOL_TOURNAMENT_MIN_QUALITY", .60)
        min_probability = _f("GOOL_TOURNAMENT_MIN_PROBABILITY", .66)
        edge = probability - float(raw.market_probability)
        roi = probability * price - 1.0
        if not all(math.isfinite(x) for x in (price, probability, edge, roi, raw.data_quality)):
            return None
        if not (min_odd <= price <= max_odd
                and raw.data_quality >= min_quality
                and probability >= min_probability
                and edge >= _f("GOOL_TOURNAMENT_MIN_EDGE", .04)
                and roi >= _f("GOOL_TOURNAMENT_MIN_EXPECTED_ROI", .02)):
            return None
        return {
            "event_id": str(raw.event_id), "home": raw.home, "away": raw.away,
            "league": raw.league, "selection": raw.selection, "market": raw.market,
            "odd": round(price, 4), "model_probability": round(probability, 6),
            "break_even_probability": round(1.0 / price, 6),
            "expected_roi": round(roi, 6), "quality": round(raw.data_quality, 4),
            "edge": round(edge, 6),
        }
    except (TypeError, ValueError, AttributeError, ZeroDivisionError):
        return None


def build_catalog(fixtures: list[Any], picks: list[PrematchPick], *,
                  now_ts: float, report_date: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (menu snapshot, complete publishable tournament parlays).

    Full coverage is based on today's entire fixture universe, not the
    shortlist of successful selections. Retain previously discovered fixtures
    for today, so later scans cannot silently shrink the denominator.
    """
    universe: dict[str, dict[str, Any]] = {}
    old = read_catalog()
    if old.get("date") == report_date:
        for group in old.get("tournaments") or []:
            for item in group.get("matches") or []:
                normalized = _match_item(item)
                if normalized:
                    universe[normalized["event_id"]] = normalized
    for fixture in fixtures:
        normalized = _match_item(fixture)
        if not normalized:
            continue
        if datetime.fromtimestamp(normalized["kickoff_ts"], _tz()).date().isoformat() != report_date:
            continue
        universe[normalized["event_id"]] = normalized

    by_event: dict[str, list[dict[str, Any]]] = {}
    for pick in picks:
        leg = _leg(pick)
        if leg is not None:
            by_event.setdefault(leg["event_id"], []).append(leg)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in universe.values():
        grouped.setdefault(item["league"], []).append(item)
    result = []
    tickets = []
    for league, matches in grouped.items():
        if len(matches) < 2:
            continue
        chosen = []
        matches_out = []
        for match in sorted(matches, key=lambda m: (m["kickoff_ts"], m["event_id"])):
            alternatives = by_event.get(match["event_id"]) or []
            # Prefer high-quality, likely selections; do not chase long prices.
            selected = max(alternatives, key=lambda a: (a["model_probability"] * a["quality"], a["expected_roi"]), default=None)
            phase = "STARTED" if float(match["kickoff_ts"]) <= now_ts + 180 else ("READY" if selected else "NO_VERIFIED_MARKET")
            rendered = {**match, "status": phase, "selection": selected}
            matches_out.append(rendered)
            if phase == "READY":
                chosen.append(selected)
        complete = len(chosen) == len(matches_out)
        group = {
            "key": league_key(league), "league": league,
            "fixture_count": len(matches_out), "qualified_count": len(chosen),
            "complete": complete, "matches": matches_out,
            "status": "READY_ALL_MATCHES" if complete else "INCOMPLETE",
        }
        if chosen:
            group["combined_odd"] = round(math.prod(x["odd"] for x in chosen), 3)
            group["combined_probability"] = round(math.prod(x["model_probability"] for x in chosen), 8)
        if complete:
            # Only one actual event per leg; never substitute a selection for
            # a different game and never price an event that already started.
            pick_map = {(str(p.event_id), str(p.market), str(p.selection)): p for p in picks}
            original_legs = []
            for leg in chosen:
                original = pick_map.get((leg["event_id"], leg["market"], leg["selection"]))
                if original is None:
                    break
                original_legs.append(original)
            if len(original_legs) == len(matches_out):
                tickets.append({
                    "kind": "TOURNAMENT", "tournament": league, "day": report_date,
                    "legs": original_legs, "combined_odds": group["combined_odd"],
                    "combined_probability": group["combined_probability"],
                })
        result.append(group)
    result.sort(key=lambda g: (_priority(g["league"]), -g["fixture_count"], g["league"].casefold()))
    catalog = {"date": report_date, "generated_at": datetime.now(_tz()).isoformat(), "tournaments": result,
               "fixture_count": len(universe), "ticket_count": len(tickets)}
    return catalog, tickets


def write_catalog(catalog: dict[str, Any]) -> None:
    target = state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)


def read_catalog() -> dict[str, Any]:
    try:
        result = json.loads(state_path().read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _today_state() -> dict[str, Any]:
    state = read_catalog()
    if state.get("date") != datetime.now(_tz()).date().isoformat():
        return {}
    return state


def tournament_menu() -> tuple[str, dict[str, Any] | None]:
    state = _today_state()
    if not state:
        return ("🏆 <b>Экспрессы по турнирам · сегодня</b>\n"
                "⏳ Нет свежего PREMATCH-сканирования. Проверь /prematchstatus."), None
    tournaments = state.get("tournaments") or []
    lines = [f"🏆 <b>ТУРНИРНЫЕ ЭКСПРЕССЫ · {html.escape(state['date'])}</b>",
             f"Найдено матчей: {state.get('fixture_count', 0)} · турниров: {len(tournaments)}",
             "Каждый полный экспресс включает все матчи турнира за сегодня.",
             "Недостаточно данных хотя бы по одной игре → купон не публикуем."]
    buttons = []
    for t in tournaments[:24]:
        covered, count = int(t["qualified_count"]), int(t["fixture_count"])
        icon = "✅" if t["complete"] else "⏳"
        lines.append(f"{icon} {html.escape(t['league'])} · {covered}/{count} проверенных плеч")
        buttons.append([{"text": f"{icon} {t['league'][:38]} · {covered}/{count}",
                         "callback_data": f"tx:{t['key']}"}])
    if len(tournaments) > 24:
        lines.append(f"Ещё {len(tournaments)-24} турниров вне первого экрана.")
    return "\n".join(lines)[:3900], {"inline_keyboard": buttons} if buttons else None


def tournament_details(key: str) -> str:
    state = _today_state()
    group = next((t for t in state.get("tournaments") or [] if t.get("key") == key), None)
    if group is None:
        return "⏳ Турнир не найден в сегодняшнем сканировании. Открой «🏆 Турнирные экспрессы»."
    lines = [
        f"🏆 <b>{html.escape(group['league'])}</b> · {html.escape(state['date'])}",
        f"Охват: {group['qualified_count']}/{group['fixture_count']} матчей",
    ]
    if group["complete"]:
        lines.append(f"✅ ПОЛНЫЙ ЭКСПРЕСС · кэф {group['combined_odd']:.2f} · модельная P {group['combined_probability']*100:.2f}%")
    else:
        lines.append("⏳ НЕТ ПОЛНОГО КУПОНА — есть неподтверждённые рынки или начавшиеся матчи.")
    for i, match in enumerate(group["matches"], 1):
        leg = match.get("selection") or {}
        title = f"{i}. {html.escape(match['home'])} — {html.escape(match['away'])}"
        if match["status"] == "READY" and leg:
            lines.append(f"{title}\n   {html.escape(str(leg['selection']))} @ {leg['odd']:.2f} · P {leg['model_probability']*100:.1f}% / безуб. {leg['break_even_probability']*100:.1f}%")
        else:
            reason = "матч уже начался / близок к началу" if match["status"] == "STARTED" else "нет проверенной положительной линии"
            lines.append(f"{title}\n   ⚠️ {reason}")
    lines.append("⚠️ Совместная вероятность оценочная; при корреляции исходов перемножение может быть неверным.")
    return "\n".join(lines)[:3900]
