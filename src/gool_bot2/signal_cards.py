from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .card_template_asset import PREMIUM_STADIUM_JPEG_B64
from .match_context import provider_pair
from .providers.common import UA
from .providers.flashscore import FlashscoreProvider

W = 1080
TEXT = (248, 250, 252)
MUTED = (162, 178, 196)
DARK = (2, 7, 12)
RED = (245, 68, 78)
GOLD = (255, 200, 67)
THEMES = {
    "another_goal": {"accent": (77, 239, 43), "label": "ЕЩЁ ГОЛ"},
    "over_2_5": {"accent": (55, 166, 255), "label": "ТОТАЛ БОЛЬШЕ 2.5"},
    "both_teams_to_score": {"accent": (190, 83, 255), "label": "ОБЕ ЗАБЬЮТ — ДА"},
    "goal_before_ht": {"accent": (255, 145, 37), "label": "ГОЛ ДО ПЕРЕРЫВА"},
}


def _font(size: int, bold: bool = False):
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


def _fit(draw, text, width, start=38, bold=True):
    text = str(text or "")
    for size in range(start, 11, -2):
        f = _font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= width:
            return f
    return _font(12, bold)


def _center(draw, text, y, font, fill):
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _save(img):
    out = BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def _pct(v):
    try:
        return f"{float(v) * 100:.1f}%"
    except Exception:
        return "—"


def _fmt_pair(pair, digits=0, suffix=""):
    a, b = pair
    if a is None or b is None:
        return "—"
    if digits:
        return f"{a:.{digits}f}{suffix} : {b:.{digits}f}{suffix}"
    return f"{int(round(a))}{suffix} : {int(round(b))}{suffix}"


def _stats_for_record(record):
    return {
        "xg": _fmt_pair(provider_pair(record, "xg"), 2),
        "shots": _fmt_pair(provider_pair(record, "shots")),
        "sot": _fmt_pair(provider_pair(record, "shots_on_target")),
        "corners": _fmt_pair(provider_pair(record, "corners")),
        "possession": _fmt_pair(provider_pair(record, "possession"), 0, "%"),
        "big_chances": _fmt_pair(provider_pair(record, "big_chances")),
    }


def stats_snapshot(record):
    return _stats_for_record(record)


