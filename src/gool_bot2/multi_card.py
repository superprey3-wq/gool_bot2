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
PUBLIC_STEAM_MIN_PP = 3.0


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


def _is_confidence_metric(value: Any) -> bool:
    if isinstance(value, dict):
        tags = value.get("reason_tags") or []
    else:
        tags = getattr(value, "reason_tags", []) or []
    return "confidence_metric" in {str(tag) for tag in tags}


def _gool_metric_text(value: Any, probability: Any) -> str:
    """Public-facing strength text without pretending confidence is probability."""
    try:
        number = float(probability) * 100.0
    except (TypeError, ValueError):
        return "ОЦЕНКА ЗАХОДА —"
    if _is_confidence_metric(value):
        return f"ОЦЕНКА ЗАХОДА {number:.0f}/100"
    return f"ВЕРОЯТНОСТЬ ЗАХОДА {number:.0f}%"


def _steam_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_public_steam(winner: Any) -> bool:
    pressure = _steam_value(getattr(winner, "market_pressure_pp", 0.0))
    level = str(getattr(winner, "market_level", "") or "").upper()
    return pressure >= PUBLIC_STEAM_MIN_PP or level in {"PRESSURE", "STRONG_STEAM"}


def _market_pressure_row(market_row: dict[str, Any] | None, winner: Any) -> dict[str, Any]:
    if not market_row:
        return {}
    pressure = market_row.get("pressure") or {}
    keys = [str(getattr(winner, "key", "") or "")]
    if str(getattr(winner, "strategy", "") or "") == "both_teams_to_score":
        keys.extend(("btts_yes:None", "btts_yes"))
    for key in keys:
        row = pressure.get(key)
        if isinstance(row, dict):
            return dict(row)
    return {}


def _steam_text(winner: Any, market_row: dict[str, Any] | None = None) -> str | None:
    if not _has_public_steam(winner):
        return None
    row = _market_pressure_row(market_row, winner)
    pressure = _steam_value(row.get("prob_delta_pp")) or _steam_value(getattr(winner, "market_pressure_pp", 0.0))
    try:
        old_odd = float(row.get("old_odd"))
    except (TypeError, ValueError):
        old_odd = None
    try:
        new_odd = float(row.get("new_odd"))
    except (TypeError, ValueError):
        new_odd = None
    suffix = ""
    if old_odd and new_odd and old_odd > 1.0 and new_odd > 1.0:
        suffix = f"  •  {_fmt_odd(old_odd)} → {_fmt_odd(new_odd)}"
    return f"🔥 ПРОГРУЗ 1xBET  {pressure:+.1f} п.п.{suffix}"


def _draw_match_header(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    home: str,
    away: str,
    league: str,
    minute: int,
    hs: int,
    aws: int,
    meta: dict[str, Any],
    cards: dict[str, Any],
    accent=ACCENT,
) -> None:
    draw.rounded_rectangle((24, 20, 1056, 76), 18, fill=PANEL, outline=accent, width=2)
    sc._center(draw, "GOOL MULTI • LIVE", 36, sc._font(19, True), accent)

    sc._badge(image, draw, 175, 177, sc._logo(meta, "home"), home, accent)
    sc._badge(image, draw, 905, 177, sc._logo(meta, "away"), away, accent)
    draw.rounded_rectangle((390, 102, 690, 240), 24, fill=(9, 19, 31), outline=accent, width=3)
    sc._center(draw, f"{hs} : {aws}", 122, sc._font(55, True), TEXT)
    phase = "ПЕРЕРЫВ" if minute == 45 else ("2-Й ТАЙМ" if minute >= 46 else "1-Й ТАЙМ")
    sc._center(draw, f"{phase} • {minute}'", 192, sc._font(18, True), accent)

    league_font = _fit_line(draw, league, 880, 16, True)
    sc._center(draw, league, 266, league_font, MUTED)
    for x, name in ((175, home), (905, away)):
        font = _fit_line(draw, name, 330, 23, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, 303), name, font=font, fill=TEXT)
    sc._red_card_badge(draw, 175, 338, cards.get("home_red"))
    sc._red_card_badge(draw, 905, 338, cards.get("away_red"))


