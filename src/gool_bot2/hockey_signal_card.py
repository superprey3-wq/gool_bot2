from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .xbet_multisport_markets import SCOPE_FULL, SCOPE_LABEL_RU, selection_label

BG=(5,14,20); PANEL=(11,28,36); ICE=(137,235,244); BLUE=(61,161,214)
TEXT=(246,250,252); MUTED=(148,177,187); LINE=(39,78,91); GOLD=(255,190,65)
GREEN=(80,221,126); RED=(231,92,92)


def _label(row:dict[str,Any],signal:dict[str,Any])->str:
    direction=str(signal.get("direction") or row.get("direction") or "over")
    line=float(signal.get("line") or row.get("line") or 0)
    return str(signal.get("selection") or row.get("selection") or selection_label(row,direction,line))


def _team_badges(im,draw,row,home:str,away:str,accent)->None:
    sc._badge(im,draw,125,252,sc._logo(row,"home"),home,accent)
    sc._badge(im,draw,925,252,sc._logo(row,"away"),away,accent)


def _header(draw,title:str,phase:str)->None:
    draw.rounded_rectangle((28,24,1052,100),22,fill=PANEL,outline=ICE,width=2)
    draw.text((52,44),f"🏒 GOOL HOCKEY • {phase}",font=sc._font(25,True),fill=TEXT)
    draw.text((770,48),title,font=sc._fit(draw,title,245,18,True),fill=ICE)


