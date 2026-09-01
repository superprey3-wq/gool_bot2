from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc

# Deliberately different from trained-model card colors.
LIVE_THEMES = {
    "two_more_goals": ((255, 64, 129), "ЕЩЁ +2 ГОЛА", "НУЖНЫ ДВА НОВЫХ ГОЛА", "GOOL ATTACK RADAR"),
    "both_teams_to_score": ((0, 214, 201), "ОБЕ ЗАБЬЮТ — ДА", "ДАВЛЕНИЕ НЕЗАБИВШЕЙ КОМАНДЫ", "GOOL BTTS RADAR"),
}


def _save(img: Image.Image) -> bytes:
    out = BytesIO();img.convert("RGB").save(out,"PNG",optimize=True);return out.getvalue()

def _num(v,fmt=".0f"):
    try:return format(float(v),fmt)
    except:return "—"

def _momentum_pair(momentum:dict[str,Any],key:str,suffix:str="") -> str:
    h=momentum.get(f"home_{key}");a=momentum.get(f"away_{key}")
    if h is None or a is None:return "—"
    return f"{_num(h)}{suffix} : {_num(a)}{suffix}"

def _scoreless_side(match:dict[str,Any], analyzer:dict[str,Any]|None=None)->str:
    analyzer=analyzer or {};side=str(analyzer.get("scoreless_side") or "")
    if side in {"home","away"}:return side
    hs=int(match.get("home_score") or 0);aws=int(match.get("away_score") or 0)
    if hs==0 and aws>0:return "home"
    if aws==0 and hs>0:return "away"
    return ""

def render_gool_live_signal_card(record:dict[str,Any],head:str,confidence:float,pressure:float,cards:dict[str,Any])->bytes:
    accent,label,subtitle,radar=LIVE_THEMES.get(head,LIVE_THEMES["two_more_goals"]);match=record.get("match") or {};meta=sc.flashscore_meta(record);stats=sc.stats_snapshot(record);mid=str(match.get("flashscore_event_id") or "");sc._remember(mid,meta,stats)
    home=str(match.get("home") or "?");away=str(match.get("away") or "?");league=str(match.get("league") or "LIVE FOOTBALL");minute=int(match.get("minute") or 0);hs=int(match.get("home_score") or 0);aws=int(match.get("away_score") or 0);providers=len(record.get("providers") or {});momentum=record.get("live_momentum") or {}
    img=Image.new("RGBA",(1080,1240),sc.BG+(255,));d=ImageDraw.Draw(img)
    # Large colored identity rail makes LIVE strategies recognizable before reading text.
    d.rounded_rectangle((18,18,1062,1222),30,outline=accent,width=5);d.rounded_rectangle((24,20,1056,112),24,fill=sc.PANEL,outline=accent,width=2);d.rectangle((24,20,40,112),fill=accent);d.text((58,36),"GOOL 2",font=sc._font(36,True),fill=accent);d.text((58,78),radar,font=sc._font(15,True),fill=sc.TEXT);d.rounded_rectangle((825,38,1028,93),16,fill=accent);d.text((868,53),"LIVE",font=sc._font(20,True),fill=sc.BG)
    sc._center(d,label,142,sc._fit(d,label,850,40,True),accent);sc._center(d,subtitle,194,sc._fit(d,subtitle,900,17,True),sc.MUTED);sc._center(d,f"🏆 {league}",220,sc._fit(d,f"🏆 {league}",930,16,True),sc.TEXT);sc._badge(img,d,180,330,sc._logo(meta,"home"),home,accent);sc._badge(img,d,900,330,sc._logo(meta,"away"),away,accent);d.rounded_rectangle((390,250,690,413),28,fill=(9,19,31),outline=accent,width=4);sc._center(d,f"{hs} : {aws}",289,sc._font(66,True),sc.TEXT);sc._center(d,"ПЕРЕРЫВ" if match.get("is_halftime") else f"{minute}'",365,sc._font(25,True),accent)
    for x,n in ((180,home),(900,away)):
        f=sc._fit(d,n,330,29);b=d.textbbox((0,0),n,font=f);d.text((x-(b[2]-b[0])/2,440),n,font=f,fill=sc.TEXT)
    d.rounded_rectangle((55,500,1025,650),25,fill=sc.PANEL2,outline=accent,width=3);d.text((82,522),"ШАНС СОБЫТИЯ",font=sc._font(17,True),fill=sc.MUTED);d.text((82,560),f"{confidence*100:.0f}/100",font=sc._font(48,True),fill=accent);d.text((370,525),"GOOL PRESSURE",font=sc._font(15,True),fill=sc.MUTED);d.text((370,563),f"{pressure:.2f}",font=sc._font(39,True),fill=sc.TEXT);d.text((690,525),"СЧЁТ ПРИ АНАЛИЗЕ",font=sc._font(15,True),fill=sc.MUTED);d.text((690,563),f"{hs}:{aws} · {minute}'",font=sc._font(31,True),fill=sc.TEXT)
    if head=="two_more_goals":
        # +2 focuses on total attacking tempo from BOTH teams.
        sc._box(d,(55,680,285,785),"xG МАТЧ",str(stats.get("xg") or "—"),accent=accent);sc._box(d,(300,680,530,785),"УДАРЫ",str(stats.get("shots") or "—"));sc._box(d,(545,680,775,785),"В СТВОР",str(stats.get("sot") or "—"));sc._box(d,(790,680,1025,785),"BIG CHANCES",str(stats.get("big_chances") or "—"))
        sc._box(d,(55,815,285,920),"УДАРЫ 5М",_num(momentum.get("shots_total_last_5m")));sc._box(d,(300,815,530,920),"СТВОР 5М",_num(momentum.get("sot_total_last_5m")));sc._box(d,(545,815,775,920),"xG 5М",_num(momentum.get("xg_total_last_5m"),".2f"));sc._box(d,(790,815,1025,920),"УДАРЫ 10М",_num(momentum.get("shots_total_last_10m")))
        sc._center(d,"🔥 ДВА ГОЛА ОТ ТЕКУЩЕГО СЧЁТА • оценивается общий темп матча",955,sc._fit(d,"🔥 ДВА ГОЛА ОТ ТЕКУЩЕГО СЧЁТА • оценивается общий темп матча",940,17,True),accent)
    else:
        # BTTS focuses visually on the team that still needs to score.
        side=_scoreless_side(match);team=home if side=="home" else away if side=="away" else "НЕЗАБИВШАЯ КОМАНДА";prefix="home" if side=="home" else "away" if side=="away" else ""
        sc._center(d,f"🎯 ДОЛЖЕН ЗАБИТЬ: {team}",682,sc._fit(d,f"🎯 ДОЛЖЕН ЗАБИТЬ: {team}",930,24,True),accent)
        xg5=momentum.get(f"{prefix}_xg_last_5m") if prefix else None;shots5=momentum.get(f"{prefix}_shots_last_5m") if prefix else None;sot5=momentum.get(f"{prefix}_sot_last_5m") if prefix else None;shots10=momentum.get(f"{prefix}_shots_last_10m") if prefix else None
        sc._box(d,(55,735,285,840),"ЕГО xG 5М",_num(xg5,".2f"),accent=accent);sc._box(d,(300,735,530,840),"ЕГО УДАРЫ 5М",_num(shots5));sc._box(d,(545,735,775,840),"ЕГО СТВОР 5М",_num(sot5));sc._box(d,(790,735,1025,840),"ЕГО УДАРЫ 10М",_num(shots10))
        sc._box(d,(55,870,285,975),"xG МАТЧ",str(stats.get("xg") or "—"));sc._box(d,(300,870,530,975),"УДАРЫ",str(stats.get("shots") or "—"));sc._box(d,(545,870,775,975),"В СТВОР",str(stats.get("sot") or "—"));sc._box(d,(790,870,1025,975),"ВЛАДЕНИЕ",str(stats.get("possession") or "—"))
    hy,ay=cards.get("home_yellow"),cards.get("away_yellow");hr,ar=cards.get("home_red"),cards.get("away_red");cardline=f"КАРТОЧКИ  🟨 {'—' if hy is None or ay is None else f'{hy}:{ay}'}   🟥 {'—' if hr is None or ar is None else f'{hr}:{ar}'}";sc._center(d,cardline,1015,sc._font(16,True),sc.MUTED);sc._center(d,f"ИСТОЧНИКИ: {providers}/3",1055,sc._font(16,True),sc.MUTED);d.rounded_rectangle((340,1100,740,1175),20,fill=accent);sc._center(d,"●  В ИГРЕ",1120,sc._font(27,True),sc.BG);return _save(img)

