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
_ORIG_ALREADY_RECORDED = worker._already_recorded


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


def _equivalent_side(hs: int, aws: int) -> str | None:
    if hs > 0 and aws == 0:
        return "away"
    if aws > 0 and hs == 0:
        return "home"
    return None


def _copy_correlated(primary: dict, secondary: dict, *, label: str) -> dict:
    info = dict(primary or {})
    info["correlated_confirmation"] = {
        "label": label,
        "head": secondary.get("head"),
        "level": secondary.get("level"),
        "score_pp": secondary.get("score_pp"),
        "strongest_delta_pp": secondary.get("strongest_delta_pp"),
        "strongest_one_way_moves": secondary.get("strongest_one_way_moves"),
        "confirmed": bool(secondary.get("confirmed")),
        "override": bool(secondary.get("override")),
        "value_bet": bool(secondary.get("value_bet")),
        "value_override": bool(secondary.get("value_override")),
        "value_edge_pp": secondary.get("value_edge_pp"),
        "value_level": secondary.get("value_level"),
        "value_odd": secondary.get("value_odd"),
        "targets": list(secondary.get("targets") or []),
    }
    if secondary.get("override") and not info.get("override"):
        info["override"] = True
        info["level"] = secondary.get("level") or info.get("level")
        info["reason"] = f"cross-market: {label} подтверждает сильный прогруз"
    if secondary.get("value_override") and not info.get("value_override"):
        info["value_override"] = True
        info["value_bet"] = True
        info["value_reason"] = f"cross-market: {label} даёт strong value"
    info["cross_market"] = True
    return info


def _btts(record):
    result = _ORIG_BTTS(record)
    match = record.get("match") or {}; hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    info = _eval(record, "both_teams_to_score", result.get("target_side"))
    info = _decorate_value(info, result.get("confidence_score"), "gool_btts_confidence")
    result["xbet_market"] = info

    # At 1:0 / 0:1, BTTS Yes and the scoreless team's next-goal market settle
    # on exactly the same event. Never emit a second bet: the team-goal path is
    # the canonical signal and BTTS becomes its cross-market confirmation.
    equivalent = _equivalent_side(hs, aws)
    if equivalent is not None:
        result["equivalent_team_goal"] = True
        result["equivalent_side"] = equivalent
        result["passed_without_equivalence_dedupe"] = bool(result.get("passed"))
        result["passed"] = False
        result.setdefault("blocks", []).append("equivalent_team_goal_merged")
        result["value_bet"] = bool(info.get("value_bet"))
        result["value_edge_pp"] = info.get("value_edge_pp")
        result["value_level"] = info.get("value_level")
        return result

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
    hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    selected = result.get("selected_side")
    home_analysis = result.get("home") or {}
    away_analysis = result.get("away") or {}
    home_conf = home_analysis.get("confidence_score")
    away_conf = away_analysis.get("confidence_score")

    home_info = _decorate_value(_eval(record, "team_to_score", "home"), home_conf, "gool_home_goal_confidence")
    away_info = _decorate_value(_eval(record, "team_to_score", "away"), away_conf, "gool_away_goal_confidence")

    equivalent = _equivalent_side(hs, aws)
    if equivalent is not None:
        # For 1:0/0:1 the scoreless side is the only canonical target because
        # its next goal is exactly equivalent to BTTS Yes.
        selected = equivalent
        selected_conf = home_conf if selected == "home" else away_conf
        info = home_info if selected == "home" else away_info
        btts_probability = selected_conf
        btts_info = _decorate_value(_eval(record, "both_teams_to_score", selected), btts_probability, "gool_equivalent_btts_confidence")
        info = _copy_correlated(info, btts_info, label="ОЗ — Да")
        result["correlated_signal"] = True
        result["correlated_heads"] = ["team_to_score", "both_teams_to_score"]
        result["correlated_confirmation"] = info.get("correlated_confirmation")
        if selected_conf is not None:
            result["confidence_score"] = selected_conf
            side_analysis = home_analysis if selected == "home" else away_analysis
            result["pressure_score"] = side_analysis.get("pressure_score")
    elif selected == "home":
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


def _already_recorded(rows, mid: str, head: str) -> bool:
    if _ORIG_ALREADY_RECORDED(rows, mid, head):
        return True
    if head not in {"both_teams_to_score", "team_to_score"}:
        return False
    # One open exposure for the correlated BTTS/team-goal family per match.
    return any(
        str(row.get("match_id") or "") == mid
        and str(row.get("head") or "") in {"both_teams_to_score", "team_to_score"}
        and str(row.get("result") or "pending").lower() == "pending"
        for row in rows
    )


def _card(record, analysis):
    base_png = _ORIG_CARD(record, analysis)
    info = analysis.get("xbet_market") or {}
    if isinstance(info, dict) and (info.get("override") or info.get("value_bet")):
        return base_png
    return append_xbet_market_block(base_png, info)


worker.analyze_btts_shadow = _btts
worker.analyze_team_goal_shadow = _team
worker._already_recorded = _already_recorded
worker.render_shadow_market_card = _card


def main() -> None:
    storage.main()


if __name__ == "__main__":
    main()
