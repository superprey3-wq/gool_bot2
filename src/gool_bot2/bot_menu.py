from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

HEAD_LABELS={"another_goal":"⚽ Ещё гол","goal_before_ht":"⏱ Гол до перерыва","over_2_5":"📈 ТБ 2.5","both_teams_to_score":"🤝 Обе забьют","two_more_goals":"🔥 Ещё +2 гола","prefilter":"🔎 Предфильтр","model":"🧠 Модель"}
ACTIVE_HEADS=("another_goal","two_more_goals")
MENU_KEYBOARD={"keyboard":[[{"text":"📊 Отчёт"},{"text":"🟢 В игре"}],[{"text":"🧠 Анализ"}]],"resize_keyboard":True,"is_persistent":True}
def _pct(w,l):
 t=w+l;return "—" if not t else f"{w/t*100:.1f}%"
def _load_rows(path):
 try:r=json.loads(path.read_text("utf-8")) if path.exists() else []
 except Exception:r=[]
 return r if isinstance(r,list) else []
def _dedupe_signals(rows):
 latest={}
 for r in rows:
  s=r.get("score") or [0,0]
  try:h,a=int(s[0] or 0),int(s[1] or 0)
  except Exception:h,a=0,0
  latest[(str(r.get("match_id") or ""),str(r.get("head") or ""),int(r.get("minute") or 0),h,a)]=r
 return list(latest.values())
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
def report_text(path):
 rows=_dedupe_signals(_load_rows(path));lines=["📊 <b>ОТЧЁТ GOOL Bot 2</b>","","<b>Активные стратегии: 2</b>"];tw=tl=tp=0
 for head in ACTIVE_HEADS:
  sel=[r for r in rows if str(r.get("head"))==head];c=Counter(str(r.get("result") or "pending").lower() for r in sel);w,l,p=c["won"],c["lost"],c["pending"];tw+=w;tl+=l;tp+=p
  note=" · обученная модель + распределение 1Т/2Т" if head=="another_goal" else " · GOOL LIVE, любой счёт до 75'"
  lines.append(f"{HEAD_LABELS[head]}: ✅ {w} · ❌ {l} · ⏳ {p} · {_pct(w,l)}{note}")
 lines += ["",f"Всего закрыто по активным стратегиям: <b>{tw+tl}</b> · проход: <b>{_pct(tw,tl)}</b>",f"Ожидают результата: <b>{tp}</b>","<i>ОЗ, ТБ2.5 и отдельный Гол 1Т отключены для новых сигналов.</i>","<i>На один матч максимум два сигнала.</i>"]
 return "\n".join(lines)
def _in_game_row(r,states):
 ss=r.get("score") or [0,0];live=states.get(str(r.get("match_id") or "")) or {};ls=live.get("score") or ss;lm=int(live.get("minute") or r.get("minute") or 0);league=str(r.get("league") or "").strip();ll=f" · {league}" if league else "";mark="✅ принято" if bool(r.get("in_game")) else "⏳ не подтверждено";src="GOOL" if str(r.get("signal_source") or "")=="gool_live_analyzer" else "MODEL";v=float(r.get("gool_signal_strength") or r.get("probability") or 0);metric=f"шанс <b>{v*100:.0f}/100</b>" if src=="GOOL" else f"P <b>{v*100:.1f}%</b>"
 return f"{r.get('home','?')} — {r.get('away','?')}{ll}\nсейчас {lm}' · {ls[0]}:{ls[1]} · {src} {metric}\n↳ сигнал: {r.get('minute',0)}' · {ss[0]}:{ss[1]} · {mark}"
