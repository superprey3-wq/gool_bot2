from __future__ import annotations

import os

from . import shadow_market_worker as worker
from . import storage_shadow_worker as storage
from .market_card_overlay import append_xbet_market_block
from .market_override_policy import decorate_market_info
from .value_bet_policy import attach_value
from .xbet_market_pressure import evaluate_system, load_market_state

_ORIG_BTTS = worker.analyze_btts_shadow
_ORIG_TEAM = worker.analyze_team_goal_shadow
_ORIG_CARD = worker.render_shadow_market_card


def _required() -> bool:
    return str(os.getenv("XBET_MARKET_REQUIRED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _row(record):
    mid = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
    return ((load_market_state().get("matches") or {}).get(mid) or None) if mid else None


def _eval(record, head: str, side: str | None = None):
    match = record.get("match") or {}; hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    row = _row(record)
    return decorate_market_info(evaluate_system(row, head, hs, aws, side), row)


def _window_ok(record) -> bool:
    match = record.get("match") or {}; minute = int(match.get("minute") or 0)
    return 10 <= minute <= 75 and not bool(match.get("is_finished"))


def _market_strength(info) -> float:
    delta = max(0.0, float(info.get("strongest_delta_pp") or info.get("score_pp") or 0.0))
    moves = max(0, int(info.get("strongest_one_way_moves") or 0))
    return min(0.94, 0.70 + max(0.0, delta - 6.0) * 0.025 + max(0, moves - 2) * 0.02)


def _decorate_value(info, probability, source):
    return attach_value(info, probability, probability_source=source)


def _btts(record):
    result = _ORIG_BTTS(record)
    match = record.get("match") or {}; hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    info = _eval(record, "both_teams_to_score", result.get("target_side"))
    info = _decorate_value(info, result.get("confidence_score"), "gool_btts_confidence")
    result["xbet_market"] = info
    special_override = bool((info.get("override") or info.get("value_override")) and _window_ok(record) and not (hs > 0 and aws > 0))
    if special_override:
        result["passed_without_market"] = bool(result.get("passed"))
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["passed"] = True
        result["blocks"] = []
        if info.get("override"):
            result["market_override"] = True
            result["market_override_reason"] = info.get("reason")
        if info.get("value_override"):
            result["value_override"] = True
            result["value_override_reason"] = info.get("value_reason")
        if result.get("confidence_score") is None and info.get("override"):
            result["confidence_score"] = _market_strength(info)
            result["confidence_source"] = "xbet_market_strength_proxy"
    elif _required() and result.get("passed") and not info.get("confirmed") and not info.get("value_bet"):
        result["passed"] = False
        result.setdefault("blocks", []).append(f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}")
    result["value_bet"] = bool(info.get("value_bet"))
    result["value_edge_pp"] = info.get("value_edge_pp")
    result["value_level"] = info.get("value_level")
    return result


def _team(record):
    result = _ORIG_TEAM(record)
    match = record.get("match") or {}; home = str(match.get("home") or ""); away = str(match.get("away") or "")
    selected = result.get("selected_side")
    home_analysis = result.get("home") or {}
    away_analysis = result.get("away") or {}
    home_conf = home_analysis.get("confidence_score")
    away_conf = away_analysis.get("confidence_score")

    home_info = _decorate_value(_eval(record, "team_to_score", "home"), home_conf, "gool_home_goal_confidence")
    away_info = _decorate_value(_eval(record, "team_to_score", "away"), away_conf, "gool_away_goal_confidence")

    if selected == "home":
        info = home_info
    elif selected == "away":
        info = away_info
    else:
        candidates = [("home", home_info, home_conf), ("away", away_info, away_conf)]
        candidates.sort(
            key=lambda item: (
                int(bool(item[1].get("override"))),
                int(bool(item[1].get("value_override"))),
                float(item[1].get("value_edge_pp") or -999),
                float(item[1].get("strongest_delta_pp") or 0.0),
            ),
            reverse=True,
        )
        selected, info, selected_conf = candidates[0]
        if info.get("value_override") and selected_conf is not None:
            result["confidence_score"] = selected_conf
            side_analysis = home_analysis if selected == "home" else away_analysis
            result["pressure_score"] = side_analysis.get("pressure_score")

    result["xbet_market"] = info
    special_override = bool((info.get("override") or info.get("value_override")) and _window_ok(record))
    if special_override:
        result["passed_without_market"] = bool(result.get("passed"))
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["passed"] = True
        result["blocks"] = []
        if info.get("override"):
            result["market_override"] = True
            result["market_override_reason"] = info.get("reason")
        if info.get("value_override"):
            result["value_override"] = True
            result["value_override_reason"] = info.get("value_reason")
        result["selected_side"] = selected
        result["team"] = home if selected == "home" else away
        if result.get("confidence_score") is None and info.get("override"):
            result["confidence_score"] = _market_strength(info)
            result["confidence_source"] = "xbet_market_strength_proxy"
    elif _required() and result.get("passed") and not info.get("confirmed") and not info.get("value_bet"):
        result["passed"] = False
        result.setdefault("blocks", []).append(f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}")
    result["value_bet"] = bool(info.get("value_bet"))
    result["value_edge_pp"] = info.get("value_edge_pp")
    result["value_level"] = info.get("value_level")
    return result


def _card(record, analysis):
    base_png = _ORIG_CARD(record, analysis)
    info = analysis.get("xbet_market") or {}
    if isinstance(info, dict) and (info.get("override") or info.get("value_bet")):
        return base_png
    return append_xbet_market_block(base_png, info)


worker.analyze_btts_shadow = _btts
worker.analyze_team_goal_shadow = _team
worker.render_shadow_market_card = _card


def main() -> None:
    storage.main()


if __name__ == "__main__":
    main()
