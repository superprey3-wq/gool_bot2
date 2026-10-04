from __future__ import annotations

from datetime import datetime, timezone
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
RED = (220, 76, 76)
PURPLE = (170, 105, 255)


def _accent(cfg: Any) -> tuple[int, int, int]:
    return BLUE if str(getattr(cfg, "key", "")) == "basketball" else GREEN


def _phase_accent(phase: str, sport_accent: tuple[int, int, int]) -> tuple[int, int, int]:
    return PURPLE if phase == "PREMATCH" else sport_accent


def _center_range(draw: ImageDraw.ImageDraw, text: str, y: int, font: Any, fill: tuple[int, int, int], x1: int, x2: int) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    width = box[2] - box[0]
    draw.text((x1 + ((x2 - x1) - width) / 2, y), str(text), font=font, fill=fill)


def _value_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    value: str,
    accent: tuple[int, int, int] = TEXT,
) -> None:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, 18, fill=PANEL, outline=LINE, width=2)
    draw.text((x1 + 18, y1 + 14), title, font=sc._font(14, True), fill=MUTED)
    draw.text((x1 + 18, y1 + 46), value, font=sc._fit(draw, value, x2 - x1 - 36, 27, True), fill=accent)


def _selection(signal: dict[str, Any], row: dict[str, Any]) -> tuple[str, float, float]:
    direction = str(signal.get("direction") or row.get("direction") or "over")
    line = float(signal.get("line") or row.get("line") or 0.0)
    odd = float(signal.get("odd") or row.get("odd") or row.get(direction) or 0.0)
    return ("ТБ" if direction == "over" else "ТМ") + f" {line:g}", line, odd


