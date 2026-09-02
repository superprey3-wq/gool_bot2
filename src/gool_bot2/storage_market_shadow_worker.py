from __future__ import annotations

import os

from . import shadow_market_worker as worker
from . import storage_shadow_worker as storage
from .market_card_overlay import append_xbet_market_block
from .market_override_policy import decorate_market_info
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


def _btts(record):
    result = _ORIG_BTTS(record)
    match = record.get("match") or {}; hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    info = _eval(record, "both_teams_to_score", result.get("target_side"))
    result["xbet_market"] = info
    override = bool(info.get("override") and _window_ok(record) and not (hs > 0 and aws > 0))
    if override:
        result["passed_without_market"] = bool(result.get("passed"))
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["passed"] = True
        result["blocks"] = []
        result["market_override"] = True
        result["market_override_reason"] = info.get("reason")
        result["confidence_score"] = result.get("confidence_score") or 0.0
    elif _required() and result.get("passed") and not info.get("confirmed"):
        result["passed"] = False
        result.setdefault("blocks", []).append(f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}")
    return result


def _team(record):
    result = _ORIG_TEAM(record)
    match = record.get("match") or {}; home = str(match.get("home") or ""); away = str(match.get("away") or "")
    selected = result.get("selected_side")
    if selected in {"home", "away"}:
        info = _eval(record, "team_to_score", selected)
    else:
        home_info = _eval(record, "team_to_score", "home")
        away_info = _eval(record, "team_to_score", "away")
        candidates = [("home", home_info), ("away", away_info)]
        candidates.sort(key=lambda item: (int(bool(item[1].get("override"))), float(item[1].get("strongest_delta_pp") or 0.0)), reverse=True)
        selected, info = candidates[0]
    result["xbet_market"] = info
    override = bool(info.get("override") and _window_ok(record))
    if override:
        result["passed_without_market"] = bool(result.get("passed"))
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["passed"] = True
        result["blocks"] = []
        result["market_override"] = True
        result["market_override_reason"] = info.get("reason")
        result["selected_side"] = selected
        result["team"] = home if selected == "home" else away
        result["confidence_score"] = result.get("confidence_score") or 0.0
    elif _required() and result.get("passed") and not info.get("confirmed"):
        result["passed"] = False
        result.setdefault("blocks", []).append(f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}")
    return result


def _card(record, analysis):
    return append_xbet_market_block(_ORIG_CARD(record, analysis), analysis.get("xbet_market") or {})


worker.analyze_btts_shadow = _btts
worker.analyze_team_goal_shadow = _team
worker.render_shadow_market_card = _card


def main() -> None:
    storage.main()


if __name__ == "__main__":
    main()
