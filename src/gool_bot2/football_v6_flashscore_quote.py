"""Convert actual, same-bookmaker Flashscore quotes for V6 and tournament legs.

The V6 wrapper still uses supported full-time markets only. Tournament-only
quotes can include correctly scoped half totals and non-push half-line handicaps.
Never combine market sides from separate books or synthesize public odds.
"""
from __future__ import annotations

import math
from typing import Any

from .full_market_decode import _find_participant_ids, _selection_observations


def _valid_odd(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def flashscore_fulltime_quotes(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not data.get("odds"):
        return []
    rows = [row for row in data["odds"] if isinstance(row, dict)]
    home_id, away_id = _find_participant_ids(rows)
    # Without a verified home/away participant mapping, team-specific totals
    # can be silently misclassified as match totals by the shared decoder.
    # Fail closed rather than advertise those quotes as an honest market.
    if not home_id or not away_id or home_id == away_id:
        return []
    observations = _selection_observations(data, home_id, away_id)

    # Each bookmaker gets only its own offerings; pairs are taken from the
    # exact same event, scope, handicap, and bookmaker name.
    books: dict[str, dict[tuple[str, str, float | None], dict[str, float]]] = {}
    for key, items in observations.items():
        scope, typ, side, line = key
        scope = str(scope).upper()
        if scope not in {"FULL_TIME", "FIRST_HALF", "SECOND_HALF"}:
            continue
        if typ not in {"OVER_UNDER", "HOME_DRAW_AWAY", "BOTH_TEAMS_TO_SCORE", "DOUBLE_CHANCE", "DRAW_NO_BET", "ASIAN_HANDICAP"}:
            continue
        if scope != "FULL_TIME" and typ != "OVER_UNDER":
            continue
        if typ == "OVER_UNDER":
            try:
                line_value = float(line)
            except (ValueError, TypeError):
                continue
            if not math.isfinite(line_value) or line_value < 0.5 or abs(line_value % 1.0 - 0.5) > 1e-8:
                continue
            family = "match_total"
            selection = str(side).upper()
            if selection.startswith("HOME_"):
                family, selection = "home_total", selection[5:]
            elif selection.startswith("AWAY_"):
                family, selection = "away_total", selection[5:]
            if selection not in {"OVER", "UNDER"}:
                continue
            if scope == "FIRST_HALF":
                if family != "match_total":
                    continue
                family = "first_half_total"
            elif scope == "SECOND_HALF":
                if family != "match_total":
                    continue
                family = "second_half_total"
            group = (family, "total", line_value)
        elif typ == "ASIAN_HANDICAP":
            selection = str(side).upper()
            try:
                value = float(line)
            except (TypeError, ValueError):
                continue
            # Quarter-line Asian handicaps require partial settlement, whole
            # handicaps may push: only non-push ±x.5 choices are supported.
            if selection not in {"HOME", "AWAY"} or not math.isfinite(value) or abs(value % 1.0 - .5) > 1e-8:
                continue
            orientation = value if selection == "HOME" else -value
            group = ("asian_handicap", "full", round(orientation, 5))
        elif typ == "DRAW_NO_BET":
            selection = str(side).upper()
            if selection not in {"HOME", "AWAY"}:
                continue
            group = ("draw_no_bet", "full", None)
        elif typ == "HOME_DRAW_AWAY":
            selection = str(side).upper()
            if selection not in {"HOME", "DRAW", "AWAY"}:
                continue
            group = ("match_1x2", "full", None)
        elif typ == "DOUBLE_CHANCE":
            selection = str(side).upper()
            if selection not in {"HOME_OR_DRAW", "DRAW_OR_AWAY", "HOME_OR_AWAY"}:
                continue
            group = ("double_chance", "full", None)
        else:
            selection = str(side).upper()
            if selection not in {"YES", "NO"}:
                continue
            group = ("btts", "full", None)

        for item in items:
            odd = _valid_odd(item.get("odds"))
            if odd is None or item.get("market_probability") is None:
                # A one-sided quote cannot verify the opposing line.
                continue
            book = str(item.get("bookmaker") or "").strip()
            if not book or book.casefold() in {"none", "null", "unknown"}:
                continue
            options = books.setdefault(book, {}).setdefault(group, {})
            options[selection] = max(options.get(selection, 0.0), odd)

    def build(book: str, markets: dict[tuple[str, str, float | None], dict[str, float]]) -> dict[str, Any]:
        output: dict[str, Any] = {
            "source": "flashscore_actual_fulltime_odds",
            "bookmaker": book,
            "match_totals": [],
            "home_totals": [],
            "away_totals": [],
            "first_half_totals": [],
            "second_half_totals": [],
            "asian_handicaps": [],
            "draw_no_bet": {},
            "match_1x2": {},
            "double_chance": {},
            "btts": {},
        }
        for (family, _kind, line), sides in markets.items():
            if family in {"match_total", "home_total", "away_total", "first_half_total", "second_half_total"} and {"OVER", "UNDER"} <= sides.keys():
                output[{
                    "match_total": "match_totals",
                    "home_total": "home_totals",
                    "away_total": "away_totals",
                    "first_half_total": "first_half_totals",
                    "second_half_total": "second_half_totals",
                }[family]].append({
                    "line": line, "over": sides["OVER"], "under": sides["UNDER"],
                })
            elif family == "asian_handicap" and {"HOME", "AWAY"} <= sides.keys():
                output["asian_handicaps"].append({
                    "home_line": line, "away_line": -line,
                    "home": sides["HOME"], "away": sides["AWAY"],
                })
            elif family == "draw_no_bet" and {"HOME", "AWAY"} <= sides.keys():
                output["draw_no_bet"] = {"home": sides["HOME"], "away": sides["AWAY"]}
            elif family == "match_1x2" and {"HOME", "DRAW", "AWAY"} <= sides.keys():
                output["match_1x2"] = {s.lower(): sides[s] for s in ("HOME", "DRAW", "AWAY")}
            elif family == "double_chance":
                output["double_chance"] = {label.lower(): sides[label]
                    for label in ("HOME_OR_DRAW", "DRAW_OR_AWAY", "HOME_OR_AWAY") if label in sides}
            elif family == "btts" and {"YES", "NO"} <= sides.keys():
                output["btts"] = {s.lower(): sides[s] for s in ("YES", "NO")}
        for family in ("match_totals", "home_totals", "away_totals", "first_half_totals", "second_half_totals"):
            output[family].sort(key=lambda x: x["line"])
        return output

    offers = [build(book, raw) for book, raw in books.items()]
    offers.sort(key=lambda q: (
        len(q["match_totals"]) + len(q["home_totals"]) + len(q["away_totals"])
        + len(q["first_half_totals"]) + len(q["second_half_totals"]) + len(q["asian_handicaps"])
        + int(bool(q["draw_no_bet"])) + int(bool(q["match_1x2"])) + int(bool(q["double_chance"])) + int(bool(q["btts"])),
        len(q["match_totals"]),
        q["bookmaker"],
    ), reverse=True)
    return [offer for offer in offers if (
        offer["match_totals"] or offer["home_totals"] or offer["away_totals"]
        or offer["first_half_totals"] or offer["second_half_totals"]
        or offer["asian_handicaps"] or offer["draw_no_bet"]
        or offer["match_1x2"] or offer["double_chance"] or offer["btts"]
    )]


def flashscore_fulltime_quote(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """V6 must use a bookmaker with a real FULL_TIME market, not only HT lines."""
    quotes = flashscore_fulltime_quotes(data)
    return next((q for q in quotes if (
        q["match_totals"] or q["home_totals"] or q["away_totals"]
        or q["match_1x2"] or q["btts"]
    )), None)
