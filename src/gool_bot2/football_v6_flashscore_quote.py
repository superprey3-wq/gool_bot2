"""Convert real Flashscore odds observations to V6 comparable bookmaker pairs.

Observation only: this does not place, invent, or adjust public betting odds.
Never combine the OVER from one bookmaker with an UNDER from another.
Only full-time, simple binary half-goal totals, BTTS and 1X2 are supported.
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


def flashscore_fulltime_quote(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict) or not data.get("odds"):
        return None
    rows = [row for row in data["odds"] if isinstance(row, dict)]
    home_id, away_id = _find_participant_ids(rows)
    # Without a verified home/away participant mapping, team-specific totals
    # can be silently misclassified as match totals by the shared decoder.
    # Fail closed rather than advertise those quotes as an honest market.
    if not home_id or not away_id or home_id == away_id:
        return None
    observations = _selection_observations(data, home_id, away_id)

    # Each bookmaker gets only its own offerings; pairs are taken from the
    # exact same event, scope, handicap, and bookmaker name.
    books: dict[str, dict[tuple[str, str, float | None], dict[str, float]]] = {}
    for key, items in observations.items():
        scope, typ, side, line = key
        if str(scope).upper() != "FULL_TIME":
            continue
        if typ not in {"OVER_UNDER", "HOME_DRAW_AWAY", "BOTH_TEAMS_TO_SCORE"}:
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
            group = (family, "total", line_value)
        elif typ == "HOME_DRAW_AWAY":
            selection = str(side).upper()
            if selection not in {"HOME", "DRAW", "AWAY"}:
                continue
            group = ("match_1x2", "full", None)
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
            "match_1x2": {},
            "btts": {},
        }
        for (family, _kind, line), sides in markets.items():
            if family in {"match_total", "home_total", "away_total"} and {"OVER", "UNDER"} <= sides.keys():
                output[{
                    "match_total": "match_totals",
                    "home_total": "home_totals",
                    "away_total": "away_totals",
                }[family]].append({
                    "line": line, "over": sides["OVER"], "under": sides["UNDER"],
                })
            elif family == "match_1x2" and {"HOME", "DRAW", "AWAY"} <= sides.keys():
                output["match_1x2"] = {s.lower(): sides[s] for s in ("HOME", "DRAW", "AWAY")}
            elif family == "btts" and {"YES", "NO"} <= sides.keys():
                output["btts"] = {s.lower(): sides[s] for s in ("YES", "NO")}
        for family in ("match_totals", "home_totals", "away_totals"):
            output[family].sort(key=lambda x: x["line"])
        return output

    offers = [build(book, raw) for book, raw in books.items()]
    offers.sort(key=lambda q: (
        len(q["match_totals"]) + len(q["home_totals"]) + len(q["away_totals"])
        + int(bool(q["match_1x2"])) + int(bool(q["btts"])),
        len(q["match_totals"]),
        q["bookmaker"],
    ), reverse=True)
    if not offers:
        return None
    best = offers[0]
    available = (len(best["match_totals"]) + len(best["home_totals"]) +
                 len(best["away_totals"]) + int(bool(best["match_1x2"])) + int(bool(best["btts"])))
    return best if available else None
