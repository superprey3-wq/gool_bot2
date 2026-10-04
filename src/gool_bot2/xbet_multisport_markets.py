from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

SCOPE_FULL = "FULL_MATCH"

BASKETBALL_SCOPES = (
    SCOPE_FULL, "FIRST_HALF", "SECOND_HALF",
    "QUARTER_1", "QUARTER_2", "QUARTER_3", "QUARTER_4",
)
HOCKEY_SCOPES = (SCOPE_FULL, "PERIOD_1", "PERIOD_2", "PERIOD_3")


def sport_scopes(sport: str) -> tuple[str, ...]:
    return HOCKEY_SCOPES if str(sport).casefold() == "hockey" else BASKETBALL_SCOPES


def scope_from_subgame(row: dict[str, Any], sport: str) -> str | None:
    name = " ".join(str(row.get(k) or "") for k in ("PN",)).strip().casefold()
    name = re.sub(r"\s+", " ", name)
    aliases = {
        "1st quarter": "QUARTER_1", "1 quarter": "QUARTER_1", "quarter 1": "QUARTER_1",
        "2nd quarter": "QUARTER_2", "2 quarter": "QUARTER_2", "quarter 2": "QUARTER_2",
        "3rd quarter": "QUARTER_3", "3 quarter": "QUARTER_3", "quarter 3": "QUARTER_3",
        "4th quarter": "QUARTER_4", "4 quarter": "QUARTER_4", "quarter 4": "QUARTER_4",
        "1 half": "FIRST_HALF", "1st half": "FIRST_HALF", "first half": "FIRST_HALF",
        "2 half": "SECOND_HALF", "2nd half": "SECOND_HALF", "second half": "SECOND_HALF",
        "1st period": "PERIOD_1", "1 period": "PERIOD_1", "period 1": "PERIOD_1",
        "2nd period": "PERIOD_2", "2 period": "PERIOD_2", "period 2": "PERIOD_2",
        "3rd period": "PERIOD_3", "3 period": "PERIOD_3", "period 3": "PERIOD_3",
    }
    scope = aliases.get(name)
    if scope and scope in sport_scopes(sport):
        return scope

    # Some feeds localize/shorten PN but keep a period index.
    try:
        p = int(row.get("P"))
    except (TypeError, ValueError):
        p = 0
    if str(sport).casefold() == "basketball":
        return {1: "QUARTER_1", 2: "QUARTER_2", 3: "QUARTER_3", 4: "QUARTER_4", 11: "FIRST_HALF", 12: "SECOND_HALF"}.get(p)
    return {1: "PERIOD_1", 2: "PERIOD_2", 3: "PERIOD_3"}.get(p)


def _price(row: dict[str, Any]) -> float | None:
    try:
        value = row.get("C") if row.get("C") is not None else row.get("CV")
        price = float(value)
        return price if price > 1.001 else None
    except (TypeError, ValueError):
        return None


def _line(row: dict[str, Any]) -> float | None:
    try:
        return None if row.get("P") is None else float(row.get("P"))
    except (TypeError, ValueError):
        return None