def _start_label(row: dict[str, Any]) -> str:
    try:
        stamp = float(row.get("scheduled_start_ts") or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    if not stamp:
        return "ВРЕМЯ СТАРТА —"
    dt = datetime.fromtimestamp(stamp, tz=timezone.utc)
    return f"СТАРТ {dt:%d.%m · %H:%M UTC}"


def render_multisport_signal_card(
    row: dict[str, Any],
    signal: dict[str, Any],
    cfg: Any,
    *,
    phase: str | None = None,
) -> bytes:
    phase = str(phase or signal.get("phase") or row.get("phase") or "LIVE").upper()
    if phase not in {"PREMATCH", "LIVE"}:
        phase = "LIVE"
    sport_accent = _accent(cfg)
    accent = _phase_accent(phase, sport_accent)
    width, height = 1080, 920
    image = Image.new("RGBA", (width, height), BG + (255,))
    draw = ImageDraw.Draw(image)

    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or phase)
    sport_title = str(getattr(cfg, "title", "SPORT"))
    selection, line, odd = _selection(signal, row)
    strength_score = float(signal.get("strength") or 0.0)
    strength_label = "EXTREME" if bool(signal.get("extreme")) else ("STRONG" if strength_score >= 75 else "SIGNAL")

    draw.rounded_rectangle((24, 20, 1056, 96), 22, fill=PANEL, outline=accent, width=2)
    draw.text((48, 40), f"GOOL MULTI • {sport_title}", font=sc._font(24, True), fill=TEXT)
    draw.rounded_rectangle((820, 32, 1028, 82), 15, fill=(12, 17, 31), outline=accent, width=2)
    _center_range(draw, phase, 47, sc._font(18, True), accent, 820, 1028)

    draw.text((48, 118), league, font=sc._fit(draw, league, 984, 20, False), fill=MUTED)

    if phase == "LIVE":
        score = list(row.get("score") or [0, 0])
        draw.rounded_rectangle((44, 160, 1036, 355), 28, fill=PANEL2, outline=sport_accent, width=3)
        draw.text((75, 192), home, font=sc._fit(draw, home, 335, 28, True), fill=TEXT)
        draw.text((75, 293), away, font=sc._fit(draw, away, 335, 28, True), fill=TEXT)
        draw.text((455, 190), str(int(score[0])), font=sc._font(70, True), fill=TEXT)
        draw.text((525, 193), ":", font=sc._font(58, True), fill=MUTED)
        draw.text((580, 190), str(int(score[1])), font=sc._font(70, True), fill=TEXT)
        period = str(row.get("period") or "LIVE")
        clock = row.get("clock_seconds")
        if clock is not None:
            seconds = max(0, int(clock))
            period = f"{period} • {seconds // 60:02d}:{seconds % 60:02d}"
        draw.text((455, 291), period, font=sc._fit(draw, period, 300, 22, True), fill=sport_accent)
        draw.rounded_rectangle((780, 190, 1005, 325), 20, fill=(9, 19, 31), outline=GOLD, width=2)
        draw.text((814, 210), "СИЛА LIVE", font=sc._font(14, True), fill=MUTED)
        draw.text((815, 245), strength_label, font=sc._fit(draw, strength_label, 165, 27, True), fill=GOLD)
        draw.text((850, 286), f"{strength_score:.0f}/100", font=sc._font(18, True), fill=TEXT)
        market_top = 390
    else:
        draw.rounded_rectangle((44, 160, 1036, 355), 28, fill=PANEL2, outline=accent, width=3)
        draw.text((72, 190), home, font=sc._fit(draw, home, 820, 31, True), fill=TEXT)
        draw.text((72, 250), away, font=sc._fit(draw, away, 820, 31, True), fill=TEXT)
        draw.text((72, 315), _start_label(row), font=sc._font(19, True), fill=accent)
        draw.rounded_rectangle((790, 198, 1004, 322), 20, fill=(9, 19, 31), outline=GOLD, width=2)
        draw.text((825, 216), "СИЛА PRE", font=sc._font(14, True), fill=MUTED)
        draw.text((824, 248), f"{strength_score:.0f}/100", font=sc._font(30, True), fill=GOLD)
        market_top = 390

    draw.rounded_rectangle((44, market_top, 1036, market_top + 145), 24, fill=PANEL2, outline=GOLD, width=3)
    draw.text((72, market_top + 24), "РЕКОМЕНДАЦИЯ", font=sc._font(16, True), fill=MUTED)
    draw.text((72, market_top + 68), selection, font=sc._font(42, True), fill=GOLD)
    draw.text((700, market_top + 24), "КОЭФФИЦИЕНТ", font=sc._font(16, True), fill=MUTED)
    draw.text((700, market_top + 66), f"{odd:.2f}", font=sc._font(42, True), fill=TEXT)

    metric_delta = float(signal.get("metric_delta") or 0.0)
    probability_delta = float(signal.get("probability_delta_pp") or 0.0)
    line_delta = float(signal.get("line_delta") or 0.0)
    moves = int(signal.get("moves") or 0)
    y1 = market_top + 180
    _value_box(draw, (44, y1, 278, y1 + 115), "ДАВЛЕНИЕ", f"+{metric_delta:.2f}", GOLD)
    _value_box(draw, (296, y1, 530, y1 + 115), "Δ ВЕРОЯТНОСТИ", f"{probability_delta:+.1f} п.п.", accent)
    _value_box(draw, (548, y1, 782, y1 + 115), "СДВИГ ЛИНИИ", f"{line_delta:+.1f}", TEXT)
    _value_box(draw, (800, y1, 1036, y1 + 115), "ИМПУЛЬСЫ", f"{moves}x", TEXT)

    info_y = y1 + 150
    draw.rounded_rectangle((44, info_y, 1036, info_y + 110), 20, fill=PANEL, outline=LINE, width=2)
    if phase == "PREMATCH":
        opening_line = float(signal.get("opening_line") or row.get("opening_line") or line)
        opening_odd = float(signal.get("opening_odd") or row.get("opening_odd") or odd)
        draw.text((70, info_y + 20), "ДВИЖЕНИЕ ДО МАТЧА", font=sc._font(15, True), fill=MUTED)
        draw.text(
            (70, info_y + 59),
            f"линия {opening_line:g} → {line:g}  •  кэф {opening_odd:.2f} → {odd:.2f}  •  Flashscore + 1xBet ✓",
            font=sc._fit(draw, f"линия {opening_line:g} → {line:g}  •  кэф {opening_odd:.2f} → {odd:.2f}  •  Flashscore + 1xBet ✓", 930, 20, True),
            fill=GREEN,
        )
    else:
        age = float(signal.get("age_seconds") or 0.0)
        fs_id = str(row.get("flashscore_event_id") or "—")
        draw.text((70, info_y + 20), "LIVE-ПРОВЕРКА", font=sc._font(15, True), fill=MUTED)
        draw.text(
            (70, info_y + 59),
            f"счёт синхронен ✓  •  окно {age:.0f} сек  •  FS {fs_id}  •  1xBet ✓",
            font=sc._fit(draw, f"счёт синхронен ✓  •  окно {age:.0f} сек  •  FS {fs_id}  •  1xBet ✓", 930, 20, True),
            fill=GREEN,
        )

    draw.rounded_rectangle((280, 850, 800, 902), 16, fill=accent)
    _center_range(
        draw,
        "PREMATCH СИГНАЛ" if phase == "PREMATCH" else "LIVE СИГНАЛ",
        861,
        sc._font(20, True),
        BG,
        280,
        800,
    )
    return sc._save(image)


