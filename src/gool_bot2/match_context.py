from __future__ import annotations

from statistics import mean, median
from typing import Any


_ATTACK_KEYS = (
    "shots",
    "shots_on_target",
    "shots_inside_box",
    "big_chances",
    "high_xg_shots",
    "touches_box",
    "dangerous_attacks",
    "corners",
)


def _stat_pair(stats: dict[str, Any], key: str) -> tuple[float, float] | None:
    value = stats.get(key)
    if not value or not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _empty_attack_snapshot(stats: dict[str, Any]) -> bool:
    """Identify placeholder/stale secondary snapshots that are all zero.

    FotMob/365Scores can map a live match before their shot map has refreshed.
    Such a row must not outvote a non-zero Flashscore cumulative snapshot.
    Require at least two observable attack metrics before calling it empty.
    """
    seen: list[tuple[float, float]] = []
    for key in _ATTACK_KEYS:
        pair = _stat_pair(stats, key)
        if pair is not None:
            seen.append(pair)
    return len(seen) >= 2 and all(abs(home) + abs(away) <= 1e-12 for home, away in seen)


def _pairs(record: dict[str, Any], key: str) -> list[tuple[float, float]]:
    providers = record.get("providers") or {}
    flash_stats = ((providers.get("flashscore") or {}).get("stats") or {}) if isinstance(providers, dict) else {}
    flash_pair = _stat_pair(flash_stats, key)
    flash_active = flash_pair is not None and abs(flash_pair[0]) + abs(flash_pair[1]) > 1e-12

    ordinary: list[tuple[float, float]] = []
    browser: list[tuple[float, float]] = []
    for provider_name, provider in providers.items():
        stats = (provider or {}).get("stats") or {}
        pair = _stat_pair(stats, key)
        if pair is None:
            continue
        # Keep all genuine disagreements. Only ignore a secondary source when
        # its entire attacking snapshot is still zero while Flashscore already
        # has cumulative activity for this metric.
        if str(provider_name) != "flashscore" and flash_active and _empty_attack_snapshot(stats):
            continue
        if str(provider_name) == "browser365":
            browser.append(pair)
        else:
            ordinary.append(pair)

    # Chromium deliberately observes the same 365Scores match through a browser.
    # Never let that duplicate a healthy 2/3-source consensus. It only fills a
    # sparse LIVE metric when fewer than two normal providers expose it.
    if len(ordinary) >= 2:
        return ordinary
    return [*ordinary, *browser]


def provider_pair(record: dict[str, Any], key: str, mode: str = "consensus") -> tuple[float | None, float | None]:
    """Read a cumulative stat from all usable providers.

    GOOL receives Flashscore, FotMob and 365Scores. The default consensus uses
    the median per side after removing clearly empty/stale secondary snapshots.
    A fresh Chromium observation is fallback-only and never double-weights the
    normal 365Scores provider when two ordinary sources already have the metric.
    """
    pairs = _pairs(record, key)
    if not pairs:
        return None, None
    homes = [value[0] for value in pairs]
    aways = [value[1] for value in pairs]
    if mode == "max":
        return max(homes), max(aways)
    if mode == "mean":
        return mean(homes), mean(aways)
    return median(homes), median(aways)


def provider_count(record: dict[str, Any], key: str | None = None) -> int:
    providers = record.get("providers") or {}
    if key is None:
        return sum(1 for p in providers.values() if isinstance(p, dict))
    return sum(1 for p in providers.values() if isinstance(p, dict) and key in ((p.get("stats") or {})))


def _safe_pair(record: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    try:
        return provider_pair(record, key)
    except Exception:
        return None, None


def xg_or_proxy_pair(record: dict[str, Any]) -> tuple[float | None, float | None, str, int]:
    """Return real xG when available, otherwise estimate an xG-like threat value.

    The fallback intentionally uses only observable attacking events and is not a
    replacement for provider xG. It lets LIVE confirmation remain useful in leagues
    where xG is not published. The weights are conservative and roughly map a mix
    of shots, SOT, box entries and big chances onto an xG-like 0..4 scale.
    """
    real = _safe_pair(record, "xg")
    if real[0] is not None and real[1] is not None:
        return float(real[0]), float(real[1]), "provider_xg", provider_count(record, "xg")

    specs = (
        ("shots", 0.025),
        ("shots_on_target", 0.070),
        ("shots_inside_box", 0.050),
        ("big_chances", 0.180),
        ("high_xg_shots", 0.100),
        ("touches_box", 0.010),
        ("dangerous_attacks", 0.0035),
        ("corners", 0.008),
    )
    home = 0.0
    away = 0.0
    evidence = 0
    for key, weight in specs:
        pair = _safe_pair(record, key)
        if pair[0] is None or pair[1] is None:
            continue
        evidence += 1
        home += max(0.0, float(pair[0])) * weight
        away += max(0.0, float(pair[1])) * weight
    if evidence < 2:
        return None, None, "unavailable", evidence
    return min(4.0, home), min(4.0, away), "attack_proxy", evidence


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
    """Map cross-provider live facts and real 5m/10m deltas onto model features."""
    mapping = {
        "shots": ("home_shots", "away_shots"),
        "shots_on_target": ("home_shots_on_target", "away_shots_on_target"),
        "xg": ("home_xg", "away_xg"),
        "xgot": ("home_xgot", "away_xgot"),
        "shots_inside_box": ("home_shots_inside_box", "away_shots_inside_box"),
        "big_chances": ("home_big_chances", "away_big_chances"),
        "high_xg_shots": ("home_high_xg_shots", "away_high_xg_shots"),
        "touches_box": ("home_touches_box", "away_touches_box"),
        "dangerous_attacks": ("home_dangerous_attacks", "away_dangerous_attacks"),
        "corners": ("home_corners", "away_corners"),
        "red_cards": ("home_red_cards", "away_red_cards"),
        "yellow_cards": ("home_yellow_cards", "away_yellow_cards"),
    }
    out: dict[str, float | None] = {}
    for provider_key, (home_key, away_key) in mapping.items():
        mode = "max" if provider_key in {"red_cards", "yellow_cards"} else "consensus"
        home, away = provider_pair(record, provider_key, mode=mode)
        out[home_key] = home
        out[away_key] = away

    momentum = record.get("live_momentum") or {}
    aliases = {
        "home_shots_last_5m": "home_shots_last_5m",
        "away_shots_last_5m": "away_shots_last_5m",
        "home_sot_last_5m": "home_sot_last_5m",
        "away_sot_last_5m": "away_sot_last_5m",
        "home_xg_last_5m": "home_xg_last_5m",
        "away_xg_last_5m": "away_xg_last_5m",
        "home_shots_last_10m": "home_shots_last_10m",
        "away_shots_last_10m": "away_shots_last_10m",
        "home_xg_last_10m": "home_xg_last_10m",
        "away_xg_last_10m": "away_xg_last_10m",
    }
    for feature, key in aliases.items():
        value = momentum.get(key)
        try:
            out[feature] = None if value is None else max(0.0, float(value))
        except (TypeError, ValueError):
            out[feature] = None
    return out
