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
LINE = (45, 63, 88)
RED = (244, 84, 84)
GOLD = (255, 184, 48)
THEMES = {
    "another_goal": {"accent": (82, 220, 118), "deep": (4, 42, 24), "label": "ЕЩЁ ГОЛ"},
    "over_2_5": {"accent": (61, 178, 255), "deep": (3, 28, 58), "label": "ТОТАЛ БОЛЬШЕ 2.5"},
    "both_teams_to_score": {"accent": (184, 98, 255), "deep": (39, 9, 58), "label": "ОБЕ ЗАБЬЮТ — ДА"},
    "goal_before_ht": {"accent": (255, 148, 45), "deep": (61, 27, 4), "label": "ГОЛ ДО ПЕРЕРЫВА"},
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


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int = 36, bold: bool = True) -> ImageFont.ImageFont:
    for size in range(start, 14, -2):
        f = _font(size, bold)
        if draw.textbbox((0, 0), str(text), font=f)[2] <= width:
            return f
    return _font(14, bold)


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _save(img: Image.Image) -> bytes:
    out = BytesIO(); img.convert("RGB").save(out, "PNG", optimize=True); return out.getvalue()


def _safe_pct(value: Any) -> str:
    try: return f"{float(value)*100:.1f}%"
    except Exception: return "—"


def _fmt_pair(pair: tuple[float | None, float | None], digits: int = 0, suffix: str = "") -> str:
    a, b = pair
    if a is None or b is None: return "—"
    if digits: return f"{a:.{digits}f}{suffix} : {b:.{digits}f}{suffix}"
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


