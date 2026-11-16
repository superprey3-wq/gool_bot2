"""PROGRUZ 1 Telegram Bot API 10.4 score widgets.

Only Flashscore scoreboard deltas drive the red goal dot; NO inferred
'big chance', xG spike, or bookmaker price movement is called a goal.
Existing two scenario selection and HT/FT settlement remain unchanged.
The widget is optional and private-chat-only; sendMessage is the fallback.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import zlib
from urllib import error, parse, request

from progruz1_bet_display import exact_total, exact_total_with_period
from progruz1_live_bet_status import live_card_status, observe_live_total


WIDGET_HTML = r"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
*{box-sizing:border-box}
body{margin:0;font:13px system-ui,-apple-system,sans-serif;color:#f2f6fc}
.board{position:relative;overflow:hidden;width:100%;padding:14px 14px 11px;background:linear-gradient(160deg,#15273a 0%,#101c2a 54%,#0d1725 100%);border:1px solid #35465a;border-radius:18px}
.board:before{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:linear-gradient(90deg,#16a085,#44ddaf,#17a184)}
.top,.brand,.live,.bet,.foot{display:flex;justify-content:space-between;align-items:center;gap:9px}
.brand{justify-content:flex-start;gap:7px;color:#e4edf8;font-size:11px;font-weight:800;letter-spacing:.9px}
.brand-mark{width:22px;height:22px;display:flex;align-items:center;justify-content:center;border-radius:7px;background:#23c89820;border:1px solid #2ed0a153;color:#64edc4;font-size:10px;font-weight:900}
.live{justify-content:flex-end;gap:6px;padding:4px 8px;border-radius:16px;background:#17453470;color:#65e4ab;font-size:10px;font-weight:800;letter-spacing:.6px}
.live:before{content:"";display:block;width:6px;height:6px;border-radius:50%;background:#4cd9a0}
.competition{margin:11px 0 10px;color:#b5c8dc;font-size:11px;font-weight:600;overflow-wrap:anywhere;line-height:1.35}
.score-panel{background:linear-gradient(180deg,#ffffff09,#ffffff03);border:1px solid #ffffff14;border-radius:13px;padding:12px 9px}
.teams{display:grid;grid-template-columns:minmax(0,1fr) 90px minmax(0,1fr);gap:6px;align-items:center}
.team{font-weight:730;font-size:13px;line-height:1.3;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:5px;text-align:center;min-width:0;overflow-wrap:anywhere}
.team span{min-width:0}
.score-wrap{text-align:center;min-width:0}
.score{font-size:33px;font-weight:900;letter-spacing:-1px;white-space:nowrap;font-variant-numeric:tabular-nums;color:#fff}
.minute{font-size:10px;font-weight:800;color:#82e5bd;min-height:15px;margin-bottom:3px}
.dot{display:none;flex:none;width:10px;height:10px;border-radius:50%;background:#f43f5e}
.dot.on{display:inline-block;animation:pulse 1.5s ease-in-out infinite;box-shadow:0 0 8px #f43f5e}
.dot.goal{animation:pulse 1.5s ease-in-out infinite;box-shadow:0 0 0 5px #f43f5e44,0 0 15px #f43f5e}
.redcard{display:none;flex:none;width:10px;height:14px;border-radius:2px;background:#e32b35;box-shadow:0 0 2px #b40000}
.redcard.on{display:inline-block}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.12;transform:scale(.68)}}
.bet{margin-top:10px;border-radius:12px;background:linear-gradient(100deg,#153f36,#12372f);border:1px solid #2d806536;padding:10px 12px}
.bet-name{display:block;font-size:13px;letter-spacing:.3px;font-weight:900;color:#f4fcf8}
.bet-note{display:block;font-size:10px;color:#9bbcaf;margin-top:3px}
.odd{font-size:21px;line-height:1;font-weight:900;white-space:nowrap;color:#66edb4}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:10px}
.stat{padding:8px 3px 7px;border-radius:10px;text-align:center;background:#ffffff09;border:1px solid #ffffff0d}
.stat b{display:block;font-size:15px;font-weight:800;color:#eaf3ff;white-space:nowrap;font-variant-numeric:tabular-nums}
.stat small{display:block;margin-bottom:4px;font-size:10px;color:#91a8c1}
.foot{margin-top:10px;color:#8da4b9;font-size:10px;flex-wrap:wrap}
.foot b{font-weight:800;color:#d3e5f4}
.foot .done{color:#62e2ab}
.foot .bad{color:#ff858d}
</style></head><body><div class="board">
<div class="top">
 <div class="brand"><span class="brand-mark">P1</span><span>ПРОГРУЗ 1</span></div>
 <div class="live">LIVE</div>
</div>
<div class="competition">🏆 <span id="league">Турнир</span></div>
<div class="score-panel"><div class="teams">
 <div class="team team-home"><span id="home">Хозяева</span><i id="hr" class="redcard"></i><i id="hd" class="dot"></i></div>
 <div class="score-wrap"><div class="minute" id="minute">—</div><div class="score" id="score">— : —</div></div>
 <div class="team team-away"><i id="ad" class="dot"></i><i id="ar" class="redcard"></i><span id="away">Гости</span></div>
</div></div>
<div class="bet">
 <div><span class="bet-name" id="betline">ТБ —</span><span class="bet-note" id="marketperiod">Коэффициент на момент сигнала</span></div>
 <span class="odd" id="odd">—</span>
</div>
<div class="stats">
 <div class="stat"><small>Удары</small><b id="shots">—</b></div>
 <div class="stat"><small>В створ</small><b id="sot">—</b></div>
 <div class="stat"><small>xG</small><b id="xg">—</b></div>
</div>
<div class="foot"><span id="updated">Статистика LIVE</span><b id="status">⏳ Ожидаем</b></div>
</div>
<script>
const W=window.TelegramWidget;
const el=id=>document.getElementById(id);
const GOAL_SCORE_DELAY_MS=2000;
let sourceScore=null;
let displayedScore=null;
let queuedScore=null;
let revealAt=0;
function sameScore(a,b){
 return Array.isArray(a)&&Array.isArray(b)&&a.length===2&&b.length===2&&a[0]===b[0]&&a[1]===b[1];
}
function scoreToDisplay(s){
 const incoming=Array.isArray(s.score)&&s.score.length===2?s.score:null;
 const now=Date.now();
 if(!incoming)return null;
 if(sourceScore===null){
  sourceScore=incoming.slice();
  displayedScore=incoming.slice();
 }else if(!sameScore(incoming,sourceScore)){
  const homeGoal=incoming[0]===sourceScore[0]+1&&incoming[1]===sourceScore[1];
  const awayGoal=incoming[1]===sourceScore[1]+1&&incoming[0]===sourceScore[0];
  const animated=s.status==='PENDING'&&Date.now()/1000<Number(s.flash_until||0)&&
    ((homeGoal&&s.flash_side==='home')||(awayGoal&&s.flash_side==='away'));
  sourceScore=incoming.slice();
  if(animated){
   // Let the scorer's red dot blink BEFORE changing the visible score.
   queuedScore=incoming.slice();
   revealAt=now+GOAL_SCORE_DELAY_MS;
  }else{
   // VAR correction / multi-goal gap / unrelated scoreboard edit:
   // display truth immediately instead of inventing a new goal animation.
   displayedScore=incoming.slice();
   queuedScore=null;
  }
 }
 // Settled signals must never keep an obsolete score on screen.
 if(s.status!=='PENDING'&&queuedScore){
  displayedScore=sourceScore.slice();
  queuedScore=null;
 }
 if(queuedScore&&now>=revealAt){
  displayedScore=queuedScore.slice();
  queuedScore=null;
 }
 return displayedScore;
}
function render(){
 let s={};try{s=JSON.parse(W.chatData||'{}')}catch(e){}
 const a=(v)=>Array.isArray(v)&&v.length===2?v.join(' : '):'—';
 el('home').textContent=s.home||'Хозяева';
 el('away').textContent=s.away||'Гости';
 el('league').textContent=(s.flag?s.flag+' ':'')+(s.league||'Турнир не указан');
 el('score').textContent=a(scoreToDisplay(s));
 el('minute').textContent=(s.minute_text||((s.minute==null?'—':String(s.minute))+"'"))+' · '+(s.period||'LIVE');
 const validLine=s.line!=null&&Number.isFinite(Number(s.line))&&Number(s.line)>=0.5;
 el('betline').textContent=validLine?'ТБ '+String(Number(s.line)):'ТБ —';
 el('marketperiod').textContent=(s.market_period||'Матч')+
  ' · '+(s.quote_label||'Источник не указан')+' · кф на момент сигнала';
 el('odd').textContent=s.odd==null?'—':'@'+Number(s.odd).toFixed(2);
 el('shots').textContent=a(s.shots);
 el('sot').textContent=a(s.sot);
 el('xg').textContent=a(s.xg);
 let moment=Date.now()/1000;
 let side=(s.status==='PENDING'&&moment<Number(s.flash_until||0))?s.flash_side:'';
 let burst=moment<Number(s.goal_at||0)+8;
 el('hd').className='dot'+(side==='home'?' on'+(burst?' goal':''):'');
 el('ad').className='dot'+(side==='away'?' on'+(burst?' goal':''):'');
 el('hr').className='redcard'+(Array.isArray(s.red)&&Number(s.red[0])>0?' on':'');
 el('ar').className='redcard'+(Array.isArray(s.red)&&Number(s.red[1])>0?' on':'');
 el('updated').textContent=s.stats_at?'Статистика LIVE · '+new Date(s.stats_at*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):'Статистика LIVE';
 const labels={PENDING:'⏳ Ожидаем',WON:'✅ ЗАШЁЛ',LOST:'❌ НЕ ЗАШЁЛ',VOID:'↩️ Возврат'};
 const shown=s.status==='PENDING'&&s.live_goal_confirmed===true?'WON':s.status;
 el('status').textContent=labels[shown]||'⏳ Ожидаем';
 el('status').className=shown==='WON'?'done':shown==='LOST'?'bad':'';
}
W.onEvent('chatDataChanged',render);render();setInterval(render,250);W.ready?.();
</script></body></html>"""


