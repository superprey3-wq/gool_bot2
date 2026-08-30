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
BG = (5, 10, 18)
PANEL = (13, 22, 36)
PANEL2 = (19, 31, 49)
TEXT = (247, 249, 252)
MUTED = (151, 166, 188)
RED = (244, 84, 84)
GOLD = (255, 184, 48)
LINE = (45, 63, 88)

THEMES = {
    "another_goal": {"accent": (52, 210, 82), "deep": (4, 49, 24), "label": "ЕЩЁ ГОЛ", "icon": "⚽"},
    "over_2_5": {"accent": (39, 145, 255), "deep": (4, 34, 73), "label": "ТОТАЛ БОЛЬШЕ 2.5", "icon": "📈"},
    "both_teams_to_score": {"accent": (170, 72, 255), "deep": (49, 8, 72), "label": "ОБЕ ЗАБЬЮТ — ДА", "icon": "🤝"},
    "goal_before_ht": {"accent": (255, 137, 28), "deep": (77, 31, 2), "label": "ГОЛ ДО ПЕРЕРЫВА", "icon": "⏱"},
}

HEAD_LABELS = {key: value["label"] for key, value in THEMES.items()}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 42, bold: bool = True) -> ImageFont.ImageFont:
    text = str(text or "")
    for size in range(start, 17, -2):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= width:
            return font
    return _font(18, bold)


def _save(img: Image.Image) -> bytes:
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _safe_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_pair(pair: tuple[float | None, float | None], digits: int = 0, suffix: str = "") -> str:
    home, away = pair
    if home is None or away is None:
        return "—"
    if digits:
        return f"{home:.{digits}f}{suffix} : {away:.{digits}f}{suffix}"
    return f"{int(round(home))}{suffix} : {int(round(away))}{suffix}"


def _asset_cache_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return runtime / "live" / "signal_card_assets.json"


def _read_asset_cache() -> dict[str, Any]:
    path = _asset_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember_assets(match_id: str, meta: dict[str, Any], stats: dict[str, str]) -> None:
    if not match_id:
        return
    path = _asset_cache_path()
    cache = _read_asset_cache()
    cache[str(match_id)] = {"flashscore_meta": meta, "stats_snapshot": stats}
    if len(cache) > 500:
        cache = dict(list(cache.items())[-500:])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _cached_assets(match_id: str) -> dict[str, Any]:
    if not match_id:
        return {}
    return dict(_read_asset_cache().get(str(match_id)) or {})


