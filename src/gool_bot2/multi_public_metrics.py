from __future__ import annotations

from typing import Any

ORDINARY_FORMULA = "65% футбол + 15% данные + 10% momentum + 10% 1xBet"
STEAM_FORMULA = "30% футбол + 15% данные + 15% momentum + 40% прогруз"

_EXPERT_BY_STRATEGY = {
    "another_goal": "another_goal",
    "two_more_goals": "two_more_goals",
    "goal_before_ht": "goal_before_ht",
    "home_goal": "home_goal",
    "away_goal": "away_goal",
    "both_teams_to_score": "btts",
    "steam_another_goal": "another_goal",
    "steam_two_more_goals": "two_more_goals",
    "steam_goal_before_ht": "goal_before_ht",
    "steam_home_goal": "home_goal",
    "steam_away_goal": "away_goal",
    "steam_btts": "btts",
}

_STRONG_STEAM_LEVELS = {"STRONG_STEAM", "MULTI_MARKET_STEAM"}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(value: Any, target: float) -> float | None:
    number = _number(value)
    if number is None or target <= 0:
        return None
    return _clamp(number / target)


def _weighted(parts: list[tuple[float | None, float]], fallback: float) -> float:
    usable = [(float(value), float(weight)) for value, weight in parts if value is not None]
    if not usable:
        return fallback
    total = sum(weight for _, weight in usable)
    return _clamp(sum(value * weight for value, weight in usable) / total)


def momentum_score(record: dict[str, Any]) -> float:
    momentum = record.get("live_momentum") or {}
    five = _weighted([
        (_ratio(momentum.get("xg_total_last_5m"), 0.13), 0.34),
        (_ratio(momentum.get("sot_total_last_5m"), 0.45), 0.24),
        (_ratio(momentum.get("shots_total_last_5m"), 1.35), 0.16),
        (_ratio(momentum.get("big_total_last_5m"), 0.18), 0.14),
        (_ratio(momentum.get("danger_total_last_5m"), 5.3), 0.12),
    ], 0.45)
    ten = _weighted([
        (_ratio(momentum.get("xg_total_last_10m"), 0.26), 0.34),
        (_ratio(momentum.get("sot_total_last_10m"), 0.90), 0.24),
        (_ratio(momentum.get("shots_total_last_10m"), 2.70), 0.16),
        (_ratio(momentum.get("big_total_last_10m"), 0.36), 0.14),
        (_ratio(momentum.get("danger_total_last_10m"), 10.6), 0.12),
    ], 0.45)
    return _clamp(0.60 * five + 0.40 * ten)


def _expert_strength(experts: dict[str, Any], strategy: str, fallback: Any) -> float:
    key = _EXPERT_BY_STRATEGY.get(str(strategy or ""))
    row = dict(experts.get(key) or {}) if key else {}
    value = _number(row.get("probability"))
    if value is None:
        value = _number(fallback)
    if value is None:
        return 0.50
    if value > 1.0:
        value /= 100.0
    return _clamp(value)


def _market_score(pressure_pp: Any) -> float:
    pressure = _number(pressure_pp) or 0.0
    return _clamp(0.50 + pressure / 12.0)


def _is_steam(winner: Any) -> bool:
    return str(getattr(winner, "source", "") or "").startswith("1xbet:autonomous_steam")


def _steam_support(winner: Any) -> bool:
    return bool(
        getattr(winner, "market_override", False)
        and str(getattr(winner, "market_level", "") or "").upper() in _STRONG_STEAM_LEVELS
        and (_number(getattr(winner, "market_pressure_pp", 0.0)) or 0.0) > 0.0
    )


def _breadth_count(winner: Any) -> int:
    for tag in list(getattr(winner, "reason_tags", []) or []):
        raw = str(tag or "")
        if not raw.startswith("market_breadth:"):
            continue
        try:
            return max(0, int(raw.split(":", 1)[1]))
        except (TypeError, ValueError):
            return 0
    return 0


