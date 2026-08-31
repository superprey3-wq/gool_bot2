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
PANEL = (6, 14, 23)
PANEL2 = (9, 22, 34)
LINE = (43, 66, 88)
RED = (241, 72, 82)
GOLD = (255, 194, 55)

# Four real visual templates. Layout is shared, identity is market-specific.
THEMES = {
    "another_goal": {"accent": (55, 224, 79), "deep": (0, 48, 21), "label": "ЕЩЁ ГОЛ", "subtitle": "LIVE-СИГНАЛ"},
    "over_2_5": {"accent": (39, 151, 255), "deep": (0, 31, 73), "label": "ТОТАЛ БОЛЬШЕ 2.5", "subtitle": "LIVE-СИГНАЛ"},
    "both_teams_to_score": {"accent": (181, 65, 255), "deep": (57, 5, 82), "label": "ОБЕ ЗАБЬЮТ — ДА", "subtitle": "LIVE-СИГНАЛ"},
    "goal_before_ht": {"accent": (255, 132, 26), "deep": (82, 31, 0), "label": "ГОЛ ДО ПЕРЕРЫВА", "subtitle": "LIVE-СИГНАЛ"},
}
HEAD_LABELS = {k: v["label"] for k, v in THEMES.items()}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 34, bold: bool = True) -> ImageFont.ImageFont:
    text = str(text or "")
    for size in range(start, 12, -2):
        f = _font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= width:
            return f
    return _font(12, bold)


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    b = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (b[2] - b[0])) / 2, y), str(text), font=font, fill=fill)


def _save(img: Image.Image) -> bytes:
    out = BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def _safe_pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
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


def _analysis_path() -> Path:
    runtime = os.getenv("RUNTIME_DATA_DIR", "data")
    return Path(os.getenv("SIGNAL_ANALYSIS_PATH", runtime + "/live/gool_bot2_analysis.jsonl"))


def _read_assets() -> dict[str, Any]:
    try:
        p = _asset_path()
        data = json.loads(p.read_text("utf-8")) if p.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember(match_id: str, meta: dict[str, Any], stats: dict[str, str]) -> None:
    if not match_id:
        return
    cache = _read_assets()
    old = dict(cache.get(match_id) or {})
    old.update({"flashscore_meta": meta, "stats_snapshot": stats})
    cache[match_id] = old
    if len(cache) > 700:
        cache = dict(list(cache.items())[-700:])
    try:
        p = _asset_path(); p.parent.mkdir(parents=True, exist_ok=True)
        t = p.with_suffix(".tmp"); t.write_text(json.dumps(cache, ensure_ascii=False), "utf-8"); t.replace(p)
    except Exception:
        pass


def _probability_history(match_id: str, head: str, limit: int = 18) -> list[tuple[int, float]]:
    """Real model history from analysis journal; never synthetic/decorative."""
    p = _analysis_path()
    if not match_id or not p.exists():
        return []
    rows: list[tuple[int, float]] = []
    try:
        # Reading the journal is acceptable at signal render time; retain only final points.
        for line in p.read_text("utf-8", errors="ignore").splitlines()[-6000:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("match_id") or "") != match_id or str(row.get("head") or "") != head:
                continue
            prob = row.get("probability")
            if prob is None:
                continue
            try:
                minute = int(row.get("minute") or 0); value = float(prob)
            except Exception:
                continue
            if 0.0 <= value <= 1.0:
                rows.append((minute, value))
    except Exception:
        return []
    # Collapse repeated minute snapshots to the most recent estimate.
    by_minute: dict[int, float] = {}
    for minute, value in rows:
        by_minute[minute] = value
    return sorted(by_minute.items())[-limit:]


