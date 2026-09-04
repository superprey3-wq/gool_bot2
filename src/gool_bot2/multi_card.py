from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .multi_public_metrics import ORDINARY_FORMULA, STEAM_FORMULA, source_label
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


def _fit_line(draw: ImageDraw.ImageDraw, text: str, width: int, size: int = 23, bold: bool = False):
    return sc._fit(draw, text, width, size, bold)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score100(value: Any, fallback: float = 0.0) -> float:
    number = _num(value)
    if number is None:
        return fallback
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return max(0.0, min(100.0, number))


def _is_confidence_metric(value: Any) -> bool:
    if isinstance(value, dict):
        tags = value.get("reason_tags") or []
    else:
        tags = getattr(value, "reason_tags", []) or []
    return "confidence_metric" in {str(tag) for tag in tags}


def _gool_metric_text(value: Any, probability: Any) -> str:
    score = _score100(probability)
    if _is_confidence_metric(value):
        return f"ОЦЕНКА СОБЫТИЯ {score:.0f}/100"
    return f"ВЕРОЯТНОСТЬ СОБЫТИЯ {score:.0f}%"


def _event_metric_text(value: Any, probability: Any, event_score: Any = None) -> str:
    score = _score100(event_score, fallback=_score100(probability))
    if _is_confidence_metric(value):
        return f"ОЦЕНКА СОБЫТИЯ {score:.0f}/100"
    return f"ВЕРОЯТНОСТЬ СОБЫТИЯ {score:.0f}%"


def _confidence_text(entry: dict[str, Any] | None, winner: Any) -> str:
    score = _score100((entry or {}).get("confidence_score"), fallback=_score100(getattr(winner, "rating", 0.0)))
    return f"УВЕРЕННОСТЬ GOOL {score:.0f}/100"


def _formula_text(entry: dict[str, Any] | None) -> str:
    formula = str((entry or {}).get("formula") or "")
    if formula:
        return formula
    layer = str((entry or {}).get("layer") or "").upper()
    return STEAM_FORMULA if layer == "STEAM" else ORDINARY_FORMULA


def _steam_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_public_steam(winner: Any) -> bool:
    pressure = _steam_value(getattr(winner, "market_pressure_pp", 0.0))
    level = str(getattr(winner, "market_level", "") or "").upper()
    return pressure >= PUBLIC_STEAM_MIN_PP or level in {"PRESSURE", "STRONG_STEAM", "AUTONOMOUS_STEAM"}


def _market_pressure_row(market_row: dict[str, Any] | None, winner: Any) -> dict[str, Any]:
    if not market_row:
        return {}
    pressure = market_row.get("pressure") or {}
    keys = [str(getattr(winner, "key", "") or "")]
    if str(getattr(winner, "strategy", "") or "") in {"both_teams_to_score", "steam_btts"}:
        keys.extend(("btts_yes:None", "btts_yes"))
    for key in keys:
        row = pressure.get(key)
        if isinstance(row, dict):
            return dict(row)
    return {}


def _steam_lines(winner: Any, market_row: dict[str, Any] | None = None) -> tuple[str, str]:
    row = _market_pressure_row(market_row, winner)
    pressure = _steam_value(row.get("prob_delta_pp")) or _steam_value(getattr(winner, "market_pressure_pp", 0.0))
    try:
        moves = int(row.get("one_way_moves") or 0)
    except (TypeError, ValueError):
        moves = 0
    old_odd = _num(row.get("old_odd"))
    new_odd = _num(row.get("new_odd")) or _num(getattr(winner, "odd", None))

    if _has_public_steam(winner):
        line1 = f"ПРОГРУЗ 1xBet  {pressure:+.1f} п.п. • импульсов {moves}"
        if old_odd and new_odd and old_odd > 1.0 and new_odd > 1.0:
            move_pct = (new_odd - old_odd) / old_odd * 100.0
            line2 = f"КЭФ  {old_odd:.2f} → {new_odd:.2f}  •  Δкэф {move_pct:+.1f}%"
        else:
            line2 = f"ТЕКУЩИЙ КЭФ  {_fmt_odd(new_odd)}"
        return line1, line2
    return "1xBet: выраженного прогруза нет", f"ТЕКУЩИЙ КЭФ  {_fmt_odd(getattr(winner, 'odd', None))}"


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
    title: str = "GOOL MULTI • LIVE",
) -> None:
    draw.rounded_rectangle((24, 20, 1056, 76), 18, fill=PANEL, outline=accent, width=2)
    sc._center(draw, title, 36, sc._font(19, True), accent)

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