def iter_market_selections(game: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize E/AE/GE 1xBet layouts while preserving raw G/GS/T ids.

    AE/GE groups often carry G/GS only on the parent; old recursive decoders that
    read G only from the selection silently lose those markets. Prefer grouped
    layouts, then enrich with E groups that were not already represented.
    """
    out: list[dict[str, Any]] = []
    grouped_keys: set[tuple[int, int | None]] = set()

    def add(sel: dict[str, Any], layout: str, g: Any = None, gs: Any = None) -> None:
        try:
            t = int(sel.get("T"))
            gi = int(sel.get("G") if sel.get("G") is not None else g)
        except (TypeError, ValueError):
            return
        try:
            gsi = int(sel.get("GS") if sel.get("GS") is not None else gs)
        except (TypeError, ValueError):
            gsi = None
        price = _price(sel)
        if price is None:
            return
        out.append({
            "layout": layout,
            "G": gi,
            "GS": gsi,
            "T": t,
            "P": _line(sel),
            "C": price,
            "blocked": bool(sel.get("B") or False),
        })

    ae = game.get("AE")
    if isinstance(ae, list):
        for group in ae:
            if not isinstance(group, dict):
                continue
            try:
                g = int(group.get("G")) if group.get("G") is not None else None
            except (TypeError, ValueError):
                g = None
            try:
                gs = int(group.get("GS")) if group.get("GS") is not None else None
            except (TypeError, ValueError):
                gs = None
            if g is not None:
                grouped_keys.add((g, gs))
            for sel in group.get("ME") or []:
                if isinstance(sel, dict):
                    add(sel, "AE", g, gs)

    ge = game.get("GE")
    if isinstance(ge, list):
        for group in ge:
            if not isinstance(group, dict):
                continue
            try:
                g = int(group.get("G")) if group.get("G") is not None else None
            except (TypeError, ValueError):
                g = None
            try:
                gs = int(group.get("GS")) if group.get("GS") is not None else None
            except (TypeError, ValueError):
                gs = None
            if g is not None:
                grouped_keys.add((g, gs))
            for row in group.get("E") or []:
                if isinstance(row, dict):
                    add(row, "GE", g, gs)
                elif isinstance(row, list):
                    for sel in row:
                        if isinstance(sel, dict):
                            add(sel, "GE", g, gs)

    e = game.get("E")
    if isinstance(e, list):
        for sel in e:
            if not isinstance(sel, dict):
                continue
            try:
                g = int(sel.get("G"))
            except (TypeError, ValueError):
                continue
            try:
                gs = int(sel.get("GS")) if sel.get("GS") is not None else None
            except (TypeError, ValueError):
                gs = None
            if (g, gs) in grouped_keys or (g, None) in grouped_keys:
                continue
            add(sel, "E")

    # exact de-duplication without destroying alternate lines
    unique: dict[tuple[int, int | None, int, float | None], dict[str, Any]] = {}
    for row in out:
        unique.setdefault((row["G"], row["GS"], row["T"], row["P"]), row)
    return list(unique.values())


def _paired_lines(
    rows: list[dict[str, Any]],
    *,
    groups: tuple[int, ...],
    over_types: tuple[int, ...],
    under_types: tuple[int, ...],
) -> list[dict[str, float]]:
    by_line: dict[float, dict[str, float]] = {}
    for row in rows:
        if int(row.get("G") or -1) not in groups or row.get("P") is None:
            continue
        t = int(row.get("T") or -1)
        side = "over" if t in over_types else "under" if t in under_types else None
        if not side:
            continue
        line = float(row["P"])
        by_line.setdefault(line, {"line": line})[side] = float(row["C"])
    return [by_line[k] for k in sorted(by_line) if "over" in by_line[k] and "under" in by_line[k]]


def _handicap_lines(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    by_abs: dict[float, dict[str, float]] = {}
    for row in rows:
        if int(row.get("G") or -1) not in {2, 3} or row.get("P") is None:
            continue
        t = int(row.get("T") or -1)
        side = "home" if t in {4, 7} else "away" if t in {5, 8} else None
        if not side:
            continue
        line = float(row["P"])
        key = abs(line)
        item = by_abs.setdefault(key, {})
        item[f"{side}_line"] = line
        item[side] = float(row["C"])
    return [dict(v) for _, v in sorted(by_abs.items()) if "home" in v and "away" in v]


def _moneyline(rows: list[dict[str, Any]], sport: str) -> dict[str, Any]:
    groups = (
        [
            (102, {501: "home", 502: "away"}, "moneyline_2way"),
            (1, {1: "home", 2: "draw", 3: "away"}, "regulation_1x2"),
            (101, {401: "home", 403: "draw", 402: "away"}, "moneyline_3way"),
        ]
        if str(sport).casefold() == "basketball"
        else [
            (1, {1: "home", 2: "draw", 3: "away"}, "regulation_1x2"),
            (101, {401: "home", 403: "draw", 402: "away"}, "moneyline_3way"),
            (102, {501: "home", 502: "away"}, "moneyline_2way"),
        ]
    )
    candidates: list[dict[str, Any]] = []
    for group, types, kind in groups:
        odds: dict[str, float] = {}
        for row in rows:
            if int(row.get("G") or -1) != group or row.get("P") is not None:
                continue
            name = types.get(int(row.get("T") or -1))
            if name and name not in odds:
                odds[name] = float(row["C"])
        if "home" in odds and "away" in odds:
            candidates.append({"kind": kind, "group": group, **odds})
    primary = candidates[0] if candidates else {}
    return {
        "home": primary.get("home"),
        "draw": primary.get("draw"),
        "away": primary.get("away"),
        "kind": primary.get("kind"),
        "group": primary.get("group"),
        "variants": candidates,
    }


def decode_core_markets(game: dict[str, Any], sport: str, *, scope: str = SCOPE_FULL) -> dict[str, Any]:
    rows = iter_market_selections(game)
    # Verified new-builder mappings: G17 total; G15 home IT; G62 away IT; G2 handicap.
    # Real legacy mirrors also expose G4 total / G5 IT1 / G6 IT2 / G3 handicap.
    decoded = {
        "scope": scope,
        "match_total": _paired_lines(rows, groups=(17, 3, 4), over_types=(9, 11), under_types=(10, 12)),
        "home_total": _paired_lines(rows, groups=(15, 5), over_types=(11,), under_types=(12,)),
        "away_total": _paired_lines(rows, groups=(62, 16, 6), over_types=(13,), under_types=(14,)),
        "handicap": _handicap_lines(rows),
        "moneyline": _moneyline(rows, sport),
        "raw": rows,
    }
    return decoded


def balanced_total(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    candidates: list[dict[str, float]] = []
    for row in rows:
        try:
            line, over, under = float(row["line"]), float(row["over"]), float(row["under"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (1.02 < over < 20 and 1.02 < under < 20):
            continue
        inv_o, inv_u = 1.0 / over, 1.0 / under
        p = inv_o / (inv_o + inv_u)
        candidates.append({"line": line, "over": over, "under": under, "probability": p})
    return min(candidates, key=lambda x: abs(x["probability"] - 0.5)) if candidates else None


def raw_catalog(decoded: dict[str, Any]) -> list[dict[str, Any]]:
    counts: dict[tuple[int, int | None, int], dict[str, Any]] = {}
    for row in decoded.get("raw") or []:
        key = (int(row["G"]), row.get("GS"), int(row["T"]))
        item = counts.setdefault(key, {"G": key[0], "GS": key[1], "T": key[2], "lines": [], "layouts": []})
        if row.get("P") is not None and row["P"] not in item["lines"]:
            item["lines"].append(row["P"])
        if row.get("layout") not in item["layouts"]:
            item["layouts"].append(row.get("layout"))
    return list(counts.values())


def market_lanes(decoded_by_scope: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten total markets into independent movement lanes.

    A lane is one subject (match/home/away) inside one scope. The movement brain
    can monitor each lane independently; moneyline/handicap are still captured
    in the catalog but not converted into a total signal.
    """
    lanes: list[dict[str, Any]] = []
    for scope, decoded in decoded_by_scope.items():
        for family in ("match_total", "home_total", "away_total"):
            total = balanced_total(decoded.get(family) or [])
            if total is None:
                continue
            lanes.append({"scope": scope, "market_family": family, **total})
    return lanes



def _fair_two_way(a: float, b: float) -> tuple[float, float]:
    ia, ib = 1.0 / float(a), 1.0 / float(b)
    total = ia + ib
    return ia / total, ib / total


def _fair_many(values: dict[str, float]) -> dict[str, float]:
    inv = {key: 1.0 / float(value) for key, value in values.items() if float(value) > 1.001}
    total = sum(inv.values())
    return {key: value / total for key, value in inv.items()} if total > 0 else {}


def prematch_market_lanes(decoded_by_scope: dict[str, dict[str, Any]], sport: str) -> list[dict[str, Any]]:
    """Flatten every safely settleable decoded PREMATCH market.

    Totals keep their existing two-sided lane. Handicap and moneyline choices
    become independent choice lanes so movement can be tracked per selection.
    Hockey regulation 1X2 is catalogued but not auto-signalled because final
    Flashscore score may include OT/shootout; prefer a verified two-way winner.
    """
    lanes = market_lanes(decoded_by_scope)
    for scope, decoded in decoded_by_scope.items():
        for item in decoded.get("handicap") or []:
            try:
                home_odd = float(item["home"])
                away_odd = float(item["away"])
                home_line = float(item["home_line"])
                away_line = float(item["away_line"])
            except (KeyError, TypeError, ValueError):
                continue
            ph, pa = _fair_two_way(home_odd, away_odd)
            lanes.extend([
                {
                    "scope": scope, "market_family": "handicap", "choice_key": "home",
                    "selection_side": "home", "line": home_line, "odd": home_odd,
                    "probability": ph, "metric": ph * 100.0,
                    "selection": f"Ф1 {home_line:+g}",
                },
                {
                    "scope": scope, "market_family": "handicap", "choice_key": "away",
                    "selection_side": "away", "line": away_line, "odd": away_odd,
                    "probability": pa, "metric": pa * 100.0,
                    "selection": f"Ф2 {away_line:+g}",
                },
            ])

        moneyline = dict(decoded.get("moneyline") or {})
        variants = [v for v in (moneyline.get("variants") or []) if isinstance(v, dict)]
        chosen: dict[str, Any] | None = None
        if str(sport).casefold() == "hockey":
            chosen = next((v for v in variants if str(v.get("kind") or "") == "moneyline_2way"), None)
        else:
            chosen = next((v for v in variants if str(v.get("kind") or "") == "moneyline_2way"), None)
            if chosen is None and str(moneyline.get("kind") or "") == "moneyline_2way":
                chosen = moneyline
        if not chosen:
            continue
        odds = {}
        for side in ("home", "away"):
            try:
                value = float(chosen.get(side))
            except (TypeError, ValueError):
                continue
            if value > 1.001:
                odds[side] = value
        if len(odds) != 2:
            continue
        fair = _fair_many(odds)
        for side in ("home", "away"):
            lanes.append({
                "scope": scope,
                "market_family": "moneyline",
                "choice_key": side,
                "selection_side": side,
                "moneyline_kind": str(chosen.get("kind") or "moneyline_2way"),
                "line": 0.0,
                "odd": odds[side],
                "probability": fair.get(side, 0.5),
                "metric": fair.get(side, 0.5) * 100.0,
                "selection": "П1" if side == "home" else "П2",
            })
    return lanes

def period_scores(game: dict[str, Any], sport: str) -> dict[str, tuple[int, int]]:
    sc = game.get("SC") or {}
    result: dict[str, tuple[int, int]] = {}
    for item in sc.get("PS") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("Value") or {}
        if not isinstance(value, dict):
            continue
        label = str(value.get("NF") or value.get("N") or "").strip()
        fake = {"PN": label, "P": item.get("Key")}
        scope = scope_from_subgame(fake, sport)
        if not scope:
            continue
        try:
            result[scope] = (int(float(value.get("S1") or 0)), int(float(value.get("S2") or 0)))
        except (TypeError, ValueError):
            continue

    if str(sport).casefold() == "basketball":
        q1, q2 = result.get("QUARTER_1"), result.get("QUARTER_2")
        q3, q4 = result.get("QUARTER_3"), result.get("QUARTER_4")
        if q1 and q2:
            result["FIRST_HALF"] = (q1[0] + q2[0], q1[1] + q2[1])
        if q3 and q4:
            result["SECOND_HALF"] = (q3[0] + q4[0], q3[1] + q4[1])
    return result


def lane_score(
    lane: dict[str, Any],
    full_score: tuple[int, int],
    scoped_scores: dict[str, tuple[int, int]],
) -> tuple[int, int]:
    scope = str(lane.get("scope") or SCOPE_FULL)
    score = full_score if scope == SCOPE_FULL else scoped_scores.get(scope, (0, 0))
    family = str(lane.get("market_family") or "match_total")
    if family == "home_total":
        return score[0], 0
    if family == "away_total":
        return 0, score[1]
    return score


SCOPE_LABEL_RU = {
    "FULL_MATCH": "Матч",
    "FIRST_HALF": "1-я половина",
    "SECOND_HALF": "2-я половина",
    "QUARTER_1": "1-я четверть",
    "QUARTER_2": "2-я четверть",
    "QUARTER_3": "3-я четверть",
    "QUARTER_4": "4-я четверть",
    "PERIOD_1": "1-й период",
    "PERIOD_2": "2-й период",
    "PERIOD_3": "3-й период",
}


def lane_key(lane: dict[str, Any]) -> str:
    base = f"{str(lane.get('scope') or SCOPE_FULL)}:{str(lane.get('market_family') or 'match_total')}"
    choice = str(lane.get("choice_key") or "")
    return f"{base}:{choice}" if choice else base


def selection_label(lane: dict[str, Any], direction: str, line: float | None = None) -> str:
    family = str(lane.get("market_family") or "match_total")
    scope = str(lane.get("scope") or SCOPE_FULL)
    value = float(lane.get("line") if line is None else line)
    over = str(direction or "over").casefold() != "under"
    if family == "home_total":
        base = ("ИТБ1" if over else "ИТМ1") + f" {value:g}"
    elif family == "away_total":
        base = ("ИТБ2" if over else "ИТМ2") + f" {value:g}"
    else:
        base = ("ТБ" if over else "ТМ") + f" {value:g}"
    prefix = SCOPE_LABEL_RU.get(scope, scope)
    return base if scope == SCOPE_FULL else f"{prefix}: {base}"



def live_scopes_from_period(sport: str, period: str | None) -> set[str]:
    """Return the one segment scope allowed to generate a LIVE signal.

    Product policy: hockey LIVE = current period total only; basketball LIVE =
    current quarter total only. Full-match, team totals, halves, handicaps and
    moneyline remain visible in telemetry but are PREMATCH-only signal markets.
    """
    raw = re.sub(r"\s+", " ", str(period or "").strip().casefold())
    if not raw:
        return set()

    if str(sport).casefold() == "hockey":
        for idx in (1, 2, 3):
            ordinal = {1: "1st", 2: "2nd", 3: "3rd"}[idx]
            needles = (f"{ordinal} period", f"{idx} period", f"period {idx}", f"p{idx}")
            if any(n in raw for n in needles):
                return {f"PERIOD_{idx}"}
        m = re.search(r"\b([1-3])\b", raw)
        return {f"PERIOD_{m.group(1)}"} if m else set()

    for idx in (1, 2, 3, 4):
        ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}[idx]
        needles = (f"{ordinal} quarter", f"{idx} quarter", f"quarter {idx}", f"q{idx}")
        if any(n in raw for n in needles):
            return {f"QUARTER_{idx}"}
    m = re.search(r"\b([1-4])\b", raw)
    if m and ("quarter" in raw or raw.startswith("q") or raw.isdigit()):
        return {f"QUARTER_{m.group(1)}"}
    return set()

