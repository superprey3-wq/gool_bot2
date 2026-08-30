from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W = 1080
BG = (5, 10, 18)
PANEL = (13, 22, 36)
PANEL2 = (19, 31, 49)
TEXT = (247, 249, 252)
MUTED = (151, 166, 188)
GREEN = (82, 220, 118)
RED = (244, 104, 104)
GOLD = (255, 184, 48)
CYAN = (61, 178, 255)
LINE = (45, 63, 88)

HEAD_LABELS = {
    "another_goal": "ЕЩЁ ГОЛ",
    "goal_before_ht": "ГОЛ ДО ПЕРЕРЫВА",
    "over_2_5": "ТОТАЛ БОЛЬШЕ 2.5",
    "both_teams_to_score": "ОБЕ ЗАБЬЮТ",
}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 42, bold: bool = True) -> ImageFont.ImageFont:
    text = str(text or "")
    for size in range(start, 17, -2):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= width:
            return font
    return _font(18, bold)


def _save(img: Image.Image) -> bytes:
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _safe_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def render_signal_card(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> bytes:
    match = record.get("match") or {}
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    title = f"{home} — {away}"
    label = HEAD_LABELS.get(head, head.upper())
    providers = len(record.get("providers") or {})

    img = Image.new("RGB", (W, 1040), BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((24, 20, 1056, 116), 24, fill=PANEL, outline=CYAN, width=3)
    draw.text((52, 35), "GOOL BOT 2", font=_font(36, True), fill=CYAN)
    draw.text((52, 79), "LIVE SIGNAL", font=_font(16, True), fill=TEXT)
    draw.rounded_rectangle((840, 38, 1026, 94), 16, outline=GREEN, width=2)
    draw.text((883, 53), "SIGNAL", font=_font(20, True), fill=GREEN)

    _center(draw, title, 160, _fit(draw, title, 940, 38, True), TEXT)
    _center(draw, f"{minute}'  •  {hs}:{aws}", 220, _font(31, True), CYAN)

    draw.rounded_rectangle((55, 300, 1025, 565), 28, fill=PANEL2, outline=GOLD, width=3)
    _center(draw, label, 335, _fit(draw, label, 860, 46, True), TEXT)
    _center(draw, f"ВЕРОЯТНОСТЬ  {probability * 100:.1f}%", 410, _font(44, True), GREEN)

    bar_x1, bar_y1, bar_x2, bar_y2 = 145, 492, 935, 520
    draw.rounded_rectangle((bar_x1, bar_y1, bar_x2, bar_y2), 14, fill=LINE)
    filled = int(bar_x1 + (bar_x2 - bar_x1) * max(0.0, min(1.0, float(probability))))
    if filled > bar_x1:
        draw.rounded_rectangle((bar_x1, bar_y1, filled, bar_y2), 14, fill=GREEN)

    draw.rounded_rectangle((55, 610, 1025, 830), 22, fill=PANEL, outline=LINE, width=2)
    if head in {"over_2_5", "both_teams_to_score"}:
        draw.text((85, 642), "МОДЕЛЬ", font=_font(16, True), fill=MUTED)
        draw.text((85, 680), f"HT model  {_safe_pct(model_result.get('football_data', {}).get(head))}", font=_font(27, True), fill=TEXT)
    else:
        direct = model_result.get("direct", {}).get(head)
        hazard = model_result.get("hazard", {}).get(head)
        disagreement = model_result.get("disagreement", {}).get(head)
        draw.text((85, 642), "МОДЕЛИ", font=_font(16, True), fill=MUTED)
        draw.text((85, 680), f"Direct  {_safe_pct(direct)}", font=_font(25, True), fill=TEXT)
        draw.text((545, 680), f"Hazard  {_safe_pct(hazard)}", font=_font(25, True), fill=TEXT)
        draw.text((85, 728), f"Расхождение  {_safe_pct(disagreement)}", font=_font(23, True), fill=GREEN if float(disagreement or 0) <= 0.10 else GOLD)

    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    yellow = "? : ?" if hy is None or ay is None else f"{hy}:{ay}"
    red = "? : ?" if hr is None or ar is None else f"{hr}:{ar}"
    draw.text((85, 780), f"Карточки   🟨 {yellow}   🟥 {red}", font=_font(21, True), fill=MUTED)

    draw.rounded_rectangle((240, 875, 840, 950), 22, fill=PANEL2, outline=GREEN, width=2)
    _center(draw, "🎯 ВХОД", 892, _font(31, True), GREEN)
    _center(draw, f"Источников данных: {providers}", 978, _font(17, True), MUTED)

    return _save(img)


def render_result_card(row: dict[str, Any], result: str, minute: int, home_score: int, away_score: int) -> bytes:
    won = str(result).lower() == "won"
    accent = GREEN if won else RED
    status = "✅ СИГНАЛ ЗАШЁЛ" if won else "❌ СИГНАЛ НЕ ЗАШЁЛ"
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    title = f"{home} — {away}"
    head = str(row.get("head") or "")
    label = HEAD_LABELS.get(head, head.upper())
    entry_score = row.get("score") or [0, 0]
    entry_minute = int(row.get("minute") or 0)
    probability = float(row.get("probability") or 0.0)

    img = Image.new("RGB", (W, 900), BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((24, 20, 1056, 116), 24, fill=PANEL, outline=accent, width=3)
    draw.text((52, 35), "GOOL BOT 2", font=_font(36, True), fill=accent)
    draw.text((52, 79), "RESULT CARD", font=_font(16, True), fill=TEXT)

    _center(draw, status, 155, _font(44, True), accent)
    _center(draw, title, 235, _fit(draw, title, 920, 36, True), TEXT)
    _center(draw, f"{minute}'  •  {home_score}:{away_score}", 292, _font(30, True), CYAN)

    draw.rounded_rectangle((55, 370, 1025, 570), 28, fill=PANEL2, outline=accent, width=3)
    _center(draw, label, 405, _fit(draw, label, 860, 40, True), TEXT)
    _center(draw, f"P на входе  {probability * 100:.1f}%", 475, _font(31, True), GOLD)

    draw.rounded_rectangle((55, 620, 1025, 770), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((85, 645), "ВХОД", font=_font(16, True), fill=MUTED)
    draw.text((85, 683), f"{entry_minute}' • {entry_score[0]}:{entry_score[1]}", font=_font(27, True), fill=TEXT)
    draw.text((585, 645), "РЕЗУЛЬТАТ", font=_font(16, True), fill=MUTED)
    draw.text((585, 683), f"{minute}' • {home_score}:{away_score}", font=_font(27, True), fill=accent)

    _center(draw, "GOOL AI • VERIFIED LIVE RESULT", 835, _font(18, True), MUTED)
    return _save(img)
