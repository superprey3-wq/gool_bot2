from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .multi_bank import apply_settlement_fields


FINAL_RESULTS = {"won", "lost", "push", "void"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line(row: dict[str, Any]) -> float | None:
    direct = row.get("line")
    if direct is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            pass
    text = " ".join(str(row.get(k) or "") for k in ("market", "selection"))
    m = re.search(r"(\d+(?:[.,]\d+)?)", text)
    return float(m.group(1).replace(",", ".")) if m else None


def _side(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get(k) or "") for k in ("market", "selection")).casefold()
    if any(x in text for x in ("over", "тб", "больше")):
        return "over"
    if any(x in text for x in ("under", "тм", "меньше")):
        return "under"
    return ""


def settle_prematch_pick(row: dict[str, Any], home_score: int, away_score: int) -> str | None:
    """Settle supported V4 prematch 1X2 and full-match totals."""
    market = str(row.get("market") or "").casefold()
    selection = str(row.get("selection") or "").casefold()
    family = str(row.get("market_family") or "").casefold()

    if family in {"match_1x2", "1x2"} or market in {"1x2", "match_1x2"}:
        outcome = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        aliases = {
            "home": {"home", "1", "п1", "хозяева"},
            "draw": {"draw", "x", "х", "ничья"},
            "away": {"away", "2", "п2", "гости"},
        }
        chosen = next((key for key, names in aliases.items() if selection.strip() in names), "")
        return "won" if chosen and chosen == outcome else "lost" if chosen else None

    side = _side(row)
    line = _line(row)
    if side and line is not None:
        total = home_score + away_score
        if total == line:
            return "push"
        if side == "over":
            return "won" if total > line else "lost"
        return "won" if total < line else "lost"
    return None


def settle_prematch_row(row: dict[str, Any], record: dict[str, Any]) -> bool:
    match = record.get("match") or {}
    if not bool(match.get("is_finished")):
        return False
    if str(row.get("origin") or "").casefold() != "prematch":
        return False
    if str(row.get("result") or "pending").casefold() != "pending":
        return False
    match_id = str(match.get("flashscore_event_id") or "")
    if str(row.get("match_id") or "") != match_id:
        return False
    hs, aws = int(match.get("home_score") or 0), int(match.get("away_score") or 0)
    result = settle_prematch_pick(row, hs, aws)
    if result is None:
        return False
    row.update({
        "result": result,
        "lifecycle": "settled",
        "settled_at": _now(),
        "settled_minute": 90,
        "settled_score": [hs, aws],
        "settlement_source": "flashscore_final_score",
        "result_notification_pending": True,
        "result_notification_created_at": _now(),
    })
    apply_settlement_fields(row)
    return True


def settle_parlay(row: dict[str, Any]) -> bool:
    if str(row.get("origin") or "").casefold() not in {"prematch_parlay", "parlay"}:
        return False
    if str(row.get("result") or "pending").casefold() != "pending":
        return False
    legs = list(row.get("legs") or [])
    if not legs:
        return False
    results = [str(leg.get("result") or "pending").casefold() for leg in legs]
    # Parent result is public only after every leg is final. Even when one leg\n    # has already lost, do not publish a premature accumulator result.\n    if any(x not in FINAL_RESULTS for x in results):\n        return False\n    if any(x == "lost" for x in results):\n        result = "lost"\n    elif all(x in {"push", "void"} for x in results):
        result = "push"
    else:
        result = "won"
    effective_odds = 1.0
    for leg in legs:
        if str(leg.get("result") or "").casefold() == "won":
            effective_odds *= float(leg.get("odd") or 1.0)
    row["result"] = result
    row["settled_at"] = _now()
    row["lifecycle"] = "settled"
    row["effective_odd"] = round(effective_odds, 4)
    if result == "won":
        row["odd"] = row["effective_odd"]
    row["result_notification_pending"] = True
    row["result_notification_created_at"] = _now()
    apply_settlement_fields(row)
    return True


def sync_and_settle_parlays(rows: list[dict[str, Any]]) -> int:
    """Copy settled child-leg results into parent parlays, then settle parents."""
    children = {
        (str(r.get("event_id") or r.get("match_id") or ""), str(r.get("market") or "")): r
        for r in rows if str(r.get("origin") or "").casefold() == "prematch"
    }
    changed = 0
    for parent in rows:
        if str(parent.get("origin") or "").casefold() != "prematch_parlay":
            continue
        for leg in list(parent.get("legs") or []):
            child = children.get((str(leg.get("event_id") or ""), str(leg.get("market") or "")))
            if not child:
                continue
            for key in ("result", "settled_at", "settled_score", "settled_minute"):
                if child.get(key) is not None and leg.get(key) != child.get(key):
                    leg[key] = child.get(key)
                    changed += 1
        if settle_parlay(parent):
            changed += 1
    return changed