@lru_cache(maxsize=2048)
def _download(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": UA, "Referer": "https://www.flashscore.com/"})
        with urlopen(req, timeout=7) as response:
            raw = response.read(700_000)
        im = Image.open(BytesIO(raw)).convert("RGBA"); im.thumbnail((190, 190), Image.Resampling.LANCZOS)
        return im.copy()
    except Exception:
        return None


def _team_logo(meta: dict[str, Any], side: str) -> Image.Image | None:
    fn = str(meta.get(f"{side}_logo_file") or "").strip()
    if fn:
        im = _download(f"https://static.flashscore.com/res/image/data/{fn}")
        if im is not None: return im
    explicit = str(meta.get(f"{side}_logo_url") or "").strip()
    if explicit:
        im = _download(explicit)
        if im is not None: return im
    url = FlashscoreProvider.team_logo_url(str(meta.get(f"{side}_team_slug") or ""), str(meta.get(f"{side}_team_id") or "")) or ""
    return _download(url) if url else None


def _canvas(height: int, accent: tuple[int, int, int], deep: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (W, height), DARK)
    px = img.load()
    for y in range(height):
        t = y / max(1, height - 1)
        glow = max(0.0, 1.0 - abs(t - 0.29) * 2.5)
        for x in range(W):
            edge = abs(x - W / 2) / (W / 2)
            g = glow * (1.0 - 0.40 * edge)
            px[x, y] = tuple(min(255, int(DARK[i]*(1-g) + deep[i]*g + accent[i]*g*0.08)) for i in range(3))
    return img.convert("RGBA")


def _stadium(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
    horizon = 350
    for x in range(10, W, 50):
        draw.line((W//2, horizon, x, 575), fill=(25, 61, 76), width=1)
    draw.arc((30, 250, 1050, 605), 190, 350, fill=(36, 75, 90), width=2)
    draw.arc((90, 285, 990, 640), 190, 350, fill=(24, 56, 72), width=2)
    for x in (80, 180, 280, 380, 700, 800, 900, 1000):
        draw.ellipse((x-3, 303, x+3, 309), fill=accent)
    draw.rectangle((0, 345, W, 358), fill=(4, 18, 25))


def _header(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int], label: str, league: str, round_name: str, providers: int, result: bool = False) -> None:
    draw.rounded_rectangle((18, 16, 1062, 113), 24, fill=(4, 13, 22), outline=accent, width=3)
    draw.rounded_rectangle((32, 31, 190, 98), 16, fill=(9, 27, 31), outline=accent, width=2)
    f = _fit(draw, "GOOL v2", 128, 24, True); b = draw.textbbox((0, 0), "GOOL v2", font=f)
    draw.text((111-(b[2]-b[0])/2, 49), "GOOL v2", font=f, fill=TEXT)
    draw.text((215, 27), label, font=_fit(draw, label, 535, 33, True), fill=TEXT)
    draw.text((217, 70), "РЕЗУЛЬТАТ" if result else "LIVE-СИГНАЛ", font=_font(16, True), fill=accent)
    draw.text((945, 42), "RESULT" if result else "LIVE", font=_font(18, True), fill=accent)
    draw.text((855, 72), f"DATA {providers}/3", font=_font(14, True), fill=MUTED)
    competition = league + (f" · {round_name}" if round_name else "")
    cf = _fit(draw, competition, 920, 19, True); cb = draw.textbbox((0, 0), competition, font=cf)
    draw.text(((W-(cb[2]-cb[0]))/2, 126), competition, font=cf, fill=TEXT)


def _paste_logo(img: Image.Image, draw: ImageDraw.ImageDraw, logo: Image.Image | None, name: str, cx: int, cy: int, accent: tuple[int, int, int]) -> None:
    if logo is not None:
        box = logo.getbbox(); logo = logo.crop(box) if box else logo
        scale = min(150/max(1, logo.width), 150/max(1, logo.height))
        logo = logo.resize((max(1, int(logo.width*scale)), max(1, int(logo.height*scale))), Image.Resampling.LANCZOS)
        shadow = Image.new("RGBA", logo.size, (0,0,0,0)); alpha = logo.getchannel("A").filter(ImageFilter.GaussianBlur(5)); shadow.putalpha(alpha)
        shadow = ImageEnhance.Brightness(shadow).enhance(0)
        img.alpha_composite(shadow, (cx-logo.width//2+4, cy-logo.height//2+7)); img.alpha_composite(logo, (cx-logo.width//2, cy-logo.height//2)); return
    draw.ellipse((cx-66, cy-66, cx+66, cy+66), fill=PANEL2, outline=accent, width=3)
    initials = "".join(p[:1] for p in str(name).replace("-", " ").split()[:3]).upper() or "?"
    f = _font(30, True); b = draw.textbbox((0,0), initials, font=f)
    draw.text((cx-(b[2]-b[0])/2, cy-20), initials, font=f, fill=TEXT)


def _team_name(draw: ImageDraw.ImageDraw, text: str, center_x: int, y: int) -> None:
    f = _fit(draw, text, 335, 29, True); b = draw.textbbox((0, 0), text, font=f)
    draw.text((center_x-(b[2]-b[0])/2, y), text, font=f, fill=TEXT)


def _sparkline(draw: ImageDraw.ImageDraw, history: list[tuple[int, float]], box: tuple[int,int,int,int], accent: tuple[int,int,int], current: float) -> None:
    x1,y1,x2,y2 = box
    draw.text((x1, y1-25), "ДИНАМИКА ВЕРОЯТНОСТИ", font=_font(12, True), fill=MUTED)
    for i in range(4):
        yy = y1 + int((y2-y1) * i / 3)
        draw.line((x1, yy, x2, yy), fill=(25,45,59), width=1)
    vals = history[:] if history else [(0, current)]
    if len(vals) == 1:
        vals = [(max(0, vals[0][0]-1), vals[0][1]), vals[0]]
    probs = [v for _,v in vals]
    lo = max(0.0, min(probs)-0.06); hi = min(1.0, max(probs)+0.06)
    if hi-lo < 0.12: hi = min(1.0, lo+0.12)
    points=[]
    for i, (_, p) in enumerate(vals):
        x = x1 + (x2-x1) * i / max(1, len(vals)-1)
        y = y2 - (p-lo) / max(0.001, hi-lo) * (y2-y1)
        points.append((int(x), int(y)))
    if len(points) >= 2: draw.line(points, fill=accent, width=4, joint="curve")
    for x,y in points[-4:]: draw.ellipse((x-3,y-3,x+3,y+3), fill=accent)
    lm, lp = vals[-1]
    draw.text((x2-72, y1+3), f"{lp*100:.1f}%", font=_font(13, True), fill=accent)
    draw.text((x1, y2+6), f"{vals[0][0]}'", font=_font(10, True), fill=MUTED)
    draw.text((x2-28, y2+6), f"{lm}'", font=_font(10, True), fill=MUTED)


def _model_strip(draw: ImageDraw.ImageDraw, head: str, probability: float, model_result: dict[str, Any], accent: tuple[int,int,int], history: list[tuple[int,float]]) -> None:
    draw.rounded_rectangle((22, 570, 1058, 755), 18, fill=(4, 14, 22), outline=accent, width=2)
    draw.text((45, 594), "ВЕРОЯТНОСТЬ", font=_font(13, True), fill=MUTED)
    draw.text((45, 625), f"{probability*100:.1f}%", font=_font(42, True), fill=accent)
    draw.line((228,585,228,741), fill=(25,47,61), width=1)
    if head in {"over_2_5", "both_teams_to_score"}:
        draw.text((255, 594), "HT-MODEL", font=_font(13, True), fill=MUTED)
        draw.text((255, 632), _safe_pct(model_result.get("football_data",{}).get(head)), font=_font(29, True), fill=TEXT)
        draw.text((435, 594), "ЛИНИЯ", font=_font(13, True), fill=MUTED)
        draw.text((435, 632), "2.5" if head=="over_2_5" else "ОЗ — ДА", font=_font(27, True), fill=accent)
        graph_x = 620
    else:
        direct=model_result.get("direct",{}).get(head); hazard=model_result.get("hazard",{}).get(head); disagreement=model_result.get("disagreement",{}).get(head)
        for x,lab,val,col in [(250,"DIRECT",_safe_pct(direct),TEXT),(405,"HAZARD",_safe_pct(hazard),TEXT),(555,"РАСХОЖД.",_safe_pct(disagreement),GOLD)]:
            draw.text((x,594),lab,font=_font(12,True),fill=MUTED); draw.text((x,632),val,font=_font(24,True),fill=col)
        graph_x = 730
    _sparkline(draw, history, (graph_x, 620, 1025, 700), accent, probability)


def _stats_strip(draw: ImageDraw.ImageDraw, stats: dict[str,str], cards: dict[str,Any], providers: int, accent: tuple[int,int,int]) -> None:
    draw.rounded_rectangle((22, 775, 1058, 955), 18, fill=(5, 14, 23), outline=LINE, width=2)
    hy,ay=cards.get("home_yellow"),cards.get("away_yellow"); hr,ar=cards.get("home_red"),cards.get("away_red")
    yellow="—" if hy is None or ay is None else f"{hy}:{ay}"; red="—" if hr is None or ar is None else f"{hr}:{ar}"
    items=[("xG",stats.get("xg","—")),("УДАРЫ",stats.get("shots","—")),("В СТВОР",stats.get("sot","—")),("УГЛОВЫЕ",stats.get("corners","—")),("ВЛАДЕНИЕ",stats.get("possession","—")),("МОМЕНТЫ",stats.get("big_chances","—"))]
    for x,(lab,val) in zip([40,205,370,535,700,865],items):
        draw.text((x,797),lab,font=_font(13,True),fill=MUTED); draw.text((x,827),val,font=_fit(draw,val,150,23,True),fill=TEXT)
    draw.line((35,878,1045,878),fill=(25,46,60),width=1)
    draw.text((45,897),f"КАРТОЧКИ  Ж {yellow}   К {red}",font=_font(16,True),fill=MUTED)
    draw.text((795,897),f"ИСТОЧНИКИ {providers}/3",font=_font(17,True),fill=accent)


def render_signal_card(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> bytes:
    theme=THEMES.get(head,THEMES["another_goal"]); accent,deep=theme["accent"],theme["deep"]
    match=record.get("match") or {}; meta=flashscore_meta(record); stats=_stats_for_record(record)
    match_id=str(match.get("flashscore_event_id") or ""); _remember(match_id,meta,stats)
    home=str(match.get("home") or "?"); away=str(match.get("away") or "?"); league=str(match.get("league") or "LIVE FOOTBALL"); round_name=str(meta.get("round") or "").strip()
    minute=int(match.get("minute") or 0); hs=int(match.get("home_score") or 0); aws=int(match.get("away_score") or 0); providers=len(record.get("providers") or {})
    history=_probability_history(match_id,head)
    if not history or history[-1][0] != minute: history=(history+[(minute,float(probability))])[-18:]

    img=_canvas(1040,accent,deep); draw=ImageDraw.Draw(img); draw.rounded_rectangle((8,8,1072,1032),28,outline=accent,width=3)
    _stadium(draw,accent); _header(draw,accent,theme["label"],league,round_name,providers,False)
    _paste_logo(img,draw,_team_logo(meta,"home"),home,160,320,accent); _paste_logo(img,draw,_team_logo(meta,"away"),away,920,320,accent)
    _team_name(draw,home,190,430); _team_name(draw,away,890,430)
    draw.rounded_rectangle((458,168,622,222),14,fill=accent); _center(draw,"ПЕРЕРЫВ" if match.get("is_halftime") else f"{minute}'",178,_font(25,True),DARK)
    _center(draw,f"{hs} : {aws}",236,_font(82,True),TEXT); _center(draw,"1-Й ТАЙМ" if minute<=45 else "2-Й ТАЙМ",335,_font(18,True),MUTED)
    _model_strip(draw,head,float(probability),model_result,accent,history); _stats_strip(draw,stats,cards,providers,accent)
    draw.rounded_rectangle((390,972,690,1022),14,fill=accent); _center(draw,"В ИГРЕ",980,_font(24,True),DARK)
    return _save(img)


def render_result_card(row: dict[str, Any], result: str, minute: int, home_score: int, away_score: int) -> bytes:
    head=str(row.get("head") or "another_goal"); theme=THEMES.get(head,THEMES["another_goal"]); won=str(result).lower()=="won"
    accent=theme["accent"] if won else RED; deep=theme["deep"] if won else (63,8,14)
    home=str(row.get("home") or "?"); away=str(row.get("away") or "?"); league=str(row.get("league") or "LIVE FOOTBALL"); providers=int(row.get("provider_count") or 1)
    cache=_read_assets().get(str(row.get("match_id") or ""),{}) or {}; meta=dict(row.get("flashscore_meta") or cache.get("flashscore_meta") or {}); stats=dict(row.get("stats_snapshot") or cache.get("stats_snapshot") or {})
    round_name=str(meta.get("round") or "").strip(); entry=row.get("score") or [0,0]; entry_min=int(row.get("minute") or 0); probability=float(row.get("probability") or 0.0)

    img=_canvas(960,accent,deep); draw=ImageDraw.Draw(img); draw.rounded_rectangle((8,8,1072,952),28,outline=accent,width=3)
    _stadium(draw,accent); _header(draw,accent,theme["label"],league,round_name,providers,True)
    _paste_logo(img,draw,_team_logo(meta,"home"),home,165,310,accent); _paste_logo(img,draw,_team_logo(meta,"away"),away,915,310,accent)
    _team_name(draw,home,190,414); _team_name(draw,away,890,414); _center(draw,f"{home_score} : {away_score}",225,_font(80,True),TEXT)
    _center(draw,f"{minute}' · {'ПОДТВЕРЖДЕНО' if won else 'ЗАКРЫТО'}",326,_font(21,True),accent)
    draw.rounded_rectangle((55,500,1025,655),24,fill=(7,19,30),outline=accent,width=3)
    _center(draw,"✅ СИГНАЛ ЗАШЁЛ" if won else "❌ СИГНАЛ НЕ ЗАШЁЛ",523,_font(40,True),accent)
    _center(draw,f"{theme['label']} · P НА ВХОДЕ {probability*100:.1f}%",585,_font(21,True),TEXT)
    draw.rounded_rectangle((55,685,1025,825),20,fill=PANEL,outline=LINE,width=2)
    draw.text((85,710),"ВХОД",font=_font(15,True),fill=MUTED); draw.text((85,744),f"{entry_min}' · {int(entry[0])}:{int(entry[1])}",font=_font(28,True),fill=TEXT)
    draw.text((565,710),"ПОДТВЕРЖДЕНИЕ",font=_font(15,True),fill=MUTED); draw.text((565,744),f"{minute}' · {home_score}:{away_score}",font=_font(28,True),fill=accent)
    stat_line=f"Удары {stats.get('shots','—')} · В створ {stats.get('sot','—')} · Угловые {stats.get('corners','—')}"
    draw.text((85,790),stat_line,font=_fit(draw,stat_line,880,16,True),fill=MUTED)
    draw.rounded_rectangle((190,855,890,915),16,fill=accent); _center(draw,"РЕЗУЛЬТАТ ПОДТВЕРЖДЁН" if won else "РЕЗУЛЬТАТ ЗАКРЫТ",867,_font(25,True),DARK)
    _center(draw,"GOOL v2 · VERIFIED LIVE RESULT",925,_font(15,True),MUTED)
    return _save(img)
