from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from .full_market_math import _num, _pair_fair, _multi_fair


def _odd(item: dict[str, Any]) -> float | None:
    x = _num(item.get("value"))
    return x if x is not None and x > 1.0 and bool(item.get("active", True)) else None


def _handicap(item: dict[str, Any]) -> float | None:
    return _num((item.get("handicap") or {}).get("value"))


def _find_participant_ids(rows: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    first = Counter()
    second = Counter()
    for row in rows:
        if str(row.get("bettingType") or "") != "HOME_DRAW_AWAY":
            continue
        items = [x for x in (row.get("odds") or []) if isinstance(x, dict) and _odd(x)]
        if len(items) < 3:
            continue
        pids = [str(x.get("eventParticipantId") or "") for x in items]
        if pids[0]:
            first[pids[0]] += 1
        if len(pids) > 1 and pids[1]:
            second[pids[1]] += 1
    home = first.most_common(1)[0][0] if first else None
    away = second.most_common(1)[0][0] if second else None
    return home, away


def _side(item: dict[str, Any], home_pid: str | None, away_pid: str | None) -> str | None:
    pid = str(item.get("eventParticipantId") or "")
    if home_pid and pid == home_pid:
        return "HOME"
    if away_pid and pid == away_pid:
        return "AWAY"
    return None


def _selection_observations(
    data: dict[str, Any],
    home_pid: str | None,
    away_pid: str | None,
) -> dict[tuple, list[dict[str, Any]]]:
    books = {}
    for b in ((data.get("settings") or {}).get("bookmakers") or []):
        z = b.get("bookmaker") or {}
        books[z.get("id")] = z.get("name") or str(z.get("id"))
    rows = [r for r in (data.get("odds") or []) if isinstance(r, dict)]

    hda_fair: dict[tuple[Any, str], dict[str, float]] = {}
    for row in rows:
        scope = str(row.get("bettingScope") or "")
        typ = str(row.get("bettingType") or "")
        if typ != "HOME_DRAW_AWAY":
            continue
        items = [x for x in (row.get("odds") or []) if isinstance(x, dict) and _odd(x)]
        mapped = []
        for item in items:
            side = _side(item, home_pid, away_pid) or ("DRAW" if not item.get("eventParticipantId") else None)
            if side:
                mapped.append((side, _odd(item), item))
        if {x[0] for x in mapped} >= {"HOME", "DRAW", "AWAY"}:
            by = {s: o for s, o, _ in mapped}
            fair = _multi_fair([by["HOME"], by["DRAW"], by["AWAY"]])
            hda_fair[(row.get("bookmakerId"), scope)] = dict(zip(("HOME", "DRAW", "AWAY"), fair))

    out: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        scope = str(row.get("bettingScope") or "")
        typ = str(row.get("bettingType") or "")
        bid = row.get("bookmakerId")
        book = books.get(bid, str(bid))
        items = [x for x in (row.get("odds") or []) if isinstance(x, dict) and _odd(x)]
        if not items:
            continue
        fair_map: dict[tuple, float] = {}
        entries: list[tuple[tuple, dict[str, Any]]] = []

        if typ == "HOME_DRAW_AWAY":
            for item in items:
                sel = _side(item, home_pid, away_pid) or ("DRAW" if not item.get("eventParticipantId") else None)
                if sel:
                    entries.append(((scope, typ, sel, None), item))
            if len(entries) >= 3:
                odds = [_odd(x[1]) for x in entries]
                if all(odds):
                    for (key, _), p in zip(entries, _multi_fair([float(x) for x in odds])):
                        fair_map[key] = p

        elif typ == "DRAW_NO_BET":
            base = hda_fair.get((bid, scope), {})
            for item in items:
                sel = _side(item, home_pid, away_pid)
                if sel in {"HOME", "AWAY"}:
                    entries.append(((scope, typ, sel, None), item))
            z = float(base.get("HOME", 0)) + float(base.get("AWAY", 0))
            if z > 0:
                fair_map[(scope, typ, "HOME", None)] = float(base.get("HOME", 0)) / z
                fair_map[(scope, typ, "AWAY", None)] = float(base.get("AWAY", 0)) / z
            elif len(entries) == 2:
                a, b = _pair_fair(float(_odd(entries[0][1])), float(_odd(entries[1][1])))
                fair_map[entries[0][0]] = a
                fair_map[entries[1][0]] = b

        elif typ == "DOUBLE_CHANCE":
            base = hda_fair.get((bid, scope), {})
            for item in items:
                s = _side(item, home_pid, away_pid)
                sel = "HOME_OR_DRAW" if s == "HOME" else "DRAW_OR_AWAY" if s == "AWAY" else "HOME_OR_AWAY"
                entries.append(((scope, typ, sel, None), item))
                if base:
                    fair_map[(scope, typ, sel, None)] = {
                        "HOME_OR_DRAW": base.get("HOME", 0) + base.get("DRAW", 0),
                        "DRAW_OR_AWAY": base.get("DRAW", 0) + base.get("AWAY", 0),
                        "HOME_OR_AWAY": base.get("HOME", 0) + base.get("AWAY", 0),
                    }[sel]

        elif typ == "OVER_UNDER":
            grouped: dict[tuple[str | None, float], dict[str, tuple[dict, float]]] = defaultdict(dict)
            for item in items:
                sel = str(item.get("selection") or "").upper()
                line = _handicap(item)
                s = _side(item, home_pid, away_pid)
                if sel not in {"OVER", "UNDER"} or line is None:
                    continue
                grouped[(s, line)][sel] = (item, float(_odd(item)))
                label = f"{s}_{sel}" if s else sel
                entries.append(((scope, typ, label, line), item))
            for (s, line), pair in grouped.items():
                if {"OVER", "UNDER"} <= pair.keys():
                    po, pu = _pair_fair(pair["OVER"][1], pair["UNDER"][1])
                    fair_map[(scope, typ, f"{s}_OVER" if s else "OVER", line)] = po
                    fair_map[(scope, typ, f"{s}_UNDER" if s else "UNDER", line)] = pu

        elif typ == "BOTH_TEAMS_TO_SCORE":
            pair = {}
            for item in items:
                flag = item.get("bothTeamsToScore")
                if flag is True:
                    sel = "YES"
                elif flag is False:
                    sel = "NO"
                else:
                    continue
                entries.append(((scope, typ, sel, None), item))
                pair[sel] = float(_odd(item))
            if {"YES", "NO"} <= pair.keys():
                py, pn = _pair_fair(pair["YES"], pair["NO"])
                fair_map[(scope, typ, "YES", None)] = py
                fair_map[(scope, typ, "NO", None)] = pn

        elif typ == "ODD_OR_EVEN":
            pair = {}
            for item in items:
                sel = str(item.get("selection") or "").upper()
                if sel not in {"ODD", "EVEN"}:
                    continue
                entries.append(((scope, typ, sel, None), item))
                pair[sel] = float(_odd(item))
            if {"ODD", "EVEN"} <= pair.keys():
                po, pe = _pair_fair(pair["ODD"], pair["EVEN"])
                fair_map[(scope, typ, "ODD", None)] = po
                fair_map[(scope, typ, "EVEN", None)] = pe

        elif typ == "CORRECT_SCORE":
            for item in items:
                sel = str(item.get("score") or "").strip()
                if sel:
                    entries.append(((scope, typ, sel, None), item))
            if entries:
                probs = _multi_fair([float(_odd(x[1])) for x in entries])
                for (key, _), p in zip(entries, probs):
                    fair_map[key] = p

        elif typ == "HALF_FULL_TIME":
            for item in items:
                sel = str(item.get("winner") or "").strip().upper()
                if sel:
                    entries.append(((scope, typ, sel, None), item))
            if entries:
                probs = _multi_fair([float(_odd(x[1])) for x in entries])
                for (key, _), p in zip(entries, probs):
                    fair_map[key] = p

        elif typ == "ASIAN_HANDICAP":
            pair_groups: dict[float, dict[str, tuple[dict, float]]] = defaultdict(dict)
            for item in items:
                s = _side(item, home_pid, away_pid)
                line = _handicap(item)
                if s not in {"HOME", "AWAY"} or line is None:
                    continue
                entries.append(((scope, typ, s, line), item))
                orientation = line if s == "HOME" else -line
                pair_groups[round(orientation, 4)][s] = (item, float(_odd(item)))
            for _orientation, pair in pair_groups.items():
                if {"HOME", "AWAY"} <= pair.keys():
                    ph, pa = _pair_fair(pair["HOME"][1], pair["AWAY"][1])
                    hline = _handicap(pair["HOME"][0])
                    aline = _handicap(pair["AWAY"][0])
                    fair_map[(scope, typ, "HOME", hline)] = ph
                    fair_map[(scope, typ, "AWAY", aline)] = pa

        elif typ == "EUROPEAN_HANDICAP":
            groups: dict[float, dict[str, tuple[dict, float]]] = defaultdict(dict)
            for item in items:
                s = _side(item, home_pid, away_pid)
                line = _handicap(item)
                if line is None:
                    continue
                if s == "HOME":
                    orientation = line
                    sel = "HOME"
                elif s == "AWAY":
                    orientation = -line
                    sel = "AWAY"
                else:
                    orientation = line
                    sel = "DRAW"
                entries.append(((scope, typ, sel, orientation), item))
                groups[round(orientation, 4)][sel] = (item, float(_odd(item)))
            for orientation, grp in groups.items():
                if {"HOME", "DRAW", "AWAY"} <= grp.keys():
                    probs = _multi_fair([grp[x][1] for x in ("HOME", "DRAW", "AWAY")])
                    for sel, p in zip(("HOME", "DRAW", "AWAY"), probs):
                        fair_map[(scope, typ, sel, orientation)] = p

        for key, item in entries:
            odd = _odd(item)
            if odd is None:
                continue
            out[key].append({
                "odds": odd,
                "bookmaker": book,
                "market_probability": fair_map.get(key),
            })
    return out
