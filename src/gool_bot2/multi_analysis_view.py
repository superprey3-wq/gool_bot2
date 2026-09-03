from __future__ import annotations

import html
from collections import Counter
from typing import Any

from .multi_menu import _latest_analysis
from .value_bet_policy import ABSOLUTE_MIN_BET_ODD


_EXPERT_LABELS = {
    "another_goal": "AG",
    "goal_before_ht": "1Т",
    "two_more_goals": "+2",
    "home_goal": "H",
    "away_goal": "A",
    "btts": "ОЗ",
}

_STRATEGY_EXPERT = {
    "another_goal": "another_goal",
    "goal_before_ht": "goal_before_ht",
    "two_more_goals": "two_more_goals",
    "home_goal": "home_goal",
    "away_goal": "away_goal",
    "both_teams_to_score": "btts",
}

_CATEGORY_ORDER = ("GOOL", "PRICE", "VALUE", "RATING", "FRESH", "DATA", "TIME", "EVENT", "1XBET")


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric(expert: dict[str, Any], key: str) -> str:
    raw = str(expert.get("metric") or "").lower()
    if raw in {"probability", "confidence"}:
        return raw
    return "probability" if key in {"another_goal", "goal_before_ht"} else "confidence"


def _expert_value_text(expert: dict[str, Any], key: str) -> str:
    value = _num(expert.get("probability"))
    if value is None:
        return "—"
    if _metric(expert, key) == "confidence":
        return f"C{value * 100:.0f}/100"
    return f"P{value * 100:.0f}%"


def _expert_summary(experts: dict[str, Any]) -> str:
    out: list[str] = []
    for key, label in _EXPERT_LABELS.items():
        expert = experts.get(key) or {}
        if expert.get("probability") is None:
            continue
        mark = "✓" if bool(expert.get("passed", True)) else "×"
        out.append(f"{label} {_expert_value_text(expert, key)}{mark}")
    return " · ".join(out) or "экспертных оценок нет"


def _context_line(row: dict[str, Any]) -> str:
    context = row.get("context") or {}
    prematch = context.get("prematch") or {}
    live = context.get("live") or {}
    if prematch.get("available"):
        pre = (
            f"PRE история H{int(prematch.get('home_recent') or 0)}/A{int(prematch.get('away_recent') or 0)}"
            f" · дом/выезд {int(prematch.get('home_at_home') or 0)}/{int(prematch.get('away_away') or 0)}"
            f" · H2H {int(prematch.get('h2h') or 0)}"
        )
    else:
        pre = "PRE нет данных"
    xg = _num(live.get("xg_total"))
    shots = _num(live.get("shots_total"))
    sot = _num(live.get("sot_total"))
    dq = _num(row.get("data_quality"))
    live_text = (
        f"LIVE xG {'—' if xg is None else f'{xg:.2f}'}"
        f" · уд {'—' if shots is None else f'{shots:.0f}'}"
        f" · в створ {'—' if sot is None else f'{sot:.0f}'}"
    )
    if dq is not None:
        live_text += f" · DQ {dq:.2f}"
    return f"{pre}\n{live_text}"


