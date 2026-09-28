from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc

W = 1080
BG = (5, 10, 18)
PANEL = (12, 22, 34)
PANEL2 = (16, 30, 45)
TEXT = (239, 246, 255)
MUTED = (142, 160, 180)
ACCENT = (74, 214, 155)
GOLD = (244, 190, 70)
RED = (239, 91, 91)
NEUTRAL = (142, 160, 180)


def _fit(draw, text: str, width: int, size: int, bold: bool = False):
    return sc._fit(draw, str(text or ""), width, size, bold)


def _center(draw, text: str, y: int, font, fill):
    return sc._center(draw, str(text or ""), y, font, fill)


def _meta(row: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    direct = dict(row.get("flashscore_meta") or {})
    if direct:
        return direct
    return sc.flashscore_meta(record or {})


def _market_label(value: str) -> str:
    key = str(value or "").upper()
    labels = {"FT_OVER_2.5":"ТБ 2.5","FT_UNDER_2.5":"ТМ 2.5","BTTS_YES":"Обе забьют — Да","1H_OVER_0.5":"1-й тайм · ТБ 0.5","1H_OVER_1.5":"1-й тайм · ТБ 1.5","2H_OVER_0.5":"2-й тайм · ТБ 0.5","2H_OVER_1.5":"2-й тайм · ТБ 1.5"}
    return labels.get(key, str(value or "?").replace("_"," "))

def render_v4_prematch_card(
    row: dict[str, Any],
    record: dict[str, Any] | None = None,
    *,
    in_game: bool = False,
) -> str:
    """Premium V4 PREMATCH card with real Flashscore team identity."""
    record = record or {}
    match = record.get("match") or {}
    home = str(row.get("home") or match.get("home") or "?")
    away = str(row.get("away") or match.get("away") or "?")
    league = str(row.get("league") or match.get("league") or "FOOTBALL")
    meta = _meta(row, record)

    lifecycle = str(row.get("lifecycle") or "")
    live = bool(in_game or lifecycle == "in_game")
    minute = int(row.get("current_minute") or match.get("minute") or 0)
    score = list(row.get("current_score") or [int(match.get("home_score") or 0), int(match.get("away_score") or 0)])
    while len(score) < 2:
        score.append(0)

    tier = str(row.get("tier") or "NORMAL").upper()
    accent = GOLD if tier == "STRONG" else ACCENT
    odd = float(row.get("odd") or 0.0)
    market = _market_label(str(row.get("market") or row.get("selection") or "?"))
    probability = float(row.get("probability") or row.get("model_probability") or 0.0)
    edge = float(row.get("edge") or row.get("value_edge") or 0.0)

    img = Image.new("RGBA", (W, 980), BG + (255,))
    draw = ImageDraw.Draw(img)

    # Header: restrained brand bar, not another oversized panel.
    draw.rounded_rectangle((28, 22, 1052, 94), 22, fill=PANEL, outline=accent, width=2)
    draw.text((54, 40), "GOOL", font=sc._font(29, True), fill=TEXT)
    draw.text((154, 40), "V4", font=sc._font(29, True), fill=accent)
    state = "LIVE" if live else "PREMATCH"
    draw.rounded_rectangle((850, 36, 1020, 80), 14, fill=(9, 19, 31), outline=accent, width=2)
    _center_in_box(draw, state, (850, 36, 1020, 80), sc._font(15, True), accent)

    league_text = league.upper()
    _center(draw, league_text, 122, _fit(draw, league_text, 900, 16, True), MUTED)

    # Hero matchup. Real crests are the visual anchor.
    sc._badge(img, draw, 205, 275, sc._logo(meta, "home"), home, accent)
    sc._badge(img, draw, 875, 275, sc._logo(meta, "away"), away, accent)

    draw.rounded_rectangle((405, 205, 675, 345), 28, fill=(8, 17, 28), outline=accent, width=2)
    if live:
        _center(draw, f"{int(score[0])} : {int(score[1])}", 232, sc._font(58, True), TEXT)
        _center(draw, f"{minute}'", 304, sc._font(20, True), accent)
    else:
        _center(draw, "VS", 226, sc._font(50, True), TEXT)
        kickoff = str(row.get("scheduled_start") or row.get("kickoff") or "ДО МАТЧА")
        _center(draw, kickoff, 301, _fit(draw, kickoff, 230, 16, True), accent)

    # Team names stay close to their crests; long names shrink independently.
    _center_in_box(draw, home, (65, 360, 470, 410), _fit(draw, home, 380, 21, True), TEXT)
    _center_in_box(draw, away, (610, 360, 1015, 410), _fit(draw, away, 380, 21, True), TEXT)

    # Main bet is one decisive information block.
    draw.rounded_rectangle((48, 448, 1032, 630), 26, fill=PANEL2, outline=accent, width=2)
    draw.text((78, 475), "ВЫБОР МОДЕЛИ", font=sc._font(13, True), fill=MUTED)
    draw.text((78, 515), market, font=_fit(draw, market, 610, 38, True), fill=TEXT)
    draw.line((735, 480, 735, 598), fill=(49, 66, 82), width=2)
    draw.text((780, 475), "КОЭФФИЦИЕНТ", font=sc._font(13, True), fill=MUTED)
    draw.text((780, 518), f"{odd:.2f}" if odd > 1 else "—", font=sc._font(48, True), fill=accent)

    # Compact analytics strip.
    draw.rounded_rectangle((48, 662, 1032, 802), 22, fill=PANEL, outline=(42, 59, 75), width=2)
    draw.text((78, 687), "УРОВЕНЬ", font=sc._font(12, True), fill=MUTED)
    draw.text((78, 724), tier, font=sc._font(27, True), fill=accent)
    draw.text((410, 687), "ВЕРОЯТНОСТЬ", font=sc._font(12, True), fill=MUTED)
    draw.text((410, 724), f"{probability * 100:.1f}%", font=sc._font(27, True), fill=TEXT)
    draw.text((760, 687), "ПЕРЕВЕС", font=sc._font(12, True), fill=MUTED)
    draw.text((760, 724), f"+{edge * 100:.1f} п.п." if edge > 0 else "—", font=sc._font(27, True), fill=TEXT)

    # Thin lifecycle footer instead of a large button.
    draw.line((110, 865, 970, 865), fill=(42, 59, 75), width=2)
    _center(draw, "PREMATCH   •   В ИГРЕ   •   РЕЗУЛЬТАТ", 890, sc._font(14, True), MUTED)
    _center(draw, "GOOL V4 · MODEL SIGNAL", 930, sc._font(11, True), accent)
    return sc._save(img)

def _center_in_box(draw, text: str, box, font, fill):
    left, top, right, bottom = box
    bb = draw.textbbox((0, 0), text, font=font)
    x = left + ((right - left) - (bb[2] - bb[0])) / 2
    y = top + ((bottom - top) - (bb[3] - bb[1])) / 2 - bb[1]
    draw.text((x, y), text, font=font, fill=fill)


def render_v4_prematch_result_card(row: dict[str, Any], record: dict[str, Any] | None = None) -> str:
    """Dedicated settled PREMATCH card: WIN / LOSS / RETURN with final score."""
    record = record or {}
    match = record.get("match") or {}
    home = str(row.get("home") or match.get("home") or "?")
    away = str(row.get("away") or match.get("away") or "?")
    league = str(row.get("league") or match.get("league") or "FOOTBALL")
    meta = _meta(row, record)
    result = str(row.get("result") or "void").lower()
    result_label = {"won": "ЗАШЛА", "lost": "НЕ ЗАШЛА", "push": "ВОЗВРАТ", "void": "ВОЗВРАТ"}.get(result, "РЕЗУЛЬТАТ")
    accent = ACCENT if result == "won" else RED if result == "lost" else NEUTRAL
    settled = list(row.get("settled_score") or row.get("current_score") or [0, 0])
    while len(settled) < 2:
        settled.append(0)
    market = _market_label(str(row.get("market") or row.get("selection") or "?"))
    odd = float(row.get("odd") or 0.0)

    img = Image.new("RGBA", (W, 900), BG + (255,))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((28, 22, 1052, 94), 22, fill=PANEL, outline=accent, width=2)
    draw.text((54, 40), "GOOL", font=sc._font(29, True), fill=TEXT)
    draw.text((154, 40), "V4", font=sc._font(29, True), fill=accent)
    draw.rounded_rectangle((790, 36, 1020, 80), 14, fill=(9, 19, 31), outline=accent, width=2)
    _center_in_box(draw, result_label, (790, 36, 1020, 80), sc._font(15, True), accent)

    _center(draw, league.upper(), 122, _fit(draw, league.upper(), 900, 16, True), MUTED)
    sc._badge(img, draw, 205, 270, sc._logo(meta, "home"), home, accent)
    sc._badge(img, draw, 875, 270, sc._logo(meta, "away"), away, accent)

    draw.rounded_rectangle((405, 198, 675, 345), 28, fill=(8, 17, 28), outline=accent, width=3)
    _center(draw, f"{int(settled[0])} : {int(settled[1])}", 226, sc._font(60, True), TEXT)
    _center(draw, "ФИНАЛ", 304, sc._font(17, True), accent)
    _center_in_box(draw, home, (65, 355, 470, 405), _fit(draw, home, 380, 21, True), TEXT)
    _center_in_box(draw, away, (610, 355, 1015, 405), _fit(draw, away, 380, 21, True), TEXT)

    draw.rounded_rectangle((48, 448, 1032, 620), 26, fill=PANEL2, outline=accent, width=2)
    draw.text((78, 474), "СТАВКА", font=sc._font(13, True), fill=MUTED)
    draw.text((78, 514), market, font=_fit(draw, market, 610, 36, True), fill=TEXT)
    draw.line((735, 478, 735, 590), fill=(49, 66, 82), width=2)
    draw.text((780, 474), "ВЗЯТЫЙ КЭФ", font=sc._font(13, True), fill=MUTED)
    draw.text((780, 514), f"{odd:.2f}" if odd > 1 else "—", font=sc._font(46, True), fill=accent)

    draw.rounded_rectangle((190, 675, 890, 770), 24, fill=(9, 19, 31), outline=accent, width=3)
    _center_in_box(draw, result_label, (190, 675, 890, 770), sc._font(34, True), accent)
    _center(draw, "PREMATCH · РЕЗУЛЬТАТ ПОДТВЕРЖДЁН", 820, sc._font(14, True), MUTED)
    _center(draw, "GOOL V4", 855, sc._font(11, True), accent)
    return sc._save(img)

def render_v4_parlay_card(row: dict[str, Any], *, result: bool = False) -> str:
    """Combined entry/result PNG for DOUBLE and SUPER parent bets."""
    kind = str(row.get("kind") or "DOUBLES").upper()
    legs = list(row.get("legs") or [])
    final = str(row.get("result") or "pending").lower()
    if result:
        title = {"won": "ЗАШЁЛ", "lost": "НЕ ЗАШЁЛ", "push": "ВОЗВРАТ", "void": "VOID"}.get(final, "РЕЗУЛЬТАТ")
        accent = ACCENT if final == "won" else GOLD
    else:
        title = "SUPER 10" if kind == "SUPER" else "ЭКСПРЕСС"
        accent = GOLD if kind == "SUPER" else ACCENT
    height = max(760, 330 + len(legs) * 92)
    img = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 20, 1056, 105), 22, fill=PANEL, outline=accent, width=3)
    draw.text((50, 40), "GOOL V4", font=sc._font(30, True), fill=accent)
    _center_in_box(draw, title, (700, 32, 1028, 92), sc._font(22, True), accent)
    odd = float(row.get("effective_odd") or row.get("odd") or 0.0)
    _center(draw, f"{'ИТОГ · ' if result else ''}{'SUPER 10' if kind == 'SUPER' else 'ЭКСПРЕСС'}", 135, sc._font(31, True), TEXT)
    _center(draw, f"Общий коэффициент {odd:.2f}", 180, sc._font(25, True), accent)
    y = 245
    icons = {"won": "✓", "lost": "×", "push": "↩", "void": "↩", "pending": "•"}
    for i, leg in enumerate(legs, 1):
        lr = str(leg.get("result") or "pending").lower()
        draw.rounded_rectangle((45, y, 1035, y + 76), 16, fill=PANEL2)
        draw.text((65, y + 13), f"{icons.get(lr, '•')} {i}.", font=sc._font(18, True), fill=accent)
        teams = f"{leg.get('home','?')} — {leg.get('away','?')}"
        draw.text((125, y + 10), teams, font=_fit(draw, teams, 590, 19, True), fill=TEXT)
        result_label = {"won":"ЗАШЛА","lost":"НЕ ЗАШЛА","push":"ВОЗВРАТ","void":"VOID","pending":"ЖДЁМ"}.get(lr, "ЖДЁМ")
        detail = f"{leg.get('league','FOOTBALL')} · {leg.get('scheduled_start','')} · {_market_label(str(leg.get('market','?')))}"
        if result:
            detail += f" · {result_label}"
        draw.text((125, y + 43), detail, font=_fit(draw, detail, 700, 13, False), fill=MUTED)
        draw.text((875, y + 22), f"@ {float(leg.get('odd') or 0):.2f}", font=sc._font(18, True), fill=TEXT)
        y += 92
    footer = "РЕЗУЛЬТАТ ЗАФИКСИРОВАН" if result else "ЕДИНАЯ СТАВКА · ВСЕ НОГИ В ЖУРНАЛЕ"
    draw.rounded_rectangle((245, height - 78, 835, height - 25), 16, fill=accent)
    _center_in_box(draw, footer, (245, height - 78, 835, height - 25), sc._font(15, True), BG)
    return sc._save(img)