def lane_phase_policy(sport: str, phase: str, lane: dict[str, Any], period: str | None = None) -> tuple[bool, str]:
    """Central PREMATCH/LIVE routing policy.

    PREMATCH may use every decoded total lane. LIVE is deliberately narrow:
    only the total of the segment currently being played.
    """
    phase = str(phase or "LIVE").upper()
    scope = str(lane.get("scope") or SCOPE_FULL)
    family = str(lane.get("market_family") or "match_total")

    if phase == "PREMATCH":
        if family in {"match_total", "home_total", "away_total", "handicap", "moneyline"}:
            return True, "prematch_full_market_tree"
        return False, "prematch_unknown_market_catalog_only"

    if family != "match_total":
        return False, "live_segment_total_only"
    allowed = live_scopes_from_period(sport, period)
    if scope not in allowed:
        return False, "live_not_current_segment"
    return True, "live_current_segment_total_only"

def policy_text_ru(sport: str) -> tuple[str, str]:
    if str(sport).casefold() == "hockey":
        return (
            "Все рынки до матча: матчевые тоталы/ИТ, форы, исходы + рынки периодов",
            "Только ТБ/ТМ текущего периода",
        )
    return (
        "Все рынки до матча: матчевые тоталы/ИТ, форы, исходы + рынки половин/четвертей",
        "Только ТБ/ТМ текущей четверти",
    )
