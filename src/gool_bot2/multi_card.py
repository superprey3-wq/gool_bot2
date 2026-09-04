from __future__ import annotations

import math
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

# Flashscore league headers are usually shaped like
# ``VENEZUELA: Liga FUTVE - Clausura``. We only need the country name ->
# ISO-2 conversion here; the actual flag is loaded as a real PNG from FlagCDN.
COUNTRY_CODES = {
    "ARGENTINA": "ar", "AUSTRALIA": "au", "AUSTRIA": "at", "BELARUS": "by",
    "BELGIUM": "be", "BOLIVIA": "bo", "BOSNIA AND HERZEGOVINA": "ba", "BRAZIL": "br",
    "BULGARIA": "bg", "CANADA": "ca", "CHILE": "cl", "CHINA": "cn", "COLOMBIA": "co",
    "COSTA RICA": "cr", "CROATIA": "hr", "CYPRUS": "cy", "CZECH REPUBLIC": "cz",
    "CZECHIA": "cz", "DENMARK": "dk", "ECUADOR": "ec", "EGYPT": "eg", "ENGLAND": "gb",
    "ESTONIA": "ee", "FINLAND": "fi", "FRANCE": "fr", "GEORGIA": "ge", "GERMANY": "de",
    "GREECE": "gr", "HUNGARY": "hu", "ICELAND": "is", "INDIA": "in", "INDONESIA": "id",
    "IRELAND": "ie", "ISRAEL": "il", "ITALY": "it", "JAPAN": "jp", "KAZAKHSTAN": "kz",
    "LATVIA": "lv", "LITHUANIA": "lt", "MEXICO": "mx", "MOROCCO": "ma",
    "NETHERLANDS": "nl", "NEW ZEALAND": "nz", "NORWAY": "no", "PARAGUAY": "py",
    "PERU": "pe", "POLAND": "pl", "PORTUGAL": "pt", "ROMANIA": "ro", "RUSSIA": "ru",
    "SAUDI ARABIA": "sa", "SCOTLAND": "gb", "SERBIA": "rs", "SLOVAKIA": "sk",
    "SLOVENIA": "si", "SOUTH AFRICA": "za", "SOUTH KOREA": "kr", "SPAIN": "es",
    "SWEDEN": "se", "SWITZERLAND": "ch", "TURKEY": "tr", "TURKIYE": "tr",
    "UAE": "ae", "UKRAINE": "ua", "UNITED ARAB EMIRATES": "ae", "UNITED STATES": "us",
    "URUGUAY": "uy", "USA": "us", "VENEZUELA": "ve", "WALES": "gb",
}


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


def _is_confidence_metric(value: Any) -> bool:
    if isinstance(value, dict):
        tags = value.get("reason_tags") or []
    else:
        tags = getattr(value, "reason_tags", []) or []
    return "confidence_metric" in {str(tag) for tag in tags}


def _gool_metric_text(value: Any, probability: Any) -> str:
    try:
        number = float(probability) * 100.0
    except (TypeError, ValueError):
        return "GOOL —"
    if _is_confidence_metric(value):
        return f"GOOL CONF {number:.1f}/100"
    return f"GOOL {number:.1f}%"


def _league_parts(league: str) -> tuple[str | None, str]:
    raw = str(league or "LIVE FOOTBALL").strip()
    if ":" not in raw:
        return None, raw
    country, competition = raw.split(":", 1)
    country = country.strip()
    competition = competition.strip() or raw
    return country, competition


def _country_code(country: str | None) -> str | None:
    key = str(country or "").strip().upper()
    if not key:
        return None
    if len(key) == 2 and key.isalpha():
        return key.lower()
    return COUNTRY_CODES.get(key)


def _flag_image(country: str | None) -> Image.Image | None:
    code = _country_code(country)
    if not code:
        return None
    # signal_cards._download is already cached and used for team-logo assets.
    return sc._download(f"https://flagcdn.com/w80/{code}.png")