def render_two_more_signal_card(record:dict[str,Any],confidence:float,pressure:float,cards:dict[str,Any])->bytes:return render_gool_live_signal_card(record,"two_more_goals",confidence,pressure,cards)

def render_gool_live_result_card(row:dict[str,Any],result:str)->bytes:
    head=str(row.get("head") or "two_more_goals");theme,label,_,radar=LIVE_THEMES.get(head,LIVE_THEMES["two_more_goals"]);won=str(result).lower()=="won";accent=theme if won else sc.RED;score=row.get("settled_score") or [0,0];home=str(row.get("home") or "?");away=str(row.get("away") or "?");minute=int(row.get("settled_minute") or 0);strength=float(row.get("gool_signal_strength") or row.get("probability") or 0);pressure=float(row.get("gool_pressure") or 0);img=Image.new("RGBA",(1080,780),sc.BG+(255,));d=ImageDraw.Draw(img);d.rounded_rectangle((18,18,1062,762),30,outline=accent,width=5);d.rounded_rectangle((24,20,1056,112),24,fill=sc.PANEL,outline=accent,width=2);d.text((52,36),"GOOL 2",font=sc._font(36,True),fill=accent);d.text((52,78),f"{radar} • RESULT",font=sc._font(15,True),fill=sc.TEXT);sc._center(d,label,150,sc._fit(d,label,850,38,True),accent);sc._center(d,f"{home} — {away}",220,sc._fit(d,f"{home} — {away}",900,30,True),sc.TEXT);sc._center(d,f"{int(score[0] or 0)} : {int(score[1] or 0)}",300,sc._font(68,True),sc.TEXT);sc._center(d,f"{minute}'",385,sc._font(28,True),accent);d.rounded_rectangle((80,455,1000,650),28,fill=sc.PANEL2,outline=accent,width=3);sc._center(d,"✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ",490,sc._font(40,True),accent);sc._center(d,f"ШАНС {strength*100:.0f}/100  •  PRESSURE {pressure:.2f}",555,sc._font(24,True),sc.TEXT);sc._center(d,label,605,sc._fit(d,label,850,22,True),sc.MUTED);return _save(img)

def render_two_more_result_card(row:dict[str,Any],result:str)->bytes:return render_gool_live_result_card(row,result)