def in_game_sections(journal_path:Path,analysis_path:Path|None=None)->list[str]:
 rows=_dedupe_signals(_load_rows(journal_path));states=_latest_live_states(analysis_path);pending=[r for r in rows if str(r.get("result") or "pending").lower()=="pending"]
 pending.sort(key=lambda r:str(r.get("created_at") or ""),reverse=True)
 if not pending:return ["🟢 <b>В ИГРЕ</b>\n\nАктивных и ещё не рассчитанных LIVE-сигналов сейчас нет."]
 groups=(("another_goal","⚽ <b>ЕЩЁ ГОЛ</b>"),("two_more_goals","🔥 <b>ЕЩЁ +2 ГОЛА</b>"),("goal_before_ht","⏱ <b>СТАРЫЕ: ГОЛ В 1-М ТАЙМЕ</b>"),("both_teams_to_score","🤝 <b>СТАРЫЕ: ОБЕ ЗАБЬЮТ</b>"),("over_2_5","📈 <b>СТАРЫЕ: ТБ 2.5</b>"))
 messages=[f"🟢 <b>В ИГРЕ</b>\nНезакрытых сигналов: <b>{len(pending)}</b>\nНовые сигналы работают только по стратегиям Ещё гол и Ещё +2 гола."]
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
def _short_block(reason):
 mp={"prefilter_not_candidate_but_models_still_run":"предфильтр слабый, но модель считает","model_unavailable":"модель не загрузилась","model_output_missing":"нет выхода модели","warmup_until_10":"до 10'","duplicate_pending_signal":"уже есть сигнал"}
 if reason in mp:return mp[reason]
 if reason.startswith("probability="):return "ниже порога "+reason
 if reason.startswith("gool_pressure="):return "GOOL давление ниже порога"
 if reason.startswith("model_disagreement="):return "модели расходятся"
 if reason.startswith("post_goal_cooldown_"):return "пауза после гола"
 if reason.startswith("entry_window_closed_"):return "окно входа закрыто"
 if reason.startswith("max_open=") or reason.startswith("max_entries="):return "лимит входов"
 return reason
def analysis_text(path):
 if not path.exists():return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nДанные анализа ещё не накоплены."
 latest={}
 try:
  for line in path.open("r",encoding="utf-8"):
   try:r=json.loads(line)
   except Exception:continue
   k=(str(r.get("match_id") or ""),str(r.get("head") or ""))
   if k[0] and k[1]:latest[k]=r
 except Exception:return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nНе удалось прочитать текущий анализ."
 rows=sorted(latest.values(),key=lambda r:str(r.get("captured_at") or ""),reverse=True)
 models=[r for r in rows if str(r.get("head")) in ACTIVE_HEADS];bc=Counter()
 for r in models:
  for x in r.get("blocks") or []:bc[_short_block(str(x))]+=1
 top=sorted(models,key=lambda r:float(r.get("probability") or r.get("gool_confidence") or 0),reverse=True)[:8];lines=["🧠 <b>LIVE-АНАЛИЗ</b>","Активные стратегии: <b>Ещё гол + Ещё +2 гола</b>",f"Оценок активных стратегий: <b>{len(models)}</b> · SIGNAL: <b>{sum(1 for r in models if r.get('decision')=='SIGNAL')}</b>"]
 if bc:lines += ["","<b>Что чаще всего блокирует:</b>"]+[f"• {x}: {n}" for x,n in bc.most_common(7)]
 if top:
  lines += ["","<b>Самые близкие к входу:</b>"]
  for r in top:
   s=r.get("score") or [0,0];dec="🔥 SIGNAL" if r.get("decision")=="SIGNAL" else "⏳ WAIT";bl=[_short_block(str(x)) for x in r.get("blocks") or []];bt=", ".join(bl[:2]) if bl else "нет блоков";v=float(r.get("probability") or r.get("gool_confidence") or 0);is_gool=bool(r.get("gool_live_analysis"));tag="ШАНС" if is_gool else "P";value=f"{v*100:.0f}/100" if is_gool else f"{v*100:.1f}%"
   lines.append(f"{HEAD_LABELS.get(str(r.get('head')),str(r.get('head')))} · {dec}\n{r.get('home','?')} — {r.get('away','?')} · {r.get('minute',0)}' · {s[0]}:{s[1]} · {tag} <b>{value}</b>\n↳ {bt}")
 return "\n\n".join(lines)
