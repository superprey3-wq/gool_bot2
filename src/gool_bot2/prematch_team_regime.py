from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from .v4_prematch_engine import PrematchPick


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(mapping: dict[str, Any], key: str) -> float | None:
    return _num((mapping or {}).get(key))


def _line(selection: str) -> float | None:
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)", str(selection or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _is_under(selection: str) -> bool:
    s = str(selection or "").casefold()
    return "under" in s or "итм" in s or "тм" in s


def _is_over(selection: str) -> bool:
    s = str(selection or "").casefold()
    return "over" in s or "итб" in s or "тб" in s


def _threshold_for_line(line: float) -> int:
    # For under/over 3.5 the damaging team event is 4+ goals, for 2.5 it is 3+.
    return max(1, int(line) + 1)


def _mean(values: list[float | None]) -> float | None:
    xs = [float(x) for x in values if x is not None]
    return None if not xs else sum(xs) / len(xs)


def _match_total_regime(full: dict[str, Any], line: float, under: bool) -> tuple[float, list[str]]:
    home = dict(full.get("home") or {})
    away = dict(full.get("away") or {})
    key = f"{line:.1f}"
    full_rate = _mean([_rate(home.get("over") or {}, key), _rate(away.get("over") or {}, key)])
    short_rate = _mean([_rate(home.get("recent5_over") or {}, key), _rate(away.get("recent5_over") or {}, key)])
    adjustment = 0.0
    tags: list[str] = []

    # Reversion: a very one-sided last-five streak is only meaningful when it
    # conflicts with the broader ten-match baseline. We move gently toward the
    # baseline rather than assuming that "the opposite must happen next".
    if full_rate is not None and short_rate is not None:
        if short_rate <= .20 and full_rate >= .48:
            adjustment += .025 if not under else -.025
            tags.append("recent_under_streak_vs_normal_baseline")
        elif short_rate >= .80 and full_rate <= .52:
            adjustment += -.025 if not under else .025
            tags.append("recent_over_streak_vs_normal_baseline")

    # Explosion risk protects UNDERS from teams that repeatedly score/concede
    # enough goals to break the selected line on their own.
    threshold = min(4, _threshold_for_line(line))
    hk = str(threshold)
    explosion = max(
        x for x in [
            _rate(home.get("scored_ge") or {}, hk),
            _rate(home.get("conceded_ge") or {}, hk),
            _rate(away.get("scored_ge") or {}, hk),
            _rate(away.get("conceded_ge") or {}, hk),
        ] if x is not None
    ) if any(
        x is not None for x in [
            _rate(home.get("scored_ge") or {}, hk),
            _rate(home.get("conceded_ge") or {}, hk),
            _rate(away.get("scored_ge") or {}, hk),
            _rate(away.get("conceded_ge") or {}, hk),
        ]
    ) else None
    if explosion is not None:
        if under and explosion >= .30:
            penalty = min(.045, .015 + max(0.0, explosion - .30) * .10)
            adjustment -= penalty
            tags.append(f"explosion_risk_{threshold}plus")
        elif not under and explosion >= .35:
            adjustment += min(.025, .010 + max(0.0, explosion - .35) * .06)
            tags.append(f"explosion_support_{threshold}plus")

    return adjustment, tags


def _team_total_regime(full: dict[str, Any], line: float, home_side: bool, under: bool) -> tuple[float, list[str]]:
    attack = dict(full.get("home") or {}) if home_side else dict(full.get("away") or {})
    defence = dict(full.get("away") or {}) if home_side else dict(full.get("home") or {})
    threshold = min(4, _threshold_for_line(line))
    key = str(threshold)
    long_attack = _rate(attack.get("scored_ge") or {}, key)
    short_attack = _rate(attack.get("recent5_scored_ge") or {}, key)
    long_concede = _rate(defence.get("conceded_ge") or {}, key)
    short_concede = _rate(defence.get("recent5_conceded_ge") or {}, key)
    risk = _mean([long_attack, long_concede])
    recent_risk = _mean([short_attack, short_concede])
    adjustment = 0.0
    tags: list[str] = []

    if risk is not None:
        if under and risk >= .28:
            adjustment -= min(.050, .018 + max(0.0, risk - .28) * .12)
            tags.append(f"team_explosion_risk_{threshold}plus")
        elif not under and risk >= .32:
            adjustment += min(.030, .012 + max(0.0, risk - .32) * .08)
            tags.append(f"team_explosion_support_{threshold}plus")

    if risk is not None and recent_risk is not None:
        if recent_risk <= .10 and risk >= .30:
            adjustment += .020 if not under else -.020
            tags.append("team_recent_cold_vs_baseline")
        elif recent_risk >= .70 and risk <= .40:
            adjustment += -.020 if not under else .020
            tags.append("team_recent_hot_vs_baseline")

    return adjustment, tags


def apply_team_regime(pick: PrematchPick, profile: dict[str, Any]) -> tuple[PrematchPick, dict[str, Any]]:
    """Apply conservative team-form/regime correction to total markets.

    This layer never creates a bet by itself. It only adjusts an existing model
    probability by at most five percentage points using each team's own recent
    scoring distribution and a mild mean-reversion check.
    """
    full = dict(profile.get("full_match") or {})
    line = _line(pick.selection)
    if line is None or not full.get("available"):
        return pick, {"adjustment_pp": 0.0, "tags": []}

    market = str(pick.market or "").casefold()
    under = _is_under(pick.selection)
    over = _is_over(pick.selection)
    if not (under or over):
        return pick, {"adjustment_pp": 0.0, "tags": []}

    if market == "match_total":
        adjustment, tags = _match_total_regime(full, line, under)
    elif market == "home_total":
        adjustment, tags = _team_total_regime(full, line, True, under)
    elif market == "away_total":
        adjustment, tags = _team_total_regime(full, line, False, under)
    else:
        return pick, {"adjustment_pp": 0.0, "tags": []}

    adjustment = max(-.05, min(.05, adjustment))
    probability = max(.03, min(.97, float(pick.model_probability) + adjustment))
    adjusted = replace(pick, model_probability=probability)
    return adjusted, {
        "adjustment_pp": round(adjustment * 100.0, 2),
        "tags": tags,
        "probability_before": round(float(pick.model_probability), 6),
        "probability_after": round(probability, 6),
    }


__all__ = ["apply_team_regime"]
