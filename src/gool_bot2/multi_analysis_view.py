from __future__ import annotations

import html
from typing import Any

from .multi_menu import _latest_analysis


_STRATEGY_EXPERT = {
    "another_goal": "another_goal",
    "goal_before_ht": "goal_before_ht",
    "two_more_goals": "two_more_goals",
    "home_goal": "home_goal",
    "away_goal": "away_goal",
    "both_teams_to_score": "btts",
}


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


def _top_candidate(router: dict[str, Any]) -> dict[str, Any] | None:
    winner = router.get("winner")
    if isinstance(winner, dict) and winner:
        return winner
    rejected = [row for row in (router.get("rejected") or []) if isinstance(row, dict)]
    if not rejected:
        return None
    return max(rejected, key=lambda row: float(row.get("rating") or 0.0))


def _general_goal_candidate(router: dict[str, Any]) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    winner = router.get("winner")
    if isinstance(winner, dict) and winner:
        rows.append(winner)
    rows.extend(row for row in (router.get("alternatives") or []) if isinstance(row, dict))
    rows.extend(row for row in (router.get("rejected") or []) if isinstance(row, dict))
    goal_rows = [
        row for row in rows
        if str(row.get("strategy") or "") == "another_goal"
        and str(row.get("family") or "") == "match_total"
    ]
    if not goal_rows:
        return None
    return max(goal_rows, key=lambda row: float(row.get("rating") or 0.0))


def _general_goal_market(row: dict[str, Any]) -> tuple[str, float | None] | None:
    market = row.get("market") or {}
    target = ((market.get("targets") or {}).get("another_goal") or {})
    label = str(target.get("label") or "").strip()
    odd = _num(target.get("odd"))
    if label and target.get("available") and odd is not None and odd > 1.0:
        return label, odd

    candidate = _general_goal_candidate(row.get("router") or {})
    if candidate:
        label = str(candidate.get("label") or "").strip()
        odd = _num(candidate.get("odd"))
        if label and odd is not None and odd > 1.0:
            return label, odd
    return None


def _live_line(row: dict[str, Any]) -> str:
    live = ((row.get("context") or {}).get("live") or {})
    xg = _num(live.get("xg_total"))
    shots = _num(live.get("shots_total"))
    sot = _num(live.get("sot_total"))
    big = _num(live.get("big_chances_total"))

    parts: list[str] = []
    if xg is not None:
        parts.append(f"xG {xg:.2f}")
    if shots is not None:
        parts.append(f"удары {shots:.0f}")
    if sot is not None:
        parts.append(f"в створ {sot:.0f}")
    if big is not None:
        parts.append(f"моменты {big:.0f}")
    return "📊 Игра: " + (" · ".join(parts) if parts else "мало LIVE-данных")


def _goal_model_line(experts: dict[str, Any]) -> str | None:
    expert = experts.get("another_goal") or {}
    value = _num(expert.get("probability"))
    if value is None:
        return None
    if _metric(expert, "another_goal") != "probability":
        return None
    state = "подтверждает" if bool(expert.get("passed", True)) else "пока не подтверждает"
    return f"🧠 Ещё гол: {value * 100:.0f}% · GOOL {state}"


def _plain_block(block: Any) -> str | None:
    raw = str(block or "").strip()
    if not raw:
        return None
    if ":" in raw and raw.split(":", 1)[0] in {"prematch", "live", "home", "away"}:
        raw = raw.split(":", 1)[1]

    exact = {
        "history": "мало истории до матча",
        "avg_goals": "команды обычно играют низово",
        "too_many_0_1_goal_games": "слишком много низовых матчей",
        "prematch_score": "слабый профиль на гол до матча",
        "no_recent_threat": "давно нет опасных атак",
        "no_quality_threat": "мало острых моментов",
        "recent_not_ready": "ещё мало свежих LIVE-данных",
        "side_pressure_low": "мало давления команды",
        "side_evidence_low": "мало атак и моментов команды",
        "side_no_recent_threat": "давно нет опасных атак команды",
        "side_no_quality_threat": "мало острых моментов команды",
        "side_prematch_scoring_profile_low": "команда редко забивает по prematch",
        "warmup": "слишком рано для ставки",
        "window_closed": "окно этой ставки уже закрыто",
        "btts_already_won": "обе команды уже забили",
    }
    if raw in exact:
        return exact[raw]
    if raw.startswith(("cum=", "5m=", "10m=", "pressure=")):
        return "мало давления"
    if raw.startswith("evidence="):
        return "мало свежих данных по атакам"
    if "window_closed" in raw:
        return "окно этой ставки уже закрыто"
    return None