def render_multi_card(
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    entry: dict[str, Any] | None = None,
    market_row: dict[str, Any] | None = None,
) -> bytes:
    """Render a clean public card: market, odds, chance, and optional 1xBet steam."""
    if decision.winner is None:
        raise ValueError("GOOL MULTI card requires a BET decision")

    match = record.get("match") or {}
    meta = sc.flashscore_meta(record)
    cards = record.get("cards") or {}
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or decision.minute or 0)
    hs = int(match.get("home_score") if match.get("home_score") is not None else decision.score[0])
    aws = int(match.get("away_score") if match.get("away_score") is not None else decision.score[1])
    winner = decision.winner
    steam = _steam_text(winner, market_row)

    height = 760
    image = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(image)
    _draw_match_header(
        image,
        draw,
        home=home,
        away=away,
        league=league,
        minute=minute,
        hs=hs,
        aws=aws,
        meta=meta,
        cards=cards,
    )

    draw.rounded_rectangle((45, 382, 1035, 655), 26, fill=PANEL2, outline=ACCENT, width=3)
    draw.text((75, 407), "BEST BET", font=sc._font(18, True), fill=ACCENT)
    draw.text((75, 449), winner.label, font=_fit_line(draw, winner.label, 610, 42, True), fill=TEXT)
    draw.text((760, 407), "КОЭФФИЦИЕНТ", font=sc._font(13, True), fill=MUTED)
    draw.text((760, 437), _fmt_odd(winner.odd), font=sc._font(49, True), fill=GREEN)

    metric = _gool_metric_text(winner, winner.model_probability)
    draw.text((75, 522), metric, font=_fit_line(draw, metric, 870, 31, True), fill=GOLD)
    if steam:
        draw.text((75, 585), steam, font=_fit_line(draw, steam, 900, 21, True), fill=GOLD)

    footer = "●  BEST BET • LIVE" if str((entry or {}).get("mode") or "shadow").lower() == "active" else "SHADOW • BEST BET PREVIEW"
    draw.rounded_rectangle((330, 688, 750, 738), 16, fill=ACCENT)
    sc._center(draw, footer, 701, _fit_line(draw, footer, 380, 16, True), BG)
    return sc._save(image)


def render_multi_result_card(row: dict[str, Any], record: dict[str, Any] | None = None) -> bytes:
    """Render a clean result card without model diagnostics or accounting clutter."""
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
    cards = dict(row.get("settled_cards") or row.get("cards") or {})
    entry_score = list(row.get("score") or [0, 0])
    settled_score = list(row.get("settled_score") or [match.get("home_score", 0), match.get("away_score", 0)])
    entry_minute = int(row.get("minute") or 0)
    settled_minute = int(row.get("settled_minute") or match.get("minute") or 90)
    market = str(row.get("market") or "BEST BET")
    odd = row.get("odd")
    probability = row.get("probability")

    height = 760
    image = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((24, 20, 1056, 76), 18, fill=PANEL, outline=accent, width=2)
    sc._center(draw, "GOOL MULTI • RESULT", 36, sc._font(19, True), accent)
    sc._badge(image, draw, 175, 177, sc._logo(meta, "home"), home, accent)
    sc._badge(image, draw, 905, 177, sc._logo(meta, "away"), away, accent)
    draw.rounded_rectangle((390, 102, 690, 240), 24, fill=(9, 19, 31), outline=accent, width=3)
    sc._center(draw, f"{int(settled_score[0])} : {int(settled_score[1])}", 122, sc._font(55, True), TEXT)
    sc._center(draw, f"{settled_minute}'", 192, sc._font(18, True), accent)
    sc._center(draw, league, 266, _fit_line(draw, league, 880, 16, True), MUTED)
    for x, name in ((175, home), (905, away)):
        font = _fit_line(draw, name, 330, 23, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, 303), name, font=font, fill=TEXT)
    sc._red_card_badge(draw, 175, 338, cards.get("home_red"))
    sc._red_card_badge(draw, 905, 338, cards.get("away_red"))

    draw.rounded_rectangle((45, 382, 1035, 655), 26, fill=PANEL2, outline=accent, width=3)
    sc._center(draw, banner, 405, _fit_line(draw, banner, 880, 38, True), accent)
    detail = f"{market}  @{_fmt_odd(odd)}"
    sc._center(draw, detail, 472, _fit_line(draw, detail, 900, 28, True), TEXT)
    metric = _gool_metric_text(row, probability)
    sc._center(draw, metric, 525, _fit_line(draw, metric, 900, 23, True), GOLD)
    sc._center(
        draw,
        f"Вход {entry_minute}' • {int(entry_score[0])}:{int(entry_score[1])}   →   {settled_minute}' • {int(settled_score[0])}:{int(settled_score[1])}",
        582,
        sc._font(18, True),
        MUTED,
    )

    draw.rounded_rectangle((330, 688, 750, 738), 16, fill=accent)
    sc._center(draw, "GOOL MULTI • VERIFIED", 701, _fit_line(draw, "GOOL MULTI • VERIFIED", 380, 16, True), BG)
    return sc._save(image)