WIDGET_HTML_LITE = r"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{background:#122337;color:white;font:14px system-ui;margin:0;padding:13px}
.top{color:#a8d6c9;font-size:11px}.teams{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:16px 0}
.team{flex:1;min-width:0;overflow-wrap:anywhere;text-align:center}
.score{font-size:24px;font-weight:800;white-space:nowrap}
.dot{display:none;background:#f43f5e;width:10px;height:10px;border-radius:100%;margin:4px auto}
.dot.on{display:block;animation:blink 1.5s infinite}
.card{color:#ef4444;font-weight:bold}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.15}}
.bet{border-top:1px solid #4b5b6c;padding-top:9px;margin-top:9px;color:#79e8b5;font-weight:800}
.stats{font-size:12px;color:#cadbe8;margin-top:9px}
</style></head><body>
<div class="top">ПРОГРУЗ 1 · LIVE · <span id="league"></span></div>
<div class="teams"><div class="team"><span id="home"></span><span id="hr" class="card"></span><i id="hd" class="dot"></i></div>
<div><div id="minute" class="top"></div><div id="score" class="score"></div></div>
<div class="team"><span id="away"></span><span id="ar" class="card"></span><i id="ad" class="dot"></i></div></div>
<div class="bet"><span id="betline">ТБ —</span> · <span id="odd"></span> · <span id="marketperiod"></span> · <span id="status"></span></div>
<div class="stats">Удары <b id="shots"></b> · В створ <b id="sot"></b> · xG <b id="xg"></b></div>
<script>
const W=window.TelegramWidget,el=id=>document.getElementById(id);
let old=null,current=null,next=null,showAt=0;
const pair=v=>Array.isArray(v)?v.join(':'):'—',eq=(a,b)=>a&&b&&a[0]===b[0]&&a[1]===b[1];
function draw(){
let s={};try{s=JSON.parse(W.chatData||'{}')}catch(e){}
let n=Array.isArray(s.score)?s.score:null,t=Date.now();
if(n&&old===null){old=n.slice();current=n.slice()}
else if(n&&!eq(old,n)){
const goal=s.flash_side&&t<Number(s.flash_until||0)*1000;
old=n.slice();
if(goal&&s.status==='PENDING'){next=n.slice();showAt=t+2000}
else{current=n.slice();next=null}}
if(next&&(t>=showAt||s.status!=='PENDING')){current=next;next=null}
el('home').textContent=s.home||'Хозяева';el('away').textContent=s.away||'Гости';
el('league').textContent=(s.flag||'')+' '+(s.league||'');
el('score').textContent=pair(current);el('minute').textContent=s.minute_text||(s.minute+"'");
el('betline').textContent=s.line!=null&&Number.isFinite(Number(s.line))&&Number(s.line)>=0.5?'ТБ '+String(Number(s.line)):'ТБ —';
el('marketperiod').textContent=(s.market_period||'')+' · '+(s.quote_label||'Источник не указан');
el('odd').textContent='@'+s.odd;
el('shots').textContent=pair(s.shots);el('sot').textContent=pair(s.sot);el('xg').textContent=pair(s.xg);
el('hr').textContent=Array.isArray(s.red)&&s.red[0]>0?'🟥':'';el('ar').textContent=Array.isArray(s.red)&&s.red[1]>0?'🟥':'';
let active=s.status==='PENDING'&&t<Number(s.flash_until||0)*1000;
el('hd').className='dot'+(active&&s.flash_side==='home'?' on':'');el('ad').className='dot'+(active&&s.flash_side==='away'?' on':'');
let shown=s.status==='PENDING'&&s.live_goal_confirmed===true?'WON':s.status;
el('status').textContent=({WON:'✅ ЗАШЁЛ',LOST:'❌ НЕ ЗАШЁЛ',VOID:'↩️',PENDING:'⏳ ОЖИДАЕМ'})[shown]||'⏳'}
W.onEvent('chatDataChanged',draw);draw();setInterval(draw,250);W.ready?.();
</script></body></html>"""


def render_text_scoreboard(state: dict, *, now: int | None = None) -> str:
    """Compact editable Telegram fallback when sendWidget is forbidden.

    Standard sendMessage/editMessageText does not execute HTML or blink.
    Only Flashscore-derived scores/stats and confirmed red-card counts
    appear here; a red dot marks a recently detected goal.
    """
    timestamp=int(now if now is not None else time.time())
    def paired(value, separator=" : "):
        if not isinstance(value,(list,tuple)) or len(value)!=2:
            return "—"
        return separator.join(str(v) for v in value)
    who=str(state.get("home") or "Хозяева")
    away=str(state.get("away") or "Гости")
    red=state.get("red")
    if isinstance(red,(list,tuple)) and len(red)==2:
        if red[0]:
            who+=" 🟥"+(str(red[0]) if red[0]>1 else "")
        if red[1]:
            away+=" 🟥"+(str(red[1]) if red[1]>1 else "")
    if (state.get("status")=="PENDING"
            and timestamp<int(state.get("flash_until") or 0)):
        if state.get("flash_side")=="home":
            who+=" 🔴"
        elif state.get("flash_side")=="away":
            away+=" 🔴"
    score=paired(state.get("score"))
    minute=str(state.get("minute_text") or (
        str(state.get("minute"))+"'" if state.get("minute") is not None else "LIVE"))
    odd=state.get("odd")
    try:
        odd_str=f"@{float(odd):.2f}"
    except (ValueError,TypeError,OverflowError):
        odd_str="@—"
    label={"PENDING":"⏳ ОЖИДАЕТ","WON":"✅ ЗАШЁЛ",
           "LOST":"❌ НЕ ЗАШЁЛ","VOID":"↩️ ВОЗВРАТ"}.get(
               live_card_status(state),"⏳ ОЖИДАЕТ")
    lines=[
        "⚽ ПРОГРУЗ 1 · LIVE",
        "🏆 "+(" ".join(x for x in (state.get("flag"),
                                    state.get("league")) if x)
                or "Турнир не указан"),
        "",
        f"🏠 {who}",
        f"         {score} · {minute}",
        f"✈️ {away}",
        "",
        f"🎯 {exact_total_with_period(state)} {odd_str}",
        f"💰 Линия: {state.get('quote_label') or 'Фонбет LIVE'}",
        f"📊 Удары {paired(state.get('shots'))}  |  "
        f"В створ {paired(state.get('sot'))}",
        f"📈 xG {paired(state.get('xg'))}",
        "",
        label,
    ]
    return "\n".join(lines)[:4096]


def _json(data: dict) -> str:
    encoded=json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8"))>1024:
        raise ValueError("Telegram widget state exceeds 1024 bytes")
    return encoded


def _pair(value, *, decimals=False):
    if not isinstance(value,(tuple,list)) or len(value)!=2:
        return None
    try:
        values=[float(v) for v in value]
        if not all(0<=v<=70 for v in values):
            return None
        return [round(v,2) for v in values] if decimals else [
            int(v) for v in values if int(v)==v]
    except (TypeError,ValueError,OverflowError):
        return None




COUNTRY_CODES = {
    "ENGLAND":"GB","SCOTLAND":"GB","WALES":"GB","N. IRELAND":"GB",
    "GERMANY":"DE","FRANCE":"FR","SPAIN":"ES","ITALY":"IT",
    "PORTUGAL":"PT","POLAND":"PL","FINLAND":"FI","SWEDEN":"SE",
    "NORWAY":"NO","DENMARK":"DK","ICELAND":"IS","NETHERLANDS":"NL",
    "BELGIUM":"BE","AUSTRIA":"AT","SWITZERLAND":"CH",
    "CZECH REPUBLIC":"CZ","CZECHIA":"CZ","SLOVAKIA":"SK",
    "HUNGARY":"HU","ROMANIA":"RO","BULGARIA":"BG","GREECE":"GR",
    "TURKEY":"TR","TÜRKIYE":"TR","SERBIA":"RS","CROATIA":"HR",
    "SLOVENIA":"SI","BOSNIA AND HERZEGOVINA":"BA","ALBANIA":"AL",
    "UKRAINE":"UA","BELARUS":"BY","RUSSIA":"RU","ESTONIA":"EE",
    "LATVIA":"LV","LITHUANIA":"LT","GEORGIA":"GE","KAZAKHSTAN":"KZ",
    "ISRAEL":"IL","CYPRUS":"CY","IRELAND":"IE","BRAZIL":"BR",
    "ARGENTINA":"AR","CHILE":"CL","URUGUAY":"UY","PARAGUAY":"PY",
    "PERU":"PE","COLOMBIA":"CO","ECUADOR":"EC","BOLIVIA":"BO",
    "VENEZUELA":"VE","MEXICO":"MX","USA":"US","UNITED STATES":"US",
    "CANADA":"CA","COSTA RICA":"CR","PANAMA":"PA",
    "JAPAN":"JP","SOUTH KOREA":"KR","CHINA":"CN","INDIA":"IN",
    "AUSTRALIA":"AU","NEW ZEALAND":"NZ","INDONESIA":"ID",
    "THAILAND":"TH","VIETNAM":"VN","MALAYSIA":"MY",
    "SOUTH AFRICA":"ZA","EGYPT":"EG","MOROCCO":"MA",
    "ALGERIA":"DZ","TUNISIA":"TN","NIGERIA":"NG","GHANA":"GH",
    "SAUDI ARABIA":"SA","QATAR":"QA","UAE":"AE",
    "ОАЭ":"AE","АНГЛИЯ":"GB","ГЕРМАНИЯ":"DE","ИСПАНИЯ":"ES",
    "ИТАЛИЯ":"IT","ФРАНЦИЯ":"FR","РОССИЯ":"RU","БРАЗИЛИЯ":"BR",
    "АРГЕНТИНА":"AR","ФИНЛЯНДИЯ":"FI","ПОЛЬША":"PL",
    "ШВЕЦИЯ":"SE","НОРВЕГИЯ":"NO","ТУРЦИЯ":"TR",
    "ЯПОНИЯ":"JP","США":"US","КИТАЙ":"CN",
}


def league_display(league: str) -> tuple[str, str]:
    """Use the country's name in Flashscore league headings, if provided."""
    league=str(league or "").strip()
    if not league:
        return "", ""
    country=league.split(":",1)[0].strip().upper()
    code=COUNTRY_CODES.get(country,"") if ":" in league else ""
    flag=("".join(chr(127397+ord(x)) for x in code) if code else "")
    return league[:110],flag


def parse_red_cards_summary(body: str) -> list[int] | None:
    """Count only source-confirmed direct red incidents with explicit side.

    Unknown teams/incident types are skipped, never assigned by guess.
    Empty/unavailable source responses mean 'unknown', not zero cards.
    """
    if not body:
        return None
    cards=[0,0]
    for chunk in str(body).split("~III"):
        typ=re.search(r"(?:^|¬)IA(?:÷|¬)(\d+)",chunk)
        if typ is None or typ.group(1)!="3":
            continue
        team=re.search(r"(?:^|¬)IK(?:÷|¬)([^¬~]+)",chunk)
        side=team.group(1).strip().casefold() if team else ""
        if side in {"1","home","h"}:
            cards[0]+=1
        elif side in {"2","away","a"}:
            cards[1]+=1
    return cards


def _telegram(token,method,payload):
    # Telegram's original beta endpoint may still accept widgets when
    # the production rollout denies this bot. Never switch text or
    # subscriber-management API calls to beta.
    beta_method=method in ("sendWidgetBeta","editMessageWidgetDataBeta")
    actual_method=method[:-4] if beta_method else method
    base="https://api.telegram.org/beta/bot" if beta_method else "https://api.telegram.org/bot"
    url=f"{base}{token}/{actual_method}"
    headers={}
    png=(payload.get("photo_png") if isinstance(payload,dict) else None)
    is_photo=method in ("sendPhoto","editMessageMedia") and png is not None
    is_gif=(method in ("sendAnimation","editMessageMedia") and
            isinstance(payload,dict) and "animation_gif" in payload)
    if is_photo or is_gif:
        media=png if is_photo else payload["animation_gif"]
        if is_photo and (not isinstance(media,bytes) or
                         not media.startswith(b"\x89PNG\r\n\x1a\n")):
            return {"ok":False,"description":"INVALID_PNG_PAYLOAD"}
        if is_gif and (not isinstance(media,bytes) or not media.startswith(b"GIF8")):
            return {"ok":False,"description":"INVALID_GIF_PAYLOAD"}
        upload_name="photo" if is_photo else "animation"
        media_type="photo" if is_photo else "animation"
        file_name="score.png" if is_photo else "score.gif"
        content_type="image/png" if is_photo else "image/gif"
        import secrets
        boundary="PROGRUZ1"+secrets.token_hex(10)
        br=boundary.encode("ascii")
        fields={"chat_id":payload["chat_id"]}
        if method in ("sendAnimation","sendPhoto"):
            fields["caption"]=str(payload.get("caption") or "")
        else:
            fields["message_id"]=str(payload["message_id"])
            fields["media"]=json.dumps({
                "type":media_type,"media":"attach://"+upload_name,
                "caption":str(payload.get("caption") or "")
            },ensure_ascii=False)
        parts=[]
        for key,value in fields.items():
            parts.append(b"--"+br+b"\r\n"+
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()+
                str(value).encode("utf-8")+b"\r\n")
        parts.append(b"--"+br+b"\r\n"+
            f'Content-Disposition: form-data; name="{upload_name}"; filename="{file_name}"\r\n'.encode("ascii")+
            f'Content-Type: {content_type}\r\n\r\n'.encode("ascii")+
            media+b"\r\n")
        parts.append(b"--"+br+b"--\r\n")
        data=b"".join(parts)
        headers={"Content-Type":"multipart/form-data; boundary="+boundary}
    else:
        data=parse.urlencode(payload).encode("utf-8")
    try:
        with request.urlopen(request.Request(url,data=data,headers=headers),timeout=12) as res:
            return json.loads(res.read(24000))
    except error.HTTPError as exc:
        try:
            return json.loads(exc.read(12000))
        except (ValueError,OSError):
            return {"ok":False,"description":f"HTTP {exc.code}"}
    except (OSError,ValueError) as exc:
        return {"ok":False,"description":type(exc).__name__}


class GoalWidgets:
    """One widget per notified fixture; changed only by trusted scores."""

    def __init__(self,db:sqlite3.Connection,token:str,chat_id:str,
                 *,transport=None,enabled=None,provider=None,
                 follower=False):
        self.db=db
        self.token=str(token or "")
        self.chat_id=str(chat_id or "")
        self.transport=transport or _telegram
        self.provider=provider
        # Owner's existing SQLite table remains untouched on upgrades.
        # Approved friends use a separate per-chat table.
        self.follower=bool(follower)
        self.table=("progruz1_goal_widgets_subscribers"
                    if self.follower else "progruz1_goal_widgets")
        try:
            private_chat=int(self.chat_id)>0
        except (TypeError,ValueError):
            private_chat=False
        configured=(os.getenv("GOOL_GOAL_WIDGETS","1")=="1"
                    if enabled is None else bool(enabled))
        self.enabled=bool(configured and self.token and private_chat)
        primary_key=("PRIMARY KEY(root_id,kickoff,chat_id)"
                     if self.follower else "PRIMARY KEY(root_id,kickoff)")
        db.execute(f"""CREATE TABLE IF NOT EXISTS {self.table}(
            root_id TEXT NOT NULL,kickoff INTEGER NOT NULL,
            fs_id TEXT NOT NULL,message_id INTEGER NOT NULL,
            chat_id TEXT NOT NULL,state_json TEXT NOT NULL,
            last_edit_at INTEGER NOT NULL,
            last_stats_at INTEGER NOT NULL DEFAULT 0,
            last_cards_at INTEGER NOT NULL DEFAULT 0,
            delivery_kind TEXT NOT NULL DEFAULT 'WIDGET',
            {primary_key}
        )""")
        cols={row[1] for row in db.execute(f"PRAGMA table_info({self.table})")}
        for key in ("last_stats_at","last_cards_at"):
            if key not in cols:
                db.execute(f"ALTER TABLE {self.table} ADD COLUMN {key} INTEGER NOT NULL DEFAULT 0")
        if "delivery_kind" not in cols:
            db.execute(f"ALTER TABLE {self.table} ADD COLUMN delivery_kind TEXT NOT NULL DEFAULT 'WIDGET'")
        db.execute("""CREATE TABLE IF NOT EXISTS progruz1_widget_delivery_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at INTEGER NOT NULL,
            chat_id TEXT NOT NULL,
            root_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            detail TEXT NOT NULL
        )""")
        db.commit()

    def _delivery_event(self,root,stage,detail,now=None):
        now=int(now if now is not None else time.time())
        note=" ".join(str(detail or "").split())[:220]
        try:
            with self.db:
                self.db.execute("""INSERT INTO progruz1_widget_delivery_log
                    (occurred_at,chat_id,root_id,stage,detail)
                    VALUES(?,?,?,?,?)""",
                    (now,self.chat_id,str(root or ""),str(stage),note))
                self.db.execute("""DELETE FROM progruz1_widget_delivery_log
                    WHERE occurred_at<?""",(now-7*86400,))
        except sqlite3.Error as exc:
            print("PROGRUZ1_WIDGET_DIAG_WRITE_FAILED",
                  type(exc).__name__,flush=True)
        print("PROGRUZ1_WIDGET_DELIVERY",stage,
              f"root={root}",note,flush=True)

    def delivery_status(self):
        """Owner-readable summary, no bot token or chat data disclosed."""
        try:
            rows=self.db.execute("""SELECT occurred_at,stage,detail
                FROM progruz1_widget_delivery_log WHERE chat_id=?
                ORDER BY id DESC LIMIT 5""",(self.chat_id,)).fetchall()
        except sqlite3.Error:
            rows=[]
        lines=["⚙️ ПРОГРУЗ 1 · проверка виджета",
               "Виджеты: "+("ВКЛЮЧЕНЫ" if self.enabled else "ВЫКЛЮЧЕНЫ"),
               "HTML: "+str(len(WIDGET_HTML.encode("utf-8")))+
               " байт; zlib: "+str(len(zlib.compress(WIDGET_HTML.encode("utf-8"),9)))+
               "/4096 байт",
               "События отправки:"]
        for ts,stage,detail in rows:
            when=time.strftime("%d.%m %H:%M",time.localtime(int(ts)))
            lines.append(f"• {when} · {stage}: {detail}")
        if not rows:
            lines.append("Пока нет записей. Проверь новый сигнал или /widgettest.")
        return "\n".join(lines)

    @staticmethod
    def _widget_denied(response):
        return (isinstance(response,dict)
                and not response.get("ok") and
                "INPUT_MEDIA_WIDGET_NOT_ALLOWED" in
                    str(response.get("description") or "").upper())

    @staticmethod
    def _beta_enabled():
        return os.getenv("GOOL_WIDGET_BETA_FALLBACK","1")=="1"

    @staticmethod
    def _photo_enabled():
        return os.getenv("GOOL_WIDGET_PHOTO_FALLBACK","1")=="1"

    @staticmethod
    def _photo_caption(state):
        # A short caption, but always the EXACT line offered at signal
        # time. A 2H match total must not be mislabeled a 2H half total.
        try:
            odd=f"@{float(state['odd']):.2f}"
        except (KeyError,ValueError,TypeError,OverflowError):
            odd="@—"
        return f"⚽ ПРОГРУЗ 1 · {exact_total_with_period(state)} {odd}"

    def _send_photo(self,state,*,now:int,caption:str|None=None):
        from progruz1_static_scoreboard import render_scoreboard_png
        return self.transport(self.token,"sendPhoto",{
            "chat_id":self.chat_id,
            "photo_png":render_scoreboard_png(state,now),
            "caption":self._photo_caption(state) if caption is None else caption})

    def _send_widget_request(self,payload,*,beta=False):
        """Retry only *explicit* transient API errors; no blind timeout replay.

        A network timeout has ambiguous delivery status and could create a
        duplicate message, so it is intentionally not retried.
        """
        method="sendWidgetBeta" if beta else "sendWidget"
        response=self.transport(self.token,method,payload)
        if not isinstance(response,dict) or response.get("ok"):
            return response
        code=response.get("error_code")
        detail=str(response.get("description") or "").lower()
        is_rate_limited=(code==429 or
                         "too many requests" in detail)
        is_unavailable=(code in (502,503,504))
        if not (is_rate_limited or is_unavailable):
            return response
        retry=(response.get("parameters") or {}).get("retry_after")
        try:
            pause=float(retry) if retry is not None else 0.6
        except (ValueError,TypeError,OverflowError):
            pause=0.6
        if pause>2.5:  # Do not delay an expiring live bet for a long outage.
            return response
        time.sleep(max(0.3,pause))
        return self.transport(self.token,method,payload)

    def probe_widget_permission(self,now=None):
        """Distinguish chat type / server permission from production HTML.

        Run only for owner on explicit /widgetprobe. A *minimal* widget is
        sent to the same destination as the signal, via the same bot token.
        This does not place a bet or alter a signal, journal or subscriber.
        """
        now=int(now if now is not None else time.time())
        if not self.enabled:
            return ("🧪 Виджеты отключены в настройках бота. "
                    "Нужны положительный личный Chat ID, токен и NOTIFY.")
        chat=self.transport(self.token,"getChat",{"chat_id":self.chat_id})
        chat_info=chat.get("result") if (
            isinstance(chat,dict) and chat.get("ok")) else None
        chat_type=(str(chat_info.get("type") or "unknown")
                   if isinstance(chat_info,dict) else "unknown")
        minimal="<html><body><p>PROGRUZ 1 widget probe</p></body></html>"
        answer=self._send_widget_request({
            "chat_id":self.chat_id,"html":minimal,
            "caption":"🧪 Проверка минимального виджета — не сигнал"})
        allowed=isinstance(answer,dict) and answer.get("ok") is True
        detail=(str(answer.get("description") or
                    answer.get("error_code") or "API_ERROR")
                if isinstance(answer,dict) else "INVALID_API_RESPONSE")
        if allowed:
            status=("✅ Минимальный виджет отправлен через основной API. "
                    "Ограничения на отправку в этот чат нет.")
            self._delivery_event("PROBE","MINIMAL_OK",
                                 f"getChat.type={chat_type}",now)
        elif self._widget_denied(answer) and self._beta_enabled():
            beta=self._send_widget_request({
                "chat_id":self.chat_id,"html":minimal,
                "caption":"🧪 Проверка бета-маршрута — не сигнал"},beta=True)
            if isinstance(beta,dict) and beta.get("ok"):
                status=("✅ Основной API запретил виджет, но бета-маршрут "
                        "Telegram принял! Для новых сигналов будет "
                        "использоваться этот резервный маршрут.")
                self._delivery_event("PROBE","BETA_MINIMAL_OK",
                                     f"getChat.type={chat_type}",now)
            else:
                beta_detail=(str(beta.get("description") or
                                 beta.get("error_code") or "API_ERROR")
                             if isinstance(beta,dict) else "INVALID_RESPONSE")
                status=("❌ Основной API: "+detail[:110]+
                        "\n❌ Бета-маршрут: "+beta_detail[:110]+
                        "\nОба маршрута отклонили виджет.")
                self._delivery_event("PROBE","BOTH_DENIED",
                                     f"getChat.type={chat_type}; main={detail}; "
                                     f"beta={beta_detail}",now)
        else:
            status=("❌ Минимальный виджет отклонён: "+detail[:160]+
                    ". Чат private, HTML здесь не причина.")
            self._delivery_event("PROBE","MINIMAL_FAILED",
                                 f"getChat.type={chat_type}; {detail}",now)
        return ("🧪 ПРОГРУЗ 1 · проверка разрешения\n"
                f"Telegram getChat.type: {chat_type}\n"+status)

    def test_widget(self,now=None):
        """Owner-only Telegram command check with the REAL production HTML."""
        now=int(now if now is not None else time.time())
        if not self.enabled:
            return "Виджет отключён. Проверь GOOL_GOAL_WIDGETS, TELEGRAM_CHAT_ID и GOOL_LITE_SHADOW."
        sample={"home":"Проверка","away":"Телеграм","league":"TEST: Telegram Widget",
                "flag":"","minute_text":"26'","minute":26,"period":"1-й тайм",
                "score":[0,0],"odd":1.8,"shots":[3,7],"sot":[1,3],
                "xg":[0.13,0.16],"stats_at":now,"red":[0,0],
                "status":"PENDING","flash_side":"","flash_until":0,"goal_at":0}
        answer=self._send_widget_request({
            "chat_id":self.chat_id,"html":WIDGET_HTML,
            "chat_data":_json(sample),
            "caption":"🧪 ТЕСТ ЖИВОГО ТАБЛО · это не ставка"})
        ok=(isinstance(answer,dict) and answer.get("ok") is True and
            isinstance(answer.get("result"),dict) and
            type(answer["result"].get("message_id")) is int)
        if ok:
            self._delivery_event("TEST","TEST_OK","sendWidget OK",now)
            return "✅ Тестовое табло отправлено. Telegram принимает текущий HTML."
        detail=str(answer.get("description") or answer.get("error_code")
                   or "API_UNKNOWN") if isinstance(answer,dict) else "INVALID_RESPONSE"
        self._delivery_event("TEST","TEST_FAILED",detail,now)
        if self._widget_denied(answer) and self._beta_enabled():
            beta=self._send_widget_request({
                "chat_id":self.chat_id,"html":WIDGET_HTML,
                "chat_data":_json(sample),
                "caption":"🧪 ТЕСТ ЖИВОГО ТАБЛО · бета-маршрут — не ставка"
            },beta=True)
            if isinstance(beta,dict) and beta.get("ok"):
                self._delivery_event("TEST","TEST_BETA_OK",
                                     "Beta sendWidget accepted",now)
                return ("✅ Бета-маршрут Telegram принял живое табло! "
                        "Новые сигналы будут пробовать этот маршрут "
                        "при запрете основного API.")
        if "INPUT_MEDIA_WIDGET_NOT_ALLOWED" in detail.upper():
            if self._photo_enabled():
                try:
                    photo=self._send_photo(sample,now=now,
                         caption="🧪 ТЕСТ: статичное PNG-табло · не ставка")
                    if isinstance(photo,dict) and photo.get("ok"):
                        self._delivery_event("TEST","TEST_PHOTO_OK",
                                             "sendPhoto accepted",now)
                        return ("✅ HTML-виджеты недоступны, "
                                "отправлено статичное PNG-табло. "
                                "Новые сигналы обновляют фото в том же сообщении.")
                except (OSError,ValueError,TypeError,ImportError) as exc:
                    self._delivery_event("TEST","TEST_PHOTO_ERROR",
                                         type(exc).__name__,now)
            text_reply=self.transport(self.token,"sendMessage",{
                "chat_id":self.chat_id,
                "text":render_text_scoreboard(sample,now=now)
                        +"\n\n🧪 ТЕСТ: текстовое табло"})
            if isinstance(text_reply,dict) and text_reply.get("ok"):
                return ("⚠️ Telegram пока запрещает HTML-виджеты "
                        "(INPUT_MEDIA_WIDGET_NOT_ALLOWED). "
                        "Отправлено короткое тестовое текстовое табло. "
                        "При настоящем сигнале оно будет обновляться "
                        "в том же сообщении.")
        return "❌ Тест табло отклонён Telegram: "+detail[:250]

    def send_signal(self,pick:dict,*,home:str,away:str,stats:dict,now:int,
                    league:str="",minute_text:str|None=None,
                    logo_meta:dict|None=None):
        """Return Telegram message ID or None so caller can send legacy text."""
        if not self.enabled:
            self._delivery_event(pick.get("root_id"),"DISABLED",
                                 "Widget disabled in configuration",now)
            return None
        old=self.db.execute(f"""SELECT message_id FROM {self.table}
            WHERE root_id=? AND kickoff=? AND chat_id=?""",
            (str(pick["root_id"]),int(pick["kickoff"]),self.chat_id)).fetchone()
        if old:
            return int(old[0])
        score=_pair(pick.get("score"))
        if score is None or len(score)!=2:
            self._delivery_event(pick.get("root_id"),"INVALID_SCORE",
                                 "No exact pair of whole-match scores",now)
            return None
        xg=_pair((stats or {}).get("xg"),decimals=True)
        league_name,flag=league_display(league)
        # An expulsion may have happened before the pick. Load the full
        # Flashscore incident history BEFORE sending the first widget frame.
        # Keep unknown distinct from verified zero on a source outage.
        red=_pair((stats or {}).get("red_cards"))
        cards_checked_at=0
        if self.provider is not None:
            try:
                detected=self._fetch_cards(str(pick["fs_event_id"]))
                if detected is not None:
                    cards_checked_at=int(now)
                    if sum(detected)>0 or red is None:
                        red=detected
            except (OSError,TimeoutError,TypeError,ValueError) as exc:
                print("PROGRUZ1_WIDGET_INITIAL_CARDS_ERROR",
                      type(exc).__name__,str(exc)[:100],flush=True)
        logos=logo_meta if isinstance(logo_meta,dict) else {}
        state={"home":str(home)[:100],"away":str(away)[:100],
               "home_logo_file":str(logos.get("home_logo_file") or "")[:100],
               "away_logo_file":str(logos.get("away_logo_file") or "")[:100],
               "league":league_name,"flag":flag,
               "minute_text":((str(minute_text).rstrip("'")+"'" )[:10]
                              if minute_text and str(minute_text)!="HT" else ""),
               "minute":int(pick["minute"]),
               "period":str(pick.get("signal_scope") or pick["period"])[:22],
               "score":score,"odd":round(float(pick["odd"]),2),
               "line":pick.get("line"),
               "market_period":str(pick.get("period") or ""),
               "first_half_score":_pair(pick.get("first_half_score")),
               "side":str(pick.get("side") or "TB"),
               "quote_label":str(pick.get("quote_label") or "Фонбет LIVE")[:60],
               "market_kind":str(pick.get("market_kind") or "general_total"),
               "live_goal_confirmed":False,
               "live_cross_first_seen":0,
               "live_cross_score":None,
               "shots":_pair((stats or {}).get("shots")),
               "sot":_pair((stats or {}).get("shots_on_target")),
               "xg":xg,"stats_at":int(now),"red":red,"status":"PENDING",
               "flash_side":"","flash_until":0,"goal_at":0}
        try:
            serialized=_json(state)
        except (ValueError,OverflowError) as exc:
            self._delivery_event(pick.get("root_id"),"STATE_ERROR",
                                 type(exc).__name__+": "+str(exc),now)
            return None
        payload={
            "chat_id":self.chat_id,"html":WIDGET_HTML,"chat_data":serialized,
            "caption":self._photo_caption(state)}
        response=self._send_widget_request(payload)
        lite_sent=False
        if isinstance(response,dict) and not response.get("ok"):
            description=str(response.get("description") or "").upper()
            # A Telegram-side HTML restriction should not deprive users
            # of a live score. Retry once with lean supported markup, not
            # for invalid chat, permission, 429 or transport ambiguity.
            html_rejected=(int(response.get("error_code") or 0)==400
                           and any(part in description for part in (
                               "WIDGET_HTML","WIDGET_TOO_LARGE",
                               "WIDGET_HTML_INVALID","MEDIA_WIDGET_HTML",
                               "INPUT_MEDIA_WIDGET_TOO_LARGE")))
            if html_rejected:
                payload["html"]=WIDGET_HTML_LITE
                response=self._send_widget_request(payload)
                lite_sent=bool(isinstance(response,dict) and
                               response.get("ok"))
        beta_sent=False
        if self._widget_denied(response) and self._beta_enabled():
            beta=self._send_widget_request(payload,beta=True)
            beta_msg=(beta.get("result") if isinstance(beta,dict)
                      and beta.get("ok") else None)
            if (isinstance(beta_msg,dict) and
                    type(beta_msg.get("message_id")) is int and
                    beta_msg["message_id"]>0):
                response=beta
                beta_sent=True
                self._delivery_event(pick.get("root_id"),"BETA_RECOVERED",
                                     "Beta sendWidget accepted",now)
            else:
                reason=(str(beta.get("description") or
                            beta.get("error_code") or "INVALID_RESULT")
                        if isinstance(beta,dict) else "INVALID_RESPONSE")
                self._delivery_event(pick.get("root_id"),"BETA_REJECTED",
                                     reason,now)
        msg=(response.get("result") if isinstance(response,dict)
             and response.get("ok") else None)
        message_id=(msg.get("message_id") if isinstance(msg,dict) else None)
        delivery_kind="WIDGET_BETA" if beta_sent else "WIDGET"
        if type(message_id) is not int or message_id<=0:
            desc=(str(response.get("description") or
                      response.get("error_code") or "API_ERROR")
                  if isinstance(response,dict) else "INVALID_API_RESPONSE")
            self._delivery_event(pick.get("root_id"),"API_REJECTED",
                                 desc,now)
            if "INPUT_MEDIA_WIDGET_NOT_ALLOWED" not in desc.upper():
                return None
            # HTML widgets are not allowed for this bot. Use a crisp,
            # editable static photo, never an endlessly looping GIF.
            if self._photo_enabled():
                try:
                    photo=self._send_photo(state,now=now)
                    result=(photo.get("result") if
                        isinstance(photo,dict) and photo.get("ok") else None)
                    photo_id=(result.get("message_id") if
                        isinstance(result,dict) else None)
                    if type(photo_id) is int and photo_id>0:
                        message_id=photo_id
                        delivery_kind="PHOTO"
                    else:
                        self._delivery_event(
                            pick.get("root_id"),"PHOTO_FALLBACK_FAILED",
                            str(photo.get("description") or "INVALID_RESULT")
                            if isinstance(photo,dict) else "INVALID_RESPONSE",now)
                except (OSError,ValueError,TypeError,ImportError) as exc:
                    self._delivery_event(pick.get("root_id"),
                        "PHOTO_FALLBACK_ERROR",type(exc).__name__,now)
            if delivery_kind!="PHOTO":
                text_reply=self.transport(self.token,"sendMessage",{
                    "chat_id":self.chat_id,
                    "text":render_text_scoreboard(state,now=now)})
                reply=(text_reply.get("result") if isinstance(text_reply,dict)
                       and text_reply.get("ok") else None)
                message_id=(reply.get("message_id")
                            if isinstance(reply,dict) else None)
                if type(message_id) is not int or message_id<=0:
                    self._delivery_event(
                        pick.get("root_id"),"TEXT_FALLBACK_FAILED",
                        str(text_reply.get("description") or "sendMessage failed")
                        if isinstance(text_reply,dict) else "INVALID_RESPONSE",now)
                    return None
                delivery_kind="TEXT"
        self._delivery_event(
            pick.get("root_id"),
            "SEND_PHOTO_OK" if delivery_kind=="PHOTO" else
            "SEND_TEXT_OK" if delivery_kind=="TEXT" else
            "SEND_BETA_OK" if beta_sent else
            "SEND_LITE_OK" if lite_sent else "SEND_OK",
            f"message_id={message_id}",now)
        with self.db:
            self.db.execute(f"""INSERT OR IGNORE INTO {self.table}
                (root_id,kickoff,fs_id,message_id,chat_id,state_json,
                 last_edit_at,last_cards_at,delivery_kind)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(pick["root_id"]),int(pick["kickoff"]),
                 str(pick["fs_event_id"]),message_id,self.chat_id,
                 serialized,int(now),cards_checked_at,delivery_kind))
        return message_id

    def _update(self,root,kick,message_id,state,now):
        serialized=_json(state)
        row=self.db.execute(f"""SELECT delivery_kind,state_json,last_edit_at
            FROM {self.table}
            WHERE root_id=? AND kickoff=? AND chat_id=?""",
            (str(root),int(kick),self.chat_id)).fetchone()
        is_text=bool(row and row[0]=="TEXT")
        is_beta=bool(row and row[0]=="WIDGET_BETA")
        is_photo=bool(row and row[0] in ("PHOTO","GIF"))
        if is_photo:
            try:
                prior=json.loads(row[1]) if row and row[1] else {}
            except (ValueError,TypeError):
                prior={}
            urgent=any(prior.get(k)!=state.get(k) for k in (
                "score","status","red","flash_side",
                "live_goal_confirmed","live_cross_first_seen"))
            blink_changed=(prior.get("flash_pulse_on")!=
                           state.get("flash_pulse_on"))
            # Each refreshed photo alternates the red dot ON/OFF beside
            # the confirmed scorer. Keep normal 20s stats throttling.
            min_gap=6 if blink_changed else 20
            if not urgent and int(now)-int(row[2])<min_gap:
                return False
            try:
                from progruz1_static_scoreboard import render_scoreboard_png
                media=render_scoreboard_png(state,int(now))
            except (OSError,ValueError,TypeError,ImportError) as exc:
                self._delivery_event(root,"PHOTO_RENDER_FAILED",
                                     type(exc).__name__,now)
                return False
            response=self.transport(self.token,"editMessageMedia",{
                "chat_id":self.chat_id,"message_id":int(message_id),
                "photo_png":media,"caption":self._photo_caption(state)})
        else:
            method=("editMessageText" if is_text else
                    "editMessageWidgetDataBeta" if is_beta else
                    "editMessageWidgetData")
            content=({"text":render_text_scoreboard(state,now=now)}
                     if is_text else {"chat_data":serialized})
            response=self.transport(self.token,method,{
                "chat_id":self.chat_id,"message_id":int(message_id),
                **content})
        desc=str(response.get("description") or "").lower() if (
            isinstance(response,dict)) else ""
        succeeded=(isinstance(response,dict) and response.get("ok") is True
                   or "message is not modified" in desc)
        if succeeded:
            with self.db:
                self.db.execute(f"""UPDATE {self.table}
                    SET state_json=?,last_edit_at=?,delivery_kind=?
                    WHERE root_id=? AND kickoff=? AND chat_id=?""",
                    (serialized,int(now),
                     "PHOTO" if is_photo else row[0] if row else "WIDGET",
                     str(root),int(kick),self.chat_id))
        if succeeded and is_photo and prior.get("score")!=state.get("score"):
            # Diagnostic for perceived Telegram image delay: the source score
            # and the scoring-side dot are published in ONE media edit.
            print("PROGRUZ1_SCORE_AND_GOAL_DOT",
                  f"root={root} message={message_id}",
                  f"old={prior.get('score')} new={state.get('score')}",
                  f"flash_side={state.get('flash_side')}",
                  f"scan_ts={int(now)}",flush=True)
        if not succeeded:
            print("PROGRUZ1_WIDGET_EDIT_FAILED",
                  f"root={root} message={message_id}",
                  str(response.get("description") if isinstance(response,dict)
                      else "INVALID_RESPONSE")[:180],flush=True)
        return succeeded

    def _fetch_stats(self,fs_id):
        """Fetch fresh full-match totals; never replace prior data on outage."""
        if self.provider is None:
            return None
        from gool_flashscore_provider.flashscore_stats_guard import parse_match_stats_body
        body=self.provider._feed(f"df_st_1_{fs_id}",timeout=3,max_hosts=1)
        return parse_match_stats_body(body) if body else None

    def _fetch_cards(self,fs_id):
        if self.provider is None:
            return None
        body=self.provider._feed(f"df_sui_1_{fs_id}",timeout=3,max_hosts=1)
        return parse_red_cards_summary(body)

    def observe_live(self,matches,now):
        """Update LIVE match scoreboard and bounded Flashscore data.

        A +1 change on an exact Flashscore match ID triggers a goal dot;
        other score changes clear the dot. All statistical fields refresh
        independently from the entry-signal snapshot.
        """
        if not self.enabled:
            return 0
        live={str(m.provider_match_id):m for m in matches
              if getattr(m,"provider_match_id",None)}
        rows=self.db.execute(f"""SELECT w.root_id,w.kickoff,w.fs_id,
            w.message_id,w.state_json,w.last_edit_at,
            w.last_stats_at,w.last_cards_at,w.delivery_kind,
            r.line,r.period,r.half_home,r.half_away,r.side,r.market_kind
            FROM {self.table} w
            JOIN adaptive_results r
              ON r.root_id=w.root_id AND r.kickoff=w.kickoff
            WHERE r.status='PENDING' AND w.kickoff>=? AND w.chat_id=?
            ORDER BY w.last_stats_at,w.last_cards_at LIMIT 40""",
            (int(now)-86400,self.chat_id)).fetchall()
        changed=0
        stats_budget=2
        cards_budget=1
        for (root,kick,fs_id,msg_id,raw,last_at,last_stats,last_cards,
             delivery_kind,book_line,book_period,half_home,half_away,
             book_side,book_kind) in rows:
            match=live.get(str(fs_id))
            if match is None:
                continue
            new=_pair((getattr(match,"home_score",None),
                       getattr(match,"away_score",None)))
            if new is None or len(new)!=2:
                continue
            state=json.loads(raw)
            prev=state.get("score")
            if not isinstance(prev,list) or len(prev)!=2:
                continue
            minute=getattr(match,"minute",None)
            try:
                minute=int(minute)
            except (ValueError,TypeError):
                minute=state.get("minute")
            new_state=dict(state)
            # Backfill existing PHOTO signals from the immutable original
            # bet, not the changing current LIVE score. New sends already
            # carry these fields in the Telegram widget state.
            if new_state.get("line") is None:
                new_state["line"]=book_line
            if not new_state.get("market_period"):
                new_state["market_period"]=book_period
            if not new_state.get("first_half_score"):
                if type(half_home) is int and type(half_away) is int:
                    new_state["first_half_score"]=[half_home,half_away]
            if not new_state.get("side"):
                new_state["side"]=book_side
            if not new_state.get("market_kind"):
                new_state["market_kind"]=book_kind
            new_state["minute"]=minute
            meta=getattr(match,"meta",None) or {}
            actual_minute=meta.get("display_minute")
            if actual_minute:
                actual_minute=str(actual_minute).rstrip("'’")
                new_state["minute_text"]=(actual_minute+"'" if
                    actual_minute!="HT" else actual_minute)
            league=getattr(match,"league",None)
            if league:
                name,flag=league_display(league)
                new_state["league"]=name
                new_state["flag"]=flag
            # The Flashscore master already includes OA / OB logo files.
            # Keep the identifier on each scan, but don't fake an emblem
            # when a league does not provide one.
            for key in ("home_logo_file","away_logo_file"):
                value=str(meta.get(key) or "").strip()[:100]
                if value and value!=new_state.get(key):
                    new_state[key]=value
            score_changed=(new!=prev)
            if score_changed:
                if new[0]==prev[0]+1 and new[1]==prev[1]:
                    new_state["flash_side"]="home"
                    new_state["flash_until"]=int(now)+90
                    new_state["goal_at"]=int(now)
                    new_state["flash_pulse_on"]=True
                elif new[1]==prev[1]+1 and new[0]==prev[0]:
                    new_state["flash_side"]="away"
                    new_state["flash_until"]=int(now)+90
                    new_state["goal_at"]=int(now)
                    new_state["flash_pulse_on"]=True
                else:
                    new_state["flash_side"]=""
                    new_state["flash_until"]=0
                    new_state["goal_at"]=0
                    new_state["flash_pulse_on"]=False
                new_state["score"]=new

            # IMPORTANT: the visible LIVE status is not the journal's
            # verified HT/FT settlement. A repeated Flashscore score
            # crossing the exact offered total makes the photo read
            # "✅ ЗАШЁЛ" without waiting for FT. VAR/score rollback
            # immediately restores "ОЖИДАЕМ ГОЛ". The journal remains
            # PENDING until the official completed period is verified.
            new_state=observe_live_total(new_state,new,minute,now)

            # Static Telegram photos don't support CSS animation.
            # Toggle only during a verified goal alert and only for photo
            # messages. At scan cadence, this is a short-lived blink,
            # not a stream of duplicate Telegram messages.
            if not score_changed and delivery_kind in ("PHOTO","GIF"):
                active=(new_state.get("flash_side") in ("home","away")
                        and new_state.get("status")=="PENDING")
                until=int(new_state.get("flash_until") or 0)
                if active and int(now)>=until:
                    new_state["flash_side"]=""
                    new_state["flash_until"]=0
                    new_state["flash_pulse_on"]=False
                elif active and int(now)-int(last_at)>=6:
                    new_state["flash_pulse_on"]=(
                        not bool(new_state.get("flash_pulse_on",True)))

            # Bound source reads across all active widgets: two stat fetches
            # and one incident fetch per scanner pass, independent of match
            # count. Failed fetches preserve last known numbers.
            # Goal frame is time-critical: send the score/dot first
            # without waiting for up to 9s of optional stats/cards HTTP.
            # Refresh those fields normally on the next scanner pass.
            if (not score_changed and self.provider is not None
                    and stats_budget and int(now)-int(last_stats)>=45):
                stats_budget-=1
                with self.db:
                    self.db.execute(f"""UPDATE {self.table}
                        SET last_stats_at=? WHERE root_id=? AND kickoff=?
                        AND chat_id=?""",
                        (int(now),root,kick,self.chat_id))
                try:
                    fresh=self._fetch_stats(fs_id)
                    if isinstance(fresh,dict):
                        for key,source in (("shots","shots"),
                                           ("sot","shots_on_target"),
                                           ("xg","xg")):
                            pair=_pair(fresh.get(source),
                                       decimals=(key=="xg"))
                            if pair is not None and len(pair)==2:
                                new_state[key]=pair
                        if any(x in fresh for x in ("shots","shots_on_target","xg")):
                            new_state["stats_at"]=int(now)
                        pair=_pair(fresh.get("red_cards"))
                        if pair is not None and len(pair)==2:
                            new_state["red"]=pair
                except (OSError,TimeoutError,TypeError,ValueError) as exc:
                    print("PROGRUZ1_WIDGET_STATS_ERROR",
                          type(exc).__name__,str(exc)[:100],flush=True)

            if (not score_changed and self.provider is not None
                    and cards_budget and int(now)-int(last_cards)>=90):
                cards_budget-=1
                with self.db:
                    self.db.execute(f"""UPDATE {self.table}
                        SET last_cards_at=? WHERE root_id=? AND kickoff=?
                        AND chat_id=?""",
                        (int(now),root,kick,self.chat_id))
                try:
                    cards=self._fetch_cards(fs_id)
                    if cards is not None:
                        # Never overwrite independently verified stat totals
                        # with an ambiguous/empty incident feed count.
                        if sum(cards)>0 or new_state.get("red") is None:
                            new_state["red"]=cards
                except (OSError,TimeoutError,TypeError,ValueError) as exc:
                    print("PROGRUZ1_WIDGET_CARDS_ERROR",
                          type(exc).__name__,str(exc)[:100],flush=True)
            if new_state!=state and self._update(
                    root,kick,msg_id,new_state,now):
                changed+=1
        return changed

    def settle(self,now):
        """Update result IN the widget instead of sending a duplicate reply."""
        if not self.enabled:
            return 0
        rows=self.db.execute(f"""SELECT w.root_id,w.kickoff,w.message_id,
            w.state_json,r.status,r.period,r.score_home,r.score_away,
            r.line
            FROM {self.table} w JOIN adaptive_results r
            ON r.root_id=w.root_id AND r.kickoff=w.kickoff
            WHERE r.status IN ('WON','LOST','VOID') AND w.chat_id=?
              AND (json_extract(w.state_json,'$.status') IS NOT r.status
                   OR (?=0 AND r.confirmation_sent=0))
            ORDER BY r.settled_at LIMIT 40""",
            (self.chat_id,int(self.follower))).fetchall()
        count=0
        for root,kick,msg_id,raw,status,period,home,away,book_line in rows:
            state=json.loads(raw)
            if state.get("line") is None:
                state["line"]=book_line
            if not state.get("market_period"):
                state["market_period"]=period
            if state.get("status")==status:
                # State JSON is persisted ONLY after a successful edit.
                # If a crash occurred before the confirmation flag update,
                # reconcile it without a redundant message or media upload.
                if not self.follower:
                    with self.db:
                        self.db.execute("""UPDATE adaptive_results
                           SET confirmation_sent=1 WHERE root_id=? AND kickoff=?
                             AND confirmation_sent=0""",(root,kick))
                continue
            state["status"]=status
            state["flash_side"]=""
            state["flash_until"]=0
            state["flash_pulse_on"]=False
            # For 2H settlement r.score_home/away are PERIOD goals, never
            # use them to overwrite the full-match scoreboard.
            if period=="1-й тайм" and isinstance(home,int) and isinstance(away,int):
                state["score"]=[home,away]
            if self._update(root,kick,msg_id,state,now):
                if not self.follower:
                    with self.db:
                        self.db.execute("""UPDATE adaptive_results
                           SET confirmation_sent=1 WHERE root_id=? AND kickoff=?
                             AND confirmation_sent=0""",(root,kick))
                count+=1
        return count


assert len(WIDGET_HTML.encode("utf-8"))<=32768
assert len(zlib.compress(WIDGET_HTML.encode("utf-8"),9))<=4096
assert len(WIDGET_HTML_LITE.encode("utf-8"))<=32768
assert len(zlib.compress(WIDGET_HTML_LITE.encode("utf-8"),9))<=4096
