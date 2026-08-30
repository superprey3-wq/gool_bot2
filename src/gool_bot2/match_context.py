from __future__ import annotations

from statistics import mean
from typing import Any


def _pairs(record: dict[str, Any], key: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for provider in (record.get("providers") or {}).values():
        stats = (provider or {}).get("stats") or {}
        value = stats.get(key)
        if not value or not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        try:
            out.append((float(value[0]), float(value[1])))
        except (TypeError, ValueError):
            continue
    return out


def provider_pair(record: dict[str, Any], key: str, mode: str = "mean") -> tuple[float | None, float | None]:
    """Read a provider-separated cumulative stat without treating missing as zero.

    Mean is useful for continuous/count cross-source consensus. For disciplinary
    events use ``mode='max'`` so the same card reported by multiple providers is
    not double-counted.
    """
    pairs = _pairs(record, key)
    if not pairs:
        return None, None
    homes = [value[0] for value in pairs]
    aways = [value[1] for value in pairs]
    if mode == "max":
        return max(homes), max(aways)
    return mean(homes), mean(aways)


def card_context(record: dict[str, Any]) -> dict[str, Any]:
    yellow = provider_pair(record, "yellow_cards", mode="max")
    red = provider_pair(record, "red_cards", mode="max")
    home_yellow = int(yellow[0]) if yellow[0] is not None else None
    away_yellow = int(yellow[1]) if yellow[1] is not None else None
    home_red = int(red[0]) if red[0] is not None else None
    away_red = int(red[1]) if red[1] is not None else None
    red_balance = None if home_red is None or away_red is None else home_red - away_red
    return {
        "home_yellow": home_yellow,
        "away_yellow": away_yellow,
        "home_red": home_red,
        "away_red": away_red,
        "red_balance": red_balance,
        "has_red_card": bool((home_red or 0) + (away_red or 0)),
    }


def live_rich_features(record: dict[str, Any]) -> dict[str, float | None]:
    """Map current provider stats onto archive/open-event feature names.

    These are current cumulative values only. Recent 5m/10m deltas are left
    missing until they are constructed from the append-only snapshot history.
    """
    mapping = {
        "shots": ("home_shots", "away_shots"),
        "shots_on_target": ("home_shots_on_target", "away_shots_on_target"),
        "xg": ("home_xg", "away_xg"),
        "corners": ("home_corners", "away_corners"),
        "red_cards": ("home_red_cards", "away_red_cards"),
        "yellow_cards": ("home_yellow_cards", "away_yellow_cards"),
    }
    out: dict[str, float | None] = {}
    for provider_key, (home_key, away_key) in mapping.items():
        mode = "max" if provider_key in {"red_cards", "yellow_cards"} else "mean"
        home, away = provider_pair(record, provider_key, mode=mode)
        out[home_key] = home
        out[away_key] = away
    for key in (
        "home_shots_last_5m",
        "away_shots_last_5m",
        "home_sot_last_5m",
        "away_sot_last_5m",
        "home_xg_last_5m",
        "away_xg_last_5m",
        "home_shots_last_10m",
        "away_shots_last_10m",
        "home_xg_last_10m",
        "away_xg_last_10m",
    ):
        out[key] = None
    return out