def stats_snapshot(record: dict[str, Any]) -> dict[str, str]: return _stats_for_record(record)
def flashscore_meta(record: dict[str, Any]) -> dict[str, Any]: return dict((((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}))


def _asset_path() -> Path: return Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "signal_card_assets.json"
def _read_assets() -> dict[str, Any]:
    try:
        p = _asset_path(); data = json.loads(p.read_text("utf-8")) if p.exists() else {}; return data if isinstance(data, dict) else {}
    except Exception: return {}


def _remember(match_id: str, meta: dict[str, Any], stats: dict[str, str]) -> None:
    if not match_id: return
    cache = _read_assets(); cache[match_id] = {"flashscore_meta": meta, "stats_snapshot": stats}
    if len(cache) > 600: cache = dict(list(cache.items())[-600:])
    try:
        p = _asset_path(); p.parent.mkdir(parents=True, exist_ok=True); t = p.with_suffix(".tmp"); t.write_text(json.dumps(cache, ensure_ascii=False), "utf-8"); t.replace(p)
    except Exception: pass


@lru_cache(maxsize=1024)
def _download(url: str) -> Image.Image | None:
    if not url: return None
    try:
        req = Request(url, headers={"User-Agent": UA, "Referer": "https://www.flashscore.com/"})
        with urlopen(req, timeout=7) as response: raw = response.read(500_000)
        im = Image.open(BytesIO(raw)).convert("RGBA"); im.thumbnail((150,150), Image.Resampling.LANCZOS); return im.copy()
    except Exception: return None


def _team_logo(meta: dict[str, Any], side: str) -> Image.Image | None:
    # Old GOOL used OA/OB image filenames straight from the Flashscore master feed.
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


def _badge(img: Image.Image, draw: ImageDraw.ImageDraw, logo: Image.Image | None, name: str, cx: int, cy: int, accent: tuple[int,int,int]) -> None:
    draw.ellipse((cx-68,cy-68,cx+68,cy+68), fill=PANEL2, outline=accent, width=3)
    if logo is not None:
        box = logo.getbbox(); logo = logo.crop(box) if box else logo
        scale = min(112/max(1,logo.width),112/max(1,logo.height)); logo = logo.resize((max(1,int(logo.width*scale)),max(1,int(logo.height*scale))), Image.Resampling.LANCZOS)
        img.paste(logo, (cx-logo.width//2,cy-logo.height//2), logo); return
    initials = "".join(x[:1] for x in str(name).replace("-"," ").split()[:3]).upper() or "?"
    f = _font(30,True); b=draw.textbbox((0,0),initials,font=f); draw.text((cx-(b[2]-b[0])/2,cy-20),initials,font=f,fill=TEXT)


def _header(draw: ImageDraw.ImageDraw, accent: tuple[int,int,int], label: str, league: str, providers: int, result: bool=False) -> None:
    draw.rounded_rectangle((24,20,1056,112),24,fill=PANEL,outline=accent,width=3)
    draw.text((50,34),"GOOL v2",font=_font(35,True),fill=accent)
    draw.text((220,36),label,font=_fit(draw,label,540,31,True),fill=TEXT)
    draw.text((865,39),"РЕЗУЛЬТАТ" if result else "LIVE",font=_font(18,True),fill=accent)
    draw.text((865,70),f"DATA {providers}/3",font=_font(14,True),fill=MUTED)
    draw.text((50,140),league,font=_fit(draw,league,960,22,True),fill=MUTED)


def _teams(img: Image.Image, draw: ImageDraw.ImageDraw, meta: dict[str,Any], home: str, away: str, score: str, minute: str, accent: tuple[int,int,int]) -> None:
    _badge(img,draw,_team_logo(meta,"home"),home,175,290,accent); _badge(img,draw,_team_logo(meta,"away"),away,905,290,accent)
    _center(draw,score,225,_font(76,True),TEXT); _center(draw,minute,322,_font(24,True),accent)
    hf=_fit(draw,home,330,30,True); hb=draw.textbbox((0,0),home,font=hf); draw.text((175-(hb[2]-hb[0])/2,385),home,font=hf,fill=TEXT)
    af=_fit(draw,away,330,30,True); ab=draw.textbbox((0,0),away,font=af); draw.text((905-(ab[2]-ab[0])/2,385),away,font=af,fill=TEXT)


def _stat_box(draw: ImageDraw.ImageDraw, xy: tuple[int,int,int,int], label: str, value: str, accent: tuple[int,int,int]|None=None) -> None:
    draw.rounded_rectangle(xy,17,fill=PANEL,outline=LINE,width=2); x1,y1,x2,_=xy
    draw.text((x1+16,y1+12),label,font=_font(14,True),fill=MUTED); draw.text((x1+16,y1+40),value,font=_fit(draw,value,x2-x1-32,24,True),fill=accent or TEXT)


def render_signal_card(record: dict[str, Any], head: str, probability: float, model_result: dict[str, Any], cards: dict[str, Any]) -> bytes:
    theme=THEMES.get(head,THEMES["another_goal"]); accent=theme["accent"]; deep=theme["deep"]
    m=record.get("match") or {}; meta=flashscore_meta(record); stats=_stats_for_record(record); match_id=str(m.get("flashscore_event_id") or ""); _remember(match_id,meta,stats)
    home=str(m.get("home") or "?"); away=str(m.get("away") or "?"); league=str(m.get("league") or "LIVE FOOTBALL"); minute=int(m.get("minute") or 0); score=f"{int(m.get('home_score') or 0)} : {int(m.get('away_score') or 0)}"; providers=len(record.get("providers") or {})
    img=Image.new("RGBA",(W,1180),BG+(255,)); draw=ImageDraw.Draw(img)
    draw.rounded_rectangle((18,14,1062,1162),30,fill=deep+(255,),outline=accent,width=3); _header(draw,accent,theme["label"],league,providers)
    _teams(img,draw,meta,home,away,score,"ПЕРЕРЫВ" if m.get("is_halftime") else f"{minute}'",accent)
    draw.rounded_rectangle((55,465,1025,650),24,fill=PANEL2,outline=accent,width=2)
    draw.text((84,492),"ВЕРОЯТНОСТЬ",font=_font(15,True),fill=MUTED); draw.text((84,528),f"{probability*100:.1f}%",font=_font(48,True),fill=accent)
    if head in {"over_2_5","both_teams_to_score"}:
        draw.text((430,492),"HT MODEL",font=_font(15,True),fill=MUTED); draw.text((430,535),_safe_pct(model_result.get("football_data",{}).get(head)),font=_font(31,True),fill=TEXT)
    else:
        vals=[("DIRECT",_safe_pct(model_result.get("direct",{}).get(head))), ("HAZARD",_safe_pct(model_result.get("hazard",{}).get(head))), ("РАСХОЖД.",_safe_pct(model_result.get("disagreement",{}).get(head)))]
        for x,(lab,val) in zip((385,605,815),vals): draw.text((x,492),lab,font=_font(14,True),fill=MUTED); draw.text((x,535),val,font=_font(27,True),fill=GOLD if lab.startswith("РАС") else TEXT)
    draw.rounded_rectangle((84,607,996,623),8,fill=LINE); draw.rounded_rectangle((84,607,84+int(912*max(0,min(1,probability))),623),8,fill=accent)
    boxes=[("xG",stats["xg"]),("УДАРЫ",stats["shots"]),("В СТВОР",stats["sot"]),("УГЛОВЫЕ",stats["corners"]),("ВЛАДЕНИЕ",stats["possession"]),("МОМЕНТЫ",stats["big_chances"])]
    for i,(lab,val) in enumerate(boxes):
        row,col=divmod(i,3); x=55+col*325; y=690+row*112; _stat_box(draw,(x,y,x+300,y+92),lab,val,accent if i==0 else None)
    hy,ay=cards.get("home_yellow"),cards.get("away_yellow"); hr,ar=cards.get("home_red"),cards.get("away_red")
    yellow="—" if hy is None or ay is None else f"{hy}:{ay}"; red="—" if hr is None or ar is None else f"{hr}:{ar}"
    draw.rounded_rectangle((55,940,1025,1030),20,fill=PANEL,outline=LINE,width=2); draw.text((84,970),f"КАРТОЧКИ   🟨 {yellow}    🟥 {red}     ИСТОЧНИКИ {providers}/3",font=_font(19,True),fill=MUTED)
    draw.rounded_rectangle((240,1060,840,1128),20,fill=accent); _center(draw,"🎯  В ИГРЕ",1075,_font(29,True),BG)
    return _save(img)


def render_result_card(row: dict[str, Any], result: str, minute: int, home_score: int, away_score: int) -> bytes:
    head=str(row.get("head") or "another_goal"); theme=THEMES.get(head,THEMES["another_goal"]); base=theme["accent"]; won=str(result).lower()=="won"; accent=base if won else RED; deep=theme["deep"]
    home=str(row.get("home") or "?"); away=str(row.get("away") or "?"); league=str(row.get("league") or "LIVE FOOTBALL"); providers=int(row.get("provider_count") or 1); cached=_read_assets().get(str(row.get("match_id") or ""),{}) or {}; meta=dict(row.get("flashscore_meta") or cached.get("flashscore_meta") or {}); stats=dict(row.get("stats_snapshot") or cached.get("stats_snapshot") or {})
    entry=row.get("score") or [0,0]; entry_min=int(row.get("minute") or 0); probability=float(row.get("probability") or 0.0)
    img=Image.new("RGBA",(W,960),BG+(255,)); draw=ImageDraw.Draw(img); draw.rounded_rectangle((18,14,1062,942),30,fill=deep+(255,),outline=accent,width=3)
    _header(draw,base,theme["label"],league,providers,result=True); _teams(img,draw,meta,home,away,f"{home_score} : {away_score}",f"{minute}' · ПОДТВЕРЖДЕНО",accent)
    draw.rounded_rectangle((70,465,1010,610),28,fill=PANEL2,outline=accent,width=3); _center(draw,"✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ",495,_font(40,True),accent); _center(draw,f"{theme['label']} · P НА ВХОДЕ {probability*100:.1f}%",553,_font(22,True),TEXT)
    draw.rounded_rectangle((70,650,1010,805),22,fill=PANEL,outline=LINE,width=2); draw.text((100,677),"ВХОД",font=_font(15,True),fill=MUTED); draw.text((100,715),f"{entry_min}' · {entry[0]}:{entry[1]}",font=_font(29,True),fill=TEXT); draw.text((590,677),"ПОДТВЕРЖДЕНИЕ",font=_font(15,True),fill=MUTED); draw.text((590,715),f"{minute}' · {home_score}:{away_score}",font=_font(29,True),fill=accent)
    statline=f"Удары {stats.get('shots','—')} · В створ {stats.get('sot','—')} · Угловые {stats.get('corners','—')}"; draw.text((100,765),statline,font=_fit(draw,statline,850,18,True),fill=MUTED)
    draw.rounded_rectangle((190,842,890,902),18,fill=accent); _center(draw,"РЕЗУЛЬТАТ ПОДТВЕРЖДЁН",855,_font(28,True),BG); _center(draw,"GOOL v2 · VERIFIED LIVE RESULT",920,_font(16,True),MUTED)
    return _save(img)
