from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .multi_router import RouterDecision


ACCENT = (53, 214, 255)
GOLD = (255, 184, 48)
GREEN = (82, 220, 118)
RED = sc.RED
VIOLET = (190, 83, 255)
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


def _fmt_pct(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_signed(value: Any, suffix: str = "") -> str:
    try:
        return f"{float(value):+.1f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _money(value: Any, *, signed: bool = False) -> str:
    try:
        amount = int(round(float(value)))
    except (TypeError, ValueError):
        return "—"
    sign = ""
    if signed:
        sign = "+" if amount > 0 else ("−" if amount < 0 else "")
        amount = abs(amount)
    return f"{sign}{amount:,}".replace(",", " ") + " ₽"


def _fit_line(draw: ImageDraw.ImageDraw, text: str, width: int, size: int = 23, bold: bool = False):
    return sc._fit(draw, text, width, size, bold)


def _source(winner: Any) -> str:
    if not bool(getattr(winner, "expert_passed", True)) and bool(getattr(winner, "market_override", False)):
        return "MARKET OVERRIDE"
    if not bool(getattr(winner, "expert_passed", True)) and bool(getattr(winner, "value_override", False)):
        return "VALUE OVERRIDE"
    return "GOOL"


def _draw_team_names(draw: ImageDraw.ImageDraw, home: str, away: str, y: int) -> None:
    for x, name in ((175, home), (905, away)):
        font = _fit_line(draw, name, 330, 25, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, y), name, font=font, fill=TEXT)


