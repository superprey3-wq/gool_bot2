from __future__ import annotations

import json
import os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

from .match_context import provider_pair
from .providers.common import UA
from .providers.flashscore import FlashscoreProvider

W = 1080
TEXT = (246, 249, 252)
MUTED = (158, 174, 194)
WHITE = (255, 255, 255)
RED = (238, 69, 69)
GOLD = (255, 188, 60)
DARK = (3, 8, 15)
PANEL = (8, 17, 29)
PANEL2 = (12, 25, 40)
LINE = (42, 65, 88)

THEMES = {
    "another_goal": {"accent": (46, 220, 80), "deep": (0, 47, 22), "label": "ЕЩЁ ГОЛ"},
    "over_2_5": {"accent": (34, 145, 255), "deep": (0, 35, 75), "label": "ТОТАЛ БОЛЬШЕ 2.5"},
    "both_teams_to_score": {"accent": (177, 72, 255), "deep": (54, 7, 80), "label": "ОБЕ ЗАБЬЮТ — ДА"},
    "goal_before_ht": {"accent": (255, 132, 22), "deep": (78, 31, 0), "label": "ГОЛ ДО ПЕРЕРЫВА"},
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


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 38, bold: bool = True) -> ImageFont.ImageFont:
    text = str(text or "")
    for size in range(start, 16, -2):
        f = _font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= width:
            return f
    return _font(16, bold)


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _save(img: Image.Image) -> bytes:
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _safe_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "—"


def _fmt_pair(pair: tuple[float | None, float | None], digits: int = 0, suffix: str = "") -> str:
    home, away = pair
    if home is None or away is None:
        return "—"
    if digits:
        return f"{home:.{digits}f}{suffix} : {away:.{digits}f}{suffix}"
    return f"{int(round(home))}{suffix} : {int(round(away))}{suffix}"


def _asset_cache_path() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "signal_card_assets.json"


def _read_asset_cache() -> dict[str, Any]:
    path = _asset_cache_path()
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember_assets(match_id: str, meta: dict[str, Any], stats: dict[str, str]) -> None:
    if not match_id:
        return
    path = _asset_cache_path()
    cache = _read_asset_cache()
    cache[str(match_id)] = {"flashscore_meta": meta, "stats_snapshot": stats}
    if len(cache) > 600:
        cache = dict(list(cache.items())[-600:])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _cached_assets(match_id: str) -> dict[str, Any]:
    return dict(_read_asset_cache().get(str(match_id)) or {}) if match_id else {}


