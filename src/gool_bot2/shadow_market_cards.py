from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 720
BG = (6, 10, 18)
PANEL = (16, 24, 38)
TEXT = (247, 249, 252)
MUTED = (156, 170, 190)
GREEN = (82, 220, 118)
RED = (239, 74, 83)
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


def _save(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _team_target(head: str, selected_side: str | None, hs: int, aws: int, team: Any) -> tuple[str, str]:
    if head != "team_to_score":
        return THEMES.get(head, THEMES["team_to_score"])[1], str(team or "")
    side = "home" if selected_side == "home" else "away"
    goals = hs if side == "home" else aws
    line = goals + 0.5
    prefix = "ИТБ1" if side == "home" else "ИТБ2"
    label = "КОМАНДА ЗАБЬЁТ" if goals == 0 else "КОМАНДА ЗАБЬЁТ ЕЩЁ 1"
    detail = f"{team or 'Команда'} • {prefix} {line:g}"
    return label, detail


def render_shadow_market_card(record: dict[str, Any], analysis: dict[str, Any]) -> bytes:
    match = record.get("match") or {}
    head = str(analysis.get("head") or "team_to_score")
    accent, _ = THEMES.get(head, THEMES["team_to_score"])
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    confidence = analysis.get("confidence_score")
    team = analysis.get("team")
    pressure = analysis.get("pressure_score")
    label, team_detail = _team_target(head, analysis.get("selected_side") or analysis.get("target_side"), hs, aws, team)

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 24, 1050, 100), 22, fill=PANEL, outline=accent, width=3)
    draw.text((55, 45), league, font=_font(24, True), fill=TEXT)
    draw.rounded_rectangle((875, 36, 1020, 86), 15, fill=BG, outline=accent, width=2)
    draw.text((920, 49), "LIVE", font=_font(18, True), fill=accent)

    _center(draw, f"{home}  {hs} : {aws}  {away}", 160, _font(36, True), TEXT)
    period = "2-Й ТАЙМ" if minute >= 46 else "1-Й ТАЙМ"
    _center(draw, f"{period} • {minute}'", 215, _font(24, True), accent)

    draw.rounded_rectangle((70, 285, 1010, 465), 28, fill=PANEL, outline=accent, width=3)
    _center(draw, label, 315, _font(34, True), accent)
    if head == "team_to_score" and team:
        _center(draw, team_detail, 368, _font(28, True), TEXT)
    elif head == "both_teams_to_score":
        _center(draw, "обе команды забьют", 368, _font(26, True), TEXT)
    strength = "—" if confidence is None else f"{float(confidence) * 100:.0f}/100"
    _center(draw, f"СИЛА СИГНАЛА: {strength}", 420, _font(22, True), MUTED)

    draw.rounded_rectangle((70, 515, 1010, 635), 24, fill=PANEL)
    if pressure is not None:
        draw.text((95, 540), f"LIVE pressure: {float(pressure):.2f}", font=_font(20, True), fill=accent)
    draw.text((95, 585), "GOOL LIVE • PREMATCH + LIVE подтверждение", font=_font(18, True), fill=TEXT)
    _center(draw, "GOOL Bot 2", 670, _font(16, True), MUTED)
    return _save(image)


def render_shadow_market_result_card(row: dict[str, Any]) -> bytes:
    head = str(row.get("head") or "team_to_score")
    base_accent, _ = THEMES.get(head, THEMES["team_to_score"])
    won = str(row.get("result") or "lost").lower() == "won"
    accent = GREEN if won else RED
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or "LIVE FOOTBALL")
    entry = row.get("score") or [0, 0]
    settled = row.get("settled_score") or entry
    minute = int(row.get("settled_minute") or row.get("minute") or 0)
    entry_minute = int(row.get("minute") or 0)
    team = row.get("team")
    label, team_detail = _team_target(head, row.get("selected_side"), int(entry[0]), int(entry[1]), team)

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 24, 1050, 100), 22, fill=PANEL, outline=base_accent, width=3)
    draw.text((55, 45), league, font=_font(24, True), fill=TEXT)
    _center(draw, f"{home}  {int(settled[0])} : {int(settled[1])}  {away}", 155, _font(36, True), TEXT)
    _center(draw, f"{minute}'", 210, _font(24, True), base_accent)

    draw.rounded_rectangle((70, 285, 1010, 465), 28, fill=PANEL, outline=accent, width=3)
    _center(draw, "✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ", 315, _font(38, True), accent)
    _center(draw, label, 370, _font(28, True), base_accent)
    if head == "team_to_score" and team:
        _center(draw, team_detail, 415, _font(22, True), TEXT)

    draw.rounded_rectangle((70, 515, 1010, 635), 24, fill=PANEL)
    draw.text((95, 535), f"Вход: {entry_minute}' • {int(entry[0])}:{int(entry[1])}", font=_font(20, True), fill=TEXT)
    draw.text((95, 580), f"Результат: {minute}' • {int(settled[0])}:{int(settled[1])}", font=_font(20, True), fill=accent)
    _center(draw, "GOOL Bot 2", 670, _font(16, True), MUTED)
    return _save(image)