def _draw_metric_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    value: str,
    *,
    accent=GOLD,
) -> None:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, 18, fill=PANEL, outline=LINE, width=2)
    draw.text((x1 + 18, y1 + 12), title, font=sc._font(13, True), fill=MUTED)
    draw.text((x1 + 18, y1 + 42), value, font=_fit_line(draw, value, x2 - x1 - 36, 26, True), fill=accent)


def _momentum_value(momentum: dict[str, Any], key: str, digits: int = 2) -> str:
    value = _num(momentum.get(key))
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _draw_stat_grid(
    draw: ImageDraw.ImageDraw,
    stats: dict[str, Any],
    momentum: dict[str, Any],
    *,
    top: int,
) -> None:
    draw.rounded_rectangle((45, top, 1035, top + 185), 24, fill=PANEL, outline=LINE, width=2)
    draw.text((70, top + 16), "LIVE • КЛЮЧЕВАЯ СТАТИСТИКА", font=sc._font(15, True), fill=MUTED)
    items = [
        ("xG", str(stats.get("xg") or "—")),
        ("УДАРЫ", str(stats.get("shots") or "—")),
        ("В СТВОР", str(stats.get("sot") or "—")),
        ("МОМЕНТЫ", str(stats.get("big_chances") or "—")),
        ("xG ЗА 5М", _momentum_value(momentum, "xg_total_last_5m")),
        ("SOT ЗА 10М", _momentum_value(momentum, "sot_total_last_10m", 1)),
    ]
    for i, (label, value) in enumerate(items):
        row, col = divmod(i, 3)
        x = 70 + col * 320
        y = top + 55 + row * 60
        draw.text((x, y), label, font=sc._font(12, True), fill=MUTED)
        draw.text((x, y + 22), value, font=_fit_line(draw, value, 270, 20, True), fill=TEXT)


def _wrap(draw: ImageDraw.ImageDraw, text: str, width: int, font) -> list[str]:
    words = str(text or "").split()
    if not words:
        return ["—"]
    lines: list[str] = []
    current = ""
    for word in words:
        probe = word if not current else f"{current} {word}"
        if draw.textbbox((0, 0), probe, font=font)[2] <= width:
            current = probe
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:2]


def _draw_reason(
    draw: ImageDraw.ImageDraw,
    reason: str,
    formula: str,
    *,
    top: int,
    accent=ACCENT,
) -> None:
    draw.rounded_rectangle((45, top, 1035, top + 125), 24, fill=PANEL2, outline=accent, width=2)
    draw.text((70, top + 15), "ПОЧЕМУ ЭТА СТАВКА", font=sc._font(14, True), fill=accent)
    font = sc._font(18, True)
    lines = _wrap(draw, reason, 920, font)
    for i, line in enumerate(lines):
        draw.text((70, top + 47 + i * 25), line, font=font, fill=TEXT)
    formula_text = f"ФОРМУЛА УВЕРЕННОСТИ: {formula}"
    draw.text((70, top + 101), formula_text, font=_fit_line(draw, formula_text, 920, 12, False), fill=MUTED)


