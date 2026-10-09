from __future__ import annotations

import html
import json
import os
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import load_signal_journal
from .multi_delivery import was_publicly_sent
from .multisport_journal import load_journal as load_multisport_journal
from .dayreport_brain_audit import basketball_detail_html, raw_evidence_html


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
    if strategy == "another_goal":
        return "Ещё гол"
    if strategy == "two_more_goals":
        return "Ещё +2 гола"
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
            parts.append(f"{index}. {teams}: {selection} @ {odd:.2f} · {_result_label(leg)}")
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
    return f"{row.get('home') or '?'} — {row.get('away') or '?'}"


def _score_text(row: dict[str, Any]) -> str:
    score = row.get("settled_score") or row.get("final_score")
    if isinstance(score, (list, tuple)) and len(score) >= 2:
        scoped = f"{score[0]}:{score[1]}"
        match = row.get("settled_match_score")
        scope = str(row.get("scope") or "")
        if scope and scope != "FULL_MATCH" and isinstance(match, (list, tuple)) and len(match) >= 2:
            return f"{scoped} ({scope}); матч {match[0]}:{match[1]}"
        return scoped
    return "—"


def _created_label(row: dict[str, Any], tz: Any) -> str:
    dt = _parse_dt(row.get("created_at") or row.get("captured_at"))
    return dt.astimezone(tz).strftime("%H:%M") if dt is not None else "—"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _diagnostic_text(row: dict[str, Any]) -> str:
    """Compact evidence preserved specifically for next-day tuning/audit."""
    bits: list[str] = []
    source = str(row.get("signal_source") or row.get("source") or "").strip()
    if source:
        bits.append(f"src={source}")

    strength = _number(
        row.get("strength")
        or row.get("gool_signal_strength")
        or row.get("confidence_score")
        or row.get("rating")
    )
    if strength is not None:
        strength = strength * 100.0 if 0.0 < strength <= 1.0 else strength
        bits.append(f"R={strength:.0f}")

    probability = _number(
        row.get("model_probability")
        or row.get("fair_probability")
        or row.get("probability")
    )
    if probability is not None:
        probability = probability * 100.0 if probability <= 1.0 else probability
        if bool(row.get("historical_probability_uncalibrated")):
            bits.append(f"P≈{probability:.1f}% (НЕ КАЛИБРОВАНА)")
        else:
            bits.append(f"P={probability:.1f}%")

    edge = _number(row.get("edge") or row.get("edge_pp") or row.get("market_edge"))
    if edge is not None:
        edge = edge * 100.0 if abs(edge) <= 1.0 else edge
        bits.append(f"edge={edge:+.1f}pp")

    pressure = _number(row.get("market_pressure_pp"))
    if pressure is not None:
        bits.append(f"1xBet={pressure:+.1f}pp")

    minute = row.get("minute")
    if minute not in {None, "", 0, "0"}:
        bits.append(f"вход={minute}'")

    scope = str(row.get("scope") or row.get("period") or "").strip()
    if scope:
        bits.append(f"scope={scope}")
    # Explicit stored Brain identity prevents retrospectively crediting all
    # correct bets to V3 merely because a historical model could select them.
    brain_mode = str(row.get("brain_mode") or row.get("signal_type") or "").strip()
    if brain_mode:
        bits.append(f"Brain={brain_mode}")
    elif _sport(row) == "basketball":
        bits.append("Brain=не записан")
    if _sport(row) == "basketball":
        tier = str(row.get("historical_tier") or "").strip()
        h, a = row.get("historical_home_hits"), row.get("historical_away_hits")
        if tier and h is not None and a is not None:
            bits.append(f"{tier} ({h}/10 + {a}/10)")
        coef = row.get("historical_coefficient_available")
        if coef is True:
            bits.append("H2H=поправка применена")
        elif coef is False:
            bits.append("H2H=нет подтверждённой поправки")
        correction = _number(row.get("historical_correction_points"))
        if correction is not None:
            bits.append(f"H2HΔ={correction:+.2f} оч")

    projected = _number(row.get("projected_total"))
    if projected is not None:
        bits.append(f"proj={projected:.2f}")
    stat_edge = _number(row.get("stat_edge"))
    if stat_edge is not None:
        bits.append(f"statΔ={stat_edge:.2f}")
    recent_rate = _number(row.get("recent_rate_per_min"))
    if recent_rate is not None:
        bits.append(f"recent={recent_rate:.2f}/min")
    expected_shots = _number(row.get("expected_shots_per_min"))
    if expected_shots is not None:
        bits.append(f"SOGexp={expected_shots:.2f}/min")
    possessions = _number(row.get("possessions_per_min"))
    if possessions is not None:
        bits.append(f"poss={possessions:.2f}/min")
    memory_quality = _number(row.get("segment_memory_quality"))
    if memory_quality is not None:
        bits.append(f"Q/Pmem={memory_quality:.2f}")
    if row.get("historical_confirmation") is True:
        bits.append("history=confirm")
    if row.get("directional_confirmation") is True:
        bits.append("live=confirm")
    if row.get("market_steam_agrees") is True:
        bits.append("1xBetMove=agree")
    elif row.get("market_steam_agrees") is False:
        bits.append("1xBetMove=oppose")
    if row.get("matchbook_money_flow_agrees") is True:
        bits.append("Matchbook=agree")
    elif row.get("matchbook_money_flow_agrees") is False:
        bits.append("Matchbook=oppose")

    reason = str(
        row.get("selection_reason")
        or row.get("brain_reason")
        or row.get("reason")
        or ""
    ).strip()
    if reason:
        bits.append(reason)

    if _is_parlay(row):
        combined_p = _number(row.get("combined_probability"))
        if combined_p is not None:
            combined_p = combined_p * 100.0 if combined_p <= 1.0 else combined_p
            bits.append(f"P экспресса={combined_p:.1f}%")
        avg_strength = _number(row.get("average_strength"))
        if avg_strength is not None:
            bits.append(f"ср.R={avg_strength:.0f}")

    return " · ".join(bits) or "—"


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
        "<th>Время</th><th>Спорт</th><th>Фаза</th><th>Тип</th><th>Матч / экспресс</th>",
        "<th>Турнир</th><th>Ставка</th><th>Кэф</th><th>Результат</th><th>Счёт</th><th>P/L</th><th>Диагностика</th><th>Сырой JSON</th>",
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
            f"<td>{html.escape(_diagnostic_text(row))}</td>"
            f"<td>{raw_evidence_html(row)}</td>"
            "</tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for raw in path.read_text("utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except Exception:
                continue
            if isinstance(value, dict):
                rows.append(dict(value))
    except Exception:
        return []
    return rows


