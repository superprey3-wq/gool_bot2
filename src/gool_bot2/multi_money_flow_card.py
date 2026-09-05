from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .multi_card import (
    ACCENT,
    BG,
    GOLD,
    GREEN,
    LINE,
    MUTED,
    PANEL,
    PANEL2,
    RED,
    TEXT,
    VIOLET,
    W,
    _draw_match_header,
    _draw_metric_box,
    _draw_stat_grid,
    _fit_line,
    _wrap,
)


FLOW = (70, 225, 170)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _money_gbp(value: Any) -> str:
    return f"£{_num(value):,.0f}"


def _flow_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("matchbook_flow") or {})


def _entry_stats(row: dict[str, Any], record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    stats = dict(row.get("stats_snapshot") or sc.stats_snapshot(record) or {})
    momentum = dict(row.get("live_momentum_snapshot") or record.get("live_momentum") or {})
    return stats, momentum


def _draw_flow_block(draw: ImageDraw.ImageDraw, row: dict[str, Any], *, top: int, accent=FLOW) -> None:
    flow = _flow_snapshot(row)
    previous = _num(flow.get("previous_fair_over")) * 100.0
    current = _num(flow.get("fair_over")) * 100.0
    delta_pp = _num(flow.get("fair_delta_pp"))
    relative = _num(flow.get("relative_pct"))
    window = str(flow.get("window") or "—")
    total_volume = _money_gbp(flow.get("market_volume"))
    lay = _num(flow.get("lay_odd"))

    draw.rounded_rectangle((45, top, 1035, top + 108), 22, fill=PANEL2, outline=accent, width=2)
    draw.text((70, top + 13), "MATCHBOOK • ДЕНЕЖНЫЙ ПОТОК", font=sc._font(14, True), fill=accent)
    line1 = f"fair Over  {previous:.1f}% → {current:.1f}%  •  {delta_pp:+.1f} п.п."
    line2 = f"объём рынка {total_volume}  •  поток {relative:+.1f}%/{window}  •  LAY {lay:.2f}"
    draw.text((70, top + 43), line1, font=_fit_line(draw, line1, 920, 20, True), fill=TEXT)
    draw.text((70, top + 74), line2, font=_fit_line(draw, line2, 920, 16, True), fill=MUTED)


def render_money_flow_signal_card(record: dict[str, Any], row: dict[str, Any]) -> bytes:
    match = record.get("match") or {}
    flow = _flow_snapshot(row)
    meta = dict(row.get("flashscore_meta") or sc.flashscore_meta(record) or {})
    cards = dict(row.get("cards") or record.get("cards") or {})
    home = str(row.get("home") or match.get("home") or "?")
    away = str(row.get("away") or match.get("away") or "?")
    league = str(row.get("league") or match.get("league") or "LIVE FOOTBALL")
    minute = int(row.get("minute") or match.get("minute") or 0)
    score = list(row.get("score") or [match.get("home_score", 0), match.get("away_score", 0)])
    market = str(row.get("market") or "MONEY FLOW")
    odd = _num(row.get("odd"))
    rating = _num(row.get("rating"))
    delta = _num(flow.get("volume_delta"))
    relative = _num(flow.get("relative_pct"))
    window = str(flow.get("window") or "—")
    level = str(flow.get("level") or "HEAVY_FLOW")
    stats, momentum = _entry_stats(row, record)

    height = 1160
    image = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(image)
    _draw_match_header(
        image,
        draw,
        home=home,
        away=away,
        league=league,
        minute=minute,
        hs=int(score[0]),
        aws=int(score[1]),
        meta=meta,
        cards=cards,
        accent=FLOW,
        title="GOOL • MONEY FLOW • LIVE",
    )

    draw.rounded_rectangle((45, 382, 1035, 620), 26, fill=PANEL2, outline=FLOW, width=3)
    draw.text((75, 405), "ОТДЕЛЬНАЯ СТАВКА ПО ДЕНЕЖНОМУ ПОТОКУ", font=sc._font(16, True), fill=FLOW)
    draw.text((75, 448), market, font=_fit_line(draw, market, 610, 39, True), fill=TEXT)
    draw.text((760, 405), "BACK MATCHBOOK", font=sc._font(13, True), fill=MUTED)
    draw.text((760, 438), f"{odd:.2f}", font=sc._font(48, True), fill=GREEN)

    _draw_metric_box(draw, (75, 525, 385, 600), f"ПРОТОРГОВАНО ЗА {window}", _money_gbp(delta), accent=GOLD)
    _draw_metric_box(draw, (405, 525, 705, 600), "РОСТ ОБЪЁМА", f"{relative:+.1f}%", accent=FLOW)
    _draw_metric_box(draw, (725, 525, 1005, 600), "FLOW SCORE", f"{rating:.0f}/100", accent=ACCENT)

    _draw_stat_grid(draw, stats, momentum, top=645)
    _draw_flow_block(draw, row, top=850, accent=FLOW)

    reason = str(row.get("reason") or "Подтверждён крупный направленный объём Matchbook в сторону следующего гола.")
    draw.rounded_rectangle((45, 978, 1035, 1080), 22, fill=PANEL, outline=FLOW, width=2)
    draw.text((70, 992), "ПОЧЕМУ ВХОД", font=sc._font(14, True), fill=FLOW)
    font = sc._font(17, True)
    for index, line in enumerate(_wrap(draw, reason, 920, font)):
        draw.text((70, 1025 + index * 24), line, font=font, fill=TEXT)

    footer = f"MATCHBOOK • {level} • НЕЗАВИСИМАЯ СИСТЕМА"
    draw.rounded_rectangle((280, 1100, 800, 1145), 15, fill=FLOW)
    sc._center(draw, footer, 1112, _fit_line(draw, footer, 480, 14, True), BG)
    return sc._save(image)


def render_money_flow_result_card(row: dict[str, Any], record: dict[str, Any] | None = None) -> bytes:
    record = record or {}
    match = record.get("match") or {}
    result = str(row.get("result") or "void").lower()
    if result == "won":
        accent, banner = GREEN, "✓  MONEY FLOW ЗАШЁЛ"
    elif result == "lost":
        accent, banner = RED, "✕  MONEY FLOW НЕ ЗАШЁЛ"
    else:
        accent, banner = VIOLET, "↩  MONEY FLOW • VOID"

    home = str(row.get("home") or match.get("home") or "?")
    away = str(row.get("away") or match.get("away") or "?")
    league = str(row.get("league") or match.get("league") or "LIVE FOOTBALL")
    meta = dict(row.get("flashscore_meta") or sc.flashscore_meta(record) or {})
    cards = dict(row.get("settled_cards") or row.get("cards") or {})
    entry_score = list(row.get("score") or [0, 0])
    settled_score = list(row.get("settled_score") or [match.get("home_score", 0), match.get("away_score", 0)])
    entry_minute = int(row.get("minute") or 0)
    settled_minute = int(row.get("settled_minute") or match.get("minute") or 90)
    market = str(row.get("market") or "MONEY FLOW")
    odd = _num(row.get("odd"))
    flow = _flow_snapshot(row)
    stats, momentum = _entry_stats(row, record)

    height = 1135
    image = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(image)
    _draw_match_header(
        image,
        draw,
        home=home,
        away=away,
        league=league,
        minute=settled_minute,
        hs=int(settled_score[0]),
        aws=int(settled_score[1]),
        meta=meta,
        cards=cards,
        accent=accent,
        title="GOOL • MONEY FLOW • RESULT",
    )

    draw.rounded_rectangle((45, 382, 1035, 610), 26, fill=PANEL2, outline=accent, width=3)
    sc._center(draw, banner, 405, _fit_line(draw, banner, 880, 36, True), accent)
    detail = f"{market}  @{odd:.2f}"
    sc._center(draw, detail, 462, _fit_line(draw, detail, 900, 28, True), TEXT)
    _draw_metric_box(draw, (75, 515, 485, 590), "ПОТОК НА ВХОДЕ", _money_gbp(flow.get("volume_delta")), accent=GOLD)
    _draw_metric_box(draw, (505, 515, 1005, 590), "FLOW SCORE НА ВХОДЕ", f"{_num(row.get('rating')):.0f}/100", accent=accent)

    _draw_stat_grid(draw, stats, momentum, top=630)
    _draw_flow_block(draw, row, top=835, accent=accent)

    draw.rounded_rectangle((45, 960, 1035, 1060), 22, fill=PANEL2, outline=accent, width=2)
    entry_line = (
        f"ВХОД {entry_minute}' • {int(entry_score[0])}:{int(entry_score[1])}   →   "
        f"РЕЗУЛЬТАТ {settled_minute}' • {int(settled_score[0])}:{int(settled_score[1])}"
    )
    sc._center(draw, entry_line, 978, _fit_line(draw, entry_line, 910, 17, True), MUTED)
    pnl = _num(row.get("profit_units"))
    pnl_line = f"P/L ставки: {pnl:+.2f}u"
    sc._center(draw, pnl_line, 1018, _fit_line(draw, pnl_line, 900, 19, True), accent)

    footer = "MATCHBOOK MONEY FLOW • VERIFIED"
    draw.rounded_rectangle((300, 1080, 780, 1120), 15, fill=accent)
    sc._center(draw, footer, 1090, _fit_line(draw, footer, 440, 14, True), BG)
    return sc._save(image)
