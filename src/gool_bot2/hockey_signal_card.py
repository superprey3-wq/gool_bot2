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
    if clock is not None:
        sec=max(0,int(clock)); period=f"{period} • {sec//60:02d}:{sec%60:02d}"
    d.text((50,122),league,font=sc._fit(d,league,980,19,False),fill=MUTED)
    d.rounded_rectangle((42,158,1038,350),28,fill=PANEL,outline=BLUE,width=2)
    d.text((72,194),home,font=sc._fit(d,home,380,29,True),fill=TEXT)
    d.text((72,288),away,font=sc._fit(d,away,380,29,True),fill=TEXT)
    d.ellipse((480,190,610,320),outline=ICE,width=4)
    sc._center(d,f"{int(score[0])}:{int(score[1])}",216,sc._font(42,True),TEXT)
    d.text((690,215),period,font=sc._fit(d,period,300,23,True),fill=ICE)

    scope=str(row.get("scope") or signal.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    pick=_label(row,signal); odd=float(signal.get("odd") or 0)
    d.rounded_rectangle((42,390,1038,540),24,fill=(8,24,31),outline=GOLD,width=3)
    d.text((70,414),f"{scope_label.upper()} • CURRENT ICE MARKET",font=sc._fit(d,f"{scope_label.upper()} • CURRENT ICE MARKET",600,16,True),fill=MUTED)
    d.text((70,460),pick,font=sc._fit(d,pick,590,39,True),fill=GOLD)
    d.text((735,420),"КЭФ",font=sc._font(15,True),fill=MUTED)
    d.text((735,458),f"{odd:.2f}",font=sc._font(44,True),fill=TEXT)

    vals=[
        ("СИЛА",f"{float(signal.get('strength') or 0):.0f}/100"),
        ("ПРОГНОЗ",f"{float(signal.get('projected_total') or signal.get('line') or 0):.2f}"),
        ("STAT EDGE",f"{float(signal.get('stat_edge') or 0):+.2f}"),
        ("ТЕМП/МИН",f"{float(signal.get('recent_rate_per_min') or 0):.2f}"),
    ]
    for i,(t,v) in enumerate(vals):
        x=42+i*249
        d.rounded_rectangle((x,575,x+230,685),18,fill=PANEL,outline=LINE,width=2)
        d.text((x+16,593),t,font=sc._font(14,True),fill=MUTED)
        d.text((x+16,630),v,font=sc._fit(d,v,195,25,True),fill=ICE if i!=1 else GOLD)
    d.rounded_rectangle((42,720,1038,826),18,fill=PANEL,outline=LINE,width=2)
    d.text((68,742),"LIVE POLICY",font=sc._font(14,True),fill=MUTED)
    d.text((68,777),"Только ТБ/ТМ текущего периода • Brain = статистика сегмента",font=sc._fit(d,"Общий матч + ИТ команд + только текущий период",900,22,True),fill=GREEN)
    d.rounded_rectangle((300,850,780,900),15,fill=ICE)
    sc._center(d,"HOCKEY LIVE • СИГНАЛ",860,sc._font(20,True),BG)
    return sc._save(im)


def render_hockey_prematch_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,900),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"ICE LINE","PREMATCH")
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    league=str(row.get("league") or "PREMATCH")
    d.text((50,122),league,font=sc._fit(d,league,980,19,False),fill=MUTED)
    d.rounded_rectangle((42,158,1038,344),28,fill=PANEL,outline=BLUE,width=2)
    d.text((72,195),home,font=sc._fit(d,home,590,31,True),fill=TEXT)
    d.text((72,282),away,font=sc._fit(d,away,590,31,True),fill=TEXT)
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
    status="ЗАШЁЛ" if result=="won" else "ВОЗВРАТ" if result=="void" else "НЕ ЗАШЁЛ"
    im=Image.new("RGBA",(1080,760),BG+(255,)); d=ImageDraw.Draw(im)

    d.rounded_rectangle((28,24,1052,104),22,fill=PANEL,outline=ICE,width=2)
    d.text((52,45),"🏒 GOOL HOCKEY • RESULT",font=sc._font(25,True),fill=TEXT)
    d.rounded_rectangle((805,42,1024,88),14,fill=(8,24,31),outline=accent,width=2)
    sc._center(d,status,52,sc._fit(d,status,185,18,True),accent)

    league=str(row.get("league") or "HOCKEY")
    d.text((50,126),league,font=sc._fit(d,league,980,19,False),fill=MUTED)

    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    score=list(row.get("settled_match_score") or row.get("settled_score") or row.get("score") or [0,0])
    d.rounded_rectangle((42,164,1038,392),28,fill=PANEL,outline=accent,width=3)
    d.text((72,205),home,font=sc._fit(d,home,470,30,True),fill=TEXT)
    d.text((72,315),away,font=sc._fit(d,away,470,30,True),fill=TEXT)
    d.rounded_rectangle((610,218,980,338),20,fill=(8,24,31),outline=ICE,width=2)
    d.text((640,236),"ФИНАЛЬНЫЙ СЧЁТ",font=sc._font(14,True),fill=MUTED)
    sc._center(d,f"{int(score[0])} : {int(score[1])}",272,sc._font(46,True),accent)

    pick=str(row.get("selection") or "?")
    odd=float(row.get("odd") or 0)
    d.rounded_rectangle((42,428,1038,590),24,fill=(8,24,31),outline=GOLD,width=3)
    d.text((70,451),"СТАВКА",font=sc._font(14,True),fill=MUTED)
    d.text((70,492),pick,font=sc._fit(d,pick,620,38,True),fill=GOLD)
    d.text((790,451),"КЭФ",font=sc._font(14,True),fill=MUTED)
    d.text((790,492),f"{odd:.2f}",font=sc._font(42,True),fill=TEXT)

    profit=float(row.get("profit_units") or 0)
    d.rounded_rectangle((42,626,1038,704),18,fill=PANEL,outline=LINE,width=2)
    d.text((70,649),"РЕЗУЛЬТАТ",font=sc._font(14,True),fill=MUTED)
    d.text((235,642),status,font=sc._font(25,True),fill=accent)
    d.text((720,649),"P/L",font=sc._font(14,True),fill=MUTED)
    d.text((785,642),f"{profit:+.2f}u",font=sc._font(27,True),fill=accent)

    d.rounded_rectangle((335,720,745,748),10,fill=ICE)
    return sc._save(im)
