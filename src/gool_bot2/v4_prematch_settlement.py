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
    text = re.sub(r"(?i)\b[12]H[_ ]|[12]-й\s*тайм\s*:", "", text)
    nums = re.findall(r"(\d+(?:[.,]\d+)?)", text)
    return float(nums[-1].replace(",", ".")) if nums else None


def _side(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get(k) or "") for k in ("market", "selection")).casefold()
    if any(x in text for x in ("over", "тб", "больше")):
        return "over"
    if any(x in text for x in ("under", "тм", "меньше")):
        return "under"
    return ""


def _signed_line(value: str) -> float | None:
    found = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(value or ""))
    if not found:
        return None
    try:
        return float(found[-1].replace(",", "."))
    except ValueError:
        return None


def settle_prematch_pick(row: dict[str, Any], home_score: int, away_score: int, *, half_time_score: tuple[int, int] | None = None) -> str | None:
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

    if family == "double_chance" or market == "double_chance":
        chosen = selection.replace("_", " ").replace("-", " ").strip()
        home_win = home_score > away_score
        away_win = away_score > home_score
        draw = home_score == away_score
        if chosen in {"home or draw", "1x", "1х"}:
            return "won" if home_win or draw else "lost"
        if chosen in {"draw or away", "x2", "х2"}:
            return "won" if draw or away_win else "lost"
        if chosen in {"home or away", "12"}:
            return "won" if home_win or away_win else "lost"
        return None

    if family == "draw_no_bet" or market == "draw_no_bet":
        chosen = selection.strip()
        if home_score == away_score:
            return "push"
        if chosen in {"home", "1", "п1", "хозяева"}:
            return "won" if home_score > away_score else "lost"
        if chosen in {"away", "2", "п2", "гости"}:
            return "won" if away_score > home_score else "lost"
        return None

    if family == "asian_handicap" or market == "asian_handicap":
        line = _signed_line(selection)
        if line is None:
            return None
        if selection.strip().startswith("home"):
            adjusted = home_score + line - away_score
        elif selection.strip().startswith("away"):
            adjusted = away_score + line - home_score
        else:
            return None
        if abs(adjusted) < 1e-9:
            return "push"
        return "won" if adjusted > 0 else "lost"

    if family == "european_handicap" or market == "european_handicap":
        line = _signed_line(selection)
        if line is None:
            return None
        chosen = selection.strip()
        if chosen.startswith("home"):
            return "won" if home_score + line > away_score else "lost"
        if chosen.startswith("away"):
            return "won" if away_score + line > home_score else "lost"
        if chosen.startswith("draw"):
            # Full-market labels encode this as: draw (home -N).
            adjusted = home_score + line - away_score
            return "won" if abs(adjusted) < 1e-9 else "lost"
        return None

    if family == "btts" or "btts" in market or "обе забьют" in market:
        text = f"{market} {selection}"
        yes = "yes" in text or "да" in text
        no = "no" in text or "нет" in text
        if yes == no:
            return None
        return "won" if (home_score > 0 and away_score > 0) == yes else "lost"

    if family in {"home_total", "away_total"} or market in {"home_total", "away_total"}:
        side = _side(row)
        line = _line(row)
        if not side or line is None:
            return None
        goals = home_score if (family == "home_total" or market == "home_total") else away_score
        if goals == line:
            return "push"
        if side == "over":
            return "won" if goals > line else "lost"
        return "won" if goals < line else "lost"

    first_half = family == "first_half_total" or market.startswith("1h_") or "1-й тайм" in market
    second_half = family == "second_half_total" or market.startswith("2h_") or "2-й тайм" in market
    if first_half or second_half:
        if half_time_score is None:
            return None
        if first_half:
            home_score, away_score = half_time_score
        else:
            home_score -= half_time_score[0]
            away_score -= half_time_score[1]
            if min(home_score, away_score) < 0:
                return None
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
    kickoff_ts = float(row.get("kickoff_ts") or 0.0)
    # Provider IDs/states can be stale. A scheduled PREMATCH can never settle before kickoff.
    if kickoff_ts and datetime.now(timezone.utc).timestamp() < kickoff_ts - 120:
        return False
    first_half = str(row.get("market_family") or "") == "first_half_total" or str(row.get("market") or "").upper().startswith("1H_")
    if not bool(match.get("is_finished")) and not (first_half and bool(match.get("is_halftime"))):
        return False
    if str(row.get("origin") or "").casefold() != "prematch":
        return False
    if str(row.get("result") or "pending").casefold() != "pending":
        return False
    match_id = str(match.get("flashscore_event_id") or "")
    if str(row.get("match_id") or "") != match_id:
        return False
    from .multi_journal import _authoritative_first_half_score
    if match.get("home_score") is None or match.get("away_score") is None:
        return False
    hs, aws = int(match["home_score"]), int(match["away_score"])
    if min(hs, aws) < 0:
        return False
    ht, _ = _authoritative_first_half_score(record)
    if ht is None:
        ht = row.get("confirmed_half_time_score")
    result = settle_prematch_pick(row, hs, aws, half_time_score=tuple(ht) if ht is not None else None)
    if result is None:
        return False
    row.update({
        "result": result,
        "lifecycle": "settled",
        "settled_at": _now(),
        "settled_minute": 45 if first_half else 90,
        "settled_score": list(ht) if first_half and ht is not None else [hs, aws],
        "settlement_source": "flashscore_confirmed_half_time_score" if first_half else "flashscore_final_score",
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
    # Never publish the accumulator result until every displayed leg is final.
    if any(x not in FINAL_RESULTS for x in results):
        return False
    if any(x == "lost" for x in results):
        result = "lost"
    elif all(x in {"push", "void"} for x in results):
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

def _pick_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("event_id") or row.get("match_id") or ""),
        str(row.get("market") or ""),
        str(row.get("selection") or ""),
    )


