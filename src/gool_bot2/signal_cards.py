from __future__ import annotations

import json
import os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .match_context import provider_pair
from .providers.common import UA
from .providers.flashscore import FlashscoreProvider

W = 1080
TEXT = (248, 250, 252)
MUTED = (165, 178, 196)
DARK = (3, 8, 15)
PANEL = (8, 15, 25)
PANEL2 = (10, 21, 34)
LINE = (48, 67, 88)
RED = (239, 73, 82)
GOLD = (255, 194, 55)

THEMES = {
    "another_goal": {"accent": (65, 222, 86), "deep": (0, 46, 20), "label": "ЕЩЁ ГОЛ"},
    "over_2_5": {"accent": (39, 151, 255), "deep": (0, 31, 69), "label": "ТОТАЛ БОЛЬШЕ 2.5"},
    "both_teams_to_score": {"accent": (181, 65, 255), "deep": (55, 5, 78), "label": "ОБЕ ЗАБЬЮТ — ДА"},
    "goal_before_ht": {"accent": (255, 132, 26), "deep": (79, 29, 0), "label": "ГОЛ ДО ПЕРЕРЫВА"},
}
HEAD_LABELS = {k: v["label"] for k, v in THEMES.items()}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 34, bold: bool = True) -> ImageFont.ImageFont:
    text = str(text or "")
    for size in range(start, 13, -2):
        f = _font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= width:
            return f
    return _font(13, bold)


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _save(img: Image.Image) -> bytes:
    out = BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True, quality=95)
    return out.getvalue()


def _safe_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "—"


def _fmt_pair(pair: tuple[float | None, float | None], digits: int = 0, suffix: str = "") -> str:
    a, b = pair
    if a is None or b is None:
        return "—"
    if digits:
        return f"{a:.{digits}f}{suffix} : {b:.{digits}f}{suffix}"
    return f"{int(round(a))}{suffix} : {int(round(b))}{suffix}"


def _stats_for_record(record: dict[str, Any]) -> dict[str, str]:
    return {
        "xg": _fmt_pair(provider_pair(record, "xg"), 2),
        "shots": _fmt_pair(provider_pair(record, "shots")),
        "sot": _fmt_pair(provider_pair(record, "shots_on_target")),
        "corners": _fmt_pair(provider_pair(record, "corners")),
        "possession": _fmt_pair(provider_pair(record, "possession"), 0, "%"),
        "big_chances": _fmt_pair(provider_pair(record, "big_chances")),
    }


def stats_snapshot(record: dict[str, Any]) -> dict[str, str]:
    return _stats_for_record(record)