def render_hockey_live_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,920),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"PERIOD BRAIN","LIVE")
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    score=list(row.get("match_score") or row.get("score") or [0,0])
    league=str(row.get("league") or "LIVE")
    period=str(row.get("period") or "LIVE")
    clock=row.get("clock_seconds")

    def center_region(text:str,cx:int,y:int,width:int,size:int,fill)->None:
        font=sc._fit(d,text,width,size,True)
        box=d.textbbox((0,0),text,font=font)
        d.text((cx-(box[2]-box[0])/2,y),text,font=font,fill=fill)

    d.rounded_rectangle((42,116,1038,160),14,fill=PANEL,outline=LINE,width=2)
    d.text((62,126),"🏆",font=sc._font(20,True),fill=GOLD)
    d.text((98,124),league,font=sc._fit(d,league,900,23,True),fill=TEXT)

    d.rounded_rectangle((42,180,1038,390),28,fill=PANEL,outline=BLUE,width=2)
    sc._badge(im,d,120,285,sc._logo(row,"home"),home,ICE)
    sc._badge(im,d,960,285,sc._logo(row,"away"),away,ICE)
    center_region(home,285,216,270,24,TEXT)
    center_region(away,795,216,270,24,TEXT)

    d.rounded_rectangle((430,205,650,303),18,fill=(8,24,31),outline=ICE,width=3)
    center_region(f"{int(score[0])}:{int(score[1])}",540,230,185,43,TEXT)

    period_text=period
    if clock is not None:
        sec=max(0,int(clock))
        period_text=f"{period} · сыграно {sec//60:02d}:{sec%60:02d}"
    d.rounded_rectangle((345,320,735,365),14,fill=(8,24,31),outline=LINE,width=2)
    center_region(period_text,540,330,350,20,ICE)

    scope=str(row.get("scope") or signal.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    pick=_label(row,signal); odd=float(signal.get("odd") or 0)
    d.rounded_rectangle((42,420,1038,565),24,fill=(8,24,31),outline=GOLD,width=3)
    d.text((70,442),f"{scope_label.upper()} • CURRENT ICE MARKET",font=sc._fit(d,f"{scope_label.upper()} • CURRENT ICE MARKET",600,16,True),fill=MUTED)
    d.text((70,488),pick,font=sc._fit(d,pick,590,39,True),fill=GOLD)
    d.text((735,448),"КЭФ",font=sc._font(15,True),fill=MUTED)
    d.text((735,486),f"{odd:.2f}",font=sc._font(44,True),fill=TEXT)

    vals=[
        ("СИЛА",f"{float(signal.get('strength') or 0):.0f}/100"),
        ("ПРОГНОЗ",f"{float(signal.get('projected_total') or signal.get('line') or 0):.2f}"),
        ("STAT EDGE",f"{float(signal.get('stat_edge') or 0):+.2f}"),
        ("БРОСКИ/МИН",f"{float(signal.get('recent_rate_per_min') or 0):.2f}"),
    ]
    for i,(t,v) in enumerate(vals):
        x=42+i*249
        d.rounded_rectangle((x,600,x+230,710),18,fill=PANEL,outline=LINE,width=2)
        d.text((x+16,618),t,font=sc._font(14,True),fill=MUTED)
        d.text((x+16,655),v,font=sc._fit(d,v,195,25,True),fill=ICE if i!=1 else GOLD)

    d.rounded_rectangle((42,742,1038,832),18,fill=PANEL,outline=LINE,width=2)
    d.text((68,760),"LIVE POLICY",font=sc._font(14,True),fill=MUTED)
    d.text((68,792),"Только ТБ/ТМ текущего периода • Brain = Flashscore статистика",font=sc._fit(d,"Только ТБ/ТМ текущего периода • Brain = Flashscore статистика",900,21,True),fill=GREEN)
    d.rounded_rectangle((300,852,780,902),15,fill=ICE)
    sc._center(d,"HOCKEY LIVE • СИГНАЛ",862,sc._font(20,True),BG)
    return sc._save(im)

def render_hockey_prematch_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,900),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"ICE LINE","PREMATCH")
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    league=str(row.get("league") or "PREMATCH")
    d.text((50,122),league,font=sc._fit(d,league,980,19,False),fill=MUTED)
    d.rounded_rectangle((42,158,1038,344),28,fill=PANEL,outline=BLUE,width=2)
    _team_badges(im,d,row,home,away,ICE)
    d.text((205,195),home,font=sc._fit(d,home,300,25,True),fill=TEXT)
    d.text((560,195),away,font=sc._fit(d,away,300,25,True),fill=TEXT)
    ts=float(row.get("start_ts") or 0)
    try: tz=ZoneInfo("Europe/Moscow")
    except Exception: tz=timezone.utc
    kickoff=datetime.fromtimestamp(ts,tz).strftime("%d.%m • %H:%M МСК") if ts else "ВРЕМЯ ?"
    d.rounded_rectangle((720,210,1000,302),16,fill=(8,24,31),outline=ICE,width=2)
    d.text((745,226),"СТАРТ",font=sc._font(13,True),fill=MUTED)
    d.text((745,260),kickoff,font=sc._fit(d,kickoff,225,19,True),fill=ICE)

    scope=str(row.get("scope") or signal.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    pick=_label(row,signal); odd=float(signal.get("odd") or 0)
    d.rounded_rectangle((42,382,1038,528),24,fill=(8,24,31),outline=GOLD,width=3)
    d.text((70,406),f"{scope_label.upper()} • ДО МАТЧА",font=sc._fit(d,f"{scope_label.upper()} • ДО МАТЧА",600,16,True),fill=MUTED)
    d.text((70,452),pick,font=sc._fit(d,pick,590,39,True),fill=GOLD)
    d.text((735,414),"КЭФ",font=sc._font(15,True),fill=MUTED)
    d.text((735,452),f"{odd:.2f}",font=sc._font(44,True),fill=TEXT)
    start=dict(signal.get("start") or {})
    direction=str(signal.get("direction") or "over")
    open_line=float(start.get("line") or row.get("opening_line") or signal.get("line") or 0)
    open_odd=float(start.get(direction) or row.get("opening_odd") or odd)
    line=float(signal.get("line") or 0)
    d.rounded_rectangle((42,565,1038,690),20,fill=PANEL,outline=LINE,width=2)
    family=str(row.get("market_family") or signal.get("market_family") or "match_total")
    family_title={
        "match_total":"ДВИЖЕНИЕ ТОТАЛА МАТЧА",
        "home_total":"ДВИЖЕНИЕ ИТ1",
        "away_total":"ДВИЖЕНИЕ ИТ2",
        "handicap":"ДВИЖЕНИЕ ФОРЫ",
        "moneyline":"ДВИЖЕНИЕ ИСХОДА",
    }.get(family,"ДВИЖЕНИЕ РЫНКА")
    d.text((68,584),family_title,font=sc._font(14,True),fill=MUTED)
    move=f"{open_line:g} → {line:g}   •   {open_odd:.2f} → {odd:.2f}   •   R{float(signal.get('strength') or 0):.0f}"
    d.text((68,628),move,font=sc._fit(d,move,920,28,True),fill=ICE)
    d.rounded_rectangle((42,720,1038,810),18,fill=PANEL,outline=LINE,width=2)
    d.text((68,740),"PREMATCH POLICY",font=sc._font(14,True),fill=MUTED)
    d.text((68,773),"Все PREMATCH рынки: тоталы/ИТ/форы/исходы + периоды",font=sc._fit(d,"Матч + ИТ команд + тоталы всех 1/2/3 периодов",900,22,True),fill=GREEN)
    d.rounded_rectangle((280,830,800,880),15,fill=ICE)
    sc._center(d,"HOCKEY PREMATCH • СИГНАЛ",840,sc._font(20,True),BG)
    return sc._save(im)



def render_hockey_result_card(row:dict[str,Any],cfg:Any)->bytes:
    result=str(row.get("result") or "pending").lower()
    accent=GREEN if result=="won" else GOLD if result=="void" else RED
    im=Image.new("RGBA",(1080,640),BG+(255,)); d=ImageDraw.Draw(im)

    title="✅ ЗАШЁЛ" if result=="won" else "↩️ ВОЗВРАТ" if result=="void" else "❌ НЕ ЗАШЁЛ"
    _header(d,title,str(row.get("phase") or "LIVE").upper())
    league=str(row.get("league") or "HOCKEY")
    d.text((52,130),league,font=sc._fit(d,league,960,19,False),fill=MUTED)

    # Symmetric result board: no overlapping names or detached score frame.
    d.rounded_rectangle((42,172,1038,398),26,fill=PANEL,outline=accent,width=3)
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    sc._badge(im,d,128,286,sc._logo(row,"home"),home,accent)
    sc._badge(im,d,952,286,sc._logo(row,"away"),away,accent)

    def center_region(text:str,cx:int,y:int,width:int,size:int,fill)->None:
        font=sc._fit(d,text,width,size,True)
        box=d.textbbox((0,0),text,font=font)
        d.text((cx-(box[2]-box[0])/2,y),text,font=font,fill=fill)

    center_region(home,290,238,230,24,TEXT)
    center_region(away,790,238,230,24,TEXT)

    score=list(row.get("settled_score") or row.get("score") or [0,0])
    d.rounded_rectangle((405,225,675,350),20,fill=(8,24,31),outline=ICE,width=3)
    center_region("ФИНАЛ",540,241,220,13,MUTED)
    center_region(f"{int(score[0])} : {int(score[1])}",540,276,230,44,TEXT)

    scope=str(row.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    center_region(scope_label.upper(),540,360,430,15,accent)

    d.rounded_rectangle((42,425,1038,590),22,fill=(8,24,31),outline=LINE,width=2)
    pick=str(row.get("selection") or "?")
    odd=float(row.get("odd") or 0)
    d.text((70,450),"СТАВКА",font=sc._font(14,True),fill=MUTED)
    d.text((70,480),f"{pick} @ {odd:.2f}",font=sc._fit(d,f"{pick} @ {odd:.2f}",690,34,True),fill=GOLD)
    d.text((70,535),f"P/L {float(row.get('profit_units') or 0):+.2f}u",font=sc._font(28,True),fill=accent)

    result_text="WIN" if result=="won" else "VOID" if result=="void" else "LOSS"
    d.rounded_rectangle((820,466,995,546),18,fill=PANEL,outline=accent,width=2)
    center_region(result_text,907,489,145,24,accent)
    return sc._save(im)

