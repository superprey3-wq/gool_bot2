from __future__ import annotations

import itertools
import math
import os
import time
from typing import Any


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def eligible_prematch_legs(
    rows: list[dict[str, Any]],
    sport: str,
    *,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Return safe candidate legs for a sport-specific PREMATCH parlay.

    LIVE rows and settled rows are never eligible. Only one best signal per
    event is kept so a parlay cannot accidentally correlate multiple markets
    from the same match.
    """
    min_strength = _float_env("GOOL_MULTISPORT_PARLAY_MIN_STRENGTH", 76.0)
    min_probability = _float_env("GOOL_MULTISPORT_PARLAY_MIN_FAIR_PROBABILITY", 0.68)
    min_edge = _float_env("GOOL_MULTISPORT_PARLAY_MIN_EDGE", 0.055)
    min_odd = _float_env("GOOL_MULTISPORT_PARLAY_MIN_ODD", 1.45)
    max_odd = _float_env("GOOL_MULTISPORT_PARLAY_MAX_ODD", 1.50)
    max_push = _float_env("GOOL_MULTISPORT_PARLAY_MAX_PUSH_PROBABILITY", 0.20)
    lead = max(0.0, _float_env("GOOL_MULTISPORT_PARLAY_MIN_LEAD_SECONDS", 180.0))
    now = time.time() if now_ts is None else float(now_ts)

    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("sport") or "") != sport:
            continue
        if str(row.get("phase") or "").upper() != "PREMATCH":
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        try:
            odd = float(row.get("odd") or 0.0)
            strength = float(row.get("strength") or 0.0)
            probability = float(row.get("fair_probability") or 0.0)
            edge = float(row.get("edge") or 0.0)
            push = float(row.get("push_probability") or 0.0)
            start_ts = float(row.get("start_ts") or row.get("scheduled_start_ts") or 0.0)
        except (TypeError, ValueError):
            continue
        if start_ts > 0.0 and start_ts <= now + lead:
            continue
        if not (min_odd <= odd <= max_odd):
            continue
        if strength < min_strength or probability < min_probability or edge < min_edge:
            continue
        if push > max_push:
            continue
        event_id = str(row.get("flashscore_event_id") or row.get("event_id") or "")
        if not event_id:
            continue
        value = {
            "entry_id": str(row.get("entry_id") or ""),
            "event_id": event_id,
            "book_event_id": str(row.get("event_id") or ""),
            "flashscore_event_id": str(row.get("flashscore_event_id") or ""),
            "sport": sport,
            "home": str(row.get("home") or "?"),
            "away": str(row.get("away") or "?"),
            "league": str(row.get("league") or ""),
            "home_logo_file": str(row.get("home_logo_file") or ""),
            "away_logo_file": str(row.get("away_logo_file") or ""),
            "home_team_id": str(row.get("home_team_id") or ""),
            "away_team_id": str(row.get("away_team_id") or ""),
            "home_team_slug": str(row.get("home_team_slug") or ""),
            "away_team_slug": str(row.get("away_team_slug") or ""),
            "scope": str(row.get("scope") or "FULL_MATCH"),
            "market_family": str(row.get("market_family") or "match_total"),
            "selection": str(row.get("selection") or "?"),
            "direction": str(row.get("direction") or ""),
            "selection_side": str(row.get("selection_side") or ""),
            "moneyline_kind": str(row.get("moneyline_kind") or ""),
            "line": float(row.get("line") or 0.0),
            "odd": odd,
            "strength": strength,
            "fair_probability": probability,
            "edge": edge,
            "push_probability": push,
            "parlay_safe": bool(row.get("parlay_safe")),
            "start_ts": start_ts,
        }
        previous = best.get(event_id)
        # Safety first: prefer the highest model probability, then strength,
        # then the lower price. A high-odds single must not win the parlay slot
        # merely because it has the same R score.
        if previous is None or (probability, strength, -odd, edge) > (
            float(previous["fair_probability"]),
            float(previous["strength"]),
            -float(previous["odd"]),
            float(previous.get("edge") or 0.0),
        ):
            best[event_id] = value
    return sorted(
        best.values(),
        key=lambda x: (float(x["fair_probability"]), float(x["strength"]), -float(x["odd"])),
        reverse=True,
    )


def build_sport_parlays(
    rows: list[dict[str, Any]],
    sport: str,
    *,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    legs = eligible_prematch_legs(rows, sport, now_ts=now_ts)
    if len(legs) < 2:
        return []

    min_combined = _float_env("GOOL_MULTISPORT_PARLAY_MIN_COMBINED_ODD", 2.00)
    target_combined = _float_env("GOOL_MULTISPORT_PARLAY_TARGET_ODD", 2.45)
    max_combined = _float_env("GOOL_MULTISPORT_PARLAY_MAX_COMBINED_ODD", 3.40)
    min_combined_probability = _float_env("GOOL_MULTISPORT_PARLAY_MIN_COMBINED_PROBABILITY", 0.46)
    max_legs = max(2, min(4, _int_env("GOOL_MULTISPORT_PARLAY_MAX_LEGS", 2)))
    max_results = max(1, min(6, _int_env("GOOL_MULTISPORT_PARLAY_MAX_RESULTS", 3)))

    candidates: list[dict[str, Any]] = []
    for count in range(2, min(max_legs, len(legs)) + 1):
        for combo in itertools.combinations(legs[:16], count):
            event_ids = {str(leg["event_id"]) for leg in combo}
            if len(event_ids) != len(combo):
                continue
            combined_odd = math.prod(float(leg["odd"]) for leg in combo)
            if not (min_combined <= combined_odd <= max_combined):
                continue
            combined_probability = math.prod(float(leg["fair_probability"]) for leg in combo)
            if combined_probability < min_combined_probability:
                continue
            average_strength = sum(float(leg["strength"]) for leg in combo) / len(combo)
            # Quality first; target-odds closeness breaks near ties.
            score = (
                average_strength
                + combined_probability * 45.0
                + sum(float(leg.get("edge") or 0.0) for leg in combo) * 20.0
                - abs(math.log(max(combined_odd, 1.001) / max(target_combined, 1.001))) * 4.0
            )
            candidates.append({
                "sport": sport,
                "legs": [dict(leg) for leg in combo],
                "combined_odd": round(combined_odd, 3),
                "combined_probability": round(combined_probability, 6),
                "average_strength": round(average_strength, 1),
                "score": round(score, 3),
            })

    candidates.sort(key=lambda row: float(row["score"]), reverse=True)
    out: list[dict[str, Any]] = []
    used_signatures: set[tuple[str, ...]] = set()
    used_events: set[str] = set()
    allow_event_reuse = max(1, _int_env("GOOL_MULTISPORT_PARLAY_MAX_EVENT_REUSE", 1))
    event_use_count: dict[str, int] = {}
    for row in candidates:
        signature = tuple(sorted(str(leg["event_id"]) for leg in row["legs"]))
        if signature in used_signatures:
            continue
        event_ids = [str(leg["event_id"]) for leg in row["legs"]]
        if any(event_use_count.get(event_id, 0) >= allow_event_reuse for event_id in event_ids):
            continue
        used_signatures.add(signature)
        out.append(row)
        for event_id in event_ids:
            used_events.add(event_id)
            event_use_count[event_id] = event_use_count.get(event_id, 0) + 1
        if len(out) >= max_results:
            break
    return out


def parlay_text(parlays: list[dict[str, Any]], sport: str) -> str:
    icon = "🏒" if sport == "hockey" else "🏀"
    title = "ХОККЕЙ" if sport == "hockey" else "БАСКЕТБОЛ"
    if not parlays:
        return f"🔗 <b>{icon} {title} · ЭКСПРЕССЫ</b>\n\nСейчас нет минимум двух подтверждённых PREMATCH-ног."
    lines = [f"🔗 <b>{icon} {title} · PREMATCH ЭКСПРЕССЫ</b>", "LIVE-сигналы сюда не попадают."]
    for idx, parlay in enumerate(parlays, 1):
        lines.append(
            f"<b>EXP {idx}</b> · кэф <b>{float(parlay.get('combined_odd') or 0):.2f}</b> · "
            f"R̄ {float(parlay.get('average_strength') or 0):.0f}"
        )
        for leg in parlay.get("legs") or []:
            lines.append(
                f"• {leg.get('home','?')} — {leg.get('away','?')}\n"
                f"  {leg.get('selection','?')} @ {float(leg.get('odd') or 0):.2f} · R{float(leg.get('strength') or 0):.0f}"
            )
    return "\n".join(lines)