def _expert_block_text(block: str) -> str:
    raw = str(block or "")
    prefix = ""
    if raw.startswith("prematch:"):
        prefix, raw = "PRE: ", raw.split(":", 1)[1]
    elif raw.startswith("live:"):
        prefix, raw = "LIVE: ", raw.split(":", 1)[1]
    elif raw.startswith("home:"):
        prefix, raw = "HOME: ", raw.split(":", 1)[1]
    elif raw.startswith("away:"):
        prefix, raw = "AWAY: ", raw.split(":", 1)[1]

    exact = {
        "history": "мало prematch-истории",
        "avg_goals": "низкий prematch avg goals",
        "too_many_0_1_goal_games": "слишком много матчей на 0–1 гол",
        "prematch_score": "prematch score ниже порога",
        "no_recent_threat": "нет свежей угрозы",
        "no_quality_threat": "нет качественной угрозы",
        "recent_not_ready": "ещё не накоплены 5m LIVE-данные",
        "side_pressure_low": "давление команды ниже порога",
        "side_evidence_low": "мало LIVE-показателей команды",
        "side_no_recent_threat": "нет свежей угрозы команды",
        "side_no_quality_threat": "нет качественной угрозы команды",
        "side_prematch_scoring_profile_low": "слабый prematch-профиль гола команды",
        "warmup": "ещё warmup",
        "window_closed": "окно стратегии закрыто",
        "btts_already_won": "ОЗ уже выполнен",
    }
    text = exact.get(raw)
    if text is None:
        if raw.startswith("cum="):
            text = f"общее давление {raw[4:]}"
        elif raw.startswith("5m="):
            text = f"давление 5m {raw[3:]}"
        elif raw.startswith("10m="):
            text = f"давление 10m {raw[4:]}"
        elif raw.startswith("evidence="):
            text = f"недостаточно momentum-истории ({raw})"
        elif raw.startswith("pressure="):
            text = f"давление {raw[len('pressure='):]}"
        elif "window_closed" in raw:
            text = "окно стратегии закрыто"
        else:
            text = raw
    return prefix + text


def _ag_detail(expert: dict[str, Any]) -> str:
    diagnostics = expert.get("diagnostics") or {}
    prematch = diagnostics.get("prematch") or {}
    live = diagnostics.get("live") or {}
    parts = [f"GOOL AG: {_expert_value_text(expert, 'another_goal')} · {'PASS' if expert.get('passed') else 'WAIT'}"]

    if prematch.get("available"):
        score = _num(prematch.get("score"))
        minimum = _num(prematch.get("minimum"))
        avg = _num(prematch.get("combined_avg_total"))
        state = "✓" if prematch.get("passed") else "×"
        value = "—" if score is None else f"{score:.2f}"
        threshold = "" if minimum is None else f"/{minimum:.2f}"
        avg_text = "" if avg is None else f" · avg {avg:.2f}"
        parts.append(f"PRE {state} score {value}{threshold}{avg_text}")

    if live.get("available"):
        state = "✓" if live.get("passed") else "×"
        pressure = _num(live.get("pressure"))
        cumulative = _num(live.get("cumulative"))
        p5 = _num(live.get("pressure_5m"))
        p10 = _num(live.get("pressure_10m"))
        mc = _num(live.get("minimum_cumulative"))
        m5 = _num(live.get("minimum_5m"))
        m10 = _num(live.get("minimum_10m"))
        bits = [f"LIVE {state} pressure {'—' if pressure is None else f'{pressure:.2f}'}"]
        if cumulative is not None:
            bits.append(f"cum {cumulative:.2f}{'' if mc is None else f'/{mc:.2f}'}")
        if p5 is not None:
            bits.append(f"5m {p5:.2f}{'' if m5 is None else f'/{m5:.2f}'}")
        if p10 is not None:
            bits.append(f"10m {p10:.2f}{'' if m10 is None else f'/{m10:.2f}'}")
        parts.append(" · ".join(bits))

    reasons = [_expert_block_text(x) for x in (expert.get("blocks") or [])]
    reasons = list(dict.fromkeys(x for x in reasons if x))
    if reasons:
        parts.append("GOOL стоп: " + "; ".join(reasons[:3]))
    return "\n".join(parts)


def _generic_expert_detail(expert: dict[str, Any], key: str) -> str:
    label = _EXPERT_LABELS.get(key, key)
    state = "PASS" if expert.get("passed") else "WAIT"
    line = f"GOOL {label}: {_expert_value_text(expert, key)} · {state}"
    pressure = _num(expert.get("pressure_score"))
    minimum = _num(expert.get("minimum"))
    if pressure is not None:
        line += f" · pressure {pressure:.2f}{'' if minimum is None else f'/{minimum:.2f}'}"
    reasons = [_expert_block_text(x) for x in (expert.get("blocks") or [])]
    reasons = list(dict.fromkeys(x for x in reasons if x))
    if reasons:
        line += "\nGOOL стоп: " + "; ".join(reasons[:3])
    return line


