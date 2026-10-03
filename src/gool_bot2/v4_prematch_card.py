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
    # Merge every available source instead of returning the first non-empty dict.
    # Older PREMATCH journal rows may contain team ids/slugs but no crest files.
    merged: dict[str, Any] = {}
    event_id = str(row.get("event_id") or row.get("match_id") or "")
    try:
        cached = (sc._read_assets().get(event_id) or {}).get("flashscore_meta") or {}
        if isinstance(cached, dict):
            merged.update(cached)
    except Exception:
        pass
    live = sc.flashscore_meta(record or {})
    if isinstance(live, dict):
        merged.update({k: v for k, v in live.items() if v not in (None, "")})
    direct = dict(row.get("flashscore_meta") or {})
    merged.update({k: v for k, v in direct.items() if v not in (None, "")})
    return merged


def _market_label(value: str) -> str:
    raw = str(value or "").strip()
    key = raw.upper()
    labels = {"FT_OVER_2.5":"ТБ 2.5","FT_UNDER_2.5":"ТМ 2.5","BTTS_YES":"Обе забьют — Да","BTTS_NO":"Обе забьют — Нет","1H_OVER_0.5":"1-й тайм · ТБ 0.5","1H_OVER_1.5":"1-й тайм · ТБ 1.5","2H_OVER_0.5":"2-й тайм · ТБ 0.5","2H_OVER_1.5":"2-й тайм · ТБ 1.5"}
    if key in labels:
        return labels[key]
    low = raw.casefold().replace("_", " ")
    import re
    m = re.search(r"\b(over|under)\s+(\d+(?:[.,]\d+)?)", low)
    if m:
        return f"{'ТБ' if m.group(1) == 'over' else 'ТМ'} {m.group(2).replace(',', '.')}"
    if "btts" in low or "обе забьют" in low:
        if "no" in low or "нет" in low:
            return "Обе забьют — Нет"
        if "yes" in low or "да" in low:
            return "Обе забьют — Да"
    return raw.replace("_", " ") or "?"