def flashscore_meta(record: dict[str, Any]) -> dict[str, Any]:
    return dict((((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}))


def _asset_path() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "signal_card_assets.json"


def _read_assets() -> dict[str, Any]:
    try:
        path = _asset_path()
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember(match_id: str, meta: dict[str, Any], stats: dict[str, str]) -> None:
    if not match_id:
        return
    cache = _read_assets()
    cache[match_id] = {"flashscore_meta": meta, "stats_snapshot": stats}
    if len(cache) > 700:
        cache = dict(list(cache.items())[-700:])
    try:
        path = _asset_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except Exception:
        pass


@lru_cache(maxsize=2048)
def _download(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": UA, "Referer": "https://www.flashscore.com/"})
        with urlopen(req, timeout=7) as response:
            raw = response.read(700_000)
        image = Image.open(BytesIO(raw)).convert("RGBA")
        image.thumbnail((190, 190), Image.Resampling.LANCZOS)
        return image.copy()
    except Exception:
        return None


def _team_logo(meta: dict[str, Any], side: str) -> Image.Image | None:
    fn = str(meta.get(f"{side}_logo_file") or "").strip()
    if fn:
        logo = _download(f"https://static.flashscore.com/res/image/data/{fn}")
        if logo is not None:
            return logo
    explicit = str(meta.get(f"{side}_logo_url") or "").strip()
    if explicit:
        logo = _download(explicit)
        if logo is not None:
            return logo
    slug = str(meta.get(f"{side}_team_slug") or "")
    team_id = str(meta.get(f"{side}_team_id") or "")
    url = FlashscoreProvider.team_logo_url(slug, team_id) or ""
    return _download(url) if url else None


def _canvas(height: int, accent: tuple[int, int, int], deep: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (W, height), DARK)
    px = img.load()
    for y in range(height):
        t = y / max(1, height - 1)
        glow = max(0.0, 1.0 - abs(t - 0.28) * 2.5)
        for x in range(W):
            edge = abs(x - W / 2) / (W / 2)
            g = glow * (1.0 - 0.38 * edge)
            px[x, y] = tuple(min(255, int(DARK[i] * (1-g) + deep[i] * g + accent[i] * g * 0.07)) for i in range(3))
    return img.convert("RGBA")


def _stadium(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
    horizon = 355
    for x in range(15, W, 55):
        draw.line((W//2, horizon, x, 570), fill=(27, 64, 77), width=1)
    draw.arc((35, 255, 1045, 600), 190, 350, fill=(36, 75, 89), width=2)
    draw.arc((95, 290, 985, 635), 190, 350, fill=(25, 57, 72), width=2)
    draw.line((80, 558, 1000, 558), fill=(31, 83, 84), width=2)
    for x in (90, 185, 280, 375, 705, 800, 895, 990):
        draw.ellipse((x-3, 304, x+3, 310), fill=accent)
    draw.rectangle((0, 345, W, 360), fill=(6, 20, 25))


def _header(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int], label: str, league: str, round_name: str, providers: int, result: bool = False) -> None:
    draw.rounded_rectangle((18, 16, 1062, 112), 24, fill=(5, 14, 23), outline=accent, width=3)
    draw.rounded_rectangle((34, 31, 190, 98), 16, fill=(10, 28, 31), outline=accent, width=2)
    gool_font = _fit(draw, "GOOL v2", 126, 25, True)
    box = draw.textbbox((0, 0), "GOOL v2", font=gool_font)
    draw.text((112 - (box[2]-box[0])/2, 49), "GOOL v2", font=gool_font, fill=TEXT)
    draw.text((215, 28), label, font=_fit(draw, label, 500, 33, True), fill=TEXT)
    draw.text((217, 70), "РЕЗУЛЬТАТ" if result else "LIVE-СИГНАЛ", font=_font(16, True), fill=accent)
    draw.text((935, 43), "RESULT" if result else "LIVE", font=_font(18, True), fill=accent)
    draw.text((850, 72), f"DATA {providers}/3", font=_font(14, True), fill=MUTED)

    competition = league + (f" · {round_name}" if round_name else "")
    comp_font = _fit(draw, competition, 900, 18, True)
    comp_box = draw.textbbox((0, 0), competition, font=comp_font)
    draw.text(((W - (comp_box[2]-comp_box[0])) / 2, 126), competition, font=comp_font, fill=TEXT)


def _paste_logo(img: Image.Image, draw: ImageDraw.ImageDraw, logo: Image.Image | None, name: str, cx: int, cy: int, accent: tuple[int, int, int]) -> None:
    if logo is not None:
        box = logo.getbbox()
        logo = logo.crop(box) if box else logo
        scale = min(150 / max(1, logo.width), 150 / max(1, logo.height))
        logo = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.Resampling.LANCZOS)
        shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
        alpha = logo.getchannel("A").filter(ImageFilter.GaussianBlur(5))
        shadow.putalpha(alpha)
        shadow = ImageEnhance.Brightness(shadow).enhance(0)
        img.alpha_composite(shadow, (cx - logo.width//2 + 4, cy - logo.height//2 + 7))
        img.alpha_composite(logo, (cx - logo.width//2, cy - logo.height//2))
        return
    draw.ellipse((cx-65, cy-65, cx+65, cy+65), fill=PANEL2, outline=accent, width=3)
    initials = "".join(p[:1] for p in str(name).replace("-", " ").split()[:3]).upper() or "?"
    f = _font(30, True)
    box = draw.textbbox((0, 0), initials, font=f)
    draw.text((cx-(box[2]-box[0])/2, cy-20), initials, font=f, fill=TEXT)


def _team_name(draw: ImageDraw.ImageDraw, text: str, center_x: int, y: int) -> None:
    f = _fit(draw, text, 335, 29, True)
    box = draw.textbbox((0, 0), text, font=f)
    draw.text((center_x-(box[2]-box[0])/2, y), text, font=f, fill=TEXT)


def _model_strip(draw: ImageDraw.ImageDraw, head: str, probability: float, model_result: dict[str, Any], accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((22, 570, 1058, 755), 18, fill=(5, 15, 22), outline=accent, width=2)
    cols = [(35, 235), (250, 450), (465, 665), (680, 850), (865, 1045)]
    draw.text((48, 594), "ВЕРОЯТНОСТЬ", font=_font(14, True), fill=MUTED)
    draw.text((48, 625), f"{probability*100:.1f}%", font=_font(43, True), fill=accent)
    if head in {"over_2_5", "both_teams_to_score"}:
        draw.text((275, 594), "HT-MODEL", font=_font(14, True), fill=MUTED)
        draw.text((275, 632), _safe_pct(model_result.get("football_data", {}).get(head)), font=_font(30, True), fill=TEXT)
        draw.text((495, 594), "ЛИНИЯ", font=_font(14, True), fill=MUTED)
        draw.text((495, 632), "2.5" if head == "over_2_5" else "ОЗ — ДА", font=_font(30, True), fill=accent)
    else:
        direct = model_result.get("direct", {}).get(head)
        hazard = model_result.get("hazard", {}).get(head)
        disagreement = model_result.get("disagreement", {}).get(head)
        for x, lab, value, color in [
            (275, "DIRECT MODEL", _safe_pct(direct), TEXT),
            (495, "HAZARD MODEL", _safe_pct(hazard), TEXT),
            (715, "РАСХОЖДЕНИЕ", _safe_pct(disagreement), GOLD),
        ]:
            draw.text((x, 594), lab, font=_font(14, True), fill=MUTED)
            draw.text((x, 632), value, font=_font(28, True), fill=color)
    draw.text((875, 594), "УВЕРЕННОСТЬ", font=_font(14, True), fill=MUTED)
    draw.rounded_rectangle((875, 632, 1025, 651), 8, fill=(31, 49, 62))
    draw.rounded_rectangle((875, 632, 875 + int(150 * max(0.0, min(1.0, probability))), 651), 8, fill=accent)
    for x1, x2 in cols[:-1]:
        draw.line((x2, 585, x2, 740), fill=(25, 47, 61), width=1)


def _stats_strip(draw: ImageDraw.ImageDraw, stats: dict[str, str], cards: dict[str, Any], providers: int, accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((22, 775, 1058, 955), 18, fill=(6, 15, 24), outline=LINE, width=2)
    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    yellow = "—" if hy is None or ay is None else f"{hy}:{ay}"
    red = "—" if hr is None or ar is None else f"{hr}:{ar}"
    items = [
        ("xG", stats.get("xg", "—")),
        ("УДАРЫ", stats.get("shots", "—")),
        ("В СТВОР", stats.get("sot", "—")),
        ("УГЛОВЫЕ", stats.get("corners", "—")),
        ("ВЛАДЕНИЕ", stats.get("possession", "—")),
        ("МОМЕНТЫ", stats.get("big_chances", "—")),
    ]
    xs = [40, 205, 370, 535, 700, 865]
    for x, (lab, val) in zip(xs, items):
        draw.text((x, 797), lab, font=_font(13, True), fill=MUTED)
        draw.text((x, 827), val, font=_fit(draw, val, 150, 23, True), fill=TEXT)
    draw.line((35, 878, 1045, 878), fill=(25, 46, 60), width=1)
    draw.text((45, 897), f"КАРТОЧКИ  Ж {yellow}   К {red}", font=_font(16, True), fill=MUTED)
    draw.text((790, 897), f"DATA {providers}/3", font=_font(18, True), fill=accent)


def render_signal_card(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> bytes:
    theme = THEMES.get(head, THEMES["another_goal"])
    accent, deep = theme["accent"], theme["deep"]
    match = record.get("match") or {}
    meta = flashscore_meta(record)
    stats = _stats_for_record(record)
    match_id = str(match.get("flashscore_event_id") or "")
    _remember(match_id, meta, stats)

    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    round_name = str(meta.get("round") or "").strip()
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    providers = len(record.get("providers") or {})

    img = _canvas(1040, accent, deep)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 1072, 1032), 28, outline=accent, width=3)
    _stadium(draw, accent)
    _header(draw, accent, theme["label"], league, round_name, providers, result=False)

    _paste_logo(img, draw, _team_logo(meta, "home"), home, 160, 320, accent)
    _paste_logo(img, draw, _team_logo(meta, "away"), away, 920, 320, accent)
    _team_name(draw, home, 190, 430)
    _team_name(draw, away, 890, 430)

    draw.rounded_rectangle((458, 168, 622, 222), 14, fill=accent)
    _center(draw, "ПЕРЕРЫВ" if match.get("is_halftime") else f"{minute}'", 178, _font(25, True), DARK)
    _center(draw, f"{hs} : {aws}", 236, _font(82, True), TEXT)
    _center(draw, "1-Й ТАЙМ" if minute <= 45 else "2-Й ТАЙМ", 335, _font(18, True), MUTED)

    _model_strip(draw, head, probability, model_result, accent)
    _stats_strip(draw, stats, cards, providers, accent)
    draw.rounded_rectangle((390, 972, 690, 1022), 14, fill=accent)
    _center(draw, "В ИГРЕ", 980, _font(24, True), DARK)
    return _save(img)


def render_result_card(row: dict[str, Any], result: str, minute: int, home_score: int, away_score: int) -> bytes:
    head = str(row.get("head") or "another_goal")
    theme = THEMES.get(head, THEMES["another_goal"])
    won = str(result).lower() == "won"
    accent = theme["accent"] if won else RED
    deep = theme["deep"] if won else (62, 8, 14)
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or "LIVE FOOTBALL")
    providers = int(row.get("provider_count") or 1)
    cache = _read_assets().get(str(row.get("match_id") or ""), {}) or {}
    meta = dict(row.get("flashscore_meta") or cache.get("flashscore_meta") or {})
    stats = dict(row.get("stats_snapshot") or cache.get("stats_snapshot") or {})
    round_name = str(meta.get("round") or "").strip()
    entry = row.get("score") or [0, 0]
    entry_min = int(row.get("minute") or 0)
    probability = float(row.get("probability") or 0.0)

    img = _canvas(960, accent, deep)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 1072, 952), 28, outline=accent, width=3)
    _stadium(draw, accent)
    _header(draw, accent, theme["label"], league, round_name, providers, result=True)

    _paste_logo(img, draw, _team_logo(meta, "home"), home, 165, 310, accent)
    _paste_logo(img, draw, _team_logo(meta, "away"), away, 915, 310, accent)
    _team_name(draw, home, 190, 414)
    _team_name(draw, away, 890, 414)
    _center(draw, f"{home_score} : {away_score}", 225, _font(80, True), TEXT)
    _center(draw, f"{minute}' · {'ПОДТВЕРЖДЕНО' if won else 'ЗАКРЫТО'}", 326, _font(21, True), accent)

    draw.rounded_rectangle((55, 500, 1025, 655), 24, fill=(8, 20, 31), outline=accent, width=3)
    _center(draw, "СИГНАЛ ЗАШЁЛ" if won else "СИГНАЛ НЕ ЗАШЁЛ", 525, _font(42, True), accent)
    _center(draw, f"{theme['label']} · P НА ВХОДЕ {probability*100:.1f}%", 585, _font(21, True), TEXT)

    draw.rounded_rectangle((55, 685, 1025, 825), 20, fill=PANEL, outline=LINE, width=2)
    draw.text((85, 710), "ВХОД", font=_font(15, True), fill=MUTED)
    draw.text((85, 744), f"{entry_min}' · {int(entry[0])}:{int(entry[1])}", font=_font(28, True), fill=TEXT)
    draw.text((565, 710), "ПОДТВЕРЖДЕНИЕ", font=_font(15, True), fill=MUTED)
    draw.text((565, 744), f"{minute}' · {home_score}:{away_score}", font=_font(28, True), fill=accent)
    stat_line = f"Удары {stats.get('shots','—')} · В створ {stats.get('sot','—')} · Угловые {stats.get('corners','—')}"
    draw.text((85, 790), stat_line, font=_fit(draw, stat_line, 880, 16, True), fill=MUTED)

    draw.rounded_rectangle((190, 855, 890, 915), 16, fill=accent)
    _center(draw, "РЕЗУЛЬТАТ ПОДТВЕРЖДЁН" if won else "РЕЗУЛЬТАТ ЗАКРЫТ", 867, _font(25, True), DARK)
    _center(draw, "GOOL v2 · VERIFIED LIVE RESULT", 925, _font(15, True), MUTED)
    return _save(img)