def _draw_stats(draw: ImageDraw.ImageDraw, stats: dict[str, Any], y: int) -> None:
    draw.rounded_rectangle((45, y, 1035, y + 275), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((70, y + 20), "КЛЮЧЕВАЯ LIVE СТАТИСТИКА", font=sc._font(16, True), fill=MUTED)
    items = [
        ("xG / PROXY", stats.get("xg", "—")),
        ("УДАРЫ", stats.get("shots", "—")),
        ("В СТВОР", stats.get("sot", "—")),
        ("МОМЕНТЫ", stats.get("big_chances", "—")),
        ("УГЛОВЫЕ", stats.get("corners", "—")),
        ("ОПАСНЫЕ АТАКИ", stats.get("dangerous_attacks", "—")),
    ]
    for idx, (label, value) in enumerate(items):
        row, col = divmod(idx, 3)
        x = 70 + col * 320
        yy = y + 64 + row * 93
        draw.text((x, yy), label, font=sc._font(13, True), fill=MUTED)
        draw.text((x, yy + 29), str(value), font=_fit_line(draw, str(value), 275, 23, True), fill=TEXT)


def render_multi_card(
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    entry: dict[str, Any] | None = None,
) -> bytes:
    """Render the single BEST BET card used by GOOL Multi.

    The card is side-effect free. Telegram delivery is handled by multi_telegram.
    Flashscore team logos are reused through signal_cards, and the same record
    supplies the LIVE statistics shown to the user at entry time.
    """
    if decision.winner is None:
        raise ValueError("GOOL MULTI card requires a BET decision")

    match = record.get("match") or {}
    meta = sc.flashscore_meta(record)
    stats = sc.stats_snapshot(record)
    cards = record.get("cards") or {}
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or decision.minute or 0)
    hs = int(match.get("home_score") or decision.score[0])
    aws = int(match.get("away_score") or decision.score[1])
    winner = decision.winner
    source = _source(winner)
    mode = str((entry or {}).get("mode") or "shadow").lower()

    H = 1490
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
    phase = "ПЕРЕРЫВ" if match.get("is_halftime") else ("2-Й ТАЙМ" if minute >= 46 else "1-Й ТАЙМ")
    clock = phase if match.get("is_halftime") else f"{phase} • {minute}'"
    sc._center(draw, clock, 239, sc._font(19, True), ACCENT)
    _draw_team_names(draw, home, away, 302)
    sc._red_card_badge(draw, 175, 345, cards.get("home_red"))
    sc._red_card_badge(draw, 905, 345, cards.get("away_red"))

    draw.rounded_rectangle((45, 405, 1035, 625), 26, fill=PANEL2, outline=ACCENT, width=3)
    draw.text((72, 430), "🎯 BEST BET", font=sc._font(22, True), fill=ACCENT)
    draw.text((72, 478), winner.label, font=_fit_line(draw, winner.label, 580, 43, True), fill=TEXT)
    draw.text((72, 538), f"GOOL {winner.model_probability * 100:.1f}%  •  RATING {winner.rating:.0f}/100", font=sc._font(19, True), fill=GOLD)
    draw.text((72, 577), source, font=_fit_line(draw, source, 430, 17, True), fill=ACCENT if source == "GOOL" else GOLD)

    draw.text((760, 430), "КОЭФФИЦИЕНТ", font=sc._font(14, True), fill=MUTED)
    draw.text((760, 463), _fmt_odd(winner.odd), font=sc._font(52, True), fill=GREEN)
    fair = "—" if winner.market_probability is None else _fmt_pct(winner.market_probability, 1)
    draw.text((760, 535), f"1xBet fair {fair}", font=sc._font(16, True), fill=TEXT)
    if entry and entry.get("virtual_stake_rub") is not None:
        draw.text((760, 571), f"Ставка {_money(entry.get('virtual_stake_rub'))}", font=sc._font(16, True), fill=GOLD)

    _draw_stats(draw, stats, 655)

    draw.rounded_rectangle((45, 960, 1035, 1095), 20, fill=(8, 20, 31), outline=LINE, width=2)
    metrics = [
        ("VALUE", f"{winner.value_edge_pp:+.1f} п.п.", GREEN if winner.value_edge_pp >= 0 else RED),
        ("ROI MODEL", f"{winner.expected_roi * 100:+.1f}%", TEXT),
        ("DATA", f"{winner.data_quality * 100:.0f}/100", TEXT),
        ("STEAM", _fmt_signed(winner.market_pressure_pp, " п.п."), ACCENT if winner.market_pressure_pp >= 0 else RED),
    ]
    for idx, (label, value, color) in enumerate(metrics):
        x = 70 + idx * 245
        draw.text((x, 982), label, font=sc._font(13, True), fill=MUTED)
        draw.text((x, 1020), value, font=_fit_line(draw, value, 205, 22, True), fill=color)

    draw.rounded_rectangle((45, 1125, 1035, 1248), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 1146), "ПОЧЕМУ ВЫБРАН", font=sc._font(14, True), fill=MUTED)
    reason = str(decision.reason or "")
    draw.text((70, 1184), reason, font=_fit_line(draw, reason, 930, 22, True), fill=TEXT)

    draw.rounded_rectangle((45, 1277, 1035, 1400), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 1297), "БЛИЖАЙШИЕ АЛЬТЕРНАТИВЫ", font=sc._font(14, True), fill=MUTED)
    alternatives = list(decision.alternatives[:2])
    if not alternatives:
        draw.text((70, 1344), "Нет равнозначного рынка — BEST BET заметно сильнее.", font=sc._font(19, True), fill=TEXT)
    else:
        y = 1338
        for idx, row in enumerate(alternatives, 2):
            line = f"#{idx}  {row.label}  @{_fmt_odd(row.odd)}  •  {row.rating:.0f}/100"
            draw.text((72, y), line, font=_fit_line(draw, line, 915, 19, True), fill=TEXT)
            y += 38

    footer = "●  BEST BET • LIVE" if mode == "active" else "SHADOW • BEST BET PREVIEW"
    draw.rounded_rectangle((330, 1420, 750, 1472), 18, fill=ACCENT)
    sc._center(draw, footer, 1433, _fit_line(draw, footer, 380, 18, True), BG)
    return sc._save(img)


