from __future__ import annotations
import json, os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from .match_context import provider_pair
from .providers.common import UA
from .providers.flashscore import FlashscoreProvider

W=1080; TEXT=(248,250,252); MUTED=(157,174,192); DARK=(2,7,13); RED=(241,72,82); GOLD=(255,194,55)
THEMES={
"another_goal":{"accent":(55,224,79),"deep":(0,48,21),"label":"ЕЩЁ ГОЛ"},
"over_2_5":{"accent":(39,151,255),"deep":(0,31,73),"label":"ТОТАЛ БОЛЬШЕ 2.5"},
"both_teams_to_score":{"accent":(181,65,255),"deep":(57,5,82),"label":"ОБЕ ЗАБЬЮТ — ДА"},
"goal_before_ht":{"accent":(255,132,26),"deep":(82,31,0),"label":"ГОЛ ДО ПЕРЕРЫВА"}}
HEAD_LABELS={k:v["label"] for k,v in THEMES.items()}

def _font(n,b=False):
 for p in [("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if b else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if b else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")]:
  try:return ImageFont.truetype(p,n)
  except OSError:pass
 return ImageFont.load_default()
def _fit(d,s,w,n=34,b=True):
 s=str(s or "")
 for z in range(n,11,-2):
  f=_font(z,b)
  if d.textbbox((0,0),s,font=f)[2]<=w:return f
 return _font(12,b)
def _center(d,s,y,f,c):
 b=d.textbbox((0,0),str(s),font=f);d.text(((W-(b[2]-b[0]))/2,y),str(s),font=f,fill=c)
def _save(im):
 o=BytesIO();im.convert("RGB").save(o,"PNG",optimize=True);return o.getvalue()
def _pct(v):
 try:return f"{float(v)*100:.1f}%"
 except:return "—"
def _pair(p,d=0,s=""):
 a,b=p
 if a is None or b is None:return "—"
 return f"{a:.{d}f}{s} : {b:.{d}f}{s}" if d else f"{int(round(a))}{s} : {int(round(b))}{s}"
def _stats(r):return {"xg":_pair(provider_pair(r,"xg"),2),"shots":_pair(provider_pair(r,"shots")),"sot":_pair(provider_pair(r,"shots_on_target")),"corners":_pair(provider_pair(r,"corners")),"possession":_pair(provider_pair(r,"possession"),0,"%"),"big_chances":_pair(provider_pair(r,"big_chances"))}
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
 c=_read_assets();old=dict(c.get(mid) or {});old.update({"flashscore_meta":meta,"stats_snapshot":stats});c[mid]=old
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
   if str(r.get("match_id") or "")!=mid or str(r.get("head") or "")!=head or r.get("probability") is None:continue
   try:m=int(r.get("minute") or 0);v=float(r["probability"])
   except:continue
   if 0<=v<=1:out[m]=v
 except:return []
 return sorted(out.items())[-limit:]
@lru_cache(maxsize=2048)
def _download(url):
 if not url:return None
 try:
  q=Request(url,headers={"User-Agent":UA,"Referer":"https://www.flashscore.com/"});raw=urlopen(q,timeout=7).read(700000);im=Image.open(BytesIO(raw)).convert("RGBA");im.thumbnail((190,190),Image.Resampling.LANCZOS);return im.copy()
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

