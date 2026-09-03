from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .multi_router import RouterDecision


ACCENT = (53, 214, 255)
GOLD = (255, 184, 48)
GREEN = (82, 220, 118)
BG = sc.BG
PANEL = sc.PANEL
PANEL2 = sc.PANEL2
TEXT = sc.TEXT
MUTED = sc.MUTED
LINE = sc.LINE
W = sc.W


def _fmt_odd(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _fit_line(draw: ImageDraw.ImageDraw, text: str, width: int, size: int = 23, bold: bool = False):
    return sc._fit(draw, text, width, size, bold)


def render_multi_card(record: dict[str, Any], decision: RouterDecision) -> bytes:
    """Render a unified GOOL MULTI card.

    This renderer is deliberately side-effect free: it creates PNG bytes only.
    The feature branch does not broadcast the card to Telegram.
    """
    if decision.winner is None:
        raise ValueError("GOOL MULTI card requires a BET decision")

    match = record.get("match") or {}
    meta = sc.flashscore_meta(record)
    cards = record.get("cards") or {}
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or decision.minute or 0)
    hs = int(match.get("home_score") or decision.score[0])
    aws = int(match.get("away_score") or decision.score[1])
    winner = decision.winner

    H = 1260
    img = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((24, 20, 1056, 92), 22, fill=PANEL, outline=ACCENT, width=2)
    draw.text((50, 37), league, font=_fit_line(draw, league, 720, 24, True), fill=TEXT)
    draw.rounded_rectangle((818, 31, 1028, 80), 14, fill=(7, 31, 42), outline=ACCENT, width=2)
    draw.text((850, 44), "GOOL MULTI", font=sc._font(18, True), fill=ACCENT)

    sc._badge(img, draw, 175, 215, sc._logo(meta, "home"), home, ACCENT)
    sc._badge(img, draw, 905, 215, sc._logo(meta, "away"), away, ACCENT)
    draw.rounded_rectangle((397, 145, 683, 285), 25, fill=(9, 19, 31), outline=ACCENT, width=3)
    sc._center(draw, f"{hs} : {aws}", 169, sc._font(58, True), TEXT)
    phase = "2-Й ТАЙМ" if minute >= 46 else "1-Й ТАЙМ"
    sc._center(draw, f"{phase} • {minute}'", 239, sc._font(19, True), ACCENT)

    for x, name in ((175, home), (905, away)):
        font = _fit_line(draw, name, 330, 25, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, 302), name, font=font, fill=TEXT)
    sc._red_card_badge(draw, 175, 345, cards.get("home_red"))
    sc._red_card_badge(draw, 905, 345, cards.get("away_red"))

    draw.rounded_rectangle((45, 405, 1035, 615), 26, fill=PANEL2, outline=ACCENT, width=3)
    draw.text((72, 430), "🎯 ЛУЧШИЙ РЫНОК", font=sc._font(22, True), fill=ACCENT)
    draw.text((72, 477), winner.label, font=_fit_line(draw, winner.label, 610, 43, True), fill=TEXT)
    draw.text((72, 538), f"GOOL RATING {winner.rating:.0f}/100", font=sc._font(20, True), fill=GOLD)

    draw.text((770, 430), "КОЭФФИЦИЕНТ", font=sc._font(14, True), fill=MUTED)
    draw.text((770, 463), _fmt_odd(winner.odd), font=sc._font(52, True), fill=GREEN)
    draw.text((770, 536), f"MODEL {_fmt_pct(winner.model_probability)}", font=sc._font(17, True), fill=TEXT)
    if winner.push_probability > 0.01:
        draw.text((770, 565), f"ВОЗВРАТ {_fmt_pct(winner.push_probability)}", font=sc._font(15, True), fill=MUTED)

    draw.rounded_rectangle((45, 645, 1035, 780), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 665), "ПОЧЕМУ ВЫБРАН", font=sc._font(15, True), fill=MUTED)
    reason = str(decision.reason or "")
    font = _fit_line(draw, reason, 930, 24, True)
    draw.text((70, 705), reason, font=font, fill=TEXT)

    draw.rounded_rectangle((45, 810, 1035, 1005), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 832), "АЛЬТЕРНАТИВЫ ЭТОГО МАТЧА", font=sc._font(15, True), fill=MUTED)
    alternatives = list(decision.alternatives[:3])
    if not alternatives:
        draw.text((70, 885), "Нет равнозначной альтернативы — текущий рынок заметно сильнее.", font=sc._font(20, True), fill=TEXT)
    else:
        y = 878
        for idx, row in enumerate(alternatives, 1):
            draw.text((72, y), f"#{idx + 1}", font=sc._font(16, True), fill=MUTED)
            draw.text((125, y - 2), row.label, font=_fit_line(draw, row.label, 420, 22, True), fill=TEXT)
            draw.text((575, y - 2), f"@{_fmt_odd(row.odd)}", font=sc._font(21, True), fill=ACCENT)
            draw.text((760, y - 2), f"{row.rating:.0f}/100", font=sc._font(21, True), fill=GOLD)
            y += 48

    draw.rounded_rectangle((45, 1035, 1035, 1155), 20, fill=(8, 20, 31), outline=LINE, width=2)
    draw.text((70, 1055), "VALUE", font=sc._font(14, True), fill=MUTED)
    draw.text((70, 1084), f"{winner.value_edge_pp:+.1f} п.п.", font=sc._font(24, True), fill=GREEN if winner.value_edge_pp >= 0 else sc.RED)
    draw.text((330, 1055), "ROI MODEL", font=sc._font(14, True), fill=MUTED)
    draw.text((330, 1084), f"{winner.expected_roi * 100:+.1f}%", font=sc._font(24, True), fill=TEXT)
    draw.text((605, 1055), "DATA QUALITY", font=sc._font(14, True), fill=MUTED)
    draw.text((605, 1084), f"{winner.data_quality * 100:.0f}/100", font=sc._font(24, True), fill=TEXT)
    draw.text((835, 1055), "STEAM", font=sc._font(14, True), fill=MUTED)
    draw.text((835, 1084), f"+{winner.market_pressure_pp:.1f} п.п.", font=sc._font(24, True), fill=TEXT)

    draw.rounded_rectangle((330, 1180, 750, 1238), 18, fill=ACCENT)
    sc._center(draw, "SHADOW • НЕ ОТПРАВЛЯЕТСЯ", 1195, sc._font(18, True), BG)
    return sc._save(img)
