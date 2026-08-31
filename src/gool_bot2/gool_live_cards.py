from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc


LIVE_THEMES = {
    "two_more_goals": ((255, 184, 48), "ЕЩЁ +2 ГОЛА", "Ещё минимум два гола"),
    "both_teams_to_score": ((190, 83, 255), "ОБЕ ЗАБЬЮТ — ДА", "Незабившая команда забьёт"),
}


def _save(img: Image.Image) -> bytes:
    out = BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def render_gool_live_signal_card(
    record: dict[str, Any],
    head: str,
    confidence: float,
    pressure: float,
    cards: dict[str, Any],
) -> bytes:
    accent, label, subtitle = LIVE_THEMES.get(head, LIVE_THEMES["two_more_goals"])
    match = record.get("match") or {}
    meta = sc.flashscore_meta(record)
    stats = sc.stats_snapshot(record)
    match_id = str(match.get("flashscore_event_id") or "")
    sc._remember(match_id, meta, stats)

    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    providers = len(record.get("providers") or {})
    momentum = record.get("live_momentum") or {}

    img = Image.new("RGBA", (1080, 1180), sc.BG + (255,))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((24, 20, 1056, 112), 24, fill=sc.PANEL, outline=accent, width=2)
    draw.text((52, 36), "GOOL 2", font=sc._font(36, True), fill=accent)
    draw.text((52, 78), "GOOL LIVE • MOMENTUM 5m / 10m", font=sc._font(15, True), fill=sc.TEXT)
    draw.rounded_rectangle((830, 38, 1028, 93), 16, outline=accent, width=2)
    draw.text((876, 53), "LIVE", font=sc._font(20, True), fill=accent)

    sc._center(draw, label, 142, sc._fit(draw, label, 850, 38, True), accent)
    sc._center(draw, league, 194, sc._fit(draw, league, 900, 19, False), sc.MUTED)

    sc._badge(img, draw, 180, 330, sc._logo(meta, "home"), home, accent)
    sc._badge(img, draw, 900, 330, sc._logo(meta, "away"), away, accent)
    draw.rounded_rectangle((390, 250, 690, 413), 28, fill=(9, 19, 31), outline=accent, width=3)
    sc._center(draw, f"{hs} : {aws}", 289, sc._font(66, True), sc.TEXT)
    sc._center(draw, "ПЕРЕРЫВ" if match.get("is_halftime") else f"{minute}'", 365, sc._font(25, True), accent)

    for x, name in ((180, home), (900, away)):
        font = sc._fit(draw, name, 330, 29)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, 440), name, font=font, fill=sc.TEXT)

    draw.rounded_rectangle((55, 500, 1025, 655), 25, fill=sc.PANEL2, outline=accent, width=2)
    draw.text((85, 525), "НЕЗАВИСИМЫЙ GOOL LIVE АНАЛИЗ", font=sc._font(18, True), fill=sc.MUTED)
    draw.text((85, 565), subtitle, font=sc._fit(draw, subtitle, 610, 29, True), fill=sc.TEXT)
    draw.text((780, 520), "CONFIDENCE", font=sc._font(13, True), fill=sc.MUTED)
    draw.text((792, 555), f"{confidence * 100:.0f}%", font=sc._font(40, True), fill=accent)

    sc._box(draw, (55, 685, 285, 790), "GOOL PRESSURE", f"{pressure:.2f}", accent=accent)
    sc._box(draw, (300, 685, 530, 790), "xG", str(stats.get("xg") or "—"))
    sc._box(draw, (545, 685, 775, 790), "УДАРЫ", str(stats.get("shots") or "—"))
    sc._box(draw, (790, 685, 1025, 790), "В СТВОР", str(stats.get("sot") or "—"))

    shots5 = momentum.get("shots_total_last_5m")
    sot5 = momentum.get("sot_total_last_5m")
    xg5 = momentum.get("xg_total_last_5m")
    shots10 = momentum.get("shots_total_last_10m")
    sc._box(draw, (55, 820, 285, 925), "УДАРЫ 5М", "—" if shots5 is None else f"{shots5:.0f}")
    sc._box(draw, (300, 820, 530, 925), "СТВОР 5М", "—" if sot5 is None else f"{sot5:.0f}")
    sc._box(draw, (545, 820, 775, 925), "xG 5М", "—" if xg5 is None else f"{xg5:.2f}")
    sc._box(draw, (790, 820, 1025, 925), "УДАРЫ 10М", "—" if shots10 is None else f"{shots10:.0f}")

    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    cardline = f"КАРТОЧКИ  🟨 {'—' if hy is None or ay is None else f'{hy}:{ay}'}   🟥 {'—' if hr is None or ar is None else f'{hr}:{ar}'}"
    sc._center(draw, cardline, 965, sc._font(16, True), sc.MUTED)
    sc._center(draw, f"ИСТОЧНИКИ: {providers}/3", 1010, sc._font(16, True), sc.MUTED)
    draw.rounded_rectangle((360, 1055, 720, 1120), 18, fill=accent)
    sc._center(draw, "●  В ИГРЕ", 1072, sc._font(24, True), sc.BG)
    return _save(img)


def render_two_more_signal_card(record: dict[str, Any], confidence: float, pressure: float, cards: dict[str, Any]) -> bytes:
    return render_gool_live_signal_card(record, "two_more_goals", confidence, pressure, cards)


def render_gool_live_result_card(row: dict[str, Any], result: str) -> bytes:
    head = str(row.get("head") or "two_more_goals")
    _, label, _ = LIVE_THEMES.get(head, LIVE_THEMES["two_more_goals"])
    won = str(result).lower() == "won"
    accent = (82, 220, 118) if won else sc.RED
    score = row.get("settled_score") or [0, 0]
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    minute = int(row.get("settled_minute") or 0)
    confidence = float(row.get("probability") or 0.0)

    img = Image.new("RGBA", (1080, 760), sc.BG + (255,))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 20, 1056, 112), 24, fill=sc.PANEL, outline=accent, width=2)
    draw.text((52, 36), "GOOL 2", font=sc._font(36, True), fill=accent)
    draw.text((52, 78), "GOOL LIVE RESULT", font=sc._font(15, True), fill=sc.TEXT)
    sc._center(draw, label, 150, sc._fit(draw, label, 850, 38, True), accent)
    sc._center(draw, f"{home} — {away}", 220, sc._fit(draw, f"{home} — {away}", 900, 30, True), sc.TEXT)
    sc._center(draw, f"{int(score[0] or 0)} : {int(score[1] or 0)}", 300, sc._font(68, True), sc.TEXT)
    sc._center(draw, f"{minute}'", 385, sc._font(28, True), accent)
    draw.rounded_rectangle((80, 455, 1000, 620), 28, fill=sc.PANEL2, outline=accent, width=3)
    sc._center(draw, "✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ", 495, sc._font(40, True), accent)
    sc._center(draw, f"{label} • confidence {confidence * 100:.0f}%", 555, sc._fit(draw, f"{label} • confidence {confidence * 100:.0f}%", 850, 22, True), sc.TEXT)
    return _save(img)


def render_two_more_result_card(row: dict[str, Any], result: str) -> bytes:
    return render_gool_live_result_card(row, result)