def _multisport_history_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = os.getenv("GOOL_MULTISPORT_HISTORY", "").strip() or os.getenv("XBET_MULTISPORT_HISTORY", "").strip()
    return Path(raw) if raw else runtime / "live" / "gool_multisport_history.jsonl"


def _runtime_day_states(path: Path, day: date, tz: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _load_jsonl(path):
        dt = _parse_dt(row.get("captured_at"))
        if dt is not None and dt.astimezone(tz).date() == day:
            out.append(row)
    return out


def _bet_direction(row: dict[str, Any]) -> str:
    direct = str(row.get("direction") or "").strip().casefold()
    if direct in {"over", "under"}:
        return direct
    selection = str(row.get("selection") or row.get("market") or "").casefold()
    if "тб" in selection or "over" in selection:
        return "over"
    if "тм" in selection or "under" in selection:
        return "under"
    return ""


def _live_wait_bucket(row: dict[str, Any], sport: str) -> str:
    if bool(row.get("break_transition")):
        return "пауза/переход сегмента"
    stats = dict(row.get("live_game_stats") or {})
    if not bool(stats.get("current_segment_available")):
        return "нет статистики текущего сегмента"
    try:
        points = int(row.get("history_points") or 0)
    except (TypeError, ValueError):
        points = 0
    try:
        window = float(row.get("recent_window_seconds") or 0.0)
    except (TypeError, ValueError):
        window = 0.0
    prefix = "HOCKEY" if sport == "hockey" else "BASKETBALL"
    try:
        min_points = max(2, int(float(os.getenv(f"GOOL_{prefix}_LIVE_MIN_ANALYSIS_SNAPSHOTS", "3"))))
    except (TypeError, ValueError):
        min_points = 3
    try:
        min_window = max(30.0, float(os.getenv(f"GOOL_{prefix}_LIVE_MIN_ANALYSIS_SECONDS", "60")))
    except (TypeError, ValueError):
        min_window = 60.0
    if points < min_points or window < min_window:
        return "разогрев Brain"
    return "прочий WAIT"


def _runtime_live_audit_html(
    states: list[dict[str, Any]],
    *,
    hockey_live: list[dict[str, Any]],
    basketball_live: list[dict[str, Any]],
) -> str:
    if not states:
        return "<p class='muted'>Нет runtime-снимков GOOL MULTISPORT за выбранный день.</p>"

    cards: list[str] = []
    icons = {"hockey": "🏒", "basketball": "🏀"}
    live_rows = {"hockey": hockey_live, "basketball": basketball_live}
    for sport in ("hockey", "basketball"):
        sport_states = [dict((state.get("sports") or {}).get(sport) or {}) for state in states]
        sport_states = [row for row in sport_states if row]
        if not sport_states:
            cards.append(
                f'<div class="audit"><h3>{icons[sport]} {sport.upper()} LIVE funnel</h3>'
                "<p class='muted'>Нет данных по спорту.</p></div>"
            )
            continue

        fs_ids: set[str] = set()
        cand_ids: set[str] = set()
        wait_buckets: Counter[str] = Counter()
        for snap in sport_states:
            for match in snap.get("flashscore_live_matches") or []:
                if isinstance(match, dict):
                    key = str(match.get("flashscore_event_id") or "").strip()
                    if key:
                        fs_ids.add(key)
            for analysis in snap.get("flashscore_analysis_matches") or []:
                if not isinstance(analysis, dict):
                    continue
                key = str(analysis.get("flashscore_event_id") or "").strip()
                state = str(analysis.get("brain_state") or "WAIT").upper()
                if state in {"PASS", "BORDERLINE"}:
                    if key:
                        cand_ids.add(key)
                else:
                    wait_buckets[_live_wait_bucket(analysis, sport)] += 1

        def peak(name: str) -> int:
            values: list[int] = []
            for snap in sport_states:
                try:
                    values.append(int(snap.get(name) or 0))
                except (TypeError, ValueError):
                    values.append(0)
            return max(values or [0])

        directions = Counter(_bet_direction(row) for row in live_rows[sport])
        wait_text = " · ".join(
            f"{html.escape(label)}: <b>{count}</b>" for label, count in wait_buckets.most_common()
        ) or "WAIT-наблюдений нет"
        alert = ""
        # In Basketball Historical V3 the legacy Flashscore tempo PASS/WAIT
        # does NOT gate 1xBet. The actual live_brain_candidates runtime counter
        # is authoritative, not the legacy per-match brain_state.
        if fs_ids and not cand_ids and peak("live_brain_candidates") == 0:
            alert = "<p><b>⚠️ Не найдено кандидатов для запроса 1xBet; для Basketball V3 причина может быть в данных четверти.</b></p>"
        elif cand_ids and peak("xbet_live") == 0:
            alert = "<p><b>⚠️ Brain дал кандидатов, но 1xBet LIVE вернул 0 матчей.</b></p>"
        elif peak("mapped") == 0 and peak("xbet_live") > 0 and cand_ids:
            alert = "<p><b>⚠️ Есть Brain-кандидаты и линия 1xBet, но сопоставление матчей = 0.</b></p>"

        cards.append(
            f'<div class="audit"><h3>{icons[sport]} {sport.upper()} LIVE funnel</h3>'
            f"<div>Runtime-снимков: <b>{len(sport_states)}</b> · FS уник.: <b>{len(fs_ids)}</b> "
            f"· Brain кандидаты уник.: <b>{len(cand_ids)}</b></div>"
            f"<div>Пик за цикл: FS <b>{peak('flashscore_live')}</b> → Brain <b>{peak('live_brain_candidates')}</b> "
            f"→ 1xBet <b>{peak('xbet_live')}</b> → mapped <b>{peak('mapped')}</b> "
            f"→ decoded <b>{peak('decoded')}</b> → signals <b>{peak('detected')}</b></div>"
            f"<div>Отсев после декода: model/price <b>{peak('pricing_rejected')}</b> · "
            f"1xBet move <b>{peak('steam_blocked')}</b> · Matchbook <b>{peak('matchbook_blocked')}</b> "
            f"· duplicate <b>{peak('duplicate_filtered')}</b></div>"
            f"<div>Отправленные LIVE-направления: ТБ <b>{directions['over']}</b> · ТМ <b>{directions['under']}</b></div>"
            f"<div>Причины WAIT (наблюдения): {wait_text}</div>{alert}</div>"
        )
    return "".join(cards)


def _load_super10(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return []
    return [dict(row) for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _ticket_day(ticket: dict[str, Any], tz: Any) -> date | None:
    raw_day = str(ticket.get("day") or "").strip()
    if raw_day:
        try:
            return date.fromisoformat(raw_day)
        except ValueError:
            pass
    dt = _parse_dt(ticket.get("created_at"))
    return dt.astimezone(tz).date() if dt is not None else None


def _super10_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='muted'>SUPER 10 в этот день не отправлялся.</p>"
    out: list[str] = []
    for ticket in rows:
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
            f"<div class='ticket'><h3>🌐 SUPER 10 · @ {odd:.2f} · {html.escape(result_label)}</h3><ol>"
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
                f" · {html.escape(_result_label(leg))}"
                "</li>"
            )
        out.append("</ol></div>")
    return "".join(out)


def _fixture_key(leg: dict[str, Any]) -> str:
    for key in ("flashscore_event_id", "book_event_id", "event_id"):
        value = str(leg.get(key) or "").strip()
        if value:
            return value
    return f"{str(leg.get('home') or '').casefold()}|{str(leg.get('away') or '').casefold()}"


def _audit_html(rows: list[dict[str, Any]]) -> str:
    losses = [row for row in rows if str(row.get("result") or "").lower() == "lost"]
    loss_buckets = Counter(f"{_sport(row)} · {_phase(row)} · {_type_label(row)}" for row in losses)

    fixture_counts: Counter[str] = Counter()
    fixture_labels: dict[str, str] = {}
    for row in rows:
        if not _is_parlay(row):
            continue
        for leg in row.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            key = _fixture_key(leg)
            fixture_counts[key] += 1
            fixture_labels[key] = f"{leg.get('home') or '?'} — {leg.get('away') or '?'}"

    reused = [(fixture_labels[key], count) for key, count in fixture_counts.items() if count > 1]
    parts = ["<div class='audit'>"]
    if loss_buckets:
        parts.append("<h3>Где были минусы</h3><ul>")
        for label, count in loss_buckets.most_common():
            parts.append(f"<li>{html.escape(label)}: <b>{count}</b></li>")
        parts.append("</ul>")
    else:
        parts.append("<p>Минусов среди рассчитанных ставок за день нет.</p>")

    if reused:
        parts.append("<h3>⚠️ Повтор матча между экспрессами</h3><ul>")
        for label, count in reused:
            parts.append(f"<li>{html.escape(label)}: <b>{count} раза</b></li>")
        parts.append("</ul>")
    else:
        parts.append("<p>✅ Один матч не повторяется между дневными экспрессами.</p>")

    pending = sum(1 for row in rows if str(row.get("result") or "pending").lower() == "pending")
    parts.append(f"<p>Нерассчитанных ставок на момент выгрузки: <b>{pending}</b>.</p>")
    parts.append("</div>")
    return "".join(parts)


def build_daily_report(
    *,
    football_path: Path | None = None,
    multisport_path: Path | None = None,
    super10_history_path: Path | None = None,
    multisport_history_path: Path | None = None,
    now: datetime | None = None,
    report_date: date | None = None,
) -> tuple[str, bytes, str]:
    """Build one self-contained audit file for a single local report day."""
    from . import global_super10, multi_menu, multisport_menu

    tz = _tz()
    generated = now or datetime.now(tz)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=tz)
    else:
        generated = generated.astimezone(tz)
    day = report_date or generated.date()

    football_path = football_path or multi_menu.journal_path()
    multisport_path = multisport_path or multisport_menu.journal_path()
    super10_history_path = super10_history_path or global_super10.history_path()
    try:
        global_super10.reconcile_global_super10(deliver_result=False)
    except Exception as exc:
        print(f"GOOL_REPORT_SUPER10_RECONCILE_ERROR {type(exc).__name__}:{exc}", flush=True)
    multisport_history_path = multisport_history_path or _multisport_history_path()

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

    all_rows = [*football, *multisport]
    rows = [
        row for row in all_rows
        if (dt := _parse_dt(row.get("created_at") or row.get("captured_at"))) is not None
        and dt.astimezone(tz).date() == day
    ]
    rows.sort(
        key=lambda row: (
            _parse_dt(row.get("created_at") or row.get("captured_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ).timestamp()
    )

    football_rows = [row for row in rows if _sport(row) == "football"]
    hockey_rows = [row for row in rows if _sport(row) == "hockey"]
    basketball_rows = [row for row in rows if _sport(row) == "basketball"]
    parlay_rows = [row for row in rows if _is_parlay(row)]
    singles_rows = [row for row in rows if not _is_parlay(row)]

    football_live = [row for row in football_rows if _phase(row) == "LIVE" and not _is_parlay(row)]
    football_pre = [row for row in football_rows if _phase(row) == "PREMATCH" and not _is_parlay(row)]
    hockey_pre = [row for row in hockey_rows if _phase(row) == "PREMATCH" and not _is_parlay(row)]
    hockey_live = [row for row in hockey_rows if _phase(row) == "LIVE" and not _is_parlay(row)]
    basketball_pre = [row for row in basketball_rows if _phase(row) == "PREMATCH" and not _is_parlay(row)]
    basketball_live = [row for row in basketball_rows if _phase(row) == "LIVE" and not _is_parlay(row)]

    super10_rows = [
        ticket for ticket in _load_super10(super10_history_path)
        if _ticket_day(ticket, tz) == day
    ]
    multisport_runtime = _runtime_day_states(multisport_history_path, day, tz)

    css = """
    body{font-family:Arial,sans-serif;background:#f5f6f8;color:#16181d;margin:0;padding:24px}
    h1,h2,h3{margin:0 0 10px}.wrap{max-width:1700px;margin:auto}
    .meta,.muted{color:#6a717c}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:18px 0}
    .stat,.ticket,.audit{background:white;border:1px solid #dde1e7;border-radius:12px;padding:14px;margin:10px 0}
    table{width:100%;border-collapse:collapse;background:white;font-size:12.5px;margin:12px 0 28px}
    th,td{border:1px solid #dde1e7;padding:8px;vertical-align:top;text-align:left}
    th{background:#eef1f5;position:sticky;top:0}.section{margin-top:32px;overflow-x:auto}
    .raw-json{white-space:pre-wrap;overflow-wrap:anywhere;max-height:450px;overflow:auto;padding:10px;background:#f1f3f7}
    details>summary{cursor:pointer;font-weight:bold;padding:8px 0}
    ol,ul{margin-bottom:0}li{margin:6px 0}
    """
    html_doc = [
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<style>{css}</style><title>GOOL Day Report</title></head><body><div class='wrap'>",
        f"<h1>📄 GOOL BOT · ПОЛНЫЙ ОТЧЁТ ЗА {day.strftime('%d.%m.%Y')}</h1>",
        (
            f"<p class='meta'>Сформирован: {generated.strftime('%d.%m.%Y %H:%M')} · "
            "только реально отправленные ставки. Этот файл предназначен для дневного аудита и настройки Brain.</p>"
        ),
        "<h2>Итоги дня</h2><div class='grid'>",
        _stat_html("Все ставки", rows),
        _stat_html("⚽ Футбол", football_rows),
        _stat_html("🏒 Хоккей", hockey_rows),
        _stat_html("🏀 Баскетбол", basketball_rows),
        _stat_html("Ординары / LIVE", singles_rows),
        _stat_html("Экспрессы", parlay_rows),
        "</div>",
        "<h2>Разбивка для анализа</h2><div class='grid'>",
        _stat_html("⚽ Футбол PREMATCH", football_pre),
        _stat_html("⚽ Футбол LIVE", football_live),
        _stat_html("🏒 Хоккей PREMATCH", hockey_pre),
        _stat_html("🏒 Хоккей LIVE", hockey_live),
        _stat_html("🏀 Баскетбол PREMATCH", basketball_pre),
        _stat_html("🏀 Баскетбол LIVE", basketball_live),
        "</div>",
        "<div class='section'><h2>LIVE funnel · хоккей / баскетбол</h2>",
        _runtime_live_audit_html(
            multisport_runtime,
            hockey_live=hockey_live,
            basketball_live=basketball_live,
        ),
        "</div>",
        "<div class='section'><h2>Контроль и проблемные места</h2>",
        _audit_html(rows),
        "</div>",
        "<div class='section'><h2>🏀 Дневной аудит Brain · все факты по каждой баскетбольной ставке</h2>",
        basketball_detail_html([*basketball_pre, *basketball_live]),
        "</div>",
        "<div class='section'><h2>Все ординары и LIVE за день</h2>",
        _table(singles_rows, tz),
        "</div>",
        "<div class='section'><h2>Все экспрессы за день</h2>",
        _table(parlay_rows, tz),
        "</div>",
        "<div class='section'><h2>SUPER 10 за день</h2>",
        _super10_html(super10_rows),
        "</div>",
        "</div></body></html>",
    ]

    payload = "".join(html_doc).encode("utf-8")
    filename = f"GOOL_DAY_REPORT_{day.isoformat()}.html"
    summary = _summary(rows)
    caption = (
        f"📄 <b>GOOL · ПОЛНЫЙ ОТЧЁТ ЗА {day.strftime('%d.%m.%Y')}</b>\n"
        f"Ставок: <b>{summary['total']}</b> · ✅ {summary['won']} · ❌ {summary['lost']} · "
        f"⏳ {summary['pending']} · P/L <b>{summary['profit']:+.2f}u</b>"
    )
    return filename, payload, caption


# Backward-compatible name used by both Telegram responders.
def build_full_report(**kwargs: Any) -> tuple[str, bytes, str]:
    return build_daily_report(**kwargs)


__all__ = ["build_daily_report", "build_full_report"]
