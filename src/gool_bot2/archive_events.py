from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Side = Literal["home", "away"]


@dataclass(frozen=True)
class CanonicalEvent:
    minute: float
    period: int
    side: Side
    event_type: str
    xg: float | None = None
    on_target: bool = False
    location_x: float | None = None
    location_y: float | None = None


def event_to_dict(event: CanonicalEvent) -> dict[str, Any]:
    return {
        "minute": float(event.minute),
        "period": int(event.period),
        "side": event.side,
        "event_type": event.event_type,
        "xg": None if event.xg is None else float(event.xg),
        "on_target": bool(event.on_target),
        "location_x": event.location_x,
        "location_y": event.location_y,
    }


def event_features(events: list[dict[str, Any]], minute: float) -> dict[str, float]:
    """Leakage-safe cumulative and recent event features at cutoff minute."""
    seen = [event for event in events if float(event.get("minute", 0.0)) <= minute]

    def count(kind: str, side: str | None = None, since: float | None = None) -> float:
        rows = seen
        if since is not None:
            rows = [row for row in rows if float(row.get("minute", 0.0)) > since]
        if side is not None:
            rows = [row for row in rows if row.get("side") == side]
        return float(sum(1 for row in rows if row.get("event_type") == kind))

    def xg(side: str, since: float | None = None) -> float:
        rows = seen
        if since is not None:
            rows = [row for row in rows if float(row.get("minute", 0.0)) > since]
        total = 0.0
        for row in rows:
            if row.get("side") != side or row.get("event_type") != "shot":
                continue
            try:
                total += float(row.get("xg") or 0.0)
            except (TypeError, ValueError):
                continue
        return float(total)

    def sot(side: str, since: float | None = None) -> float:
        rows = seen
        if since is not None:
            rows = [row for row in rows if float(row.get("minute", 0.0)) > since]
        return float(sum(1 for row in rows if row.get("side") == side and row.get("event_type") == "shot" and bool(row.get("on_target"))))

    def attacking_actions(side: str, since: float) -> float:
        kinds = {"shot", "corner", "pass", "carry", "dribble"}
        rows = [row for row in seen if row.get("side") == side and float(row.get("minute", 0.0)) > since]
        return float(sum(1 for row in rows if row.get("event_type") in kinds and float(row.get("location_x") or 0.0) >= 80.0))

    home_attack_3m = attacking_actions("home", minute - 3.0)
    away_attack_3m = attacking_actions("away", minute - 3.0)
    home_attack_5m = attacking_actions("home", minute - 5.0)
    away_attack_5m = attacking_actions("away", minute - 5.0)
    home_attack_10m = attacking_actions("home", minute - 10.0)
    away_attack_10m = attacking_actions("away", minute - 10.0)
    home_xg_3m = xg("home", minute - 3.0)
    away_xg_3m = xg("away", minute - 3.0)
    home_xg_5m = xg("home", minute - 5.0)
    away_xg_5m = xg("away", minute - 5.0)
    home_xg_10m = xg("home", minute - 10.0)
    away_xg_10m = xg("away", minute - 10.0)

    return {
        "home_shots": count("shot", "home"), "away_shots": count("shot", "away"),
        "home_shots_on_target": sot("home"), "away_shots_on_target": sot("away"),
        "home_xg": xg("home"), "away_xg": xg("away"),
        "home_corners": count("corner", "home"), "away_corners": count("corner", "away"),
        "home_yellow_cards": count("yellow_card", "home"), "away_yellow_cards": count("yellow_card", "away"),
        "home_red_cards": count("red_card", "home"), "away_red_cards": count("red_card", "away"),
        "home_shots_last_3m": count("shot", "home", minute - 3.0), "away_shots_last_3m": count("shot", "away", minute - 3.0),
        "home_xg_last_3m": home_xg_3m, "away_xg_last_3m": away_xg_3m,
        "home_shots_last_5m": count("shot", "home", minute - 5.0), "away_shots_last_5m": count("shot", "away", minute - 5.0),
        "home_sot_last_5m": sot("home", minute - 5.0), "away_sot_last_5m": sot("away", minute - 5.0),
        "home_xg_last_5m": home_xg_5m, "away_xg_last_5m": away_xg_5m,
        "home_shots_last_10m": count("shot", "home", minute - 10.0), "away_shots_last_10m": count("shot", "away", minute - 10.0),
        "home_xg_last_10m": home_xg_10m, "away_xg_last_10m": away_xg_10m,
        "home_attacking_actions_last_3m": home_attack_3m, "away_attacking_actions_last_3m": away_attack_3m,
        "home_attacking_actions_last_5m": home_attack_5m, "away_attacking_actions_last_5m": away_attack_5m,
        "home_attacking_actions_last_10m": home_attack_10m, "away_attacking_actions_last_10m": away_attack_10m,
        "attacking_pressure_diff_3m": home_attack_3m - away_attack_3m,
        "attacking_pressure_diff_5m": home_attack_5m - away_attack_5m,
        "attacking_pressure_diff_10m": home_attack_10m - away_attack_10m,
        "xg_diff_last_3m": home_xg_3m - away_xg_3m,
        "xg_diff_last_5m": home_xg_5m - away_xg_5m,
        "xg_diff_last_10m": home_xg_10m - away_xg_10m,
        "first_half_time_remaining": max(0.0, 45.0 - minute),
    }