def _expert_detail(experts: dict[str, Any], strategy: str) -> str:
    key = _STRATEGY_EXPERT.get(strategy, strategy)
    expert = experts.get(key) or {}
    if not expert:
        return "GOOL: для этого рынка нет экспертной оценки."
    if key == "another_goal":
        return _ag_detail(expert)
    return _generic_expert_detail(expert, key)


def _top_candidate(router: dict[str, Any]) -> dict[str, Any] | None:
    winner = router.get("winner")
    if isinstance(winner, dict) and winner:
        return winner
    rejected = [row for row in (router.get("rejected") or []) if isinstance(row, dict)]
    if not rejected:
        return None
    return max(rejected, key=lambda row: float(row.get("rating") or 0.0))


def _candidate_metric(candidate: dict[str, Any], experts: dict[str, Any]) -> str:
    key = _STRATEGY_EXPERT.get(str(candidate.get("strategy") or ""), "")
    expert = experts.get(key) or {}
    value = _num(candidate.get("model_probability"))
    if value is None:
        return ""
    if _metric(expert, key) == "confidence":
        return f"C {value * 100:.0f}/100"
    return f"P {value * 100:.1f}%"


def _candidate_line(candidate: dict[str, Any], experts: dict[str, Any]) -> str:
    odd = _num(candidate.get("odd")) or 0.0
    rating = _num(candidate.get("rating")) or 0.0
    market_p = _num(candidate.get("market_probability"))
    roi = _num(candidate.get("expected_roi"))
    edge = _num(candidate.get("value_edge_pp"))
    steam = _num(candidate.get("market_pressure_pp"))
    parts = [f"{candidate.get('label') or '?'} @ {odd:.2f}", f"R {rating:.0f}/100"]
    metric = _candidate_metric(candidate, experts)
    if metric:
        parts.append(metric)
    if market_p is not None:
        parts.append(f"fair {market_p * 100:.1f}%")
    if roi is not None:
        parts.append(f"EV {roi * 100:+.1f}%")
    if edge is not None:
        parts.append(f"edge {edge:+.1f}п.п.")
    if steam is not None and abs(steam) >= 0.05:
        parts.append(f"ΔP {steam:+.1f}п.п.")
    return " · ".join(parts)


def _block_category(block: str) -> str:
    raw = str(block or "")
    if raw == "price_too_low":
        return "PRICE"
    if raw == "no_positive_value":
        return "VALUE"
    if raw == "gool_wait_without_verified_override":
        return "GOOL"
    if raw == "router_rating_below_62":
        return "RATING"
    if raw.startswith("market_stale") or raw == "market_timestamp_missing":
        return "FRESH"
    if raw == "data_quality_too_low":
        return "DATA"
    if "window_closed" in raw or raw.startswith("warmup_until"):
        return "TIME"
    return "1XBET"