def _background(h,accent,deep):
 im=Image.new("RGBA",(W,h),DARK+(255,));ov=Image.new("RGBA",(W,h),(0,0,0,0));d=ImageDraw.Draw(ov)
 # cinematic stadium glow
 for r,a in [(560,30),(430,38),(300,45)]:d.ellipse((W//2-r,100-r//3,W//2+r,100+r//2),fill=accent+(a,))
 for x in range(-200,1300,80):d.line((540,360,x,650),fill=(70,110,130,30),width=1)
 d.arc((30,210,1050,620),190,350,fill=(110,160,180,45),width=3);d.arc((110,270,970,640),190,350,fill=(90,140,160,35),width=2)
 for x in range(70,1040,90):d.ellipse((x,305,x+5,310),fill=accent+(210,))
 ov=ov.filter(ImageFilter.GaussianBlur(5));im=Image.alpha_composite(im,ov)
 # top-to-bottom premium tint
 tint=Image.new("RGBA",(W,h),(0,0,0,0));td=ImageDraw.Draw(tint)
 td.rectangle((0,0,W,180),fill=deep+(105,));td.rectangle((0,500,W,h),fill=(1,6,12,115));return Image.alpha_composite(im,tint)
def _panel(d,box,accent=None,alpha=230,r=22):d.rounded_rectangle(box,r,fill=(4,13,22,alpha),outline=(accent+(180,) if accent else (45,66,86,220)),width=2)
def _header(d,theme,league,round_name,providers,result=False):
 a=theme["accent"];_panel(d,(24,20,1056,126),a,245,26)
 d.rounded_rectangle((43,38,184,105),18,fill=(8,24,31,255),outline=a,width=2);f=_fit(d,"GOOL",110,27);d.text((64,54),"GOOL",font=f,fill=TEXT);d.text((145,57),"2",font=_font(19,True),fill=a)
 d.text((215,35),theme["label"],font=_fit(d,theme["label"],560,37),fill=TEXT);d.text((218,82),"РЕЗУЛЬТАТ" if result else "LIVE PREDICTION",font=_font(14,True),fill=a)
 d.rounded_rectangle((894,42,1028,82),15,fill=a);txt="RESULT" if result else "LIVE";b=d.textbbox((0,0),txt,font=_font(17,True));d.text((961-(b[2]-b[0])/2,51),txt,font=_font(17,True),fill=DARK)
 d.text((908,91),f"DATA {providers}/3",font=_font(13,True),fill=MUTED)
 comp=league+(f"  •  {round_name}" if round_name else "");_center(d,comp,145,_fit(d,comp,920,19),TEXT)
def _paste_logo(im,d,logo,name,cx,cy,a):
 d.ellipse((cx-83,cy-83,cx+83,cy+83),fill=(5,15,23,220),outline=a,width=2)
 if logo:
  b=logo.getbbox();logo=logo.crop(b) if b else logo;s=min(140/max(1,logo.width),140/max(1,logo.height));logo=logo.resize((max(1,int(logo.width*s)),max(1,int(logo.height*s))),Image.Resampling.LANCZOS);im.alpha_composite(logo,(cx-logo.width//2,cy-logo.height//2));return
 ini="".join(x[:1] for x in str(name).replace("-"," ").split()[:3]).upper() or "?";f=_font(30,True);b=d.textbbox((0,0),ini,font=f);d.text((cx-(b[2]-b[0])/2,cy-20),ini,font=f,fill=TEXT)
def _team(d,name,cx,y):
 f=_fit(d,name,330,30);b=d.textbbox((0,0),name,font=f);d.text((cx-(b[2]-b[0])/2,y),name,font=f,fill=TEXT)
def _spark(d,hist,box,a,current):
 x1,y1,x2,y2=box
 for i in range(3):yy=y1+(y2-y1)*i//2;d.line((x1,yy,x2,yy),fill=(35,57,73),width=1)
 vals=hist or [(0,current)];vals=([(max(0,vals[0][0]-1),vals[0][1]),vals[0]] if len(vals)==1 else vals);ps=[v for _,v in vals];lo=max(0,min(ps)-.05);hi=min(1,max(ps)+.05)
 if hi-lo<.1:hi=min(1,lo+.1)
 pts=[]
 for i,(_,v) in enumerate(vals):pts.append((int(x1+(x2-x1)*i/max(1,len(vals)-1)),int(y2-(v-lo)/max(.001,hi-lo)*(y2-y1))))
 d.line(pts,fill=a,width=5)
 for p in pts:d.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4),fill=a)
 d.text((x1,y2+10),f"{vals[0][0]}'",font=_font(12,True),fill=MUTED);d.text((x2-34,y2+10),f"{vals[-1][0]}'",font=_font(12,True),fill=MUTED)
def _models(d,head,p,m,a,hist):
 _panel(d,(30,590,1050,790),a,240,24);d.text((55,613),"MODEL CONFIDENCE",font=_font(13,True),fill=MUTED);d.text((55,641),f"{p*100:.1f}%",font=_font(54,True),fill=a)
 if head in {"over_2_5","both_teams_to_score"}:
  vals=[("HT MODEL",_pct((m.get("football_data") or {}).get(head))),("MARKET","O 2.5" if head=="over_2_5" else "BTTS YES")]
 else:vals=[("DIRECT",_pct((m.get("direct") or {}).get(head))),("HAZARD",_pct((m.get("hazard") or {}).get(head))),("Δ MODELS",_pct((m.get("disagreement") or {}).get(head)))]
 x=285
 for lab,val in vals:d.text((x,620),lab,font=_font(12,True),fill=MUTED);d.text((x,651),val,font=_font(25,True),fill=(GOLD if lab.startswith("Δ") else TEXT));x+=175
 d.text((760,613),"PROBABILITY TREND",font=_font(12,True),fill=MUTED);_spark(d,hist,(760,653,1015,716),a,p)
 d.rounded_rectangle((55,741,700,755),7,fill=(28,47,61));d.rounded_rectangle((55,741,55+int(645*max(0,min(1,p))),755),7,fill=a)
def _stat_panel(d,stats,cards,providers,a):
 _panel(d,(30,815,1050,965),None,245,22);items=[("xG",stats.get("xg","—")),("УДАРЫ",stats.get("shots","—")),("В СТВОР",stats.get("sot","—")),("УГЛОВЫЕ",stats.get("corners","—")),("ВЛАДЕНИЕ",stats.get("possession","—")),("МОМЕНТЫ",stats.get("big_chances","—"))]
 for i,(lab,val) in enumerate(items):x=52+i*166;d.text((x,840),lab,font=_font(12,True),fill=MUTED);d.text((x,870),val,font=_fit(d,val,145,21),fill=TEXT)
 d.line((50,916,1030,916),fill=(31,51,67),width=1);hy,ay=cards.get("home_yellow"),cards.get("away_yellow");hr,ar=cards.get("home_red"),cards.get("away_red");ys="—" if hy is None or ay is None else f"{hy}:{ay}";rs="—" if hr is None or ar is None else f"{hr}:{ar}";d.text((52,930),f"КАРТОЧКИ   🟨 {ys}   🟥 {rs}",font=_font(14,True),fill=MUTED);d.text((900,930),f"{providers}/3",font=_font(15,True),fill=a)

def render_signal_card(record,head,probability,model_result,cards):
 th=THEMES.get(head,THEMES["another_goal"]);a,deep=th["accent"],th["deep"];match=record.get("match") or {};meta=flashscore_meta(record);stats=_stats(record);mid=str(match.get("flashscore_event_id") or "");_remember(mid,meta,stats)
 home=str(match.get("home") or "?");away=str(match.get("away") or "?");league=str(match.get("league") or "LIVE FOOTBALL");rnd=str(meta.get("round") or "");minute=int(match.get("minute") or 0);hs=int(match.get("home_score") or 0);aws=int(match.get("away_score") or 0);providers=len(record.get("providers") or {})
 im=_background(1060,a,deep);d=ImageDraw.Draw(im);d.rounded_rectangle((8,8,1072,1052),30,outline=a,width=3);_header(d,th,league,rnd,providers)
 # hero match stage
 _paste_logo(im,d,_logo(meta,"home"),home,190,335,a);_paste_logo(im,d,_logo(meta,"away"),away,890,335,a);_team(d,home,190,442);_team(d,away,890,442)
 d.rounded_rectangle((470,205,610,252),15,fill=a);_center(d,"HT" if match.get("is_halftime") else f"{minute}'",214,_font(23,True),DARK);_center(d,f"{hs}  :  {aws}",275,_font(86,True),TEXT);_center(d,"1-Й ТАЙМ" if minute<=45 else "2-Й ТАЙМ",385,_font(16,True),MUTED)
 d.line((370,455,710,455),fill=a,width=2);_center(d,th["label"],478,_font(19,True),a)
 _models(d,head,probability,model_result,a,_history(mid,head));_stat_panel(d,stats,cards,providers,a);d.rounded_rectangle((380,988,700,1038),16,fill=a);_center(d,"●  В ИГРЕ",998,_font(23,True),DARK);return _save(im)

def render_result_card(row,result,minute,home_score,away_score):
 head=str(row.get("head") or "another_goal");th=THEMES.get(head,THEMES["another_goal"]);won=str(result).lower()=="won";a=th["accent"] if won else RED;deep=th["deep"] if won else (62,8,14);home=str(row.get("home") or "?");away=str(row.get("away") or "?");league=str(row.get("league") or "LIVE FOOTBALL");providers=int(row.get("provider_count") or 1);cache=_read_assets().get(str(row.get("match_id") or ""),{}) or {};meta=dict(row.get("flashscore_meta") or cache.get("flashscore_meta") or {});stats=dict(row.get("stats_snapshot") or cache.get("stats_snapshot") or {});entry=row.get("score") or [0,0];em=int(row.get("minute") or 0);p=float(row.get("probability") or 0)
 im=_background(960,a,deep);d=ImageDraw.Draw(im);d.rounded_rectangle((8,8,1072,952),30,outline=a,width=3);_header(d,{**th,"accent":a},league,str(meta.get("round") or ""),providers,True);_paste_logo(im,d,_logo(meta,"home"),home,190,330,a);_paste_logo(im,d,_logo(meta,"away"),away,890,330,a);_team(d,home,190,438);_team(d,away,890,438);_center(d,f"{home_score}  :  {away_score}",270,_font(82,True),TEXT);_center(d,f"{minute}'  •  FINAL STATUS",380,_font(16,True),MUTED)
 _panel(d,(85,500,995,675),a,245,28);_center(d,"✓  СИГНАЛ ЗАШЁЛ" if won else "✕  СИГНАЛ НЕ ЗАШЁЛ",530,_font(43,True),a);_center(d,f"{th['label']}  •  ВЕРОЯТНОСТЬ {p*100:.1f}%",594,_font(20,True),TEXT)
 _panel(d,(85,710,995,840),None,245,22);d.text((120,735),"ВХОД",font=_font(13,True),fill=MUTED);d.text((120,768),f"{em}'  •  {int(entry[0])}:{int(entry[1])}",font=_font(28,True),fill=TEXT);d.text((585,735),"РЕЗУЛЬТАТ",font=_font(13,True),fill=MUTED);d.text((585,768),f"{minute}'  •  {home_score}:{away_score}",font=_font(28,True),fill=a);line=f"Удары {stats.get('shots','—')}   •   В створ {stats.get('sot','—')}   •   Угловые {stats.get('corners','—')}";_center(d,line,815,_fit(d,line,800,15),MUTED)
 d.rounded_rectangle((260,870,820,925),17,fill=a);_center(d,"GOOL 2  •  VERIFIED RESULT",884,_font(21,True),DARK);return _save(im)
