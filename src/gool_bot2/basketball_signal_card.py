from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .xbet_multisport_markets import SCOPE_FULL, SCOPE_LABEL_RU, selection_label

BG=(20,10,5); PANEL=(39,23,14); COURT=(242,139,49); ORANGE=(255,166,61)
TEXT=(250,247,243); MUTED=(191,166,145); LINE=(91,61,39); GOLD=(255,205,93)
GREEN=(92,222,130); RED=(232,91,86)


def _label(row:dict[str,Any],signal:dict[str,Any])->str:
    direction=str(signal.get("direction") or row.get("direction") or "over")
    line=float(signal.get("line") or row.get("line") or 0)
    return str(signal.get("selection") or row.get("selection") or selection_label(row,direction,line))


def _header(draw,phase:str)->None:
    draw.rounded_rectangle((28,24,1052,100),22,fill=PANEL,outline=COURT,width=2)
    draw.text((52,44),f"🏀 GOOL BASKETBALL • {phase}",font=sc._font(25,True),fill=TEXT)
    draw.arc((870,32,1020,92),180,360,fill=ORANGE,width=4)


def render_basketball_live_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,920),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"LIVE")
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    score=list(row.get("match_score") or row.get("score") or [0,0])
    league=str(row.get("league") or "LIVE"); period=str(row.get("period") or "LIVE")
    clock=row.get("clock_seconds")
    if clock is not None:
        sec=max(0,int(clock)); period=f"{period} • {sec//60:02d}:{sec%60:02d}"
    d.text((50,122),league,font=sc._fit(d,league,980,19,False),fill=MUTED)
    d.rounded_rectangle((42,158,1038,350),28,fill=PANEL,outline=COURT,width=2)
    d.text((72,190),home,font=sc._fit(d,home,360,28,True),fill=TEXT)
    d.text((72,286),away,font=sc._fit(d,away,360,28,True),fill=TEXT)
    d.rounded_rectangle((470,188,670,320),18,fill=(31,17,10),outline=ORANGE,width=3)
    sc._center(d,f"{int(score[0])} : {int(score[1])}",220,sc._font(40,True),TEXT)
    d.text((720,228),period,font=sc._fit(d,period,270,23,True),fill=ORANGE)

    scope=str(row.get("scope") or signal.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    pick=_label(row,signal); odd=float(signal.get("odd") or 0)
    d.rounded_rectangle((42,390,1038,540),24,fill=(31,17,10),outline=GOLD,width=3)
    d.text((70,414),f"{scope_label.upper()} • COURT PRESSURE",font=sc._fit(d,f"{scope_label.upper()} • COURT PRESSURE",600,16,True),fill=MUTED)
    d.text((70,460),pick,font=sc._fit(d,pick,590,39,True),fill=GOLD)
    d.text((735,420),"КЭФ",font=sc._font(15,True),fill=MUTED)
    d.text((735,458),f"{odd:.2f}",font=sc._font(44,True),fill=TEXT)

    vals=[
        ("СИЛА",f"{float(signal.get('strength') or 0):.0f}/100"),
        ("ПРОГНОЗ",f"{float(signal.get('projected_total') or signal.get('line') or 0):.1f}"),
        ("STAT EDGE",f"{float(signal.get('stat_edge') or 0):+.1f}"),
        ("ТЕМП/МИН",f"{float(signal.get('recent_rate_per_min') or 0):.1f}"),
    ]
    for i,(t,v) in enumerate(vals):
        x=42+i*249
        d.rounded_rectangle((x,575,x+230,685),18,fill=PANEL,outline=LINE,width=2)
        d.text((x+16,593),t,font=sc._font(14,True),fill=MUTED)
        d.text((x+16,630),v,font=sc._fit(d,v,195,25,True),fill=ORANGE if i!=2 else GOLD)
    d.rounded_rectangle((42,720,1038,826),18,fill=PANEL,outline=LINE,width=2)
    d.text((68,742),"LIVE POLICY",font=sc._font(14,True),fill=MUTED)
    d.text((68,777),"Только ТБ/ТМ текущей четверти • Brain = статистика сегмента",font=sc._fit(d,"Матч + ИТ команд + текущая четверть и её половина",900,22,True),fill=GREEN)
    d.rounded_rectangle((300,850,780,900),15,fill=COURT)
    sc._center(d,"BASKET LIVE • СИГНАЛ",860,sc._font(20,True),BG)
    return sc._save(im)


def render_basketball_prematch_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,900),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"PREMATCH")
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    league=str(row.get("league") or "PREMATCH")
    d.text((50,122),league,font=sc._fit(d,league,980,19,False),fill=MUTED)
    d.rounded_rectangle((42,158,1038,344),28,fill=PANEL,outline=COURT,width=2)
    d.text((72,195),home,font=sc._fit(d,home,590,31,True),fill=TEXT)
    d.text((72,282),away,font=sc._fit(d,away,590,31,True),fill=TEXT)
    ts=float(row.get("start_ts") or 0)
    try: tz=ZoneInfo("Europe/Moscow")
    except Exception: tz=timezone.utc
    kickoff=datetime.fromtimestamp(ts,tz).strftime("%d.%m • %H:%M МСК") if ts else "ВРЕМЯ ?"
    d.rounded_rectangle((720,210,1000,302),16,fill=(31,17,10),outline=ORANGE,width=2)
    d.text((745,226),"TIP-OFF",font=sc._font(13,True),fill=MUTED)
    d.text((745,260),kickoff,font=sc._fit(d,kickoff,225,19,True),fill=ORANGE)

    scope=str(row.get("scope") or signal.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    pick=_label(row,signal); odd=float(signal.get("odd") or 0)
    d.rounded_rectangle((42,382,1038,528),24,fill=(31,17,10),outline=GOLD,width=3)
    d.text((70,406),f"{scope_label.upper()} • ДО МАТЧА",font=sc._fit(d,f"{scope_label.upper()} • ДО МАТЧА",600,16,True),fill=MUTED)
    d.text((70,452),pick,font=sc._fit(d,pick,590,39,True),fill=GOLD)
    d.text((735,414),"КЭФ",font=sc._font(15,True),fill=MUTED)
    d.text((735,452),f"{odd:.2f}",font=sc._font(44,True),fill=TEXT)
    start=dict(signal.get("start") or {}); direction=str(signal.get("direction") or "over")
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
    d.text((68,628),move,font=sc._fit(d,move,920,28,True),fill=ORANGE)
    d.rounded_rectangle((42,720,1038,810),18,fill=PANEL,outline=LINE,width=2)
    d.text((68,740),"PREMATCH POLICY",font=sc._font(14,True),fill=MUTED)
    d.text((68,773),"Все PREMATCH рынки: тоталы/ИТ/форы/исходы + половины/четверти",font=sc._fit(d,"Матч + ИТ + обе половины + тоталы всех 4 четвертей",900,22,True),fill=GREEN)
    d.rounded_rectangle((280,830,800,880),15,fill=COURT)
    sc._center(d,"BASKET PREMATCH • СИГНАЛ",840,sc._font(20,True),BG)
    return sc._save(im)


def render_basketball_result_card(row:dict[str,Any],cfg:Any)->bytes:
    result=str(row.get("result") or "pending").lower()
    accent=GREEN if result=="won" else GOLD if result=="void" else RED
    status="ЗАШЁЛ" if result=="won" else "ВОЗВРАТ" if result=="void" else "НЕ ЗАШЁЛ"
    im=Image.new("RGBA",(1080,760),BG+(255,)); d=ImageDraw.Draw(im)

    # Header matches the PREMATCH/LIVE visual language, but RESULT is explicit.
    d.rounded_rectangle((28,24,1052,104),22,fill=PANEL,outline=COURT,width=2)
    d.text((52,45),"🏀 GOOL BASKETBALL • RESULT",font=sc._font(25,True),fill=TEXT)
    d.rounded_rectangle((805,42,1024,88),14,fill=(31,17,10),outline=accent,width=2)
    sc._center(d,status,52,sc._fit(d,status,185,18,True),accent)

    league=str(row.get("league") or "BASKETBALL")
    d.text((50,126),league,font=sc._fit(d,league,980,19,False),fill=MUTED)

    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    score=list(row.get("settled_match_score") or row.get("settled_score") or row.get("score") or [0,0])
    d.rounded_rectangle((42,164,1038,392),28,fill=PANEL,outline=accent,width=3)
    d.text((72,205),home,font=sc._fit(d,home,470,30,True),fill=TEXT)
    d.text((72,315),away,font=sc._fit(d,away,470,30,True),fill=TEXT)
    d.rounded_rectangle((610,218,980,338),20,fill=(31,17,10),outline=ORANGE,width=2)
    d.text((640,236),"ФИНАЛЬНЫЙ СЧЁТ",font=sc._font(14,True),fill=MUTED)
    sc._center(d,f"{int(score[0])} : {int(score[1])}",272,sc._font(46,True),accent)

    pick=str(row.get("selection") or "?")
    odd=float(row.get("odd") or 0)
    d.rounded_rectangle((42,428,1038,590),24,fill=(31,17,10),outline=GOLD,width=3)
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

    d.rounded_rectangle((335,720,745,748),10,fill=COURT)
    return sc._save(im)
