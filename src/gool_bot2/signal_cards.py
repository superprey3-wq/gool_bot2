from __future__ import annotations
import json, os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image, ImageDraw, ImageFont
from .match_context import provider_pair
from .providers.common import UA
from .providers.flashscore import FlashscoreProvider

W=1080; BG=(5,10,18); PANEL=(13,22,36); PANEL2=(19,31,49); TEXT=(247,249,252); MUTED=(151,166,188); LINE=(45,63,88); GOLD=(255,184,48); RED=(239,74,83)
THEMES={"another_goal":((82,220,118),"ЕЩЁ ГОЛ"),"over_2_5":((55,166,255),"ТОТАЛ БОЛЬШЕ 2.5"),"both_teams_to_score":((190,83,255),"ОБЕ ЗАБЬЮТ — ДА"),"goal_before_ht":((255,145,37),"ГОЛ ДО ПЕРЕРЫВА")}

def _font(s,b=False):
 for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if b else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if b else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]:
  try:return ImageFont.truetype(p,s)
  except OSError:pass
 return ImageFont.load_default()
def _fit(d,t,w,s=32,b=True):
 for z in range(s,13,-2):
  f=_font(z,b)
  if d.textbbox((0,0),str(t),font=f)[2]<=w:return f
 return _font(14,b)
def _center(d,t,y,f,c):
 b=d.textbbox((0,0),str(t),font=f);d.text(((W-(b[2]-b[0]))/2,y),str(t),font=f,fill=c)
def _save(img):
 o=BytesIO();img.convert("RGB").save(o,"PNG",optimize=True);return o.getvalue()
def _pct(v):
 try:return f"{float(v)*100:.1f}%"
 except:return "—"
def _pair(r,k,digits=0,suffix=""):
 try:a,b=provider_pair(r,k)
 except:return "—"
 if a is None or b is None:return "—"
 if digits:return f"{a:.{digits}f}{suffix} : {b:.{digits}f}{suffix}"
 return f"{int(round(a))}{suffix} : {int(round(b))}{suffix}"
def _stats(r):
 return {
  "xg":_pair(r,"xg",2),"xgot":_pair(r,"xgot",2),"shots":_pair(r,"shots"),
  "sot":_pair(r,"shots_on_target"),"inside":_pair(r,"shots_inside_box"),
  "big_chances":_pair(r,"big_chances"),"corners":_pair(r,"corners"),
  "touches_box":_pair(r,"touches_box"),"dangerous_attacks":_pair(r,"dangerous_attacks"),
 }
def stats_snapshot(r):return _stats(r)
def flashscore_meta(r):return dict((((r.get("providers") or {}).get("flashscore") or {}).get("meta") or {}))
def _asset_path():return Path(os.getenv("RUNTIME_DATA_DIR","data"))/"live"/"signal_card_assets.json"
def _analysis_path():return Path(os.getenv("SIGNAL_ANALYSIS_PATH",os.getenv("RUNTIME_DATA_DIR","data")+"/live/gool_bot2_analysis.jsonl"))
def _read_assets():
 try:
  p=_asset_path();x=json.loads(p.read_text("utf-8")) if p.exists() else {};return x if isinstance(x,dict) else {}
 except:return {}
def _remember(mid,meta,stats):
 if not mid:return
 c=_read_assets();c[mid]={**dict(c.get(mid) or {}),"flashscore_meta":meta,"stats_snapshot":stats}
 try:
  p=_asset_path();p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(".tmp");t.write_text(json.dumps(c,ensure_ascii=False),"utf-8");t.replace(p)
 except:pass
def _history(mid,head,limit=18):
 p=_analysis_path();out={}
 if not mid or not p.exists():return []
 try:
  for line in p.read_text("utf-8",errors="ignore").splitlines()[-6000:]:
   try:r=json.loads(line)
   except:continue
   if str(r.get("match_id") or "")!=mid or str(r.get("head") or "")!=head:continue
   try:m=int(r.get("minute") or 0);v=float(r.get("probability"))
   except:continue
   if 0<=v<=1:out[m]=v
 except:return []
 return sorted(out.items())[-limit:]