def render_multisport_steam_card(row: dict[str, Any], signal: dict[str, Any], cfg: Any) -> bytes:
    return render_multisport_signal_card(row, signal, cfg, phase="LIVE")


def render_multisport_result_card(row: dict[str, Any], cfg: Any) -> bytes:
    result = str(row.get("result") or "").lower()
    phase = str(row.get("phase") or "LIVE").upper()
    won = result == "won"
    void = result == "void"
    accent = GREEN if won else (GOLD if void else RED)
    width, height = 1080, 680
    image = Image.new("RGBA", (width, height), BG + (255,))
    draw = ImageDraw.Draw(image)
    score = list(row.get("settled_score") or row.get("score") or [0, 0])
    title = "✅ СИГНАЛ ЗАШЁЛ" if won else ("↩️ ВОЗВРАТ" if void else "❌ СИГНАЛ НЕ ЗАШЁЛ")

    draw.rounded_rectangle((24, 20, 1056, 104), 22, fill=PANEL, outline=accent, width=3)
    sc._center(draw, title, 44, sc._font(28, True), accent)
    draw.rounded_rectangle((830, 30, 1024, 78), 14, fill=(12, 17, 31), outline=_phase_accent(phase, _accent(cfg)), width=2)
    _center_range(draw, phase, 43, sc._font(16, True), _phase_accent(phase, _accent(cfg)), 830, 1024)

    league = str(row.get("league") or getattr(cfg, "title", "SPORT"))
    draw.text((48, 132), league, font=sc._fit(draw, league, 984, 20, False), fill=MUTED)
    draw.rounded_rectangle((44, 180, 1036, 410), 28, fill=PANEL2, outline=accent, width=3)
    draw.text((75, 216), str(row.get("home") or "?"), font=sc._fit(draw, str(row.get("home") or "?"), 420, 28, True), fill=TEXT)
    draw.text((75, 318), str(row.get("away") or "?"), font=sc._fit(draw, str(row.get("away") or "?"), 420, 28, True), fill=TEXT)
    draw.text((565, 244), f"{int(score[0])} : {int(score[1])}", font=sc._font(62, True), fill=TEXT)

    direction = str(row.get("direction") or "over")
    market_label = ("ТБ" if direction == "over" else "ТМ") + f" {float(row.get('line') or 0):g}"
    draw.rounded_rectangle((44, 445, 1036, 615), 24, fill=PANEL, outline=LINE, width=2)
    draw.text((72, 472), f"{market_label}  @  {float(row.get('odd') or 0):.2f}", font=sc._font(36, True), fill=GOLD)
    profit = float(row.get("profit_units") or 0.0)
    draw.text((72, 548), f"P/L {profit:+.2f}u", font=sc._font(28, True), fill=accent)
    draw.text((720, 550), f"{getattr(cfg, 'icon', '')} {phase}", font=sc._font(21, True), fill=MUTED)
    return sc._save(image)
