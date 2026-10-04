from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc


BG = (5, 10, 18)
PANEL = (13, 22, 36)
PANEL2 = (19, 31, 49)
TEXT = (247, 249, 252)
MUTED = (151, 166, 188)
LINE = (45, 63, 88)
GOLD = (255, 184, 48)
GREEN = (82, 220, 118)
BLUE = (55, 166, 255)


def _value_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], title: str, value: str, accent: tuple[int, int, int] = TEXT) -> None:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, 18, fill=PANEL, outline=LINE, width=2)
    draw.text((x1 + 18, y1 + 14), title, font=sc._font(14, True), fill=MUTED)
    draw.text((x1 + 18, y1 + 46), value, font=sc._fit(draw, value, x2 - x1 - 36, 27, True), fill=accent)


def render_multisport_steam_card(row: dict[str, Any], signal: dict[str, Any], cfg: Any) -> bytes:
    """Render a GOOL-style image card for hockey/basketball 1xBet STEAM."""
    accent = BLUE if str(getattr(cfg, "key", "")) == "basketball" else GREEN
    width, height = 1080, 920
    image = Image.new("RGBA", (width, height), BG + (255,))
    draw = ImageDraw.Draw(image)

    score = list(row.get("score") or [0, 0])
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or "LIVE")
    strength = "EXTREME" if bool(signal.get("extreme")) else "STRONG"
    strength_score = float(signal.get("strength") or 0.0)
    direction = str(signal.get("direction") or "over")
    sport_title = str(getattr(cfg, "title", "STEAM"))
    end = dict(signal.get("end") or {})

    draw.rounded_rectangle((24, 20, 1056, 92), 22, fill=PANEL, outline=accent, width=2)
    draw.text((48, 39), f"GOOL • 1xBET • {sport_title}", font=sc._font(24, True), fill=TEXT)
    draw.rounded_rectangle((808, 32, 1028, 80), 14, fill=(8, 35, 24), outline=GREEN, width=2)
    draw.text((838, 46), "FLASHSCORE LIVE", font=sc._font(16, True), fill=GREEN)

    draw.text((48, 116), league, font=sc._fit(draw, league, 984, 20, False), fill=MUTED)
    draw.rounded_rectangle((44, 160, 1036, 350), 28, fill=PANEL2, outline=accent, width=3)
    draw.text((75, 190), home, font=sc._fit(draw, home, 330, 28, True), fill=TEXT)
    draw.text((75, 292), away, font=sc._fit(draw, away, 330, 28, True), fill=TEXT)
    draw.text((455, 187), str(int(score[0])), font=sc._font(70, True), fill=TEXT)
    draw.text((525, 190), ":", font=sc._font(58, True), fill=MUTED)
    draw.text((580, 187), str(int(score[1])), font=sc._font(70, True), fill=TEXT)

    period = str(row.get("period") or "LIVE")
    clock = row.get("clock_seconds")
    if clock is not None:
        seconds = max(0, int(clock))
        period = f"{period} • {seconds // 60:02d}:{seconds % 60:02d}"
    draw.text((455, 285), period, font=sc._fit(draw, period, 310, 22, True), fill=accent)

    draw.rounded_rectangle((780, 188, 1004, 322), 20, fill=(9, 19, 31), outline=GOLD, width=2)
    draw.text((811, 208), "СИЛА ПРОГРУЗА", font=sc._font(14, True), fill=MUTED)
    draw.text((819, 242), strength, font=sc._fit(draw, strength, 160, 28, True), fill=GOLD)
    draw.text((846, 282), f"{strength_score:.0f}/100", font=sc._font(18, True), fill=TEXT)

    line = float(signal.get("line") or end.get("line") or row.get("line") or 0.0)
    odd = float(signal.get("odd") or end.get(direction) or row.get(direction) or 0.0)
    market_label = ("ТБ" if direction == "over" else "ТМ") + f" {line:g}"
    draw.rounded_rectangle((44, 385, 1036, 525), 24, fill=PANEL2, outline=GOLD, width=3)
    draw.text((72, 410), "1xBET LIVE TOTAL", font=sc._font(16, True), fill=MUTED)
    draw.text((72, 454), market_label, font=sc._font(40, True), fill=GOLD)
    draw.text((680, 410), "КОЭФФИЦИЕНТ", font=sc._font(16, True), fill=MUTED)
    draw.text((680, 452), f"{odd:.2f}", font=sc._font(42, True), fill=TEXT)

    metric_delta = float(signal.get("metric_delta") or 0.0)
    probability_delta = float(signal.get("probability_delta_pp") or 0.0)
    line_delta = float(signal.get("line_delta") or 0.0)
    moves = int(signal.get("moves") or 0)
    age = float(signal.get("age_seconds") or 0.0)

    _value_box(draw, (44, 560, 278, 675), "ДАВЛЕНИЕ", f"+{metric_delta:.2f}", GOLD)
    _value_box(draw, (296, 560, 530, 675), "Δ ВЕРОЯТНОСТИ", f"{probability_delta:+.1f} п.п.", accent)
    _value_box(draw, (548, 560, 782, 675), "СДВИГ ЛИНИИ", f"{line_delta:+.1f}", TEXT)
    _value_box(draw, (800, 560, 1036, 675), "ИМПУЛЬСЫ", f"{moves}x", TEXT)

    fs_id = str(row.get("flashscore_event_id") or "—")
    map_score = float(row.get("flashscore_match_score") or 0.0)
    draw.rounded_rectangle((44, 710, 1036, 820), 20, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 730), "ПРОВЕРКА ИСТОЧНИКОВ", font=sc._font(15, True), fill=MUTED)
    draw.text((70, 770), f"Flashscore ✓  •  1xBet ✓  •  счёт синхронен ✓  •  окно {age:.0f} сек", font=sc._font(21, True), fill=GREEN)
    draw.text((70, 800), f"FS match {fs_id}  •  mapping {map_score:.0%}", font=sc._font(14, False), fill=MUTED)

    draw.rounded_rectangle((285, 848, 795, 900), 16, fill=accent)
    sc._center(draw, "ПРОГРУЗ ПОДТВЕРЖДЁН", 860, sc._font(20, True), BG)
    return sc._save(image)


