from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .multi_card import BG, GOLD, PANEL2, render_multi_card
from .multi_router import RouterDecision


STRONG_STEAM_MIN_PP = 6.0


def _pressure_row(market_row: dict[str, Any] | None, winner: Any) -> dict[str, Any]:
    if not market_row:
        return {}
    pressure = market_row.get("pressure") or {}
    keys = [str(getattr(winner, "key", "") or "")]
    if str(getattr(winner, "strategy", "") or "") == "both_teams_to_score":
        keys.extend(("btts_yes:None", "btts_yes"))
    for key in keys:
        row = pressure.get(key)
        if isinstance(row, dict):
            return dict(row)
    return {}


def steam_snapshot(market_row: dict[str, Any] | None, decision: RouterDecision) -> dict[str, Any]:
    winner = decision.winner
    if winner is None:
        return {"strong": False, "pressure_pp": 0.0, "moves": 0, "old_odd": None, "new_odd": None}

    try:
        pressure_pp = float(getattr(winner, "market_pressure_pp", 0.0) or 0.0)
    except (TypeError, ValueError):
        pressure_pp = 0.0

    row = _pressure_row(market_row, winner)
    try:
        moves = int(row.get("one_way_moves") or 0)
    except (TypeError, ValueError):
        moves = 0

    def _odd(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 1.0 else None

    old_odd = _odd(row.get("old_odd"))
    new_odd = _odd(row.get("new_odd")) or _odd(getattr(winner, "odd", None))
    level = str(getattr(winner, "market_level", "") or "").upper()
    strong = pressure_pp >= STRONG_STEAM_MIN_PP or level == "STRONG_STEAM"
    return {
        "strong": bool(strong),
        "pressure_pp": pressure_pp,
        "moves": moves,
        "old_odd": old_odd,
        "new_odd": new_odd,
    }


def is_strong_steam(decision: RouterDecision) -> bool:
    winner = decision.winner
    if winner is None:
        return False
    try:
        pressure_pp = float(getattr(winner, "market_pressure_pp", 0.0) or 0.0)
    except (TypeError, ValueError):
        pressure_pp = 0.0
    level = str(getattr(winner, "market_level", "") or "").upper()
    return pressure_pp >= STRONG_STEAM_MIN_PP or level == "STRONG_STEAM"


def render_multi_signal_card(
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    entry: dict[str, Any] | None = None,
    market_row: dict[str, Any] | None = None,
) -> bytes:
    """Render the same clean card, with gold emphasis and readable steam movement."""
    base_png = render_multi_card(record, decision, entry=entry, market_row=market_row)
    snapshot = steam_snapshot(market_row, decision)
    if not snapshot["strong"]:
        return base_png

    image = Image.open(BytesIO(base_png)).convert("RGBA")
    draw = ImageDraw.Draw(image)
    width, height = image.size

    draw.rounded_rectangle((24, 20, width - 24, 76), 18, fill=PANEL2, outline=GOLD, width=3)
    sc._center(draw, "GOOL MULTI • СИЛЬНЫЙ ПРОГРУЗ 1xBET", 36, sc._font(17, True), GOLD)
    draw.rounded_rectangle((45, 382, width - 45, 655), 26, outline=GOLD, width=4)

    pressure_pp = float(snapshot.get("pressure_pp") or 0.0)
    moves = int(snapshot.get("moves") or 0)
    old_odd = snapshot.get("old_odd")
    new_odd = snapshot.get("new_odd")

    draw.rectangle((68, 565, width - 68, 646), fill=PANEL2)
    line1 = f"ПРОГРУЗ 1xBET  {pressure_pp:+.1f} п.п. • ИМПУЛЬСОВ {moves}"
    draw.text((75, 568), line1, font=sc._fit(draw, line1, 900, 22, True), fill=GOLD)

    if old_odd is not None and new_odd is not None:
        move_pct = ((float(new_odd) - float(old_odd)) / float(old_odd)) * 100.0
        line2 = f"КЭФ БЫЛ {float(old_odd):.2f}  →  СТАЛ {float(new_odd):.2f} • ΔКЭФ {move_pct:+.1f}%"
    elif new_odd is not None:
        line2 = f"ТЕКУЩИЙ КЭФ {float(new_odd):.2f} • СТАРТОВЫЙ КЭФ НЕДОСТУПЕН"
    else:
        line2 = "ДВИЖЕНИЕ КЭФФИЦИЕНТА: ДАННЫЕ НЕДОСТУПНЫ"
    draw.text((75, 607), line2, font=sc._fit(draw, line2, 900, 21, True), fill=sc.TEXT)

    draw.rounded_rectangle((330, height - 72, 750, height - 22), 16, fill=GOLD)
    sc._center(draw, "ПРОГРУЗ • BEST BET", height - 59, sc._font(16, True), BG)
    return sc._save(image)
