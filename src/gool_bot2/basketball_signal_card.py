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


def _team_badges(im,draw,row,home:str,away:str,accent)->None:
    sc._badge(im,draw,120,262,sc._logo(row,"home"),home,accent)
    sc._badge(im,draw,960,262,sc._logo(row,"away"),away,accent)


def _header(draw,phase:str)->None:
    draw.rounded_rectangle((28,24,1052,100),22,fill=PANEL,outline=COURT,width=2)
    draw.text((52,44),f"GOOL BASKETBALL • {phase}",font=sc._font(25,True),fill=TEXT)
    draw.arc((870,32,1020,92),180,360,fill=ORANGE,width=4)


def render_basketball_live_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,920),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"LIVE")
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

    d.rounded_rectangle((42,180,1038,390),28,fill=PANEL,outline=COURT,width=2)
    sc._badge(im,d,120,285,sc._logo(row,"home"),home,ORANGE)
    sc._badge(im,d,960,285,sc._logo(row,"away"),away,ORANGE)
    center_region(home,285,216,270,24,TEXT)
    center_region(away,795,216,270,24,TEXT)

    d.rounded_rectangle((420,205,660,303),18,fill=(31,17,10),outline=ORANGE,width=3)
    center_region(f"{int(score[0])} : {int(score[1])}",540,230,205,40,TEXT)

    period_text=period
    if clock is not None:
        sec=max(0,int(clock))
        period_text=f"{period} · сыграно {sec//60:02d}:{sec%60:02d}"
    d.rounded_rectangle((345,320,735,365),14,fill=(31,17,10),outline=LINE,width=2)
    center_region(period_text,540,330,350,20,ORANGE)

    scope=str(row.get("scope") or signal.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    market_caption="CURRENT COURT MARKET" if scope != SCOPE_FULL else "MATCH PROJECTION"
    pick=_label(row,signal); odd=float(signal.get("odd") or 0)
    d.rounded_rectangle((42,420,1038,565),24,fill=(31,17,10),outline=GOLD,width=3)
    d.text((70,442),f"{scope_label.upper()} • {market_caption}",font=sc._fit(d,f"{scope_label.upper()} • {market_caption}",600,16,True),fill=MUTED)
    d.text((70,488),pick,font=sc._fit(d,pick,590,39,True),fill=GOLD)
    d.text((735,448),"КЭФ",font=sc._font(15,True),fill=MUTED)
    d.text((735,486),f"{odd:.2f}",font=sc._font(44,True),fill=TEXT)

    vals=[
        ("СИЛА",f"{float(signal.get('strength') or 0):.0f}/100"),
        ("ПРОГНОЗ",f"{float(signal.get('projected_total') or signal.get('line') or 0):.1f}"),
        ("STAT EDGE",f"{float(signal.get('stat_edge') or 0):+.1f}"),
        ("ОЧКИ/МИН",f"{float(signal.get('recent_rate_per_min') or 0):.1f}"),
    ]
    for i,(t,v) in enumerate(vals):
        x=42+i*249
        d.rounded_rectangle((x,600,x+230,710),18,fill=PANEL,outline=LINE,width=2)
        d.text((x+16,618),t,font=sc._font(14,True),fill=MUTED)
        d.text((x+16,655),v,font=sc._fit(d,v,195,25,True),fill=ORANGE if i!=2 else GOLD)

    d.rounded_rectangle((42,742,1038,832),18,fill=PANEL,outline=LINE,width=2)
    d.text((68,760),"LIVE POLICY",font=sc._font(14,True),fill=MUTED)
    d.text((68,792),"Q/P тотал + LIVE тотал/ИТ матча • Segment Memory + Flashscore",font=sc._fit(d,"Q/P тотал + LIVE тотал/ИТ матча • Segment Memory + Flashscore",900,21,True),fill=GREEN)
    d.rounded_rectangle((300,852,780,902),15,fill=COURT)
    sc._center(d,"BASKET LIVE • СИГНАЛ",862,sc._font(20,True),BG)
    return sc._save(im)

def render_basketball_prematch_card(row:dict[str,Any],signal:dict[str,Any],cfg:Any)->bytes:
    im=Image.new("RGBA",(1080,900),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,"PREMATCH")
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    league=str(row.get("league") or "PREMATCH")
    d.text((50,122),league,font=sc._fit(d,league,980,19,False),fill=MUTED)
    d.rounded_rectangle((42,158,1038,354),28,fill=PANEL,outline=COURT,width=2)
    _team_badges(im,d,row,home,away,ORANGE)

    def center_region(text:str,cx:int,y:int,width:int,size:int,fill)->None:
        font=sc._fit(d,text,width,size,True)
        box=d.textbbox((0,0),text,font=font)
        d.text((cx-(box[2]-box[0])/2,y),text,font=font,fill=fill)

    # Keep team labels in their own lanes. The old right-side TIP-OFF panel
    # occupied the same space as the away badge/name and caused the overlap
    # visible in Telegram.
    center_region(home,315,190,220,25,TEXT)
    center_region(away,765,190,220,25,TEXT)
    ts=float(row.get("start_ts") or 0)
    try: tz=ZoneInfo("Europe/Moscow")
    except Exception: tz=timezone.utc
    kickoff=datetime.fromtimestamp(ts,tz).strftime("%d.%m • %H:%M МСК") if ts else "ВРЕМЯ ?"
    d.rounded_rectangle((425,242,655,320),16,fill=(31,17,10),outline=ORANGE,width=2)
    center_region("TIP-OFF",540,252,190,12,MUTED)
    center_region(kickoff,540,282,195,17,ORANGE)

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
    policy_text="Все PREMATCH рынки: тоталы/ИТ/форы/исходы + половины/четверти"
    d.text((68,773),policy_text,font=sc._fit(d,policy_text,900,22,True),fill=GREEN)
    d.rounded_rectangle((280,830,800,880),15,fill=COURT)
    sc._center(d,"BASKET PREMATCH • СИГНАЛ",840,sc._font(20,True),BG)
    return sc._save(im)



def render_basketball_result_card(row:dict[str,Any],cfg:Any)->bytes:
    result=str(row.get("result") or "pending").lower()
    accent=GREEN if result=="won" else GOLD if result=="void" else RED
    im=Image.new("RGBA",(1080,640),BG+(255,)); d=ImageDraw.Draw(im)
    _header(d,str(row.get("phase") or "LIVE").upper())

    title="✅ ЗАШЁЛ" if result=="won" else "↩️ ВОЗВРАТ" if result=="void" else "❌ НЕ ЗАШЁЛ"
    d.text((52,122),title,font=sc._font(28,True),fill=accent)
    league=str(row.get("league") or "BASKETBALL")
    d.text((52,160),league,font=sc._fit(d,league,960,18,False),fill=MUTED)

    # One coherent match block: badge + team | score | team + badge.
    d.rounded_rectangle((42,196,1038,405),26,fill=PANEL,outline=accent,width=3)
    home,away=str(row.get("home") or "?"),str(row.get("away") or "?")
    sc._badge(im,d,128,300,sc._logo(row,"home"),home,accent)
    sc._badge(im,d,952,300,sc._logo(row,"away"),away,accent)

    def center_region(text:str,cx:int,y:int,width:int,size:int,fill)->None:
        font=sc._fit(d,text,width,size,True)
        box=d.textbbox((0,0),text,font=font)
        d.text((cx-(box[2]-box[0])/2,y),text,font=font,fill=fill)

    center_region(home,290,252,230,23,TEXT)
    center_region(away,790,252,230,23,TEXT)

    score=list(row.get("settled_score") or row.get("score") or [0,0])
    d.rounded_rectangle((405,238,675,360),20,fill=(31,17,10),outline=ORANGE,width=3)
    center_region("ФИНАЛ",540,253,220,13,MUTED)
    center_region(f"{int(score[0])} : {int(score[1])}",540,287,230,43,TEXT)

    scope=str(row.get("scope") or SCOPE_FULL)
    scope_label=SCOPE_LABEL_RU.get(scope,scope)
    center_region(scope_label.upper(),540,369,430,15,accent)

    # Bet/result block kept separate from the scoreboard.
    d.rounded_rectangle((42,430,1038,590),22,fill=(31,17,10),outline=LINE,width=2)
    pick=str(row.get("selection") or "?")
    odd=float(row.get("odd") or 0)
    d.text((70,454),"СТАВКА",font=sc._font(14,True),fill=MUTED)
    d.text((70,482),f"{pick} @ {odd:.2f}",font=sc._fit(d,f"{pick} @ {odd:.2f}",690,34,True),fill=GOLD)
    d.text((70,535),f"P/L {float(row.get('profit_units') or 0):+.2f}u",font=sc._font(28,True),fill=accent)

    result_text="WIN" if result=="won" else "VOID" if result=="void" else "LOSS"
    d.rounded_rectangle((820,468,995,548),18,fill=PANEL,outline=accent,width=2)
    center_region(result_text,907,490,145,24,accent)
    return sc._save(im)

