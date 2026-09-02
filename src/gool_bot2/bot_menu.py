from __future__ import annotations
import json,os
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HEAD_LABELS={"another_goal":"⚽ Ещё гол","two_more_goals":"🔥 Ещё +2 гола","both_teams_to_score":"💜 Обе забьют — Да","team_to_score":"🔵 Команда забьёт","prefilter":"🔎 Предфильтр","model":"🧠 Модель"}
MAIN_HEADS=("another_goal","two_more_goals");EXPERIMENT_HEADS=("both_teams_to_score","team_to_score");ALL_HEADS=MAIN_HEADS+EXPERIMENT_HEADS
MENU_KEYBOARD={"keyboard":[[{"text":"📊 Отчёт"},{"text":"🟢 В игре"}],[{"text":"🧠 Анализ"}]],"resize_keyboard":True,"is_persistent":True}

def _pct(w,l):
 t=w+l;return "—" if not t else f"{w/t*100:.1f}%"
def _load_rows(path):
 if path is None:return []
 try:r=json.loads(path.read_text("utf-8")) if path.exists() else []
 except Exception:r=[]
 return r if isinstance(r,list) else []
def _dedupe(rows):
 latest={}
 for r in rows:
  s=r.get("score") or [0,0]
  try:h,a=int(s[0] or 0),int(s[1] or 0)
  except Exception:h=a=0
  latest[(str(r.get("match_id") or ""),str(r.get("head") or ""),int(r.get("minute") or 0),h,a)]=r
 return list(latest.values())
def _half(m):
 try:return "1Т" if int(m or 0)<=45 else "2Т"
 except Exception:return "?"
def _parse_dt(v):
 try:
  dt=datetime.fromisoformat(str(v or "").replace("Z","+00:00"));return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
 except Exception:return None
def _tz():
 try:return ZoneInfo(os.getenv("REPORT_TIMEZONE","Europe/Moscow"))
 except Exception:return timezone.utc
def _experiment_journal(main_path):
 env=os.getenv("SHADOW_MARKET_JOURNAL","").strip()
 return Path(env) if env else main_path.with_name("gool_bot2_shadow_markets.json")
def _experiment_analysis(main_path):
 env=os.getenv("SHADOW_MARKET_ANALYSIS","").strip()
 return Path(env) if env else main_path.with_name("gool_bot2_shadow_analysis.jsonl")
def _stats(rows):
 out=[];tw=tl=tp=0
 for head in ALL_HEADS:
  c=Counter(str(r.get("result") or "pending").lower() for r in rows if str(r.get("head") or "")==head);w,l,p=c["won"],c["lost"],c["pending"];tw+=w;tl+=l;tp+=p
  out.append(f"{HEAD_LABELS[head]}: ✅ {w} · ❌ {l} · ⏳ {p} · <b>{_pct(w,l)}</b>")
 out += [f"Всего закрыто: <b>{tw+tl}</b> · проход: <b>{_pct(tw,tl)}</b>",f"Ожидают результата: <b>{tp}</b>"]
 return out

def report_text(path:Path,experiment_path:Path|None=None):
 experiment_path=experiment_path or _experiment_journal(path);rows=[r for r in _dedupe(_load_rows(path)) if str(r.get("head") or "") in MAIN_HEADS];rows += [r for r in _dedupe(_load_rows(experiment_path)) if str(r.get("head") or "") in EXPERIMENT_HEADS]
 tz=_tz();today=datetime.now(tz).date();today_rows=[]
 for r in rows:
  dt=_parse_dt(r.get("created_at") or r.get("captured_at"))
  if dt and dt.astimezone(tz).date()==today:today_rows.append(r)
 lines=["📊 <b>ОТЧЁТ GOOL Bot 2</b>","",f"📅 <b>СЕГОДНЯ · {today.strftime('%d.%m.%Y')}</b>",*_stats(today_rows),"","────────────","","📚 <b>ОБЩИЙ ЗА ВСЁ ВРЕМЯ</b>",*_stats(rows),"","<i>Все 4 стратегии считаются отдельно; сверху — только сегодняшний день.</i>"]
 return "\n".join(lines)

def _latest_live_states(path):
 if path is None or not path.exists():return {}
 latest={}
 try:
  for line in path.open("r",encoding="utf-8"):
   try:r=json.loads(line)
   except Exception:continue
   mid=str(r.get("match_id") or "")
   if mid and (mid not in latest or str(r.get("captured_at") or "")>=str(latest[mid].get("captured_at") or "")):latest[mid]=r
 except Exception:return {}
 return latest