def _draw_league_under_score(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    league: str,
    y: int,
) -> None:
    country, competition = _league_parts(league)
    font = _fit_line(draw, competition, 430, 16, True)
    box = draw.textbbox((0, 0), competition, font=font)
    text_w = box[2] - box[0]
    flag = _flag_image(country)
    flag_w = 0
    gap = 0
    if flag is not None:
        flag = flag.resize((32, 22), Image.Resampling.LANCZOS)
        flag_w = flag.width
        gap = 9
    total_w = flag_w + gap + text_w
    x = int((W - total_w) / 2)
    if flag is not None:
        img.alpha_composite(flag, (x, y + 1))
        x += flag_w + gap
    draw.text((x, y), competition, font=font, fill=MUTED)


def _draw_team_names(draw: ImageDraw.ImageDraw, home: str, away: str, y: int) -> None:
    for x, name in ((175, home), (905, away)):
        font = _fit_line(draw, name, 330, 24, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, y), name, font=font, fill=TEXT)


def _draw_stats(draw: ImageDraw.ImageDraw, stats: dict[str, Any], y: int) -> None:
    draw.rounded_rectangle((45, y, 1035, y + 205), 20, fill=PANEL, outline=LINE, width=2)
    draw.text((70, y + 16), "КЛЮЧЕВАЯ LIVE СТАТИСТИКА", font=sc._font(15, True), fill=MUTED)
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
        yy = y + 50 + row * 74
        draw.text((x, yy), label, font=sc._font(12, True), fill=MUTED)
        draw.text((x, yy + 25), str(value), font=_fit_line(draw, str(value), 275, 21, True), fill=TEXT)


def _goal_note(goals: int) -> str:
    goals = max(1, int(goals))
    if goals % 10 == 1 and goals % 100 != 11:
        word = "гол"
    elif goals % 10 in {2, 3, 4} and goals % 100 not in {12, 13, 14}:
        word = "гола"
    else:
        word = "голов"
    return f"ещё {goals} {word}"