@lru_cache(maxsize=2048)
def _download(url):
 if not url:return None
 try:
  q=Request(url,headers={"User-Agent":UA,"Referer":"https://www.flashscore.com/"});raw=urlopen(q,timeout=6).read(700000);return Image.open(BytesIO(raw)).convert("RGBA")
 except:return None
def _logo(meta,side):
 fn=str(meta.get(f"{side}_logo_file") or "").strip()
 if fn:
  im=_download(f"https://static.flashscore.com/res/image/data/{fn}")
  if im:return im
 u=str(meta.get(f"{side}_logo_url") or "").strip()
 if u:
  im=_download(u)
  if im:return im
 u=FlashscoreProvider.team_logo_url(str(meta.get(f"{side}_team_slug") or ""),str(meta.get(f"{side}_team_id") or "")) or "";return _download(u) if u else None
def _badge(img,d,x,y,logo,name,accent):
 r=64;d.ellipse((x-r-8,y-r-8,x+r+8,y+r+8),outline=accent,width=3);d.ellipse((x-r,y-r,x+r,y+r),fill=PANEL2,outline=LINE,width=2)
 if logo:
  bb=logo.getbbox();logo=logo.crop(bb) if bb else logo;s=min(108/max(1,logo.width),108/max(1,logo.height));logo=logo.resize((max(1,int(logo.width*s)),max(1,int(logo.height*s))),Image.Resampling.LANCZOS);img.alpha_composite(logo,(x-logo.width//2,y-logo.height//2))
 else:
  ini="".join(z[:1] for z in str(name).split()[:3]).upper() or "?";f=_font(26,True);b=d.textbbox((0,0),ini,font=f);d.text((x-(b[2]-b[0])/2,y-16),ini,font=f,fill=TEXT)
def _red_card_badge(d,x,y,count):
 try:n=int(count or 0)
 except:n=0
 if n<=0:return
 w=126 if n==1 else 154
 d.rounded_rectangle((x-w//2,y,x+w//2,y+42),12,fill=(77,10,18),outline=RED,width=2)
 d.rounded_rectangle((x-w//2+12,y+9,x-w//2+28,y+33),3,fill=RED)
 txt="УДАЛЕНИЕ" if n==1 else f"УДАЛЕНИЕ ×{n}"
 d.text((x-w//2+37,y+9),txt,font=_font(13,True),fill=RED)
def _box(d,xy,title,value,sub="",accent=TEXT):
 d.rounded_rectangle(xy,18,fill=PANEL,outline=LINE,width=2);x1,y1,x2,y2=xy;d.text((x1+18,y1+13),title,font=_font(14,True),fill=MUTED);d.text((x1+18,y1+42),value,font=_fit(d,value,x2-x1-36,24,True),fill=accent)
 if sub:d.text((x1+18,y2-23),sub,font=_fit(d,sub,x2-x1-36,12,False),fill=MUTED)
def _spark(d,hist,box,accent,current):
 x1,y1,x2,y2=box;vals=hist or [(0,current)]
 if len(vals)==1:vals=[(max(0,vals[0][0]-1),vals[0][1]),vals[0]]
 ps=[v for _,v in vals];lo=max(0,min(ps)-.04);hi=min(1,max(ps)+.04)
 if hi-lo<.08:hi=min(1,lo+.08)
 pts=[]
 for i,(_,v) in enumerate(vals):pts.append((int(x1+(x2-x1)*i/max(1,len(vals)-1)),int(y2-(v-lo)/max(.001,hi-lo)*(y2-y1))))
 for j in range(3):yy=y1+(y2-y1)*j//2;d.line((x1,yy,x2,yy),fill=LINE,width=1)
 d.line(pts,fill=accent,width=4)
 for p in pts:d.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4),fill=accent)
def _model_values(head,m):
 if head in {"over_2_5","both_teams_to_score"}:return [("HT MODEL",_pct((m.get("football_data") or {}).get(head)))]
 if head=="another_goal":
  live=m.get("another_goal_live") or {};pressure=live.get("combined_pressure")
  live_value="—" if pressure is None else f"{float(pressure):.2f}x"
  return [("DIRECT",_pct((m.get("direct") or {}).get(head))),("HAZARD",_pct((m.get("hazard") or {}).get(head))),("LIVE",live_value)]
 return [("DIRECT",_pct((m.get("direct") or {}).get(head))),("HAZARD",_pct((m.get("hazard") or {}).get(head))),("РАЗНИЦА",_pct((m.get("disagreement") or {}).get(head)))]
def _goal_timing_split(match,probability,model_result):
 minute=int(match.get("minute") or 0);is_ht=bool(match.get("is_halftime"))
 try:p_any=max(0.01,min(0.99,float(probability)))
 except:return None,None
 if is_ht or minute>=46:return None,100.0*p_any
 if minute<=0:return None,100.0*p_any
 try:p_ht=float((model_result.get("trained_probability") or {}).get("goal_before_ht"))
 except:return None,100.0*p_any
 p_ht=max(0.0,min(p_any,p_ht));return 100.0*p_ht,100.0*p_any

def render_signal_card(record,head,probability,model_result,cards):
 accent,label=THEMES.get(head,THEMES["another_goal"]);match=record.get("match") or {};meta=flashscore_meta(record);stats=_stats(record);mid=str(match.get("flashscore_event_id") or "");_remember(mid,meta,stats);home=str(match.get("home") or "?");away=str(match.get("away") or "?");minute=int(match.get("minute") or 0);hs=int(match.get("home_score") or 0);aws=int(match.get("away_score") or 0);providers=len(record.get("providers") or {});timing=head=="another_goal";second_half=bool(match.get("is_halftime")) or minute>=46;H=1180
 img=Image.new("RGBA",(W,H),BG+(255,));d=ImageDraw.Draw(img)
 league=str(match.get("league") or "LIVE FOOTBALL")
 d.rounded_rectangle((24,20,1056,92),22,fill=PANEL,outline=accent,width=2);d.text((50,37),league,font=_fit(d,league,760,24,True),fill=TEXT);d.rounded_rectangle((890,31,1028,80),14,fill=(8,35,24),outline=accent,width=2);d.text((925,44),f"{minute}'",font=_font(20,True),fill=accent)
 _badge(img,d,175,215,_logo(meta,"home"),home,accent);_badge(img,d,905,215,_logo(meta,"away"),away,accent)
 d.rounded_rectangle((397,145,683,285),25,fill=(9,19,31),outline=accent,width=3);_center(d,f"{hs} : {aws}",169,_font(58,True),TEXT);_center(d,"ПЕРЕРЫВ" if match.get("is_halftime") else ("2-Й ТАЙМ" if second_half else "1-Й ТАЙМ"),239,_font(19,True),accent)
 for x,n in ((175,home),(905,away)):
  f=_fit(d,n,330,25);b=d.textbbox((0,0),n,font=f);d.text((x-(b[2]-b[0])/2,302),n,font=f,fill=TEXT)
 _red_card_badge(d,175,345,cards.get("home_red"));_red_card_badge(d,905,345,cards.get("away_red"))
 d.rounded_rectangle((45,405,1035,550),24,fill=PANEL2,outline=accent,width=2);d.text((72,430),label,font=_font(30,True),fill=accent);d.text((72,475),"СИГНАЛ",font=_font(15,True),fill=MUTED);d.text((760,425),"ГОЛ ДО КОНЦА",font=_font(14,True),fill=MUTED);d.text((760,458),f"{probability*100:.1f}%",font=_font(42,True),fill=accent)
 if timing:
  first,full=_goal_timing_split(match,probability,model_result)
  if second_half:_center(d,f"ДО КОНЦА МАТЧА {full:.0f}%" if full is not None else "ОЦЕНКА НЕДОСТУПНА",570,_font(25,True),GOLD)
  elif first is not None:d.text((95,570),f"ГОЛ В 1Т {first:.0f}%",font=_font(24,True),fill=accent);d.text((600,570),f"ДО КОНЦА {full:.0f}%",font=_font(24,True),fill=GOLD)
 vals=_model_values(head,model_result)
 for i,(k,v) in enumerate(vals):_box(d,(45+i*250,625,280+i*250,730),k,v,accent=(GOLD if k=="LIVE" else TEXT))
 _box(d,(795,625,1035,730),"ИСТОЧНИКИ",f"{providers}/3",accent=accent)
 d.rounded_rectangle((45,760,1035,1045),24,fill=PANEL,outline=LINE,width=2);d.text((70,780),"КЛЮЧЕВАЯ LIVE СТАТИСТИКА",font=_font(16,True),fill=MUTED)
 items=[("xG",stats["xg"]),("xGOT",stats["xgot"]),("УДАРЫ",stats["shots"]),("В СТВОР",stats["sot"]),("ИЗ ШТРАФНОЙ",stats["inside"]),("МОМЕНТЫ",stats["big_chances"])]
 for i,(k,v) in enumerate(items):
  row=i//3;col=i%3;x1=70+col*320;y1=820+row*98
  d.text((x1,y1),k,font=_font(14,True),fill=MUTED);d.text((x1,y1+30),v,font=_fit(d,v,270,23,True),fill=TEXT)
 d.rounded_rectangle((330,1080,750,1145),18,fill=accent);_center(d,"●  В ИГРЕ",1097,_font(24,True),BG);return _save(img)

def render_result_card(row,result,minute,home_score,away_score):
 head=str(row.get("head") or "another_goal");base,label=THEMES.get(head,THEMES["another_goal"]);won=str(result).lower()=="won";accent=base if won else RED;home=str(row.get("home") or "?");away=str(row.get("away") or "?");league=str(row.get("league") or "LIVE FOOTBALL");providers=int(row.get("provider_count") or 1);cache=_read_assets().get(str(row.get("match_id") or ""),{}) or {};meta=dict(row.get("flashscore_meta") or cache.get("flashscore_meta") or {});stats=dict(row.get("stats_snapshot") or cache.get("stats_snapshot") or {});entry=row.get("score") or [0,0];em=int(row.get("minute") or 0);p=float(row.get("probability") or 0);H=900
 img=Image.new("RGBA",(W,H),BG+(255,));d=ImageDraw.Draw(img);d.rounded_rectangle((24,20,1056,112),24,fill=PANEL,outline=accent,width=2);d.text((52,36),"GOOL 2",font=_font(36,True),fill=accent);d.text((52,78),"VERIFIED LIVE RESULT",font=_font(15,True),fill=TEXT);d.rounded_rectangle((830,38,1028,93),16,outline=accent,width=2);d.text((858,53),"RESULT",font=_font(20,True),fill=accent);_center(d,league,140,_fit(d,league,900,19,False),MUTED);_badge(img,d,180,285,_logo(meta,"home"),home,accent);_badge(img,d,900,285,_logo(meta,"away"),away,accent);d.rounded_rectangle((390,205,690,368),28,fill=(9,19,31),outline=accent,width=3);_center(d,f"{home_score} : {away_score}",244,_font(66,True),TEXT);_center(d,f"{minute}'",320,_font(25,True),accent)
 for x,n in ((180,home),(900,away)):
  f=_fit(d,n,330,29);b=d.textbbox((0,0),n,font=f);d.text((x-(b[2]-b[0])/2,395),n,font=f,fill=TEXT)
 d.rounded_rectangle((70,470,1010,640),28,fill=PANEL2,outline=accent,width=3);_center(d,"✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ",510,_fit(d,"✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ",850,42,True),accent);_center(d,f"{label} • {p*100:.1f}%",575,_font(22,True),TEXT);_box(d,(70,675,500,785),"ВХОД",f"{em}' • {int(entry[0])}:{int(entry[1])}");_box(d,(580,675,1010,785),"РЕЗУЛЬТАТ",f"{minute}' • {home_score}:{away_score}",accent=accent);_center(d,f"Удары {stats.get('shots','—')} • В створ {stats.get('sot','—')} • DATA {providers}/3",825,_font(16,True),MUTED);return _save(img)