def render_multisport_result_card(row: dict[str, Any], cfg: Any) -> bytes:
    result = str(row.get("result") or "").lower()
    won = result == "won"
    void = result == "void"
    accent = GREEN if won else (GOLD if void else (220, 76, 76))
    width, height = 1080, 650
    image = Image.new("RGBA", (width, height), BG + (255,))
    draw = ImageDraw.Draw(image)
    score = list(row.get("settled_score") or row.get("score") or [0, 0])
    title = "✅ ПРОГРУЗ ЗАШЁЛ" if won else ("↩️ ВОЗВРАТ" if void else "❌ ПРОГРУЗ НЕ ЗАШЁЛ")
    draw.rounded_rectangle((24, 20, 1056, 100), 22, fill=PANEL, outline=accent, width=3)
    sc._center(draw, title, 43, sc._font(28, True), accent)
    draw.text((48, 132), str(row.get("league") or getattr(cfg, "title", "STEAM")), font=sc._fit(draw, str(row.get("league") or "LIVE"), 984, 20, False), fill=MUTED)
    draw.rounded_rectangle((44, 180, 1036, 390), 28, fill=PANEL2, outline=accent, width=3)
    draw.text((75, 215), str(row.get("home") or "?"), font=sc._fit(draw, str(row.get("home") or "?"), 360, 28, True), fill=TEXT)
    draw.text((75, 315), str(row.get("away") or "?"), font=sc._fit(draw, str(row.get("away") or "?"), 360, 28, True), fill=TEXT)
    draw.text((500, 225), f"{int(score[0])} : {int(score[1])}", font=sc._font(62, True), fill=TEXT)
    draw.rounded_rectangle((44, 425, 1036, 590), 24, fill=PANEL, outline=LINE, width=2)
    direction = str(row.get("direction") or "over")
    market_label = ("ТБ" if direction == "over" else "ТМ") + f" {float(row.get('line') or 0):g}"
    draw.text((72, 452), f"{market_label}  @  {float(row.get('odd') or 0):.2f}", font=sc._font(36, True), fill=GOLD)
    profit = float(row.get("profit_units") or 0.0)
    draw.text((72, 520), f"P/L {profit:+.2f}u", font=sc._font(28, True), fill=accent)
    return sc._save(image)