@lru_cache(maxsize=1024)
def _download_logo(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": UA, "Referer": "https://www.flashscore.com/"})
        with urlopen(req, timeout=8) as response:
            raw = response.read(600_000)
        image = Image.open(BytesIO(raw)).convert("RGBA")
        image.thumbnail((170, 170), Image.Resampling.LANCZOS)
        return image.copy()
    except Exception:
        return None


def _team_logo(meta: dict[str, Any], side: str) -> Image.Image | None:
    explicit = str(meta.get(f"{side}_logo_url") or "").strip()
    if explicit:
        logo = _download_logo(explicit)
        if logo is not None:
            return logo
    slug = str(meta.get(f"{side}_team_slug") or "").strip()
    team_id = str(meta.get(f"{side}_team_id") or "").strip()
    url = FlashscoreProvider.team_logo_url(slug, team_id) or ""
    return _download_logo(url) if url else None


def _badge(img: Image.Image, draw: ImageDraw.ImageDraw, logo: Image.Image | None, name: str, cx: int, cy: int, accent: tuple[int, int, int]) -> None:
    draw.ellipse((cx - 78, cy - 78, cx + 78, cy + 78), fill=(7, 16, 29), outline=accent, width=4)
    if logo is not None:
        x = cx - logo.width // 2
        y = cy - logo.height // 2
        img.paste(logo, (x, y), logo)
        return
    initials = "".join(p[:1] for p in str(name).replace("-", " ").split()[:3]).upper() or "?"
    f = _font(34, True)
    box = draw.textbbox((0, 0), initials, font=f)
    draw.text((cx - (box[2]-box[0])/2, cy - 22), initials, font=f, fill=TEXT)


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


def _gradient_canvas(height: int, deep: tuple[int, int, int], accent: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (W, height), DARK)
    px = img.load()
    for y in range(height):
        t = y / max(1, height - 1)
        glow = max(0.0, 1.0 - abs(t - 0.27) * 3.2)
        for x in range(W):
            edge = abs(x - W/2) / (W/2)
            g = glow * (1.0 - 0.45 * edge)
            px[x, y] = tuple(min(255, int(DARK[i] * (1-g) + deep[i] * g + accent[i] * g * 0.08)) for i in range(3))
    return img


def _stadium(draw: ImageDraw.ImageDraw, height: int, accent: tuple[int, int, int]) -> None:
    horizon = 350
    for x in range(40, W, 70):
        draw.line((W//2, horizon, x, 650), fill=(22, 55, 72), width=1)
    draw.arc((70, 260, 1010, 620), 190, 350, fill=(38, 72, 90), width=2)
    draw.arc((120, 300, 960, 670), 190, 350, fill=(25, 58, 75), width=2)
    draw.line((120, 635, 960, 635), fill=(32, 77, 78), width=2)
    for x in (130, 250, 370, 710, 830, 950):
        draw.ellipse((x-3, 305, x+3, 311), fill=accent)


def _top_header(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int], label: str, league: str, round_name: str, providers: int, result: bool = False) -> None:
    draw.rounded_rectangle((24, 20, 1056, 116), 24, fill=(6, 15, 26), outline=accent, width=3)
    draw.text((50, 37), "GOOL v2", font=_font(34, True), fill=accent)
    draw.text((220, 38), label, font=_fit(draw, label, 520, 31, True), fill=TEXT)
    draw.text((875, 40), "РЕЗУЛЬТАТ" if result else "LIVE", font=_font(20, True), fill=accent)
    draw.text((875, 72), f"DATA {providers}/3", font=_font(15, True), fill=MUTED)
    comp = league + (f" · {round_name}" if round_name else "")
    draw.text((50, 142), comp, font=_fit(draw, comp, 960, 23, True), fill=MUTED)


def _model_panel(draw: ImageDraw.ImageDraw, head: str, probability: float, model_result: dict[str, Any], accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((50, 570, 1030, 760), 22, fill=PANEL2, outline=accent, width=2)
    draw.text((78, 598), "ВЕРОЯТНОСТЬ", font=_font(15, True), fill=MUTED)
    draw.text((78, 632), f"{probability*100:.1f}%", font=_font(50, True), fill=accent)
    if head in {"over_2_5", "both_teams_to_score"}:
        draw.text((390, 598), "HT MODEL", font=_font(15, True), fill=MUTED)
        draw.text((390, 640), _safe_pct(model_result.get("football_data", {}).get(head)), font=_font(31, True), fill=TEXT)
    else:
        direct = model_result.get("direct", {}).get(head)
        hazard = model_result.get("hazard", {}).get(head)
        disagreement = model_result.get("disagreement", {}).get(head)
        cols = [(360, "DIRECT", _safe_pct(direct), TEXT), (585, "HAZARD", _safe_pct(hazard), TEXT), (805, "РАСХОЖДЕНИЕ", _safe_pct(disagreement), GOLD)]
        for x, lab, val, color in cols:
            draw.text((x, 598), lab, font=_font(14, True), fill=MUTED)
            draw.text((x, 640), val, font=_font(28, True), fill=color)
    draw.rounded_rectangle((78, 714, 1000, 730), 8, fill=(32, 49, 66))
    fill_x = 78 + int(922 * max(0.0, min(1.0, probability)))
    draw.rounded_rectangle((78, 714, fill_x, 730), 8, fill=accent)


def _stats_panel(draw: ImageDraw.ImageDraw, stats: dict[str, str], cards: dict[str, Any], accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((50, 790, 1030, 1030), 22, fill=PANEL, outline=LINE, width=2)
    items = [
        ("xG", stats.get("xg", "—")), ("УДАРЫ", stats.get("shots", "—")), ("В СТВОР", stats.get("sot", "—")),
        ("УГЛОВЫЕ", stats.get("corners", "—")), ("ВЛАДЕНИЕ", stats.get("possession", "—")), ("ГОЛ. МОМЕНТЫ", stats.get("big_chances", "—")),
    ]
    xs = (78, 385, 700)
    for idx, (lab, val) in enumerate(items):
        row = idx // 3
        col = idx % 3
        y = 820 + row * 92
        draw.text((xs[col], y), lab, font=_font(14, True), fill=MUTED)
        draw.text((xs[col], y+30), val, font=_fit(draw, val, 245, 25, True), fill=TEXT)
    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    yellow = "—" if hy is None or ay is None else f"{hy}:{ay}"
    red = "—" if hr is None or ar is None else f"{hr}:{ar}"
    draw.text((78, 990), f"КАРТОЧКИ  Ж {yellow}   К {red}", font=_font(17, True), fill=accent)


def render_signal_card(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> bytes:
    theme = THEMES.get(head, THEMES["another_goal"])
    accent, deep = theme["accent"], theme["deep"]
    match = record.get("match") or {}
    fs_meta = flashscore_meta(record)
    stats = _stats_for_record(record)
    match_id = str(match.get("flashscore_event_id") or "")
    _remember_assets(match_id, fs_meta, stats)

    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "Неизвестный чемпионат")
    round_name = str(fs_meta.get("round") or "").strip()
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    providers = len(record.get("providers") or {})

    img = _gradient_canvas(1180, deep, accent)
    draw = ImageDraw.Draw(img)
    _stadium(draw, 1180, accent)
    _top_header(draw, accent, theme["label"], league, round_name, providers, result=False)

    home_logo = _team_logo(fs_meta, "home")
    away_logo = _team_logo(fs_meta, "away")
    _badge(img, draw, home_logo, home, 175, 350, accent)
    _badge(img, draw, away_logo, away, 905, 350, accent)
    draw.text((35, 455), home, font=_fit(draw, home, 360, 31, True), fill=TEXT)
    af = _fit(draw, away, 360, 31, True)
    aw = draw.textbbox((0,0), away, font=af)[2]
    draw.text((1045-aw, 455), away, font=af, fill=TEXT)
    _center(draw, f"{minute}'", 255, _font(30, True), accent)
    _center(draw, f"{hs}:{aws}", 305, _font(88, True), WHITE)
    _center(draw, "LIVE · 1-Й ТАЙМ" if minute <= 45 else "LIVE · 2-Й ТАЙМ", 405, _font(18, True), accent)

    _model_panel(draw, head, probability, model_result, accent)
    _stats_panel(draw, stats, cards, accent)
    draw.rounded_rectangle((180, 1060, 900, 1135), 22, fill=accent)
    _center(draw, "В ИГРЕ", 1078, _font(32, True), DARK)
    _center(draw, "GOOL v2 · AI FOOTBALL ANALYTICS", 1148, _font(16, True), MUTED)
    return _save(img)


def render_result_card(row: dict[str, Any], result: str, minute: int, home_score: int, away_score: int) -> bytes:
    head = str(row.get("head") or "another_goal")
    theme = THEMES.get(head, THEMES["another_goal"])
    base_accent = theme["accent"]
    won = str(result).lower() == "won"
    accent = base_accent if won else RED
    deep = theme["deep"] if won else (70, 10, 15)
    status = "СИГНАЛ ЗАШЁЛ" if won else "СИГНАЛ НЕ ЗАШЁЛ"
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or "Неизвестный чемпионат")
    cached = _cached_assets(str(row.get("match_id") or ""))
    fs_meta = dict(row.get("flashscore_meta") or cached.get("flashscore_meta") or {})
    stats = dict(row.get("stats_snapshot") or cached.get("stats_snapshot") or {})
    label = HEAD_LABELS.get(head, head.upper())
    entry_score = row.get("score") or [0, 0]
    entry_minute = int(row.get("minute") or 0)
    probability = float(row.get("probability") or 0.0)
    providers = int(row.get("provider_count") or 0)
    round_name = str(fs_meta.get("round") or "").strip()

    img = _gradient_canvas(1120, deep, accent)
    draw = ImageDraw.Draw(img)
    _stadium(draw, 1120, accent)
    _top_header(draw, accent, label, league, round_name, providers, result=True)

    home_logo = _team_logo(fs_meta, "home")
    away_logo = _team_logo(fs_meta, "away")
    _badge(img, draw, home_logo, home, 175, 350, base_accent)
    _badge(img, draw, away_logo, away, 905, 350, base_accent)
    draw.text((35, 455), home, font=_fit(draw, home, 360, 31, True), fill=TEXT)
    af = _fit(draw, away, 360, 31, True)
    aw = draw.textbbox((0,0), away, font=af)[2]
    draw.text((1045-aw, 455), away, font=af, fill=TEXT)
    _center(draw, f"{home_score}:{away_score}", 300, _font(90, True), WHITE)
    _center(draw, f"{minute}' · ПОДТВЕРЖДЕНО", 405, _font(18, True), accent)

    draw.rounded_rectangle((65, 555, 1015, 700), 26, fill=PANEL2, outline=accent, width=3)
    _center(draw, status, 585, _font(42, True), accent)
    _center(draw, f"{label} · P НА ВХОДЕ {probability*100:.1f}%", 645, _font(23, True), TEXT)

    draw.rounded_rectangle((65, 735, 1015, 900), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((95, 765), "ВХОД", font=_font(14, True), fill=MUTED)
    draw.text((95, 800), f"{entry_minute}' · {entry_score[0]}:{entry_score[1]}", font=_font(28, True), fill=TEXT)
    draw.text((570, 765), "ПОДТВЕРЖДЕНИЕ", font=_font(14, True), fill=MUTED)
    draw.text((570, 800), f"{minute}' · {home_score}:{away_score}", font=_font(28, True), fill=accent)
    line = f"Удары {stats.get('shots','—')} · В створ {stats.get('sot','—')} · Угловые {stats.get('corners','—')}"
    draw.text((95, 855), line, font=_fit(draw, line, 860, 19, True), fill=MUTED)

    draw.rounded_rectangle((180, 950, 900, 1028), 22, fill=accent)
    _center(draw, "РЕЗУЛЬТАТ ПОДТВЕРЖДЁН", 969, _font(29, True), DARK)
    _center(draw, "GOOL v2 · VERIFIED LIVE RESULT", 1060, _font(16, True), MUTED)
    return _save(img)
