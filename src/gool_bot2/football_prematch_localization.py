"""Russian-facing football PREMATCH selection labels.

Render-layer ONLY: journal 'market' and 'selection' stay provider-normalized
English for result settlement, historical dedupe and model comparison.
Never guess what a bare 'yes', 'home' or 'away' means without market context.
"""
from __future__ import annotations

import re
from typing import Any


def football_selection_ru(market: Any, selection: Any) -> str:
    family = str(market or "").strip().casefold()
    original = str(selection or "").strip()
    raw = original.casefold().strip().replace("_", " ")
    normalized = " ".join(raw.split())
    upper = original.upper()

    legacy = {
        "BTTS_YES": "Обе забьют — Да",
        "BTTS_NO": "Обе забьют — Нет",
    }
    if upper in legacy:
        return legacy[upper]

    m = re.fullmatch(r"(?:FT|1H|2H)_(OVER|UNDER)_([0-9]+(?:[.,][0-9]+)?)", upper)
    if m:
        half = "1-й тайм · " if upper.startswith("1H") else "2-й тайм · " if upper.startswith("2H") else ""
        return f"{half}{'ТБ' if m.group(1) == 'OVER' else 'ТМ'} {m.group(2).replace(',', '.')}"

    aliases = {
        "match_1x2": {"home": "П1 — победа хозяев", "draw": "Х — ничья", "away": "П2 — победа гостей",
                      "1": "П1 — победа хозяев", "x": "Х — ничья", "2": "П2 — победа гостей"},
        "1x2": {"home": "П1 — победа хозяев", "draw": "Х — ничья", "away": "П2 — победа гостей"},
        "double_chance": {
            "home or draw": "1Х — хозяева не проиграют",
            "draw or away": "Х2 — гости не проиграют",
            "home or away": "12 — ничьей не будет",
            "1x": "1Х — хозяева не проиграют",
            "x2": "Х2 — гости не проиграют",
            "12": "12 — ничьей не будет",
        },
        "draw_no_bet": {
            "home": "П1 — возврат при ничьей",
            "away": "П2 — возврат при ничьей",
        },
        "btts": {"yes": "Обе забьют — Да", "no": "Обе забьют — Нет"},
    }
    if family in aliases:
        return aliases[family].get(normalized, original or "?")
    if family.startswith("btts") and normalized in {"yes", "no"}:
        return f"Обе забьют — {'Да' if normalized == 'yes' else 'Нет'}"

    handicap = re.fullmatch(r"(home|away)\s+([+-]?\d+(?:[.,]\d+)?)", normalized)
    if family in {"asian_handicap", "european_handicap", "handicap"} and handicap:
        who = "1" if handicap.group(1) == "home" else "2"
        value = float(handicap.group(2).replace(",", "."))
        return f"Ф{who} ({value:+g})"

    total = re.fullmatch(r"(over|under)\s+([0-9]+(?:[.,][0-9]+)?)", normalized)
    if total:
        direction = "Б" if total.group(1) == "over" else "М"
        line = total.group(2).replace(",", ".")
        period = (
            "1-й тайм · " if family.startswith(("1h_", "first_half"))
            else "2-й тайм · " if family.startswith(("2h_", "second_half"))
            else ""
        )
        base = "ИТ" if family in {"home_total", "away_total"} else "Т"
        suffix = "1" if family == "home_total" else "2" if family == "away_total" else ""
        return f"{period}{base}{direction}{suffix} {line}"

    # An unknown provider code must not be translated by guesswork.
    return original.replace("_", " ") or str(market or "?")
