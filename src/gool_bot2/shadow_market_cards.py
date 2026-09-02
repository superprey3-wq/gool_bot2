from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 720
BG = (6, 10, 18)
PANEL = (16, 24, 38)
TEXT = (247, 249, 252)
MUTED = (156, 170, 190)
THEMES = {
    "both_teams_to_score": ((179, 92, 255), "ОБЕ ЗАБЬЮТ — ДА"),
    "team_to_score": ((52, 190, 255), "КОМАНДА ЗАБЬЁТ"),
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


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)


def render_shadow_market_card(record: dict[str, Any], analysis: dict[str, Any]) -> bytes:
    match = record.get("match") or {}
    head = str(analysis.get("head") or "team_to_score")
    accent, label = THEMES.get(head, THEMES["team_to_score"])
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    confidence = analysis.get("confidence_score")
    team = analysis.get("team")

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 24, 1050, 100), 22, fill=PANEL, outline=accent, width=3)
    draw.text((55, 45), league, font=_font(24, True), fill=TEXT)
    draw.rounded_rectangle((875, 36, 1020, 86), 15, fill=BG, outline=accent, width=2)
    draw.text((914, 49), "SHADOW", font=_font(16, True), fill=accent)

    _center(draw, f"{home}  {hs} : {aws}  {away}", 160, _font(36, True), TEXT)
    _center(draw, f"{minute}'", 215, _font(24, True), accent)

    draw.rounded_rectangle((70, 285, 1010, 455), 28, fill=PANEL, outline=accent, width=3)
    _center(draw, label, 315, _font(34, True), accent)
    if head == "team_to_score" and team:
        _center(draw, str(team), 365, _font(30, True), TEXT)
    elif head == "both_teams_to_score":
        _center(draw, "обе команды должны забить", 365, _font(24, True), TEXT)
    strength = "—" if confidence is None else f"{float(confidence) * 100:.0f}/100"
    _center(draw, f"ТЕСТОВАЯ СИЛА: {strength}", 415, _font(22, True), MUTED)

    draw.rounded_rectangle((70, 505, 1010, 635), 24, fill=PANEL)
    draw.text((95, 530), "Режим:", font=_font(18, True), fill=MUTED)
    draw.text((205, 530), "журнал / тест без Telegram-сигнала", font=_font(18, True), fill=TEXT)
    pressure = analysis.get("pressure_score")
    if pressure is not None:
        draw.text((95, 575), f"LIVE pressure: {float(pressure):.2f}", font=_font(18, True), fill=accent)
    _center(draw, "GOOL Bot 2 · SHADOW MARKET", 670, _font(16, True), MUTED)

    buf = BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()
