from __future__ import annotations

import os
from typing import Any


_STRONG_XBET = {"STRONG_STEAM", "MULTI_MARKET_STEAM"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def apply_matchbook_confirmation(decision: Any, record: dict[str, Any]) -> Any:
    """Apply public Matchbook flow to an already-created ordinary GOOL BET.

    Exchange flow never creates a BET and never changes football probability.
    Strong same-direction flow can add at most a tiny rating confirmation, and
    only when 1xBet already shows strong support. Exchange opposition makes the
    rating stricter before the normal confidence gate. Autonomous STEAM is
    applied later in the runtime and is therefore untouched by this layer.
    """
    if str(getattr(decision, "status", "")) != "BET" or getattr(decision, "winner", None) is None:
        return decision

    row = decision.winner
    strategy = str(getattr(row, "strategy", "") or "")
    if strategy not in {"goal_before_ht", "another_goal"}:
        return decision

    exchange = dict(record.get("matchbook_exchange") or {})
    context = dict(((exchange.get("systems") or {}).get(strategy)) or {})
    if not context.get("available"):
        return decision

    level = str(context.get("level") or "NEUTRAL").upper()
    volume = float(context.get("volume") or 0.0)
    fair_over = context.get("fair_over")
    flow = dict(context.get("flow") or {})
    delta_pp = float(flow.get("direction_pp") or 0.0)
    activity = float(flow.get("activity_volume") or 0.0)

    tags = list(getattr(row, "reason_tags", []) or [])
    tags = [tag for tag in tags if not str(tag).startswith("matchbook_")]
    tags.extend([
        f"matchbook_flow:{level.lower()}",
        f"matchbook_volume:{volume:.0f}",
        f"matchbook_delta_pp:{delta_pp:+.2f}",
    ])
    row.reason_tags = tags

    before = float(getattr(row, "rating", 0.0) or 0.0)
    after = before
    xbet_strong = bool(
        str(getattr(row, "market_level", "") or "").upper() in _STRONG_XBET
        and float(getattr(row, "market_pressure_pp", 0.0) or 0.0) > 0.0
    )

    if level == "STRONG_SUPPORT" and xbet_strong:
        after += max(0.0, min(1.0, _f("GOOL_MATCHBOOK_MAX_SUPPORT_RATING", 1.0)))
        row.reason_tags.append("matchbook_xbet_confluence")
    elif level == "STRONG_OPPOSITION":
        after -= max(0.0, min(3.0, _f("GOOL_MATCHBOOK_STRONG_OPPOSITION_PENALTY", 2.0)))
        row.reason_tags.append("matchbook_strong_opposition")
    elif level == "OPPOSITION":
        after -= max(0.0, min(2.0, _f("GOOL_MATCHBOOK_OPPOSITION_PENALTY", 1.0)))
        row.reason_tags.append("matchbook_opposition")

    row.rating = max(0.0, min(100.0, after))
    context["applied"] = {
        "rating_before": round(before, 3),
        "rating_after": round(float(row.rating), 3),
        "rating_delta": round(float(row.rating) - before, 3),
        "xbet_strong": xbet_strong,
        "fair_over": fair_over,
        "flow_delta_pp": delta_pp,
        "flow_activity_volume": activity,
    }
    systems = dict(exchange.get("systems") or {})
    systems[strategy] = context
    exchange["systems"] = systems
    record["matchbook_exchange"] = exchange
    return decision
