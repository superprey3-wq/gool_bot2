from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 900
BG = (5, 10, 18)
PANEL = (13, 22, 36)
PANEL2 = (19, 31, 49)
TEXT = (247, 249, 252)
MUTED = (151, 166, 188)
GOLD = (255, 184, 48)
GREEN = (82, 220, 118)
RED = (244, 104, 104)
CYAN = (61, 178, 255)
LINE = (45, 63, 88)

HEAD_LABELS = {
    "another_goal": "ЕЩЁ ГОЛ",
    "goal_before_ht": "ГОЛ ДО ПЕРЕРЫВА",
    "two_plus_goals_second_half": "2+ ГОЛА ВО 2 ТАЙМЕ",
}


def _font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font, fill) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 38, bold: bool = True):
    for size in range(start, 15, -2):
        font = _font(size, bold)
        if draw.textbbox((0, 0), str(text), font=font)[2] <= width:
            return font
    return _font(16, bold)


def _stat(record: dict[str, Any], *names: str) -> float | None:
    providers = record.get("providers") or {}
    for provider in ("flashscore", "fotmob", "scores365"):
        stats = (providers.get(provider) or {}).get("stats") or {}
        for name in names:
            value = stats.get(name)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def _pair(record: dict[str, Any], base: str) -> str:
    home = _stat(record, f"home_{base}", f"{base}_home")
    away = _stat(record, f"away_{base}", f"{base}_away")
    if home is None or away is None:
        return "—"
    if base in {"xg", "expected_goals"}:
        return f"{home:.2f} — {away:.2f}"
    return f"{int(home)} — {int(away)}"


def render_signal_card(
    record: dict[str, Any],
    head: str,
    probability: float,
    model_result: dict[str, Any],
    cards: dict[str, Any],
) -> bytes:
    """Render a local PNG signal card using the visual language of GOOL v1."""
    match = record.get("match") or {}
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    as_ = int(match.get("away_score") or 0)
    direct = model_result.get("direct", {}).get(head)
    hazard = model_result.get("hazard", {}).get(head)
    disagreement = model_result.get("disagreement", {}).get(head)
    providers = len(record.get("providers") or {})

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 20, 1056, 112), 24, fill=PANEL, outline=GOLD, width=3)
    draw.text((52, 34), "GOOL LIVE", font=_font(36, True), fill=GOLD)
    draw.text((52, 78), "LOCAL FOOTBALL MODEL", font=_font(15, True), fill=TEXT)
    draw.rounded_rectangle((840, 38, 1026, 92), 16, outline=GREEN, width=2)
    draw.text((873, 53), "SIGNAL", font=_font(20, True), fill=GREEN)

    title = f"{home} — {away}"
    _center(draw, title, 150, _fit(draw, title, 920, 36, True), TEXT)
    _center(draw, f"{minute}'  •  {hs} : {as_}", 205, _font(31, True), CYAN)

    draw.rounded_rectangle((55, 275, 1025, 500), 28, fill=PANEL2, outline=GREEN, width=3)
    _center(draw, HEAD_LABELS.get(head, head), 305, _fit(draw, HEAD_LABELS.get(head, head), 850, 42, True), TEXT)
    _center(draw, f"{probability * 100:.1f}%", 365, _font(62, True), GREEN)
    if direct is not None and hazard is not None and disagreement is not None:
        _center(
            draw,
            f"DIRECT {float(direct)*100:.1f}%  •  HAZARD {float(hazard)*100:.1f}%  •  Δ {float(disagreement)*100:.1f}%",
            445,
            _font(21, True),
            MUTED,
        )

    draw.rounded_rectangle((55, 540, 1025, 755), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((85, 565), "LIVE STATS", font=_font(17, True), fill=MUTED)
    draw.text((85, 605), f"xG       {_pair(record, 'xg')}", font=_font(23, True), fill=TEXT)
    draw.text((85, 648), f"SOT      {_pair(record, 'shots_on_target')}", font=_font(23, True), fill=TEXT)
    draw.text((85, 691), f"SHOTS    {_pair(record, 'shots')}", font=_font(23, True), fill=TEXT)

    hr, ar = cards.get("home_red"), cards.get("away_red")
    red_text = "—" if hr is None or ar is None else f"{hr} — {ar}"
    draw.text((590, 565), "CONTEXT", font=_font(17, True), fill=MUTED)
    draw.text((590, 605), f"RED      {red_text}", font=_font(23, True), fill=RED if (hr or ar) else TEXT)
    draw.text((590, 648), f"SOURCES  {providers}", font=_font(23, True), fill=TEXT)
    draw.text((590, 691), "ENTRY    READY", font=_font(23, True), fill=GREEN)

    _center(draw, "GOOL AI • LIVE PROBABILITY SIGNAL", 825, _font(18, True), MUTED)
    output = BytesIO()
    img.save(output, "PNG", optimize=True)
    return output.getvalue()