@lru_cache(maxsize=1024)
def _download_logo(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": UA, "Referer": "https://www.flashscore.com/"})
        with urlopen(req, timeout=8) as response:
            raw = response.read(500_000)
        image = Image.open(BytesIO(raw)).convert("RGBA")
        image.thumbnail((150, 150), Image.Resampling.LANCZOS)
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


def _badge(draw: ImageDraw.ImageDraw, img: Image.Image | None, name: str, x: int, y: int, accent: tuple[int, int, int]) -> None:
    if img is not None:
        px = x + (150 - img.width) // 2
        py = y + (150 - img.height) // 2
        draw._image.paste(img, (px, py), img)
        return
    draw.ellipse((x + 20, y + 20, x + 130, y + 130), fill=(22, 31, 46), outline=accent, width=4)
    initials = "".join(part[:1] for part in str(name).replace("-", " ").split()[:3]).upper() or "?"
    font = _font(31, True)
    box = draw.textbbox((0, 0), initials, font=font)
    draw.text((x + 75 - (box[2] - box[0]) / 2, y + 56), initials, font=font, fill=TEXT)


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


def render_signal_card(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> bytes:
    theme = THEMES.get(head, THEMES["another_goal"])
    accent = theme["accent"]
    deep = theme["deep"]
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

    img = Image.new("RGB", (W, 1180), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((20, 18, 1060, 1160), 30, fill=deep, outline=accent, width=3)
    draw.rounded_rectangle((38, 36, 1042, 130), 22, fill=PANEL, outline=accent, width=2)
    draw.text((62, 53), "GOOL v2", font=_font(34, True), fill=accent)
    draw.text((225, 55), str(theme["label"]), font=_fit(draw, theme["label"], 520, 32, True), fill=TEXT)
    draw.text((865, 58), f"DATA {providers}/3", font=_font(19, True), fill=MUTED)

    comp = league + (f" · {round_name}" if round_name else "")
    draw.text((62, 155), comp, font=_fit(draw, comp, 930, 24, True), fill=MUTED)

    home_logo = _team_logo(fs_meta, "home")
    away_logo = _team_logo(fs_meta, "away")
    _badge(draw, home_logo, home, 70, 225, accent)
    _badge(draw, away_logo, away, 860, 225, accent)
    draw.text((48, 392), home, font=_fit(draw, home, 350, 31, True), fill=TEXT)
    away_font = _fit(draw, away, 350, 31, True)
    away_box = draw.textbbox((0, 0), away, font=away_font)
    draw.text((1032 - (away_box[2] - away_box[0]), 392), away, font=away_font, fill=TEXT)

    _center(draw, f"{minute}'", 225, _font(30, True), accent)
    _center(draw, f"{hs}:{aws}", 270, _font(78, True), TEXT)
    _center(draw, "LIVE", 360, _font(20, True), accent)

    draw.rounded_rectangle((50, 455, 1030, 650), 24, fill=PANEL2, outline=accent, width=2)
    draw.text((82, 485), "ВЕРОЯТНОСТЬ", font=_font(17, True), fill=MUTED)
    draw.text((82, 520), f"{probability * 100:.1f}%", font=_font(52, True), fill=accent)
    if head in {"over_2_5", "both_teams_to_score"}:
        draw.text((390, 485), "HT MODEL", font=_font(17, True), fill=MUTED)
        draw.text((390, 525), _safe_pct(model_result.get("football_data", {}).get(head)), font=_font(31, True), fill=TEXT)
    else:
        direct = model_result.get("direct", {}).get(head)
        hazard = model_result.get("hazard", {}).get(head)
        disagreement = model_result.get("disagreement", {}).get(head)
        draw.text((355, 485), "DIRECT", font=_font(17, True), fill=MUTED)
        draw.text((355, 525), _safe_pct(direct), font=_font(30, True), fill=TEXT)
        draw.text((590, 485), "HAZARD", font=_font(17, True), fill=MUTED)
        draw.text((590, 525), _safe_pct(hazard), font=_font(30, True), fill=TEXT)
        draw.text((815, 485), "РАСХОЖДЕНИЕ", font=_font(15, True), fill=MUTED)
        draw.text((815, 525), _safe_pct(disagreement), font=_font(29, True), fill=GOLD)
    draw.rounded_rectangle((82, 605, 998, 626), 10, fill=LINE)
    fill_x = 82 + int(916 * max(0.0, min(1.0, probability)))
    draw.rounded_rectangle((82, 605, fill_x, 626), 10, fill=accent)

    draw.rounded_rectangle((50, 680, 1030, 930), 22, fill=PANEL, outline=LINE, width=2)
    stat_rows = [
        ("xG", stats["xg"], "УДАРЫ", stats["shots"], "В СТВОР", stats["sot"]),
        ("УГЛОВЫЕ", stats["corners"], "ВЛАДЕНИЕ", stats["possession"], "ГОЛ. МОМЕНТЫ", stats["big_chances"]),
    ]
    y = 712
    for row in stat_rows:
        xs = (80, 390, 700)
        for idx in range(3):
            label = row[idx * 2]
            value = row[idx * 2 + 1]
            draw.text((xs[idx], y), label, font=_font(15, True), fill=MUTED)
            draw.text((xs[idx], y + 34), value, font=_fit(draw, value, 245, 26, True), fill=TEXT)
        y += 105

    hy, ay = cards.get("home_yellow"), cards.get("away_yellow")
    hr, ar = cards.get("home_red"), cards.get("away_red")
    yellow = "—" if hy is None or ay is None else f"{hy}:{ay}"
    red = "—" if hr is None or ar is None else f"{hr}:{ar}"
    draw.text((80, 900), f"КАРТОЧКИ   🟨 {yellow}     🟥 {red}", font=_font(20, True), fill=MUTED)

    draw.rounded_rectangle((170, 980, 910, 1065), 24, fill=accent)
    _center(draw, "🎯  В ИГРЕ", 999, _font(35, True), (4, 12, 18))
    _center(draw, "GOOL v2 · AI FOOTBALL ANALYTICS", 1100, _font(18, True), MUTED)
    return _save(img)


def render_result_card(row: dict[str, Any], result: str, minute: int, home_score: int, away_score: int) -> bytes:
    head = str(row.get("head") or "another_goal")
    theme = THEMES.get(head, THEMES["another_goal"])
    base_accent = theme["accent"]
    won = str(result).lower() == "won"
    accent = base_accent if won else RED
    status = "✅ СИГНАЛ ЗАШЁЛ" if won else "❌ СИГНАЛ НЕ ЗАШЁЛ"
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

    img = Image.new("RGB", (W, 1050), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((20, 18, 1060, 1030), 30, fill=theme["deep"], outline=accent, width=3)
    draw.rounded_rectangle((38, 36, 1042, 130), 22, fill=PANEL, outline=accent, width=2)
    draw.text((62, 53), "GOOL v2", font=_font(34, True), fill=base_accent)
    draw.text((225, 56), label, font=_fit(draw, label, 500, 30, True), fill=TEXT)
    draw.text((790, 58), "РЕЗУЛЬТАТ", font=_font(22, True), fill=accent)
    draw.text((62, 155), league, font=_fit(draw, league, 900, 23, True), fill=MUTED)

    home_logo = _team_logo(fs_meta, "home")
    away_logo = _team_logo(fs_meta, "away")
    _badge(draw, home_logo, home, 70, 220, base_accent)
    _badge(draw, away_logo, away, 860, 220, base_accent)
    draw.text((48, 385), home, font=_fit(draw, home, 350, 30, True), fill=TEXT)
    af = _fit(draw, away, 350, 30, True)
    ab = draw.textbbox((0, 0), away, font=af)
    draw.text((1032 - (ab[2] - ab[0]), 385), away, font=af, fill=TEXT)
    _center(draw, f"{home_score}:{away_score}", 255, _font(82, True), TEXT)
    _center(draw, f"{minute}' · ИТОГ", 350, _font(21, True), MUTED)

    draw.rounded_rectangle((80, 445, 1000, 575), 26, fill=PANEL2, outline=accent, width=3)
    _center(draw, status, 475, _font(43, True), accent)
    _center(draw, f"{label} · P на входе {probability * 100:.1f}%", 535, _font(24, True), TEXT)

    draw.rounded_rectangle((60, 615, 1020, 790), 22, fill=PANEL, outline=LINE, width=2)
    draw.text((90, 642), "ВХОД", font=_font(16, True), fill=MUTED)
    draw.text((90, 680), f"{entry_minute}' · {entry_score[0]}:{entry_score[1]}", font=_font(28, True), fill=TEXT)
    draw.text((580, 642), "ПОДТВЕРЖДЕНИЕ", font=_font(16, True), fill=MUTED)
    draw.text((580, 680), f"{minute}' · {home_score}:{away_score}", font=_font(28, True), fill=accent)
    if stats:
        line = f"Удары {stats.get('shots','—')} · В створ {stats.get('sot','—')} · Угловые {stats.get('corners','—')}"
        draw.text((90, 735), line, font=_fit(draw, line, 870, 19, True), fill=MUTED)

    draw.rounded_rectangle((150, 845, 930, 920), 22, fill=accent)
    _center(draw, "РЕЗУЛЬТАТ ПОДТВЕРЖДЁН", 863, _font(31, True), (4, 12, 18))
    _center(draw, "GOOL v2 · VERIFIED LIVE RESULT", 960, _font(18, True), MUTED)
    return _save(img)