def render_v4_prematch_card(
    row: dict[str, Any],
    record: dict[str, Any] | None = None,
    *,
    in_game: bool = False,
) -> str:
    """PREMATCH card using the same visual hierarchy as GOOL LIVE."""
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
    while len(score) < 2: score.append(0)

    tier = str(row.get("tier") or "NORMAL").upper()
    product = str(row.get("product") or "").casefold()
    is_value = product == "value_hunter" or tier == "VALUE"
    accent = GOLD if tier == "STRONG" or is_value else ACCENT
    odd = float(row.get("odd") or 0.0)
    market = _market_label(str(row.get("selection") or row.get("market") or "?"))
    probability = float(row.get("probability") or row.get("model_probability") or 0.0)
    edge = float(row.get("edge") or row.get("value_edge") or 0.0)
    market_probability = float(row.get("market_probability") or max(0.0, probability - edge))
    kickoff = str(row.get("scheduled_start") or row.get("kickoff") or "ДО МАТЧА")

    img = Image.new("RGBA", (W, 1120), BG + (255,))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((18,18,1062,1102),30,outline=accent,width=5)
    draw.rounded_rectangle((24,20,1056,112),24,fill=PANEL,outline=accent,width=2)
    draw.rectangle((24,20,40,112),fill=accent)
    draw.text((58,36),"GOOL V4",font=sc._font(36,True),fill=accent)
    draw.text((58,78),"VALUE HUNTER • HIGH ODDS" if is_value else "PREMATCH • MODEL RADAR",font=sc._font(15,True),fill=TEXT)
    draw.rounded_rectangle((825,38,1028,93),16,fill=accent)
    _center_in_box(draw,"В ИГРЕ" if live else ("VALUE" if is_value else "PREMATCH"),(825,38,1028,93),sc._font(18,True),BG)

    _center(draw, f"🏆 {league}", 145, _fit(draw, f"🏆 {league}", 930, 18, True), MUTED)
    sc._badge(img, draw, 180, 300, sc._logo(meta,"home"), home, accent)
    sc._badge(img, draw, 900, 300, sc._logo(meta,"away"), away, accent)
    draw.rounded_rectangle((390,220,690,385),28,fill=(9,19,31),outline=accent,width=4)
    if live:
        _center(draw,f"{int(score[0])} : {int(score[1])}",258,sc._font(62,True),TEXT)
        _center(draw,f"{minute}'",335,sc._font(24,True),accent)
    else:
        _center(draw,"VS",245,sc._font(58,True),TEXT)
        _center(draw,kickoff,330,_fit(draw,kickoff,260,18,True),accent)
    for x,n in ((180,home),(900,away)):
        font=_fit(draw,n,330,27,True); bb=draw.textbbox((0,0),n,font=font)
        draw.text((x-(bb[2]-bb[0])/2,415),n,font=font,fill=TEXT)

    draw.rounded_rectangle((55,485,1025,700),26,fill=PANEL2,outline=accent,width=3)
    draw.text((82,510),"BEST BET",font=sc._font(18,True),fill=accent)
    draw.text((82,550),market,font=_fit(draw,market,590,43,True),fill=TEXT)
    draw.text((755,510),"КОЭФФИЦИЕНТ",font=sc._font(14,True),fill=MUTED)
    draw.text((755,550),f"{odd:.2f}" if odd>1 else "—",font=sc._font(52,True),fill=accent)
    draw.line((710,520,710,665),fill=(49,66,82),width=2)
    draw.text((82,635),f"УРОВЕНЬ • {tier}",font=sc._font(17,True),fill=accent)

    draw.rounded_rectangle((55,730,1025,880),24,fill=PANEL,outline=(42,59,75),width=2)
    draw.text((82,752),"ВЕРОЯТНОСТЬ МОДЕЛИ",font=sc._font(13,True),fill=MUTED)
    draw.text((82,790),f"{probability*100:.1f}%",font=sc._font(36,True),fill=TEXT)
    draw.text((405,752),"ОЦЕНКА РЫНКА",font=sc._font(13,True),fill=MUTED)
    draw.text((405,790),f"{market_probability*100:.1f}%",font=sc._font(36,True),fill=TEXT)
    draw.text((735,752),"ПЕРЕВЕС",font=sc._font(13,True),fill=MUTED)
    draw.text((735,790),f"{edge*100:+.1f} п.п.",font=sc._font(36,True),fill=accent if edge>0 else MUTED)

    draw.rounded_rectangle((55,910,1025,985),20,fill=PANEL2,outline=accent,width=2)
    footer_text = "GOOL НАШЁЛ ЗАВЫШЕННУЮ ЦЕНУ БУКМЕКЕРА" if is_value else "GOOL ВЫБРАЛ ЛУЧШИЙ ДОСТУПНЫЙ РЫНОК ДЛЯ ЭТОГО МАТЧА"
    _center(draw,footer_text,932,_fit(draw,footer_text,900,16,True),TEXT)
    _center(draw,"PREMATCH  •  В ИГРЕ  •  РЕЗУЛЬТАТ",1030,sc._font(15,True),MUTED)
    _center(draw,"GOOL V4 • MODEL SIGNAL",1068,sc._font(12,True),accent)
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
    draw.rounded_rectangle((18, 18, 1062, 882), 30, outline=accent, width=5)
    draw.rounded_rectangle((28, 22, 1052, 94), 22, fill=PANEL, outline=accent, width=2)
    draw.text((54, 40), "GOOL", font=sc._font(29, True), fill=TEXT)
    draw.text((154, 40), "V4 · PREMATCH RESULT", font=sc._font(25, True), fill=accent)
    draw.rounded_rectangle((790, 36, 1020, 80), 14, fill=(9, 19, 31), outline=accent, width=2)
    _center_in_box(draw, result_label, (790, 36, 1020, 80), sc._font(15, True), accent)

    # Tournament is primary PREMATCH context, not muted technical metadata.
    draw.rounded_rectangle((120, 112, 960, 174), 18, fill=PANEL2, outline=accent, width=2)
    tournament = f"🏆 {league}"
    _center_in_box(draw, tournament, (120, 112, 960, 174), _fit(draw, tournament, 790, 24, True), TEXT)
    kickoff = str(row.get("scheduled_start") or "")
    if kickoff:
        _center(draw, f"PREMATCH · СТАРТ {kickoff}", 184, _fit(draw, f"PREMATCH · СТАРТ {kickoff}", 760, 15, True), accent)
    sc._badge(img, draw, 205, 300, sc._logo(meta, "home"), home, accent)
    sc._badge(img, draw, 875, 300, sc._logo(meta, "away"), away, accent)

    draw.rounded_rectangle((405, 228, 675, 375), 28, fill=(8, 17, 28), outline=accent, width=3)
    _center(draw, f"{int(settled[0])} : {int(settled[1])}", 256, sc._font(60, True), TEXT)
    _center(draw, "ФИНАЛ", 334, sc._font(17, True), accent)
    _center_in_box(draw, home, (65, 385, 470, 435), _fit(draw, home, 380, 21, True), TEXT)
    _center_in_box(draw, away, (610, 385, 1015, 435), _fit(draw, away, 380, 21, True), TEXT)

    draw.rounded_rectangle((48, 468, 1032, 640), 26, fill=PANEL2, outline=accent, width=2)
    draw.text((78, 494), "СТАВКА", font=sc._font(13, True), fill=MUTED)
    draw.text((78, 534), market, font=_fit(draw, market, 610, 36, True), fill=TEXT)
    draw.line((735, 498, 735, 610), fill=(49, 66, 82), width=2)
    draw.text((780, 494), "ВЗЯТЫЙ КЭФ", font=sc._font(13, True), fill=MUTED)
    draw.text((780, 534), f"{odd:.2f}" if odd > 1 else "—", font=sc._font(46, True), fill=accent)

    draw.rounded_rectangle((190, 675, 890, 770), 24, fill=(9, 19, 31), outline=accent, width=3)
    _center_in_box(draw, result_label, (190, 675, 890, 770), sc._font(34, True), accent)
    probability = float(row.get("probability") or row.get("model_probability") or 0.0)
    edge = float(row.get("edge") or row.get("value_edge") or 0.0)
    draw.text((95, 802), f"МОДЕЛЬ {probability*100:.1f}%", font=sc._font(14, True), fill=TEXT)
    draw.text((780, 802), f"ПЕРЕВЕС {edge*100:+.1f} п.п.", font=sc._font(14, True), fill=accent)
    _center(draw, "PREMATCH · РЕЗУЛЬТАТ ПОДТВЕРЖДЁН", 840, sc._font(13, True), MUTED)
    _center(draw, "GOOL V4", 865, sc._font(10, True), accent)
    return sc._save(img)