def flashscore_meta(record):
    return dict((((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}))


def _asset_path():
    return Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "signal_card_assets.json"


def _analysis_path():
    runtime = os.getenv("RUNTIME_DATA_DIR", "data")
    return Path(os.getenv("SIGNAL_ANALYSIS_PATH", runtime + "/live/gool_bot2_analysis.jsonl"))


def _read_assets():
    try:
        path = _asset_path()
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember(match_id, meta, stats):
    if not match_id:
        return
    cache = _read_assets()
    old = dict(cache.get(match_id) or {})
    old.update({"flashscore_meta": meta, "stats_snapshot": stats})
    cache[match_id] = old
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


def _probability_history(match_id, head, limit=18):
    path = _analysis_path()
    if not match_id or not path.exists():
        return []
    by_minute = {}
    try:
        for line in path.read_text("utf-8", errors="ignore").splitlines()[-6000:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("match_id") or "") != match_id or str(row.get("head") or "") != head:
                continue
            try:
                minute = int(row.get("minute") or 0)
                value = float(row.get("probability"))
            except Exception:
                continue
            if 0 <= value <= 1:
                by_minute[minute] = value
    except Exception:
        return []
    return sorted(by_minute.items())[-limit:]


@lru_cache(maxsize=1)
def _stadium_template():
    raw = base64.b64decode(PREMIUM_STADIUM_JPEG_B64.encode("ascii"))
    img = Image.open(BytesIO(raw)).convert("RGB")
    return img.resize((1080, 1080), Image.Resampling.LANCZOS).convert("RGBA")


def _canvas(height, accent):
    base = _stadium_template().copy()
    if height != 1080:
        base = base.resize((W, height), Image.Resampling.LANCZOS)
    base = ImageEnhance.Contrast(base).enhance(1.08)
    tint = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    td = ImageDraw.Draw(tint)
    td.rectangle((0, 0, W, height), fill=(0, 0, 0, 45))
    td.rectangle((0, 0, W, 170), fill=(0, 0, 0, 150))
    td.rectangle((0, 530, W, height), fill=(0, 0, 0, 175))
    td.ellipse((260, 100, 820, 560), fill=accent + (30,))
    tint = tint.filter(ImageFilter.GaussianBlur(18))
    return Image.alpha_composite(base, tint)


def _panel(draw, box, accent=None, alpha=220, radius=22, width=2):
    outline = accent + (190,) if accent else (79, 96, 112, 180)
    draw.rounded_rectangle(box, radius, fill=(2, 8, 14, alpha), outline=outline, width=width)


@lru_cache(maxsize=2048)
def _download(url):
    if not url:
        return None
    try:
        request = Request(url, headers={"User-Agent": UA, "Referer": "https://www.flashscore.com/"})
        with urlopen(request, timeout=7) as response:
            raw = response.read(700_000)
        image = Image.open(BytesIO(raw)).convert("RGBA")
        image.thumbnail((210, 210), Image.Resampling.LANCZOS)
        return image.copy()
    except Exception:
        return None


def _team_logo(meta, side):
    filename = str(meta.get(f"{side}_logo_file") or "").strip()
    if filename:
        image = _download(f"https://static.flashscore.com/res/image/data/{filename}")
        if image is not None:
            return image
    explicit = str(meta.get(f"{side}_logo_url") or "").strip()
    if explicit:
        image = _download(explicit)
        if image is not None:
            return image
    url = FlashscoreProvider.team_logo_url(
        str(meta.get(f"{side}_team_slug") or ""), str(meta.get(f"{side}_team_id") or "")
    ) or ""
    return _download(url) if url else None


def _paste_logo(img, draw, logo, name, cx, cy, accent):
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((cx - 95, cy - 95, cx + 95, cy + 95), fill=accent + (40,))
    glow = glow.filter(ImageFilter.GaussianBlur(24))
    img.alpha_composite(glow)
    if logo is not None:
        box = logo.getbbox()
        logo = logo.crop(box) if box else logo
        scale = min(165 / max(1, logo.width), 165 / max(1, logo.height))
        logo = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.Resampling.LANCZOS)
        img.alpha_composite(logo, (cx - logo.width // 2, cy - logo.height // 2))
        return
    draw.ellipse((cx - 72, cy - 72, cx + 72, cy + 72), fill=(4, 13, 20, 230), outline=accent, width=3)
    initials = "".join(p[:1] for p in str(name).replace("-", " ").split()[:3]).upper() or "?"
    f = _font(31, True)
    b = draw.textbbox((0, 0), initials, font=f)
    draw.text((cx - (b[2] - b[0]) / 2, cy - 20), initials, font=f, fill=TEXT)


def _team_name(draw, text, cx, y):
    f = _fit(draw, text, 350, 31, True)
    b = draw.textbbox((0, 0), text, font=f)
    draw.text((cx - (b[2] - b[0]) / 2, y), text, font=f, fill=TEXT)


def _sparkline(draw, history, box, accent, current):
    x1, y1, x2, y2 = box
    vals = history[:] if history else [(0, current)]
    if len(vals) == 1:
        vals = [(max(0, vals[0][0] - 1), vals[0][1]), vals[0]]
    probs = [v for _, v in vals]
    lo = max(0.0, min(probs) - 0.05)
    hi = min(1.0, max(probs) + 0.05)
    if hi - lo < 0.10:
        hi = min(1.0, lo + 0.10)
    for i in range(3):
        yy = y1 + int((y2 - y1) * i / 2)
        draw.line((x1, yy, x2, yy), fill=(42, 61, 74), width=1)
    pts = []
    for i, (_, p) in enumerate(vals):
        x = x1 + (x2 - x1) * i / max(1, len(vals) - 1)
        y = y2 - (p - lo) / max(0.001, hi - lo) * (y2 - y1)
        pts.append((int(x), int(y)))
    if len(pts) >= 2:
        fill_poly = [(pts[0][0], y2)] + pts + [(pts[-1][0], y2)]
        draw.polygon(fill_poly, fill=accent + (35,))
        draw.line(pts, fill=accent, width=5)
    for p in pts:
        draw.ellipse((p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4), fill=accent)
    draw.text((x1, y2 + 8), f"{vals[0][0]}'", font=_font(12, True), fill=MUTED)
    draw.text((x2 - 38, y2 + 8), f"{vals[-1][0]}'", font=_font(12, True), fill=MUTED)


def _header(draw, theme, league, round_name, providers, result=False):
    accent = theme["accent"]
    draw.text((42, 28), "GOOL", font=_font(43, True), fill=TEXT)
    draw.text((170, 39), "2", font=_font(24, True), fill=accent)
    draw.text((42, 78), "AI FOOTBALL ANALYTICS", font=_font(12, True), fill=accent)
    label = theme["label"]
    _center(draw, label, 30, _fit(draw, label, 560, 45, True), TEXT)
    _center(draw, "РЕЗУЛЬТАТ" if result else "• LIVE-СИГНАЛ •", 84, _font(15, True), accent)
    draw.rounded_rectangle((877, 30, 1037, 95), 15, fill=(2, 8, 14, 220), outline=accent, width=2)
    draw.text((914, 42), "RESULT" if result else "LIVE", font=_font(16, True), fill=accent)
    draw.text((914, 68), f"DATA {providers}/3", font=_font(12, True), fill=MUTED)
    competition = league + (f"  •  {round_name}" if round_name else "")
    _center(draw, competition, 132, _fit(draw, competition, 900, 18, True), TEXT)


def _model_values(head, model_result):
    if head in {"over_2_5", "both_teams_to_score"}:
        value = _pct((model_result.get("football_data") or {}).get(head))
        return [("HT MODEL", value)]
    return [
        ("DIRECT", _pct((model_result.get("direct") or {}).get(head))),
        ("HAZARD", _pct((model_result.get("hazard") or {}).get(head))),
        ("Δ MODELS", _pct((model_result.get("disagreement") or {}).get(head))),
    ]


def render_signal_card(record, head, probability, model_result, cards):
    theme = THEMES.get(head, THEMES["another_goal"])
    accent = theme["accent"]
    match = record.get("match") or {}
    meta = flashscore_meta(record)
    stats = _stats_for_record(record)
    match_id = str(match.get("flashscore_event_id") or "")
    _remember(match_id, meta, stats)
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    rnd = str(meta.get("round") or "")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    providers = len(record.get("providers") or {})

    img = _canvas(1080, accent)
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rounded_rectangle((10, 10, 1070, 1070), 28, outline=accent + (210,), width=3)
    _header(draw, theme, league, rnd, providers, False)

    _paste_logo(img, draw, _team_logo(meta, "home"), home, 200, 350, accent)
    _paste_logo(img, draw, _team_logo(meta, "away"), away, 880, 350, accent)
    _team_name(draw, home, 200, 465)
    _team_name(draw, away, 880, 465)
    draw.rounded_rectangle((472, 205, 608, 252), 14, fill=accent)
    _center(draw, "HT" if match.get("is_halftime") else f"{minute}'", 214, _font(23, True), DARK)
    _center(draw, f"{hs} : {aws}", 278, _font(88, True), TEXT)
    _center(draw, "1-Й ТАЙМ" if minute <= 45 else "2-Й ТАЙМ", 395, _font(15, True), accent)

    _panel(draw, (28, 545, 352, 805), accent, 225)
    draw.text((55, 570), "ВЕРОЯТНОСТЬ", font=_font(14, True), fill=MUTED)
    draw.text((55, 610), f"{probability * 100:.1f}%", font=_font(58, True), fill=accent)
    draw.text((57, 685), theme["label"], font=_fit(draw, theme["label"], 270, 22, True), fill=TEXT)
    draw.rounded_rectangle((55, 742, 323, 755), 6, fill=(35, 49, 60, 220))
    draw.rounded_rectangle((55, 742, 55 + int(268 * max(0, min(1, probability))), 755), 6, fill=accent)

    _panel(draw, (368, 545, 690, 805), accent, 225)
    draw.text((392, 570), "МОДЕЛИ", font=_font(14, True), fill=MUTED)
    y = 612
    for label, value in _model_values(head, model_result):
        draw.text((394, y), label, font=_font(13, True), fill=MUTED)
        draw.text((548, y - 6), value, font=_font(25, True), fill=GOLD if label.startswith("Δ") else TEXT)
        y += 62

    _panel(draw, (706, 545, 1052, 805), accent, 225)
    draw.text((730, 570), "ДИНАМИКА ВЕРОЯТНОСТИ", font=_font(13, True), fill=MUTED)
    _sparkline(draw, _probability_history(match_id, head), (730, 620, 1018, 730), accent, probability)
    draw.text((904, 752), f"{probability * 100:.1f}%", font=_font(22, True), fill=accent)

    _panel(draw, (28, 825, 1052, 966), accent, 220)
    items = [
        ("xG", stats.get("xg", "—")),
        ("УДАРЫ", stats.get("shots", "—")),
        ("В СТВОР", stats.get("sot", "—")),
        ("УГЛОВЫЕ", stats.get("corners", "—")),
        ("ВЛАДЕНИЕ", stats.get("possession", "—")),
        ("МОМЕНТЫ", stats.get("big_chances", "—")),
    ]
    for i, (label, value) in enumerate(items):
        x = 50 + i * 166
        draw.text((x, 852), label, font=_font(12, True), fill=MUTED)
        draw.text((x, 886), value, font=_fit(draw, value, 140, 20, True), fill=TEXT)
    draw.line((48, 928, 1032, 928), fill=(64, 82, 96, 180), width=1)
    draw.text((50, 941), f"ВХОД  {minute}' • {hs}:{aws}", font=_font(14, True), fill=TEXT)
    draw.text((815, 941), f"DATA {providers}/3", font=_font(14, True), fill=accent)

    draw.rounded_rectangle((360, 990, 720, 1046), 16, fill=accent)
    _center(draw, "●  В ИГРЕ", 1002, _font(24, True), DARK)
    return _save(img)


def render_result_card(row, result, minute, home_score, away_score):
    head = str(row.get("head") or "another_goal")
    theme = THEMES.get(head, THEMES["another_goal"])
    won = str(result).lower() == "won"
    accent = theme["accent"] if won else RED
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or "LIVE FOOTBALL")
    providers = int(row.get("provider_count") or 1)
    cache = _read_assets().get(str(row.get("match_id") or ""), {}) or {}
    meta = dict(row.get("flashscore_meta") or cache.get("flashscore_meta") or {})
    stats = dict(row.get("stats_snapshot") or cache.get("stats_snapshot") or {})
    entry = row.get("score") or [0, 0]
    entry_minute = int(row.get("minute") or 0)
    probability = float(row.get("probability") or 0)

    img = _canvas(1080, accent)
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rounded_rectangle((10, 10, 1070, 1070), 28, outline=accent + (210,), width=3)
    result_theme = dict(theme)
    result_theme["accent"] = accent
    _header(draw, result_theme, league, str(meta.get("round") or ""), providers, True)

    _paste_logo(img, draw, _team_logo(meta, "home"), home, 205, 350, accent)
    _paste_logo(img, draw, _team_logo(meta, "away"), away, 875, 350, accent)
    _team_name(draw, home, 205, 465)
    _team_name(draw, away, 875, 465)
    _center(draw, f"{home_score} : {away_score}", 282, _font(88, True), TEXT)
    _center(draw, f"{minute}' • РЕЗУЛЬТАТ", 398, _font(16, True), MUTED)

    _panel(draw, (65, 555, 1015, 735), accent, 230, 28, 3)
    _center(draw, "✓  СИГНАЛ ЗАШЁЛ!" if won else "✕  СИГНАЛ НЕ ЗАШЁЛ", 588, _font(44, True), accent)
    _center(draw, f"{theme['label']} • ВЕРОЯТНОСТЬ {probability * 100:.1f}%", 655, _font(20, True), TEXT)

    _panel(draw, (65, 765, 1015, 930), accent, 220)
    draw.text((105, 792), "ВХОД В СИГНАЛ", font=_font(13, True), fill=MUTED)
    draw.text((105, 828), f"{entry_minute}' • {int(entry[0])}:{int(entry[1])}", font=_font(28, True), fill=TEXT)
    draw.text((600, 792), "РЕЗУЛЬТАТ МАТЧА", font=_font(13, True), fill=MUTED)
    draw.text((600, 828), f"{minute}' • {home_score}:{away_score}", font=_font(28, True), fill=accent)
    statline = f"Удары {stats.get('shots','—')}  •  В створ {stats.get('sot','—')}  •  Угловые {stats.get('corners','—')}"
    _center(draw, statline, 888, _fit(draw, statline, 820, 15, True), MUTED)

    draw.rounded_rectangle((270, 970, 810, 1038), 18, fill=accent)
    _center(draw, "GOOL 2 • VERIFIED RESULT", 987, _font(21, True), DARK)
    return _save(img)