def _candidate_block_text(block: str, candidate: dict[str, Any]) -> str:
    raw = str(block or "")
    odd = _num(candidate.get("odd")) or 0.0
    rating = _num(candidate.get("rating")) or 0.0
    roi = _num(candidate.get("expected_roi"))
    quality = _num(candidate.get("data_quality"))
    age = _num(candidate.get("market_age_seconds"))
    if raw == "price_too_low":
        return f"PRICE: кэф {odd:.2f} < {ABSOLUTE_MIN_BET_ODD:.2f}"
    if raw == "no_positive_value":
        return f"VALUE: EV {'—' if roi is None else f'{roi * 100:+.1f}%'} < +1.0%"
    if raw == "gool_wait_without_verified_override":
        return "GOOL: эксперт WAIT, подтверждённого MARKET/VALUE override нет"
    if raw == "router_rating_below_62":
        return f"RATING: {rating:.0f}/100 < 62"
    if raw == "data_quality_too_low":
        return f"DATA: quality {'—' if quality is None else f'{quality:.2f}'} < 0.35"
    if raw == "market_timestamp_missing":
        return "FRESH: у 1xBet snapshot нет времени"
    if raw.startswith("market_stale"):
        return f"FRESH: 1xBet snapshot {'—' if age is None else f'{age:.0f}s'} слишком старый"
    if raw.startswith("warmup_until_10"):
        return "TIME: до 10-й минуты warmup"
    if raw.startswith("first_half_window_closed"):
        return "TIME: рынок 1-го тайма уже закрыт"
    if raw.startswith("two_goal_window_closed"):
        return "TIME: +2 закрыт после 65-й минуты"
    if raw.startswith("another_goal_window_closed"):
        return "TIME: ещё гол закрыт после 85-й минуты"
    if raw.startswith("entry_window_closed"):
        return "TIME: ИТБ/ОЗ закрыты после 75-й минуты"
    if raw == "correlated_better_option":
        return "ROUTER: есть более сильный коррелированный рынок"
    return raw


def _guard_text(reason: Any) -> str:
    raw = str(reason or "EVENT_REPRICE")
    if "RED_CARD" in raw:
        return "EVENT GUARD: красная карточка — 1xBet переоценивает рынок, steam игнорируется"
    if "POST_GOAL" in raw:
        return "EVENT GUARD: гол/смена score epoch — ждём перерасчёт 1xBet"
    if "ODDS_SHOCK" in raw:
        return "EVENT GUARD: резкий odds shock, похожий на пенальти/VAR — не считаем его прогрузом"
    if "SUSPENSION" in raw or "REOPEN" in raw:
        return "EVENT GUARD: рынок был приостановлен/открыт заново (VAR/пенальти), ждём стабилизацию"
    if raw == "SCORE_DESYNC":
        return "SCORE: Flashscore и 1xBet показывают разный счёт"
    if "TIMELINE_SCORE_DESYNC" in raw:
        return "SCORE: timeline Flashscore ещё не синхронизирован со счётом/1xBet"
    if raw == "XBET_SCORE_UNAVAILABLE":
        return "1xBet: не удалось подтвердить счёт — рынок временно запрещён"
    return f"EVENT GUARD: {raw}"


def _no_candidate_text(row: dict[str, Any]) -> str:
    market = row.get("market") or {}
    if not market.get("available"):
        return "1xBet: матч не сопоставлен или нет свежего market snapshot."
    if market.get("repricing_guard"):
        return _guard_text(market.get("repricing_guard_reason"))
    if market.get("score_desync"):
        return "SCORE: Flashscore и 1xBet не совпадают — ставки запрещены."
    if market.get("timeline_score_desync"):
        return "SCORE: Flashscore timeline не синхронизирован — ставки запрещены."

    experts = row.get("experts") or {}
    targets = market.get("targets") or {}
    missing: list[str] = []
    for expert_key, expert in experts.items():
        if expert.get("probability") is None:
            continue
        target = targets.get(expert_key)
        if isinstance(target, dict) and not target.get("available"):
            missing.append(str(target.get("label") or expert_key))
    if missing:
        return "1xBet: нет нужной классической .5 линии: " + ", ".join(missing[:4]) + "."
    age = _num(market.get("age_seconds"))
    if age is not None:
        return f"1xBet: рынок есть ({age:.0f}s), но ни один GOOL-эксперт не построил допустимый кандидат."
    return "1xBet: рынок есть, но допустимый Multi-кандидат не построен."