def _in_game_row(r,states):
 ss=r.get("score") or [0,0];live=states.get(str(r.get("match_id") or "")) or {};ls=live.get("score") or ss;lm=int(live.get("minute") or r.get("minute") or 0);league=str(r.get("league") or "").strip();ll=f" · {league}" if league else "";src="GOOL" if str(r.get("signal_source") or "")=="gool_live_analyzer" else "MODEL";v=float(r.get("gool_signal_strength") or r.get("probability") or 0);metric=f"шанс <b>{v*100:.0f}/100</b>" if src=="GOOL" else f"P <b>{v*100:.1f}%</b>"
 return f"{r.get('home','?')} — {r.get('away','?')}{ll}\nсейчас {lm}' ({_half(lm)}) · {ls[0]}:{ls[1]} · {src} {metric}\n↳ вход: {r.get('minute',0)}' ({_half(r.get('minute'))}) · {ss[0]}:{ss[1]}"
def in_game_sections(journal_path:Path,analysis_path:Path|None=None)->list[str]:
 rows=_dedupe(_load_rows(journal_path));states=_latest_live_states(analysis_path);pending=[r for r in rows if str(r.get("result") or "pending").lower()=="pending" and str(r.get("head") or "") in MAIN_HEADS];pending.sort(key=lambda r:str(r.get("created_at") or ""),reverse=True)
 if not pending:return ["🟢 <b>В ИГРЕ</b>\n\nАктивных сигналов Ещё гол / Ещё +2 гола сейчас нет."]
 groups=(("another_goal","⚽ <b>ЕЩЁ ГОЛ</b>"),("two_more_goals","🔥 <b>ЕЩЁ +2 ГОЛА</b>"));messages=[f"🟢 <b>В ИГРЕ</b>\nАктивных сигналов: <b>{len(pending)}</b>"]
 for head,title in groups:
  sel=[r for r in pending if str(r.get("head") or "")==head]
  if not sel:continue
  parts=[f"{title} · <b>{len(sel)}</b>"]+[f"<b>{i}.</b> {_in_game_row(r,states)}" for i,r in enumerate(sel,1)];chunk=parts[0]
  for part in parts[1:]:
   candidate=chunk+"\n\n"+part
   if len(candidate)>3800:messages.append(chunk);chunk=title+" · продолжение\n\n"+part
   else:chunk=candidate
  messages.append(chunk)
 return messages
def in_game_text(journal_path:Path,analysis_path:Path|None=None)->str:return in_game_sections(journal_path,analysis_path)[0]

def _short_block(x):
 if x.startswith("probability="):return "ниже порога модели"
 if x.startswith("gool_pressure="):return "GOOL давление ниже порога"
 if x.startswith("gool_strength="):return "шанс +2 ниже порога"
 if x.startswith("model_disagreement="):return "модели расходятся"
 if x.startswith("post_goal_cooldown_"):return "пауза после гола"
 if x.startswith("entry_window_closed_") or x=="window_closed":return "окно входа закрыто"
 if x.startswith("max_open=") or x.startswith("max_entries="):return "лимит входов"
 if x=="duplicate_pending_signal":return "уже есть сигнал"
 if x=="history":return "PREMATCH: мало истории"
 if x=="avg_goals":return "PREMATCH: низкий средний тотал"
 if x=="too_many_0_1_goal_games":return "PREMATCH: много матчей 0–1 гол"
 if x=="prematch_score":return "PREMATCH: общий балл ниже порога"
 if x.startswith("evidence="):return "LIVE: мало доступных показателей"
 if x.startswith("cum="):return "LIVE: общее давление ниже порога"
 if x.startswith("5m="):return "LIVE: слабые последние 5 минут"
 if x.startswith("10m="):return "LIVE: слабые последние 10 минут"
 if x=="no_recent_threat" or x.endswith(":side_no_recent_threat") or x=="side_no_recent_threat":return "LIVE: нет свежей угрозы"
 if x=="no_quality_threat" or x.endswith(":side_no_quality_threat") or x=="side_no_quality_threat":return "LIVE: нет качественной угрозы"
 if x=="no_team_passed":return "ни одна команда не прошла фильтр"
 if x.endswith(":side_pressure_low") or x=="side_pressure_low":return "давление команды ниже порога"
 if x.endswith(":side_evidence_low") or x=="side_evidence_low":return "мало статистики по команде"
 if x.endswith(":side_prematch_scoring_profile_low") or x=="side_prematch_scoring_profile_low":return "PREMATCH: команда редко забивает"
 if x=="warmup":return "ещё идёт LIVE-прогрев"
 if x=="btts_already_won":return "обе уже забили"
 return x
