from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc


BG = sc.BG
PANEL = sc.PANEL
PANEL2 = sc.PANEL2
TEXT = sc.TEXT
MUTED = sc.MUTED
LINE = sc.LINE
GREEN = (82, 220, 118)
RED = sc.RED
ACCENT = (53, 214, 255)
GOLD = (255, 184, 48)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any, *, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "—"
    rounded = int(round(number))
    if signed:
        sign = "+" if rounded > 0 else ("−" if rounded < 0 else "")
        rounded = abs(rounded)
    else:
        sign = ""
    return f"{sign}{rounded:,}".replace(",", " ") + " ₽"


def _risk(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return f"{number:.1f}%"


def _profit_accent(value: Any):
    number = _number(value)
    if number is None or abs(number) < 0.005:
        return GOLD
    return GREEN if number > 0 else RED


def _cell(
    draw: ImageDraw.ImageDraw,
    *,
    x1: int,
    x2: int,
    label: str,
    value: str,
    accent,
    top: int,
) -> None:
    draw.text((x1, top + 15), label, font=sc._font(12, True), fill=MUTED)
    draw.text(
        (x1, top + 39),
        value,
        font=sc._fit(draw, value, x2 - x1 - 10, 23, True),
        fill=accent,
    )


def append_bank_strip(
    png: bytes,
    entry: dict[str, Any] | None,
    *,
    result: bool = False,
) -> bytes:
    """Append virtual-bank accounting to a public Multi card.

    The bankroll engine remains the single source of truth. This renderer only
    exposes fields already attached to the journal entry.
    """
    entry = dict(entry or {})
    source = Image.open(BytesIO(png)).convert("RGBA")
    width, height = source.size
    extra = 94
    image = Image.new("RGBA", (width, height + extra), BG + (255,))
    image.alpha_composite(source, (0, 0))
    draw = ImageDraw.Draw(image)

    top = height + 8
    draw.rounded_rectangle(
        (45, top, width - 45, height + extra - 10),
        20,
        fill=PANEL2,
        outline=LINE,
        width=2,
    )
    draw.text((70, top + 10), "ВИРТУАЛЬНЫЙ БАНК • УЧЁТ СТАВКИ", font=sc._font(12, True), fill=ACCENT)

    bank = _money(entry.get("virtual_bank_before_rub"))
    stake = _money(entry.get("virtual_stake_rub"))
    risk = _risk(entry.get("virtual_stake_pct"))

    if result:
        profit_raw = entry.get("virtual_profit_rub")
        profit = _money(profit_raw, signed=True)
        cells = (
            ("БАНК НА ВХОДЕ", bank, ACCENT),
            ("СТАВКА", stake, GOLD),
            ("P/L СТАВКИ", profit, _profit_accent(profit_raw)),
        )
    else:
        cells = (
            ("БАНК ДО ВХОДА", bank, ACCENT),
            ("СТАВКА GOOL", stake, GOLD),
            ("РИСК", risk, TEXT),
        )

    left = 70
    usable = width - 140
    cell_w = usable // 3
    for index, (label, value, accent) in enumerate(cells):
        x1 = left + index * cell_w
        x2 = left + (index + 1) * cell_w
        if index:
            draw.line((x1 - 18, top + 33, x1 - 18, height + extra - 22), fill=LINE, width=1)
        _cell(draw, x1=x1, x2=x2, label=label, value=value, accent=accent, top=top)

    out = BytesIO()
    image.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