def sync_and_settle_parlays(rows: list[dict[str, Any]]) -> int:
    """Copy settled child-leg results into parent parlays, then settle parents."""
    children = {
        _pick_identity(r): r
        for r in rows if str(r.get("origin") or "").casefold() == "prematch"
    }
    changed = 0
    for parent in rows:
        if str(parent.get("origin") or "").casefold() != "prematch_parlay":
            continue
        for leg in list(parent.get("legs") or []):
            child = children.get(_pick_identity(leg))
            if not child:
                continue
            for key in ("result", "settled_at", "settled_score", "settled_minute"):
                if child.get(key) is not None and leg.get(key) != child.get(key):
                    leg[key] = child.get(key)
                    changed += 1
        if settle_parlay(parent):
            changed += 1
    return changed


def sync_prematch_journal(journal_path, record: dict[str, Any]) -> None:
    from .journal import load_signal_journal, save_signal_journal
    from .production_journal_serialization import _locked
    from .v4_prematch_lifecycle import sync_prematch_with_live_record
    with _locked(journal_path):
        rows = load_signal_journal(journal_path)
        changed = sync_prematch_with_live_record(rows, record)
        for row in rows:
            changed += int(settle_prematch_row(row, record))
            if str(row.get("origin") or "").lower() not in {"prematch_parlay", "parlay"}:
                continue
            if str(row.get("result") or "pending").lower() != "pending":
                continue
            legs = row.get("legs") or []
            changed += sync_prematch_with_live_record(legs, record)
            for leg in legs:
                changed += int(settle_prematch_row(leg, record))
            changed += int(settle_parlay(row))
        if changed:
            save_signal_journal(journal_path, rows)


def reconcile_pending_prematch(journal_path) -> int:
    """Settle all pending PREMATCH singles and parlay-only legs from Flashscore."""
    from .journal import load_signal_journal, save_signal_journal
    from .production_journal_serialization import _locked
    from .providers.flashscore import FlashscoreProvider

    with _locked(journal_path):
        rows = load_signal_journal(journal_path)
        pending_rows: list[dict[str, Any]] = []
        for row in rows:
            origin = str(row.get("origin") or "").casefold()
            if origin == "prematch" and str(row.get("result") or "pending").casefold() == "pending":
                pending_rows.append(row)
            elif origin in {"prematch_parlay", "parlay"} and str(row.get("result") or "pending").casefold() == "pending":
                pending_rows.extend(
                    leg for leg in (row.get("legs") or [])
                    if str(leg.get("result") or "pending").casefold() == "pending"
                )

        pending_ids = {
            str(r.get("match_id") or r.get("event_id") or "")
            for r in pending_rows
            if str(r.get("match_id") or r.get("event_id") or "")
        }
        if not pending_ids:
            return 0

        provider = FlashscoreProvider()
        states = provider.event_states(pending_ids)
        changed = 0
        for row in pending_rows:
            mid = str(row.get("match_id") or row.get("event_id") or "")
            state = states.get(mid) or {}
            if not bool(state.get("is_finished")):
                continue
            record = {"match": {
                "flashscore_event_id": mid,
                "is_finished": True,
                "home_score": state.get("home_score"),
                "away_score": state.get("away_score"),
            }}
            changed += int(settle_prematch_row(row, record))

        # Copy exact single results into matching legs when both products exist,
        # then settle every parent whose displayed legs are final.
        changed += sync_and_settle_parlays(rows)
        for parent in rows:
            if str(parent.get("origin") or "").casefold() in {"prematch_parlay", "parlay"}:
                changed += int(settle_parlay(parent))

        if changed:
            save_signal_journal(journal_path, rows)
            print(f"GOOL_PREMATCH_RECONCILE settled_or_synced={changed} events={len(pending_ids)}", flush=True)
        return changed