def _market_total_alternatives(
    market_row: dict[str, Any] | None,
    hs: int,
    aws: int,
    winner: Any,
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Return nearest real 1xBet match-total prices for the current score.

    These rows are informational alternatives, not extra GOOL selections. This
    keeps the card useful when BEST BET is a team goal: at 1:1 we can still show
    ТБ 2.5 (one more goal) and ТБ 3.5 (two more), using the bookmaker snapshot
    captured at the same score.
    """
    if not market_row:
        return []
    try:
        market_hs = int(market_row.get("score_home"))
        market_aws = int(market_row.get("score_away"))
    except (TypeError, ValueError):
        return []
    if (market_hs, market_aws) != (int(hs), int(aws)):
        return []

    current_total = int(hs) + int(aws)
    winner_key = str(getattr(winner, "key", "") or "")
    rows = []
    for item in list(((market_row.get("markets") or {}).get("match_total") or [])):
        try:
            line = float(item.get("line"))
            odd = float(item.get("over"))
        except (TypeError, ValueError):
            continue
        if odd <= 1.0 or line <= current_total:
            continue
        # GOOL Multi intentionally uses classic x.5 totals only.
        if abs((line % 1.0) - 0.5) > 1e-6:
            continue
        key = f"match_total:{line:g}"
        if key == winner_key:
            continue
        goals_needed = max(1, int(math.ceil(line - current_total)))
        rows.append({
            "key": key,
            "label": f"ТБ {line:g}",
            "odd": odd,
            "goals_needed": goals_needed,
            "note": _goal_note(goals_needed),
        })
    rows.sort(key=lambda row: (row["goals_needed"], float(row["odd"])))
    return rows[:max(0, int(limit))]


def _fallback_alternatives(decision: RouterDecision, limit: int = 2) -> list[Any]:
    return list(decision.alternatives[:max(0, int(limit))])


def render_multi_card(
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    entry: dict[str, Any] | None = None,
    market_row: dict[str, Any] | None = None,
) -> bytes:
    """Render the compact single BEST BET card used by GOOL Multi."""
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
    confidence_metric = _is_confidence_metric(winner)

    H = 1260
    img = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(img)

    # Compact brand strip: the tournament is intentionally moved under score.
    draw.rounded_rectangle((24, 20, 1056, 74), 18, fill=PANEL, outline=ACCENT, width=2)
    sc._center(draw, "GOOL MULTI • LIVE", 34, sc._font(19, True), ACCENT)

    sc._badge(img, draw, 175, 165, sc._logo(meta, "home"), home, ACCENT)
    sc._badge(img, draw, 905, 165, sc._logo(meta, "away"), away, ACCENT)
    draw.rounded_rectangle((392, 96, 688, 230), 24, fill=(9, 19, 31), outline=ACCENT, width=3)
    sc._center(draw, f"{hs} : {aws}", 114, sc._font(54, True), TEXT)
    phase = "ПЕРЕРЫВ" if match.get("is_halftime") else ("2-Й ТАЙМ" if minute >= 46 else "1-Й ТАЙМ")
    clock = phase if match.get("is_halftime") else f"{phase} • {minute}'"
    sc._center(draw, clock, 181, sc._font(18, True), ACCENT)
    _draw_league_under_score(img, draw, league, 244)
    _draw_team_names(draw, home, away, 282)
    sc._red_card_badge(draw, 175, 316, cards.get("home_red"))
    sc._red_card_badge(draw, 905, 316, cards.get("away_red"))

    draw.rounded_rectangle((45, 350, 1035, 530), 24, fill=PANEL2, outline=ACCENT, width=3)
    draw.text((72, 371), "BEST BET", font=sc._font(20, True), fill=ACCENT)
    draw.text((72, 410), winner.label, font=_fit_line(draw, winner.label, 580, 39, True), fill=TEXT)
    metric_line = f"{_gool_metric_text(winner, winner.model_probability)}  •  RATING {winner.rating:.0f}/100"
    draw.text((72, 463), metric_line, font=sc._font(17, True), fill=GOLD)
    draw.text((72, 496), source, font=_fit_line(draw, source, 430, 15, True), fill=ACCENT if source == "GOOL" else GOLD)

    draw.text((760, 371), "КОЭФФИЦИЕНТ", font=sc._font(13, True), fill=MUTED)
    draw.text((760, 398), _fmt_odd(winner.odd), font=sc._font(47, True), fill=GREEN)
    fair = "—" if winner.market_probability is None else _fmt_pct(winner.market_probability, 1)
    draw.text((760, 460), f"1xBet fair {fair}", font=sc._font(15, True), fill=TEXT)
    if entry and entry.get("virtual_stake_rub") is not None:
        draw.text((760, 493), f"Ставка {_money(entry.get('virtual_stake_rub'))}", font=sc._font(15, True), fill=GOLD)

    _draw_stats(draw, stats, 555)

    draw.rounded_rectangle((45, 780, 1035, 885), 18, fill=(8, 20, 31), outline=LINE, width=2)
    value_text = "—" if confidence_metric else f"{winner.value_edge_pp:+.1f} п.п."
    roi_text = "—" if confidence_metric else f"{winner.expected_roi * 100:+.1f}%"
    metrics = [
        ("VALUE", value_text, MUTED if confidence_metric else (GREEN if winner.value_edge_pp >= 0 else RED)),
        ("ROI MODEL", roi_text, MUTED if confidence_metric else TEXT),
        ("DATA", f"{winner.data_quality * 100:.0f}/100", TEXT),
        ("STEAM", _fmt_signed(winner.market_pressure_pp, " п.п."), ACCENT if winner.market_pressure_pp >= 0 else RED),
    ]
    for idx, (label, value, color) in enumerate(metrics):
        x = 70 + idx * 245
        draw.text((x, 799), label, font=sc._font(12, True), fill=MUTED)
        draw.text((x, 833), value, font=_fit_line(draw, value, 205, 20, True), fill=color)

    draw.rounded_rectangle((45, 905, 1035, 1000), 20, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 923), "ПОЧЕМУ ВЫБРАН", font=sc._font(13, True), fill=MUTED)
    reason = str(decision.reason or "")
    draw.text((70, 956), reason, font=_fit_line(draw, reason, 930, 19, True), fill=TEXT)

    draw.rounded_rectangle((45, 1020, 1035, 1168), 20, fill=PANEL, outline=LINE, width=2)
    draw.text((70, 1038), "БЛИЖАЙШИЕ АЛЬТЕРНАТИВЫ", font=sc._font(13, True), fill=MUTED)
    market_alternatives = _market_total_alternatives(market_row, hs, aws, winner, limit=2)
    if market_alternatives:
        y = 1073
        for row in market_alternatives:
            draw.text((72, y), str(row["label"]), font=sc._font(19, True), fill=TEXT)
            draw.text((430, y), f"1xBet @{_fmt_odd(row['odd'])}", font=sc._font(18, True), fill=GREEN)
            draw.text((690, y + 1), str(row["note"]), font=sc._font(16, True), fill=MUTED)
            y += 42
    else:
        alternatives = _fallback_alternatives(decision, 2)
        if not alternatives:
            draw.text((70, 1085), "Нет доступной соседней линии 1xBet на этом счёте.", font=sc._font(18, True), fill=TEXT)
        else:
            y = 1073
            for idx, row in enumerate(alternatives, 2):
                line = f"#{idx}  {row.label}  @{_fmt_odd(row.odd)}  •  {row.rating:.0f}/100"
                draw.text((72, y), line, font=_fit_line(draw, line, 915, 18, True), fill=TEXT)
                y += 42

    footer = "●  BEST BET • LIVE" if mode == "active" else "SHADOW • BEST BET PREVIEW"
    draw.rounded_rectangle((330, 1188, 750, 1240), 16, fill=ACCENT)
    sc._center(draw, footer, 1201, _fit_line(draw, footer, 380, 17, True), BG)
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
    detail = f"{market}  @{_fmt_odd(odd)}  •  {_gool_metric_text(row, probability)}"
    sc._center(draw, detail, 522, _fit_line(draw, detail, 900, 21, True), TEXT)

    sc._box(draw, (55, 600, 510, 710), "ВХОД", f"{entry_minute}' • {int(entry_score[0])}:{int(entry_score[1])}")
    sc._box(draw, (570, 600, 1025, 710), "ФИНИШ", f"{settled_minute}' • {int(settled_score[0])}:{int(settled_score[1])}", accent=accent)

    draw.rounded_rectangle((55, 745, 1025, 1030), 24, fill=PANEL, outline=LINE, width=2)
    draw.text((80, 765), "КЛЮЧЕВАЯ LIVE СТАТИСТИКА", font=sc._font(16, True), fill=MUTED)
    items = [
        ("xG / PROXY", stats.get("xg", "—")),
        ("УДАРЫ", stats.get("shots", "—")),
        ("В СТВОР", stats.get("sot", "—")),
        ("МОМЕНТЫ", stats.get("big_chances", "—")),
        ("УГЛОВЫЕ", stats.get("corners", "—")),
        ("ОПАСНЫЕ АТАКИ", stats.get("dangerous_attacks", "—")),
    ]
    for idx, (label, value) in enumerate(items):
        r, c = divmod(idx, 3)
        x = 80 + c * 310
        y = 810 + r * 92
        draw.text((x, y), label, font=sc._font(13, True), fill=MUTED)
        draw.text((x, y + 28), str(value), font=_fit_line(draw, str(value), 260, 22, True), fill=TEXT)

    draw.rounded_rectangle((330, 1062, 750, 1134), 18, fill=accent)
    sc._center(draw, "GOOL MULTI • VERIFIED", 1082, _fit_line(draw, "GOOL MULTI • VERIFIED", 380, 19, True), BG)
    return sc._save(img)
