from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .multi_card import BG, GOLD, GREEN, MUTED, PANEL, PANEL2, TEXT, _fit_line, _fmt_odd, render_multi_card
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
        return {
            "strong": False,
            "pressure_pp": 0.0,
            "moves": 0,
            "old_odd": None,
            "new_odd": None,
        }

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


def _odds_move_text(snapshot: dict[str, Any]) -> str:
    old_odd = snapshot.get("old_odd")
    new_odd = snapshot.get("new_odd")
    if old_odd is not None and new_odd is not None:
        return f"{_fmt_odd(old_odd)} → {_fmt_odd(new_odd)}"
    if new_odd is not None:
        return f"@{_fmt_odd(new_odd)}"
    return "—"


def render_multi_signal_card(
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    entry: dict[str, Any] | None = None,
    market_row: dict[str, Any] | None = None,
) -> bytes:
    """Render normal Multi card, or a dedicated strong-1xBet-steam variant."""
    base_png = render_multi_card(record, decision, entry=entry, market_row=market_row)
    snapshot = steam_snapshot(market_row, decision)
    if not snapshot["strong"]:
        return base_png

    image = Image.open(BytesIO(base_png)).convert("RGBA")
    draw = ImageDraw.Draw(image)
    winner = decision.winner
    if winner is None:
        return base_png

    # Separate visual identity for an objectively strong bookmaker move.
    draw.rounded_rectangle((24, 20, 1056, 74), 18, fill=PANEL2, outline=GOLD, width=3)
    sc._center(draw, "GOOL MULTI • СИЛЬНЫЙ ПРОГРУЗ 1xBET", 34, sc._font(18, True), GOLD)

    # Turn the BEST BET header itself into an explicit steam card.
    draw.rounded_rectangle((45, 350, 1035, 530), 24, outline=GOLD, width=4)
    draw.rectangle((64, 360, 420, 405), fill=PANEL2)
    draw.text((72, 371), "СИЛЬНЫЙ ПРОГРУЗ", font=sc._font(19, True), fill=GOLD)

    # Replace generic VALUE/ROI strip with facts about the bookmaker move.
    draw.rounded_rectangle((45, 780, 1035, 885), 18, fill=(8, 20, 31), outline=GOLD, width=3)
    metrics = [
        ("ПРОГРУЗ", f"{float(snapshot['pressure_pp']):+.1f} п.п.", GOLD),
        ("КЭФ", _odds_move_text(snapshot), GREEN),
        ("ИМПУЛЬСОВ", str(int(snapshot["moves"] or 0)), TEXT),
        ("DATA", f"{float(winner.data_quality) * 100:.0f}/100", TEXT),
    ]
    for idx, (label, value, color) in enumerate(metrics):
        x = 70 + idx * 245
        draw.text((x, 799), label, font=sc._font(12, True), fill=MUTED)
        draw.text((x, 833), value, font=_fit_line(draw, value, 205, 20, True), fill=color)

    # Make the reason block clearly identify why this card has a different style.
    draw.rectangle((62, 914, 480, 948), fill=PANEL)
    draw.text((70, 923), "ПОЧЕМУ ВЫБРАН • ПРОГРУЗ", font=sc._font(13, True), fill=GOLD)

    draw.rounded_rectangle((330, 1188, 750, 1240), 16, fill=GOLD)
    sc._center(
        draw,
        "СИЛЬНЫЙ ПРОГРУЗ • BEST BET",
        1201,
        _fit_line(draw, "СИЛЬНЫЙ ПРОГРУЗ • BEST BET", 380, 16, True),
        BG,
    )
    return sc._save(image)