def render_multi_result_card(row: dict[str, Any], record: dict[str, Any] | None = None) -> bytes:
    """Render settlement for the exact Multi market that was sent at entry."""
    record = record or {}
    match = record.get("match") or {}
    result = str(row.get("result") or "void").lower()
    if result == "won":
        accent, banner = GREEN, "✓  СИГНАЛ ЗАШЁЛ"
    elif result == "lost":
        accent, banner = RED, "✕  СИГНАЛ НЕ ЗАШЁЛ"
    else:
        accent, banner = VIOLET, "↩  ВОЗВРАТ / VOID"

    home = str(row.get("home") or match.get("home") or "?")
    away = str(row.get("away") or match.get("away") or "?")
    league = str(row.get("league") or match.get("league") or "LIVE FOOTBALL")
    meta = dict(row.get("flashscore_meta") or sc.flashscore_meta(record) or {})
    stats = dict(row.get("settled_stats_snapshot") or row.get("stats_snapshot") or (sc.stats_snapshot(record) if record else {}) or {})
    cards = dict(row.get("settled_cards") or row.get("cards") or {})
    entry_score = list(row.get("score") or [0, 0])
    settled_score = list(row.get("settled_score") or [match.get("home_score", 0), match.get("away_score", 0)])
    entry_minute = int(row.get("minute") or 0)
    settled_minute = int(row.get("settled_minute") or match.get("minute") or 90)
    market = str(row.get("market") or "BEST BET")
    odd = row.get("odd")
    probability = row.get("probability")

    H = 1180
    img = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((24, 20, 1056, 105), 22, fill=PANEL, outline=accent, width=2)
    draw.text((50, 38), "GOOL MULTI", font=sc._font(31, True), fill=accent)
    draw.text((50, 76), "VERIFIED RESULT", font=sc._font(14, True), fill=TEXT)
    draw.text((640, 48), league, font=_fit_line(draw, league, 370, 18, True), fill=MUTED)

    sc._badge(img, draw, 175, 245, sc._logo(meta, "home"), home, accent)
    sc._badge(img, draw, 905, 245, sc._logo(meta, "away"), away, accent)
    draw.rounded_rectangle((397, 165, 683, 305), 25, fill=(9, 19, 31), outline=accent, width=3)
    sc._center(draw, f"{int(settled_score[0])} : {int(settled_score[1])}", 189, sc._font(58, True), TEXT)
    sc._center(draw, f"{settled_minute}'", 257, sc._font(20, True), accent)
    _draw_team_names(draw, home, away, 335)
    sc._red_card_badge(draw, 175, 376, cards.get("home_red"))
    sc._red_card_badge(draw, 905, 376, cards.get("away_red"))

    draw.rounded_rectangle((55, 425, 1025, 570), 28, fill=PANEL2, outline=accent, width=3)
    sc._center(draw, banner, 460, _fit_line(draw, banner, 850, 40, True), accent)
    detail = f"{market}  @{_fmt_odd(odd)}  •  GOOL {_fmt_pct(probability, 1)}"
    sc._center(draw, detail, 522, _fit_line(draw, detail, 900, 21, True), TEXT)

    sc._box(draw, (55, 600, 510, 710), "ВХОД", f"{entry_minute}' • {int(entry_score[0])}:{int(entry_score[1])}")
    sc._box(draw, (570, 600, 1025, 710), "РАСЧЁТ", f"{settled_minute}' • {int(settled_score[0])}:{int(settled_score[1])}", accent=accent)

    stake = row.get("virtual_stake_rub")
    profit = row.get("virtual_profit_rub")
    draw.rounded_rectangle((55, 740, 1025, 845), 20, fill=(8, 20, 31), outline=LINE, width=2)
    draw.text((85, 760), "СТАВКА", font=sc._font(13, True), fill=MUTED)
    draw.text((85, 793), _money(stake), font=sc._font(23, True), fill=TEXT)
    draw.text((385, 760), "P/L", font=sc._font(13, True), fill=MUTED)
    draw.text((385, 793), _money(profit, signed=True), font=sc._font(23, True), fill=accent)
    draw.text((685, 760), "ИСТОЧНИК", font=sc._font(13, True), fill=MUTED)
    draw.text((685, 793), str(row.get("signal_source") or "GOOL"), font=_fit_line(draw, str(row.get("signal_source") or "GOOL"), 300, 18, True), fill=GOLD)

    _draw_stats(draw, stats, 875)
    return sc._save(img)