def render_multi_card(
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    entry: dict[str, Any] | None = None,
    market_row: dict[str, Any] | None = None,
) -> bytes:
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
    entry = dict(entry or {})
    stats = sc.stats_snapshot(record)
    momentum = dict(record.get("live_momentum") or {})
    reason = str(entry.get("selection_reason") or decision.reason or "Выбран лучший проходящий LIVE-сценарий.")
    steam1, steam2 = _steam_lines(winner, market_row)

    height = 1120
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

    draw.rounded_rectangle((45, 382, 1035, 610), 26, fill=PANEL2, outline=ACCENT, width=3)
    draw.text((75, 405), "BEST BET", font=sc._font(18, True), fill=ACCENT)
    draw.text((75, 445), winner.label, font=_fit_line(draw, winner.label, 610, 40, True), fill=TEXT)
    draw.text((760, 405), "КОЭФФИЦИЕНТ 1xBet", font=sc._font(13, True), fill=MUTED)
    draw.text((760, 438), _fmt_odd(winner.odd), font=sc._font(48, True), fill=GREEN)

    event_text = _event_metric_text(winner, winner.model_probability, entry.get("event_score"))
    confidence_text = _confidence_text(entry, winner)
    _draw_metric_box(draw, (75, 515, 485, 590), "СИЛА ФУТБОЛЬНОГО СЦЕНАРИЯ", event_text.replace("ОЦЕНКА СОБЫТИЯ ", "").replace("ВЕРОЯТНОСТЬ СОБЫТИЯ ", ""), accent=GOLD)
    _draw_metric_box(draw, (505, 515, 1005, 590), "ИТОГОВАЯ УВЕРЕННОСТЬ", confidence_text.replace("УВЕРЕННОСТЬ GOOL ", ""), accent=ACCENT)

    _draw_stat_grid(draw, stats, momentum, top=630)

    market_accent = GOLD if _has_public_steam(winner) else ACCENT
    draw.rounded_rectangle((45, 835, 1035, 900), 20, fill=PANEL, outline=market_accent, width=2)
    draw.text((70, 847), steam1, font=_fit_line(draw, steam1, 920, 18, True), fill=market_accent)
    draw.text((70, 875), steam2, font=_fit_line(draw, steam2, 920, 16, True), fill=TEXT)

    _draw_reason(draw, reason, _formula_text(entry), top=920, accent=market_accent)

    footer = "BEST BET • LIVE • " + source_label(entry.get("signal_source") or winner.source)
    draw.rounded_rectangle((300, 1060, 780, 1105), 15, fill=market_accent)
    sc._center(draw, footer, 1072, _fit_line(draw, footer, 440, 14, True), BG)
    return sc._save(image)


def _entry_stats(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    stats = dict(row.get("stats_snapshot") or {})
    momentum = dict(row.get("live_momentum_snapshot") or {})
    return stats, momentum


def render_multi_result_card(row: dict[str, Any], record: dict[str, Any] | None = None) -> bytes:
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
    confidence = _score100(row.get("confidence_score"), fallback=_score100(row.get("rating")))
    event_score = _score100(row.get("event_score"), fallback=_score100(row.get("probability")))
    reason = str(row.get("selection_reason") or row.get("reason") or "Сигнал был выбран GOOL по LIVE-состоянию матча.")
    stats, momentum = _entry_stats(row)

    height = 1040
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
        title="GOOL MULTI • RESULT",
    )

    draw.rounded_rectangle((45, 382, 1035, 610), 26, fill=PANEL2, outline=accent, width=3)
    sc._center(draw, banner, 405, _fit_line(draw, banner, 880, 36, True), accent)
    detail = f"{market}  @{_fmt_odd(odd)}"
    sc._center(draw, detail, 462, _fit_line(draw, detail, 900, 28, True), TEXT)

    _draw_metric_box(draw, (75, 515, 485, 590), "ОЦЕНКА СОБЫТИЯ НА ВХОДЕ", f"{event_score:.0f}/100", accent=GOLD)
    _draw_metric_box(draw, (505, 515, 1005, 590), "УВЕРЕННОСТЬ НА ВХОДЕ", f"{confidence:.0f}/100", accent=accent)

    _draw_stat_grid(draw, stats, momentum, top=630)

    draw.rounded_rectangle((45, 835, 1035, 950), 24, fill=PANEL2, outline=accent, width=2)
    entry_line = f"ВХОД {entry_minute}' • {int(entry_score[0])}:{int(entry_score[1])}   →   РЕЗУЛЬТАТ {settled_minute}' • {int(settled_score[0])}:{int(settled_score[1])}"
    sc._center(draw, entry_line, 850, _fit_line(draw, entry_line, 910, 17, True), MUTED)
    draw.text((70, 890), "ПОЧЕМУ БЫЛ ВЫБРАН ВХОД", font=sc._font(13, True), fill=accent)
    font = sc._font(17, True)
    for i, line in enumerate(_wrap(draw, reason, 920, font)):
        draw.text((70, 916 + i * 22), line, font=font, fill=TEXT)

    footer = f"GOOL MULTI • VERIFIED • {source_label(row.get('signal_source'))}"
    draw.rounded_rectangle((300, 975, 780, 1025), 16, fill=accent)
    sc._center(draw, footer, 988, _fit_line(draw, footer, 440, 14, True), BG)
    return sc._save(image)
