from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

HEAD_LABELS={"another_goal":"⚽ Ещё гол","two_more_goals":"🔥 Ещё +2 гола","prefilter":"🔎 Предфильтр","model":"🧠 Модель"}
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
def _another_goal_period_stats(rows):
 closed=[r for r in rows if str(r.get("head"))=="another_goal" and str(r.get("result") or "pending").lower() in {"won","lost"}]
 buckets={"1Т":[0,0],"2Т":[0,0]}
 for r in closed:
  minute=int(r.get("minute") or 0);key="1Т" if minute<=45 else "2Т";result=str(r.get("result") or "").lower();buckets[key][0 if result=="won" else 1]+=1
 return buckets
def report_text(path):
 rows=_dedupe_signals(_load_rows(path));lines=["📊 <b>ОТЧЁТ GOOL Bot 2</b>",""];tw=tl=tp=0
 for head in ACTIVE_HEADS:
  sel=[r for r in rows if str(r.get("head"))==head];c=Counter(str(r.get("result") or "pending").lower() for r in sel);w,l,p=c["won"],c["lost"],c["pending"];tw+=w;tl+=l;tp+=p
  lines.append(f"{HEAD_LABELS[head]}: ✅ {w} · ❌ {l} · ⏳ {p} · <b>{_pct(w,l)}</b>")
  if head=="another_goal":
   periods=_another_goal_period_stats(rows)
   w1,l1=periods["1Т"];w2,l2=periods["2Т"]
   lines.append(f"   ↳ вход в 1Т: ✅ {w1} · ❌ {l1} · <b>{_pct(w1,l1)}</b>")
   lines.append(f"   ↳ вход во 2Т: ✅ {w2} · ❌ {l2} · <b>{_pct(w2,l2)}</b>")
 lines += ["",f"Всего закрыто: <b>{tw+tl}</b> · проход: <b>{_pct(tw,tl)}</b>",f"Ожидают результата: <b>{tp}</b>","<i>Работают только две стратегии: Ещё гол и Ещё +2 гола.</i>","<i>На один матч максимум два сигнала.</i>"]
 return "\n".join(lines)
def _in_game_row(r,states):
 ss=r.get("score") or [0,0];live=states.get(str(r.get("match_id") or "")) or {};ls=live.get("score") or ss;lm=int(live.get("minute") or r.get("minute") or 0);league=str(r.get("league") or "").strip();ll=f" · {league}" if league else "";src="GOOL" if str(r.get("signal_source") or "")=="gool_live_analyzer" else "MODEL";v=float(r.get("gool_signal_strength") or r.get("probability") or 0);metric=f"шанс <b>{v*100:.0f}/100</b>" if src=="GOOL" else f"P <b>{v*100:.1f}%</b>";period="1Т" if int(r.get("minute") or 0)<=45 else "2Т"
 return f"{r.get('home','?')} — {r.get('away','?')}{ll}\nсейчас {lm}' · {ls[0]}:{ls[1]} · {src} {metric}\n↳ вход: {r.get('minute',0)}' ({period}) · {ss[0]}:{ss[1]}"
def in_game_sections(journal_path:Path,analysis_path:Path|None=None)->list[str]:
 rows=_dedupe_signals(_load_rows(journal_path));states=_latest_live_states(analysis_path);pending=[r for r in rows if str(r.get("result") or "pending").lower()=="pending" and str(r.get("head") or "") in ACTIVE_HEADS];pending.sort(key=lambda r:str(r.get("created_at") or ""),reverse=True)
 if not pending:return ["🟢 <b>В ИГРЕ</b>\n\nАктивных сигналов Ещё гол / Ещё +2 гола сейчас нет."]
 groups=(("another_goal","⚽ <b>ЕЩЁ ГОЛ</b>"),("two_more_goals","🔥 <b>ЕЩЁ +2 ГОЛА</b>"));messages=[f"🟢 <b>В ИГРЕ</b>\nАктивных сигналов: <b>{len(pending)}</b>\nТолько две рабочие стратегии."]
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
 if reason.startswith("probability="):return "ниже порога"
 if reason.startswith("gool_pressure="):return "GOOL давление ниже порога"
 if reason.startswith("gool_strength="):return "шанс +2 ниже порога"
 if reason.startswith("model_disagreement="):return "модели расходятся"
 if reason.startswith("post_goal_cooldown_"):return "пауза после гола"
 if reason.startswith("entry_window_closed_"):return "окно входа закрыто"
 if reason.startswith("max_open=") or reason.startswith("max_entries="):return "лимит входов"
 if reason=="duplicate_pending_signal":return "уже есть сигнал"
 return reason
def analysis_text(path):
 if not path.exists():return "🧠 <b>АНАЛИЗ</b>\n\nДанные ещё не накоплены."
 latest={}
 try:
  for line in path.open("r",encoding="utf-8"):
   try:r=json.loads(line)
   except Exception:continue
   k=(str(r.get("match_id") or ""),str(r.get("head") or ""))
   if k[0] and k[1] and k[1] in ACTIVE_HEADS:latest[k]=r
 except Exception:return "🧠 <b>АНАЛИЗ</b>\n\nНе удалось прочитать текущий анализ."
 rows=sorted(latest.values(),key=lambda r:str(r.get("captured_at") or ""),reverse=True);bc=Counter()
 for r in rows:
  for x in r.get("blocks") or []:bc[_short_block(str(x))]+=1
 top=sorted(rows,key=lambda r:float(r.get("probability") or r.get("gool_confidence") or 0),reverse=True)[:10];lines=["🧠 <b>АНАЛИЗ ДВУХ СТРАТЕГИЙ</b>","⚽ Ещё гол — MODEL\n🔥 Ещё +2 гола — GOOL LIVE",f"Текущих оценок: <b>{len(rows)}</b> · готовых SIGNAL: <b>{sum(1 for r in rows if r.get('decision')=='SIGNAL')}</b>"]
 if bc:lines += ["","<b>Основные блокировки:</b>"]+[f"• {x}: {n}" for x,n in bc.most_common(6)]
 if top:
  lines += ["","<b>Ближайшие входы:</b>"]
  for r in top:
   s=r.get("score") or [0,0];dec="🔥 SIGNAL" if r.get("decision")=="SIGNAL" else "⏳ WAIT";v=float(r.get("probability") or r.get("gool_confidence") or 0);is_gool=bool(r.get("gool_live_analysis"));value=f"{v*100:.0f}/100" if is_gool else f"{v*100:.1f}%";bl=[_short_block(str(x)) for x in r.get("blocks") or []];bt=", ".join(bl[:2]) if bl else "готово"
   lines.append(f"{HEAD_LABELS.get(str(r.get('head')),str(r.get('head')))} · {dec}\n{r.get('home','?')} — {r.get('away','?')} · {r.get('minute',0)}' · {s[0]}:{s[1]} · <b>{value}</b>\n↳ {bt}")
 return "\n\n".join(lines)