def _mini_team_badge(img: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, meta: dict[str, Any], side: str, name: str, accent) -> None:
    r = 34
    draw.ellipse((x-r-4, y-r-4, x+r+4, y+r+4), outline=accent, width=2)
    draw.ellipse((x-r, y-r, x+r, y+r), fill=PANEL2, outline=(42, 59, 75), width=1)
    logo = sc._logo(meta, side)
    if logo:
        bb = logo.getbbox()
        logo = logo.crop(bb) if bb else logo
        scale = min(54 / max(1, logo.width), 54 / max(1, logo.height))
        logo = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.Resampling.LANCZOS)
        img.alpha_composite(logo, (x-logo.width//2, y-logo.height//2))
        return
    initials = "".join(part[:1] for part in str(name).split()[:2]).upper() or "?"
    font = sc._font(15, True)
    bb = draw.textbbox((0, 0), initials, font=font)
    draw.text((x-(bb[2]-bb[0])/2, y-10), initials, font=font, fill=TEXT)


def render_v4_parlay_card(row: dict[str, Any], *, result: bool = False) -> str:
    """PREMATCH parlay card with team crests, tournament, Moscow kickoff and leg status."""
    kind = str(row.get("kind") or "DOUBLES").upper()
    legs = list(row.get("legs") or [])
    final = str(row.get("result") or "pending").lower()
    if result:
        title = {"won": "ЗАШЁЛ", "lost": "НЕ ЗАШЁЛ", "push": "ВОЗВРАТ", "void": "ВОЗВРАТ"}.get(final, "РЕЗУЛЬТАТ")
        accent = ACCENT if final == "won" else RED if final == "lost" else NEUTRAL
    else:
        title = "SUPER 10" if kind == "SUPER" else "ЭКСПРЕСС"
        accent = GOLD if kind == "SUPER" else ACCENT

    row_h = 152
    height = max(780, 260 + len(legs) * row_h + 120)
    img = Image.new("RGBA", (W, height), BG + (255,))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((18, 18, 1062, height - 18), 30, outline=accent, width=5)
    draw.rounded_rectangle((28, 22, 1052, 100), 22, fill=PANEL, outline=accent, width=2)
    draw.text((52, 42), "GOOL V4", font=sc._font(29, True), fill=TEXT)
    draw.text((190, 43), "PREMATCH · ЭКСПРЕСС", font=sc._font(23, True), fill=accent)
    _center_in_box(draw, title, (760, 34, 1025, 88), sc._font(18, True), accent)

    odd = float(row.get("effective_odd") or row.get("odd") or 0.0)
    headline = "ИТОГ ЭКСПРЕССА" if result else ("SUPER 10" if kind == "SUPER" else "ЭКСПРЕСС")
    _center(draw, headline, 125, sc._font(31, True), TEXT)
    _center(draw, f"Общий коэффициент · {odd:.2f}", 168, sc._font(22, True), accent)

    y = 220
    status_labels = {"won":"✓ ЗАШЛА","lost":"✕ НЕ ЗАШЛА","push":"↩ ВОЗВРАТ","void":"↩ ВОЗВРАТ","pending":"ЖДЁМ"}
    for i, leg in enumerate(legs, 1):
        lr = str(leg.get("result") or "pending").lower()
        leg_accent = ACCENT if lr == "won" else RED if lr == "lost" else accent
        meta = dict(leg.get("flashscore_meta") or {})
        home = str(leg.get("home") or "?")
        away = str(leg.get("away") or "?")
        league = str(leg.get("league") or "FOOTBALL")
        kickoff = str(leg.get("scheduled_start") or "Время не указано")
        market = _market_label(str(leg.get("selection") or leg.get("market") or "?"))
        leg_odd = float(leg.get("odd") or 0.0)

        draw.rounded_rectangle((45, y, 1035, y + 132), 22, fill=PANEL2, outline=leg_accent if result else (42,59,75), width=2)
        draw.text((64, y + 14), f"{i}.", font=sc._font(18, True), fill=leg_accent)
        _mini_team_badge(img, draw, 112, y + 66, meta, "home", home, leg_accent)
        _mini_team_badge(img, draw, 190, y + 66, meta, "away", away, leg_accent)

        teams = f"{home} — {away}"
        draw.text((245, y + 13), teams, font=_fit(draw, teams, 520, 20, True), fill=TEXT)
        tournament = f"ТУРНИР · {league}"
        draw.text((245, y + 45), tournament, font=_fit(draw, tournament, 585, 14, True), fill=MUTED)
        timing = f"СТАРТ · {kickoff}"
        draw.text((245, y + 72), timing, font=_fit(draw, timing, 400, 14, True), fill=MUTED)
        bet = f"{market}  @ {leg_odd:.2f}"
        draw.text((245, y + 99), bet, font=_fit(draw, bet, 520, 18, True), fill=leg_accent if result else accent)
        if result:
            label = status_labels.get(lr, "ЖДЁМ")
            draw.text((805, y + 19), label, font=_fit(draw, label, 200, 16, True), fill=leg_accent)
            score = leg.get("settled_score") or leg.get("current_score")
            if isinstance(score, (list, tuple)) and len(score) >= 2:
                score_text = f"{int(score[0] or 0)}:{int(score[1] or 0)}"
                draw.text((865, y + 58), score_text, font=sc._font(25, True), fill=TEXT)
        y += row_h

    footer_top = height - 92
    draw.rounded_rectangle((235, footer_top, 845, height - 35), 18, fill=accent)
    footer = "РЕЗУЛЬТАТ ЭКСПРЕССА ПОДТВЕРЖДЁН" if result else "PREMATCH · ВСЕ НОГИ ЗАФИКСИРОВАНЫ"
    _center_in_box(draw, footer, (235, footer_top, 845, height - 35), _fit(draw, footer, 560, 15, True), BG)
    return sc._save(img)