def _wait_categories(states: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in states:
        router = row.get("router") or {}
        if str(router.get("status") or "WAIT") == "BET":
            continue
        candidate = _top_candidate(router)
        if candidate:
            categories = {_block_category(block) for block in (candidate.get("blocks") or [])}
            for category in categories:
                counts[category] += 1
            continue
        market = row.get("market") or {}
        if market.get("repricing_guard"):
            counts["EVENT"] += 1
        elif market.get("score_desync") or market.get("timeline_score_desync"):
            counts["1XBET"] += 1
        else:
            counts["1XBET"] += 1
    return counts


def _wait_summary(states: list[dict[str, Any]]) -> str:
    counts = _wait_categories(states)
    labels = {
        "GOOL": "GOOL WAIT",
        "PRICE": f"PRICE<{ABSOLUTE_MIN_BET_ODD:.2f}",
        "VALUE": "VALUE/EV",
        "RATING": "RATING<62",
        "FRESH": "STALE",
        "DATA": "DATA",
        "TIME": "TIME",
        "EVENT": "EVENT GUARD",
        "1XBET": "1xBet/LINE",
    }
    parts = [f"{labels[key]} {counts[key]}" for key in _CATEGORY_ORDER if counts.get(key)]
    return "Главные стопы WAIT: " + (" · ".join(parts) if parts else "нет")


def _row_rank(row: dict[str, Any]) -> tuple[int, float]:
    router = row.get("router") or {}
    status = str(router.get("status") or "WAIT")
    candidate = _top_candidate(router) or {}
    return (1 if status == "BET" else 0, float(candidate.get("rating") or 0.0))


def analysis_text(*_: Any, **__: Any) -> str:
    states = list(_latest_analysis().values())
    if not states:
        return "🧠 <b>GOOL MULTI · АНАЛИЗ</b>\n\nСейчас нет свежих Multi-оценок в рабочем окне до 85'."

    states.sort(key=_row_rank, reverse=True)
    bets = sum(1 for row in states if str((row.get("router") or {}).get("status") or "") == "BET")
    overrides = sum(
        1
        for row in states
        if bool((((row.get("router") or {}).get("winner") or {}).get("market_override")))
        or bool((((row.get("router") or {}).get("winner") or {}).get("value_override")))
    )
    parts = [
        "🧠 <b>GOOL MULTI · АНАЛИЗ ОНЛАЙН</b>",
        "MODEL + PREMATCH + LIVE + 1xBet → один BEST BET / WAIT",
        "P = модельная вероятность · C = GOOL confidence (не калиброванная вероятность)",
        f"Матчей: <b>{len(states)}</b> · BET: <b>{bets}</b> · WAIT: <b>{len(states) - bets}</b> · override: <b>{overrides}</b>",
        _wait_summary(states),
    ]

    shown = 0
    for row in states:
        router = row.get("router") or {}
        status = str(router.get("status") or "WAIT")
        candidate = _top_candidate(router)
        score = row.get("score") or [0, 0]
        decision = "🔥 BET" if status == "BET" else "⏳ WAIT"
        experts = row.get("experts") or {}
        lines = [
            f"<b>{_h(row.get('home'))} — {_h(row.get('away'))}</b> · {int(row.get('minute') or 0)}' · {score[0]}:{score[1]} · {decision}",
            _h(_expert_summary(experts)),
            _h(_context_line(row)),
        ]

        if candidate:
            prefix = "BEST" if status == "BET" else "Ближайший рынок"
            lines.append(f"1xBet {prefix}: {_h(_candidate_line(candidate, experts))}")
            if status == "WAIT":
                blocks = [_candidate_block_text(x, candidate) for x in (candidate.get("blocks") or [])]
                blocks = list(dict.fromkeys(x for x in blocks if x))
                if blocks:
                    lines.append("❌ " + _h("; ".join(blocks[:4])))
                lines.append(_h(_expert_detail(experts, str(candidate.get("strategy") or ""))))
            else:
                lines.append("↳ " + _h(router.get("reason") or "Лучший проходящий рынок."))
        else:
            lines.append("❌ " + _h(_no_candidate_text(row)))

        block = "\n".join(lines)
        if len("\n\n".join(parts + [block])) > 3850:
            break
        parts.append(block)
        shown += 1
        if shown >= 6:
            break

    if shown < len(states):
        parts.append(f"<i>Показано {shown} из {len(states)} матчей — сначала BET и самые близкие WAIT.</i>")
    return "\n\n".join(parts)