def confidence_snapshot(
    record: dict[str, Any],
    winner: Any,
    experts: dict[str, Any],
    *,
    data_quality: float,
) -> dict[str, Any]:
    football = _expert_strength(
        experts,
        str(getattr(winner, "strategy", "") or ""),
        getattr(winner, "model_probability", None),
    )
    data = _clamp(data_quality)
    momentum = momentum_score(record)
    market = _market_score(getattr(winner, "market_pressure_pp", 0.0))
    steam_strength = _clamp((_number(getattr(winner, "rating", None)) or 70.0) / 100.0)
    breadth = _breadth_count(winner)

    if _is_steam(winner):
        confidence = 0.30 * football + 0.15 * data + 0.15 * momentum + 0.40 * steam_strength
        formula = STEAM_FORMULA
        layer = "STEAM"
    else:
        confidence = 0.65 * football + 0.15 * data + 0.10 * momentum + 0.10 * market
        formula = ORDINARY_FORMULA
        layer = "GOOL"

    return {
        "confidence_score": round(_clamp(confidence) * 100.0, 1),
        "event_score": round(football * 100.0, 1),
        "football_score": round(football * 100.0, 1),
        "data_score": round(data * 100.0, 1),
        "momentum_score": round(momentum * 100.0, 1),
        "market_score": round(market * 100.0, 1),
        "steam_score": round(steam_strength * 100.0, 1) if layer == "STEAM" else None,
        "steam_confirmation": _steam_support(winner),
        "market_breadth_count": breadth,
        "multi_market_confirmation": breadth > 0,
        "layer": layer,
        "formula": formula,
    }


def brief_selection_reason(decision: Any, winner: Any) -> str:
    strategy = str(getattr(winner, "strategy", "") or "")
    source = str(getattr(winner, "source", "") or "")
    pressure = _number(getattr(winner, "market_pressure_pp", 0.0)) or 0.0
    supported = _steam_support(winner)
    breadth = _breadth_count(winner)

    if source.startswith("1xbet:autonomous_steam"):
        if breadth:
            return (
                f"Сверхсильный прогруз 1xBet {pressure:+.1f} п.п.; "
                f"движение подтверждают ещё {breadth} связанн. рынк."
            )
        return (
            f"Экстремальный прогруз 1xBet {pressure:+.1f} п.п.; "
            "усиленный порог пройден даже без второго рынка."
        )

    if strategy == "another_goal":
        text = "Матч сохраняет голевое давление; общий тотал покрывает следующий гол любой команды."
    elif strategy == "goal_before_ht":
        text = "Есть свежая угроза до перерыва; выбран реальный тотал 1-го тайма 1xBet."
    elif strategy == "two_more_goals":
        text = "Сильное LIVE-давление и запас времени поддерживают сценарий ещё двух голов."
    elif strategy == "home_goal":
        text = "Хозяева сильнее по текущему голевому состоянию; их командный тотал оправдан."
    elif strategy == "away_goal":
        text = "Гости сильнее по текущему голевому состоянию; их командный тотал оправдан."
    elif strategy == "both_teams_to_score":
        text = "Команда без гола сохраняет достаточную угрозу; ОЗ лучше всего выражает этот сценарий."
    else:
        fallback = str(getattr(decision, "reason", "") or "").strip()
        text = fallback[:170] if fallback else "Выбран самый сильный проходящий LIVE-сценарий с реальным рынком 1xBet."

    if supported:
        text = f"{text} Сильный STEAM 1xBet {pressure:+.1f} п.п. подтверждает вход."
    if breadth:
        text = f"{text} Связанных рынков в ту же сторону: {breadth}."
    return text


def source_label(value: Any) -> str:
    source = str(value or "GOOL").upper()
    if "STEAM" in source:
        return "1xBet STEAM"
    if "MARKET" in source:
        return "GOOL + 1xBet"
    return "GOOL STATE"


def strategy_bucket(strategy: Any) -> str:
    raw = str(strategy or "")
    # Public event statistics intentionally expose only the two new ordinary
    # systems. Autonomous STEAM is reported by the separate STEAM layer, while
    # legacy/diagnostic strategies cannot reappear as event rows.
    if raw.startswith("steam_"):
        return "steam"
    if raw == "goal_before_ht":
        return "goal_before_ht"
    if raw == "another_goal":
        return "another_goal"
    return "other"
