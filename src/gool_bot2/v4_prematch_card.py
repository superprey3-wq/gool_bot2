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
    """V4 PREMATCH card using the established GOOL visual language/assets."""
    record = record or {}
    match = record.get("match") or {}
    home = str(row.get("home") or match.get("home") or "?")
    away = str(row.get("away") or match.get("away") or "?")
    league = str(row.get("league") or match.get("league") or "FOOTBALL")
    meta = _meta(row, record)

    lifecycle = str(row.get("lifecycle") or "")
    live = bool(in_game or lifecycle == "in_game")
    minute = int(row.get("current_minute") or match.get("minute") or 0)
    score = list(row.get("current_score") or [
        int(match.get("home_score") or 0), int(match.get("away_score") or 0)
    ])
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
    draw.rounded_rectangle((24, 20, 1056, 100), 22, fill=PANEL, outline=accent, width=2)
    draw.text((50, 38), "GOOL V4", font=sc._font(30, True), fill=accent)
    state = "LIVE · PREMATCH BET" if live else "PREMATCH"
    draw.rounded_rectangle((790, 32, 1028, 87), 15, outline=accent, width=2)
    _center_in_box(draw, state, (790, 32, 1028, 87), sc._font(16, True), accent)

    _center(draw, f"🏆 {league}", 126, _fit(draw, f"🏆 {league}", 930, 19, True), TEXT)


    draw.rounded_rectangle((390, 200, 690, 360), 28, fill=(9, 19, 31), outline=accent, width=3)
    if live:
        _center(draw, f"{int(score[0])} : {int(score[1])}", 238, sc._font(62, True), TEXT)
        _center(draw, f"{minute}'", 315, sc._font(24, True), accent)
    else:
        _center(draw, "VS", 238, sc._font(58, True), TEXT)
        kickoff = str(row.get("scheduled_start") or row.get("kickoff") or "ДО МАТЧА")
        _center(draw, kickoff, 315, _fit(draw, kickoff, 260, 18, True), accent)

    _center(draw, f"{home}  —  {away}", 390, _fit(draw, f"{home}  —  {away}", 900, 28, True), TEXT)

    draw.rounded_rectangle((45, 475, 1035, 650), 25, fill=PANEL2, outline=accent, width=3)
    draw.text((75, 500), "СТАВКА", font=sc._font(16, True), fill=MUTED)
    draw.text((75, 535), market, font=_fit(draw, market, 650, 34, True), fill=TEXT)
    draw.text((780, 500), "КОЭФФИЦИЕНТ", font=sc._font(14, True), fill=MUTED)
    draw.text((780, 535), f"{odd:.2f}" if odd > 1 else "—", font=sc._font(44, True), fill=accent)

    draw.rounded_rectangle((45, 680, 1035, 825), 22, fill=PANEL, outline=accent, width=2)
    draw.text((75, 705), "УРОВЕНЬ", font=sc._font(14, True), fill=MUTED)
    draw.text((75, 740), tier, font=sc._font(29, True), fill=accent)
    draw.text((385, 705), "МОДЕЛЬ", font=sc._font(14, True), fill=MUTED)
    draw.text((385, 740), f"{probability * 100:.1f}%", font=sc._font(29, True), fill=TEXT)
    draw.text((700, 705), "ПЕРЕВЕС", font=sc._font(14, True), fill=MUTED)
    draw.text((700, 740), f"+{edge * 100:.1f} п.п." if edge > 0 else "—", font=sc._font(29, True), fill=TEXT)

    footer = "PREMATCH → В ИГРЕ → РЕЗУЛЬТАТ"
    draw.rounded_rectangle((275, 885, 805, 940), 16, fill=accent)
    _center_in_box(draw, footer, (275, 885, 805, 940), sc._font(16, True), BG)
    return sc._save(img)


def _center_in_box(draw, text: str, box, font, fill):
    left, top, right, bottom = box
    bb = draw.textbbox((0, 0), text, font=font)
    x = left + ((right - left) - (bb[2] - bb[0])) / 2
    y = top + ((bottom - top) - (bb[3] - bb[1])) / 2 - bb[1]
    draw.text((x, y), text, font=font, fill=fill)


def render_v4_prematch_result_card(row: dict[str, Any], record: dict[str, Any] | None = None) -> str:
    """Result card reusing the established badge/tournament visual identity."""
    result = str(row.get("result") or "void").lower()
    labels = {"won": "ЗАШЛА", "lost": "НЕ ЗАШЛА", "push": "ВОЗВРАТ", "void": "VOID"}
    settled = list(row.get("settled_score") or [0, 0])
    clone = dict(row)
    clone["lifecycle"] = "in_game"
    clone["current_minute"] = int(row.get("settled_minute") or 90)
    clone["current_score"] = settled
    clone["tier"] = labels.get(result, "RESULT")
    return render_v4_prematch_card(clone, record=record, in_game=True)


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