def _expert_plain_reasons(expert: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for block in (expert.get("blocks") or []):
        text = _plain_block(block)
        if text and text not in reasons:
            reasons.append(text)
    return reasons


def _candidate_plain_reasons(candidate: dict[str, Any] | None) -> list[str]:
    if not candidate:
        return []
    reasons: list[str] = []
    for raw_value in (candidate.get("blocks") or []):
        raw = str(raw_value or "")
        text: str | None = None
        if raw == "price_too_low":
            text = "слишком низкий кэф"
        elif raw == "no_positive_value":
            text = "кэф не даёт нормального запаса"
        elif raw == "router_rating_below_62":
            text = "сигнал пока слабый"
        elif raw == "data_quality_too_low":
            text = "мало надёжных LIVE-данных"
        elif raw == "market_timestamp_missing" or raw.startswith("market_stale"):
            text = "кэф 1xBet уже не свежий"
        elif raw.startswith("warmup_until"):
            text = "слишком рано для ставки"
        elif "window_closed" in raw:
            text = "окно этой ставки уже закрыто"
        elif raw == "gool_wait_without_verified_override":
            continue
        if text and text not in reasons:
            reasons.append(text)
    return reasons


def _market_plain_reason(row: dict[str, Any]) -> str | None:
    market = row.get("market") or {}
    if not market.get("available"):
        return "1xBet пока не дал свежий рынок"

    if market.get("score_desync"):
        return "счёт Flashscore и 1xBet расходится"
    if market.get("timeline_score_desync"):
        return "счёт ещё синхронизируется"

    if market.get("repricing_guard"):
        raw = str(market.get("repricing_guard_reason") or "")
        if raw == "XBET_SCORE_UNAVAILABLE":
            return "1xBet не подтвердил текущий счёт"
        if raw == "SCORE_DESYNC":
            return "счёт Flashscore и 1xBet расходится"
        if "TIMELINE_SCORE_DESYNC" in raw:
            return "счёт ещё синхронизируется"
        if "POST_GOAL" in raw:
            return "только что был гол — ждём новые кэфы"
        if "RED_CARD" in raw:
            return "было удаление — ждём новые кэфы"
        if "ODDS_SHOCK" in raw:
            return "кэфы резко дёрнулись — ждём стабилизацию"
        if "SUSPENSION" in raw or "REOPEN" in raw:
            return "рынок только что закрывался — ждём стабилизацию"
        return "1xBet сейчас переоценивает рынок"
    return None


def _wait_reason(row: dict[str, Any], candidate: dict[str, Any] | None) -> str:
    market_reason = _market_plain_reason(row)
    if market_reason:
        return market_reason

    experts = row.get("experts") or {}
    reasons = _expert_plain_reasons(experts.get("another_goal") or {})
    if not reasons and candidate:
        key = _STRATEGY_EXPERT.get(str(candidate.get("strategy") or ""), "")
        reasons = _expert_plain_reasons(experts.get(key) or {})
    reasons.extend(text for text in _candidate_plain_reasons(candidate) if text not in reasons)

    if reasons:
        return ", ".join(reasons[:3])
    return "GOOL пока не видит достаточно сильной игры для ставки"


def _bet_reason(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "GOOL подтвердил ставку"
    strategy = str(candidate.get("strategy") or "")
    family = str(candidate.get("family") or "")
    if family == "team_total":
        return "эта команда выглядит опаснее, а её кэф лучше общего тотала"
    if strategy == "another_goal":
        return "хватает давления и моментов ещё на один гол"
    if strategy == "two_more_goals":
        return "темп высокий и времени хватает ещё на два гола"
    if strategy == "goal_before_ht":
        return "есть давление на гол до перерыва"
    if strategy == "both_teams_to_score":
        return "есть хорошие шансы, что не забившая команда ответит"
    return "GOOL подтвердил рынок по игре и кэфу"


def _row_rank(row: dict[str, Any]) -> tuple[int, float]:
    router = row.get("router") or {}
    status = str(router.get("status") or "WAIT")
    candidate = _top_candidate(router) or {}
    return (1 if status == "BET" else 0, float(candidate.get("rating") or 0.0))


def analysis_text(*_: Any, **__: Any) -> str:
    states = list(_latest_analysis().values())
    if not states:
        return "🧠 <b>GOOL MULTI · КРАТКИЙ ОТЧЁТ</b>\n\nСейчас нет свежих матчей для анализа."

    states.sort(key=_row_rank, reverse=True)
    bets = sum(1 for row in states if str((row.get("router") or {}).get("status") or "") == "BET")
    waits = len(states) - bets
    parts = [
        "🧠 <b>GOOL MULTI · КРАТКИЙ ОТЧЁТ</b>",
        f"Матчей: <b>{len(states)}</b> · ставок: <b>{bets}</b> · ждём: <b>{waits}</b>",
    ]

    shown = 0
    for row in states:
        router = row.get("router") or {}
        status = str(router.get("status") or "WAIT")
        candidate = _top_candidate(router)
        score = row.get("score") or [0, 0]
        decision = "🔥 СТАВКА" if status == "BET" else "⏳ ЖДЁМ"
        lines = [
            f"<b>{_h(row.get('home'))} — {_h(row.get('away'))}</b> · {int(row.get('minute') or 0)}' · {score[0]}:{score[1]} · {decision}",
        ]

        if status == "BET" and candidate:
            odd = _num(candidate.get("odd"))
            odd_text = "—" if odd is None else f"{odd:.2f}"
            lines.append(f"🎯 BEST: {_h(candidate.get('label') or '?')} @ {odd_text}")
        else:
            goal_market = _general_goal_market(row)
            if goal_market:
                label, odd = goal_market
                odd_text = "—" if odd is None else f"{odd:.2f}"
                lines.append(f"🎯 Ещё 1 гол: {_h(label)} @ {odd_text}")

        model_line = _goal_model_line(row.get("experts") or {})
        if model_line:
            lines.append(_h(model_line))
        lines.append(_h(_live_line(row)))

        if status == "WAIT":
            lines.append("⛔ " + _h("Почему ждём: " + _wait_reason(row, candidate)))
        else:
            lines.append("✅ " + _h("Почему ставка: " + _bet_reason(candidate)))

        block = "\n".join(lines)
        if len("\n\n".join(parts + [block])) > 3850:
            break
        parts.append(block)
        shown += 1
        if shown >= 6:
            break

    if shown < len(states):
        parts.append(f"<i>Показано {shown} из {len(states)} матчей.</i>")
    return "\n\n".join(parts)