def _ag_details(r):
 if str(r.get("head") or "")!="another_goal":return []
 d=((r.get("gool_analyzer") or {}).get("details") or {});raw=[str(x) for x in (d.get("prematch") or {}).get("blocks") or []]+[str(x) for x in (d.get("live") or {}).get("blocks") or []];out=[]
 for x in raw:
  y=_short_block(x)
  if y not in out:out.append(y)
 return out
def _row_blocks(r):
 base=[_short_block(str(x)) for x in r.get("blocks") or []];details=_ag_details(r)
 if "gool_analyzer_rejected" in base and details:base=[x for x in base if x!="gool_analyzer_rejected"]+details
 out=[]
 for x in base:
  if x not in out:out.append(x)
 return out
def _fresh(r,now):
 dt=_parse_dt(r.get("captured_at"));max_age=float(os.getenv("ANALYSIS_ONLINE_MAX_AGE_MINUTES","5"));minute=int(r.get("minute") or 0)
 return bool(dt and -1 <= (now-dt.astimezone(timezone.utc)).total_seconds()/60 <= max_age and 0<minute<=75)
def _read_analysis(path,heads,now):
 if path is None or not path.exists():return []
 latest={}
 try:
  for line in path.open("r",encoding="utf-8"):
   try:r=json.loads(line)
   except Exception:continue
   head=str(r.get("head") or "");mid=str(r.get("match_id") or "")
   if not mid or head not in heads or not _fresh(r,now):continue
   k=(mid,head)
   if k not in latest or str(r.get("captured_at") or "")>=str(latest[k].get("captured_at") or ""):latest[k]=r
 except Exception:return []
 return list(latest.values())

def analysis_text(path:Path,experiment_path:Path|None=None):
 experiment_path=experiment_path or _experiment_analysis(path);now=datetime.now(timezone.utc);rows=_read_analysis(path,MAIN_HEADS,now)+_read_analysis(experiment_path,EXPERIMENT_HEADS,now);rows.sort(key=lambda r:str(r.get("captured_at") or ""),reverse=True)
 if not rows:return "🧠 <b>АНАЛИЗ ОНЛАЙН</b>\n\nСейчас нет свежих онлайн-матчей в рабочем окне до 75'."
 bc=Counter();agc=Counter()
 for r in rows:
  for x in _row_blocks(r):bc[x]+=1
  if str(r.get("head") or "")=="another_goal":
   for x in _ag_details(r):agc[x]+=1
 def val(r):return float(r.get("probability") or r.get("gool_confidence") or r.get("confidence_score") or 0)
 lines=["🧠 <b>АНАЛИЗ ОНЛАЙН · 4 СТРАТЕГИИ</b>","⚽ Ещё гол — MODEL + PREMATCH + LIVE\n🔥 Ещё +2 гола — GOOL LIVE\n💜 Обе забьют — Да\n🔵 Команда забьёт",f"Онлайн матчей: <b>{len({str(r.get('match_id') or '') for r in rows})}</b> · текущих оценок: <b>{len(rows)}</b> · SIGNAL: <b>{sum(1 for r in rows if str(r.get('decision') or '')=='SIGNAL')}</b>"]
 if bc:lines += ["","<b>Основные блокировки сейчас:</b>"]+[f"• {x}: {n}" for x,n in bc.most_common(8)]
 if agc:lines += ["","<b>Почему блокируется ⚽ Ещё гол:</b>"]+[f"• {x}: {n}" for x,n in agc.most_common(6)]
 lines += ["","<b>Ближайшие входы онлайн:</b>"]
 for r in sorted(rows,key=val,reverse=True)[:12]:
  s=r.get("score") or [0,0];head=str(r.get("head") or "");dec="🔥 SIGNAL" if str(r.get("decision") or "")=="SIGNAL" else "⏳ WAIT";v=val(r);value=f"{v*100:.1f}%" if head=="another_goal" else f"{v*100:.0f}/100";bl=_row_blocks(r);bt=", ".join(bl[:3]) if bl else "готово";team=f" · {r.get('team')}" if head=="team_to_score" and r.get("team") else ""
  lines.append(f"{HEAD_LABELS.get(head,head)} · {dec}{team}\n{r.get('home','?')} — {r.get('away','?')} · {r.get('minute',0)}' ({_half(r.get('minute'))}) · {s[0]}:{s[1]} · <b>{value}</b>\n↳ {bt}")
 return "\n\n".join(lines)
