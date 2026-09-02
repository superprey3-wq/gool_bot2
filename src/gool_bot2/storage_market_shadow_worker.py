from __future__ import annotations

import os

from . import shadow_market_worker as worker
from . import storage_shadow_worker as storage
from .market_card_overlay import append_xbet_market_block
from .xbet_market_pressure import evaluate_system, load_market_state

_ORIG_BTTS = worker.analyze_btts_shadow
_ORIG_TEAM = worker.analyze_team_goal_shadow
_ORIG_CARD = worker.render_shadow_market_card


def _required() -> bool:
    return str(os.getenv("XBET_MARKET_REQUIRED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _row(record):
    mid = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
    return ((load_market_state().get("matches") or {}).get(mid) or None) if mid else None


def _btts(record):
    result = _ORIG_BTTS(record)
    match = record.get("match") or {}; hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    info = evaluate_system(_row(record), "both_teams_to_score", hs, aws, result.get("target_side"))
    result["xbet_market"] = info
    if _required() and result.get("passed") and not info.get("confirmed"):
        result["passed"] = False
        result.setdefault("blocks", []).append(f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}")
    return result


def _team(record):
    result = _ORIG_TEAM(record)
    match = record.get("match") or {}; hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    info = evaluate_system(_row(record), "team_to_score", hs, aws, result.get("selected_side"))
    result["xbet_market"] = info
    if _required() and result.get("passed") and not info.get("confirmed"):
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
