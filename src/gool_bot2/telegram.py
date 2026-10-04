from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image

from .bot_menu import MENU_KEYBOARD, analysis_text, in_game_sections, report_text, prematch_singles_sections, prematch_parlays_sections
from .journal import load_signal_journal, mark_in_game, save_signal_journal
from .providers.flashscore import FlashscoreProvider

HEAD_TO_CODE={"another_goal":"AG","goal_before_ht":"FH","over_2_5":"O25","both_teams_to_score":"BTTS","two_more_goals":"PLUS2"}
CODE_TO_HEAD={value:key for key,value in HEAD_TO_CODE.items()}
START_TEXT=(
    "🟢 <b>GOOL MULTI BOT · работает</b>\n\n"
    "⚽ Футбол — V4 PREMATCH + LIVE\n"
    "🏒 Хоккей — PREMATCH + LIVE\n"
    "🏀 Баскетбол — PREMATCH + LIVE\n\n"
    "Хоккей и баскетбол сначала работают в shadow-режиме: собирают статистику без сырых автосигналов.\n"
    "Чтобы отключить автоматические сигналы: /stop"
)
STOP_TEXT="🔕 <b>Сигналы отключены</b>\n\nЭтот чат больше не получает автоматические сигналы GOOL Bot 4.\nЧтобы включить их снова: /start"

def _token()->str:
 token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
 if token and ":" not in token:
  bot_id=os.getenv("TELEGRAM_BOT_ID","").strip()
  if bot_id.isdigit():token=f"{bot_id}:{token}"
 return token
def _owner_chat_id()->str:return os.getenv("TELEGRAM_CHAT_ID","").strip()
def _extra_chat_ids()->set[str]:
 raw=os.getenv("TELEGRAM_EXTRA_CHAT_IDS","");return {x for x in re.split(r"[\s,;]+",raw.strip()) if x}
def subscribers_file()->Path:
 explicit=os.getenv("TELEGRAM_SUBSCRIBERS_FILE","").strip()
 if explicit:return Path(explicit)
 runtime=os.getenv("RUNTIME_DATA_DIR","").strip();return Path(runtime)/"telegram_subscribers.json" if runtime else Path("telegram_subscribers.json")
def _read_saved()->set[str]:
 p=subscribers_file()
 if not p.exists():return set()
 try:
  rows=json.loads(p.read_text("utf-8"));return {str(x).strip() for x in rows if str(x).strip()} if isinstance(rows,list) else set()
 except Exception:return set()
def _write_saved(chat_ids:Iterable[str])->None:
 p=subscribers_file();p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(sorted(set(map(str,chat_ids))),ensure_ascii=False,indent=2),"utf-8")
def _stopped_file()->Path:
 p=subscribers_file();return p.with_name(p.stem+"_stopped.json")
def _read_stopped()->set[str]:
 p=_stopped_file()
 if not p.exists():return set()
 try:
  rows=json.loads(p.read_text("utf-8"));return {str(x).strip() for x in rows if str(x).strip()} if isinstance(rows,list) else set()
 except Exception:return set()
def _write_stopped(chat_ids:Iterable[str])->None:
 p=_stopped_file();p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(sorted(set(map(str,chat_ids))),ensure_ascii=False,indent=2),"utf-8")
def get_subscribers()->list[str]:
 rows=_read_saved()|_extra_chat_ids();owner=_owner_chat_id()
 if owner:rows.add(owner)
 rows-=_read_stopped()
 return sorted(rows)
def subscribe(chat_id:str|int)->bool:
 chat_id=str(chat_id).strip();rows=_read_saved();before=len(rows);rows.add(chat_id);_write_saved(rows);stopped=_read_stopped();was_stopped=chat_id in stopped;stopped.discard(chat_id);_write_stopped(stopped);return len(rows)>before or was_stopped
def unsubscribe(chat_id:str|int)->bool:
 chat_id=str(chat_id).strip();rows=_read_saved();existed=chat_id in rows;rows.discard(chat_id);_write_saved(rows);stopped=_read_stopped();already=chat_id in stopped;stopped.add(chat_id);_write_stopped(stopped);return existed or not already
def _api_call(method:str,payload:dict[str,Any],timeout:int=15)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 req=Request(f"https://api.telegram.org/bot{token}/{method}",data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"},method="POST")
 try:
  with urlopen(req,timeout=timeout) as response:
   body=json.loads(response.read().decode("utf-8"));return body if isinstance(body,dict) else None
 except Exception as exc:
  print(f"telegram_api_error method={method} error={type(exc).__name__}:{exc}",flush=True);return None
def _multipart_call(method:str,fields:dict[str,str],file_field:str,filename:str,file_bytes:bytes,content_type:str="image/png",timeout:int=25)->dict[str,Any]|None:
 token=_token()
 if not token:return None
 boundary=f"----GOOL{uuid.uuid4().hex}";body=bytearray()
 for key,value in fields.items():
  body.extend(f"--{boundary}\r\n".encode());body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode());body.extend(str(value).encode("utf-8"));body.extend(b"\r\n")
 body.extend(f"--{boundary}\r\n".encode());body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode());body.extend(f"Content-Type: {content_type}\r\n\r\n".encode());body.extend(file_bytes);body.extend(b"\r\n");body.extend(f"--{boundary}--\r\n".encode())
 req=Request(f"https://api.telegram.org/bot{token}/{method}",data=bytes(body),headers={"Content-Type":f"multipart/form-data; boundary={boundary}"},method="POST")
 try:
  with urlopen(req,timeout=timeout) as response:
   payload=json.loads(response.read().decode("utf-8"));return payload if isinstance(payload,dict) else None
 except HTTPError as exc:
  try:detail=exc.read().decode("utf-8",errors="ignore")[:500]
  except Exception:detail=""
  print(f"telegram_photo_http_error code={exc.code} detail={detail}",flush=True);return None
 except Exception as exc:
  print(f"telegram_photo_error error={type(exc).__name__}:{exc}",flush=True);return None
def signal_keyboard(match_id:str,head:str,entered:bool=False)->dict[str,Any]:
 code=HEAD_TO_CODE.get(head,"AG");text="✅ В игре" if entered else "🎯 В игре";return {"inline_keyboard":[[{"text":text,"callback_data":f"ig:{code}:{match_id}"}]]}
def send_message(chat_id:str|int,text:str,parse_mode:str="HTML",reply_markup:dict[str,Any]|None=None)->bool:
 payload={"chat_id":str(chat_id),"text":text,"parse_mode":parse_mode,"disable_web_page_preview":True}
 if reply_markup:payload["reply_markup"]=reply_markup
 result=_api_call("sendMessage",payload);return bool(result and result.get("ok"))
def broadcast(text:str,parse_mode:str="HTML",reply_markup:dict[str,Any]|None=None)->int:
 return sum(int(send_message(cid,text,parse_mode=parse_mode,reply_markup=reply_markup)) for cid in get_subscribers())
def _jpeg_retry(png:bytes)->bytes|None:
 try:
  im=Image.open(BytesIO(png)).convert("RGB");im.thumbnail((1600,1600),Image.Resampling.LANCZOS);out=BytesIO();im.save(out,"JPEG",quality=90,optimize=True);return out.getvalue()
 except Exception as exc:
  print(f"telegram_jpeg_retry_error={type(exc).__name__}:{exc}",flush=True);return None
def send_photo(chat_id:str|int,png:bytes,caption:str="",reply_markup:dict[str,Any]|None=None)->bool:
 fields={"chat_id":str(chat_id)}
 if caption:fields.update({"caption":caption,"parse_mode":"HTML"})
 if reply_markup:fields["reply_markup"]=json.dumps(reply_markup,ensure_ascii=False,separators=(",",":"))
 result=_multipart_call("sendPhoto",fields,"photo","gool-bot2.png",png)
 if result and result.get("ok"):return True
 jpg=_jpeg_retry(png)
 if jpg:
  result=_multipart_call("sendPhoto",fields,"photo","gool-bot2.jpg",jpg,content_type="image/jpeg")
  if result and result.get("ok"):return True
 return False
def broadcast_photo(png:bytes,caption:str="",reply_markup:dict[str,Any]|None=None)->int:
 sent=sum(int(send_photo(cid,png,caption=caption,reply_markup=reply_markup)) for cid in get_subscribers())
 if sent==0 and caption.startswith(("✅ <b>ЗАШЁЛ","❌ <b>НЕ ЗАШЁЛ")):
  return broadcast(caption)
 return sent
def send_startup_status()->int:return broadcast("🚀 <b>GOOL Bot 4 · V4 ACTIVE запущен</b>\nLIVE Brain V4: ✅\nГол до перерыва: ✅\nЕщё гол: ✅\n1xBet odds + VALUE: ✅\nLIVE collector: ✅\nSignal worker: ✅\nTelegram: ✅\n\n🧠 Кнопка «Анализ» — текущие матчи и решения V4.",reply_markup=MENU_KEYBOARD)
def edit_message_reply_markup(chat_id:str|int,message_id:int,reply_markup:dict[str,Any])->bool:
 r=_api_call("editMessageReplyMarkup",{"chat_id":str(chat_id),"message_id":int(message_id),"reply_markup":reply_markup});return bool(r and r.get("ok"))
def answer_callback_query(callback_query_id:str,text:str="")->bool:
 payload={"callback_query_id":callback_query_id}
 if text:payload["text"]=text
 r=_api_call("answerCallbackQuery",payload);return bool(r and r.get("ok"))
def _analysis_path(journal_path:Path)->Path:
 explicit=os.getenv("SIGNAL_ANALYSIS_PATH","").strip();return Path(explicit) if explicit else journal_path.with_name("gool_bot2_analysis.jsonl")

def _settle_minute_from_timeline(timeline:list[dict[str,Any]],entry_total:int,target_total:int)->int|None:
 for goal in sorted(timeline,key=lambda g:int(g.get("minute") or 0)):
  score=goal.get("score") or []
  try:total=int(score[0] or 0)+int(score[1] or 0)
  except Exception:continue
  if total>=target_total and total>entry_total:
   try:return int(goal.get("minute") or 0)
   except Exception:return None
 return None

def _row_age_hours(row:dict[str,Any])->float:
 raw=str(row.get("created_at") or "").strip()
 if not raw:return 999.0
 try:
  dt=datetime.fromisoformat(raw.replace("Z","+00:00"))
  if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
  return max(0.0,(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/3600.0)
 except Exception:return 999.0

def _timeline_final_score(timeline:list[dict[str,Any]],entry:list[Any])->list[int]:
 try:home=int(entry[0] or 0);away=int(entry[1] or 0)
 except Exception:home=away=0
 for goal in timeline:
  score=goal.get("score") or []
  try:
   if len(score)>=2:
    home=max(home,int(score[0] or 0));away=max(away,int(score[1] or 0))
  except Exception:continue
 return [home,away]

def _force_reconcile_pending(journal_path:Path)->int:
 """Refresh every pending match directly from Flashscore.

    A match older than the stale window cannot remain "live" forever just because
    the master feed is stuck at 90'. Stale rows are force-reconciled from the
    event timeline plus the freshest coarse score and removed from In Game.
    """
 rows=load_signal_journal(journal_path)
 pending=[r for r in rows if str(r.get("result") or "pending").lower()=="pending" and str(r.get("head") or "") in {"another_goal","two_more_goals"}]
 ids={str(r.get("match_id") or "") for r in pending if str(r.get("match_id") or "")}
 if not ids:return 0
 try:
  provider=FlashscoreProvider();states=provider.event_states(ids)
 except Exception as exc:
  print(f"force_reconcile_state_error={type(exc).__name__}:{exc}",flush=True);states={};provider=FlashscoreProvider()
 changed=0;timelines:dict[str,list[dict[str,Any]]]={}
 now=datetime.now(timezone.utc).isoformat();stale_hours=float(os.getenv("PENDING_FORCE_CLOSE_HOURS","4"))
 for row in pending:
  mid=str(row.get("match_id") or "");state=states.get(mid) or {};finished=bool(state.get("is_finished"));age=_row_age_hours(row);stale=age>=stale_hours
  # Critical fallback: even if Flashscore still reports LIVE/90', an entry that is
  # several hours old is force-checked and closed. This prevents yesterday's
  # matches from remaining pending forever.
  if not finished and not stale:
   continue
  if mid not in timelines:
   try:timelines[mid]=provider.fetch_goal_timeline(mid)
   except Exception:timelines[mid]=[]
  entry=row.get("score") or [0,0]
  try:entry_total=int(entry[0] or 0)+int(entry[1] or 0)
  except Exception:entry_total=0
  head=str(row.get("head") or "");target=entry_total+1 if head=="another_goal" else entry_total+2
  timeline=timelines.get(mid) or []
  tscore=_timeline_final_score(timeline,entry)
  state_home=int(state.get("home_score") or 0);state_away=int(state.get("away_score") or 0)
  final_home=max(state_home,tscore[0]);final_away=max(state_away,tscore[1])
  final_total=final_home+final_away;won=final_total>=target
  settled_minute=_settle_minute_from_timeline(timeline,entry_total,target) if won else None
  if settled_minute is None:settled_minute=90
  row.update({"result":"won" if won else "lost","settled_at":now,"settled_minute":settled_minute,"settled_score":[final_home,final_away],"settled_via":"telegram_force_reconcile_finished" if finished else "telegram_force_reconcile_stale_90","reconciled_age_hours":round(age,2)})
  row.pop("terminal_seen_score",None);row.pop("terminal_seen_count",None);changed+=1
 if changed:
  save_signal_journal(journal_path,rows)
  print(f"force_reconcile_pending closed={changed} checked={len(ids)} stale_hours={stale_hours:g}",flush=True)
 return changed

_MATCH_SEARCH_WAITING:set[str]=set()


def _drain_prematch_result_notifications(journal_path: Path) -> int:
    from .multi_delivery import pending_result_notifications, finalize_result_delivery
    from .v4_prematch_delivery import emit_prematch_result, emit_parlay_result
    sent_total = 0
    for row in pending_result_notifications(
        journal_path,
        origins={"prematch", "prematch_value", "prematch_parlay", "parlay"},
    ):
        origin = str(row.get("origin") or "").casefold()
        if origin not in {"prematch", "prematch_parlay", "parlay"}:
            continue
        try:
            sent = emit_prematch_result(row, {}) if origin in {"prematch", "prematch_value"} else emit_parlay_result(row)
            if finalize_result_delivery(journal_path, row, sent):
                sent_total += int(sent or 0)
        except Exception as exc:
            print(f"V4_PREMATCH_RESULT_DRAIN_ERROR {type(exc).__name__}:{exc}", flush=True)
    return sent_total


def poll_telegram_updates(journal_path:Path,offset:int=0,timeout:int=0)->tuple[int,int]:
 try:
  from .v4_prematch_settlement import reconcile_pending_prematch
  reconcile_pending_prematch(journal_path)
  _drain_prematch_result_notifications(journal_path)
 except Exception as exc:
  print(f"V4_PREMATCH_RECONCILE_ERROR {type(exc).__name__}:{exc}",flush=True)
 result=_api_call("getUpdates",{"offset":offset,"timeout":timeout,"allowed_updates":["message","callback_query"]},timeout=max(5,timeout+5))
 if not result or not result.get("ok"):return offset,0
 changed=0;next_offset=offset
 for update in result.get("result") or []:
  next_offset=max(next_offset,int(update.get("update_id") or 0)+1);message=update.get("message") or {};raw_text=str(message.get("text") or "").strip();text=raw_text.split("@",1)[0].lower();chat_id=(message.get("chat") or {}).get("id")
  if chat_id is not None and text=="/stop":
   unsubscribe(chat_id)
   if send_message(chat_id,STOP_TEXT):changed+=1
   continue
  if chat_id is not None and text in {"/start","/prematchaudit","/prematchstatus","/livecheck","/valuehunter","/multisport","/sportreport","/sportjournal","/hockeyjournal","/basketjournal","/hockeyreport","/basketreport","📊 отчёт","📊 отчет","🟢 в игре","🎟 ординары","🔗 экспрессы","🏒 хоккей","🏀 баскетбол","📒 хоккей","📒 баскет","🧠 анализ","🔎 найти матч"}:
   if text=="/start":subscribe(chat_id)
   if text=="/start":
    replies=[START_TEXT]
   elif text in {"📊 отчёт","📊 отчет"}:
    _force_reconcile_pending(journal_path);replies=[report_text(journal_path)]
   elif text=="🟢 в игре":
    _force_reconcile_pending(journal_path);replies=in_game_sections(journal_path,_analysis_path(journal_path))
   elif text=="/prematchaudit":
    from .prematch_day_audit import prematch_day_audit_text
    replies=[prematch_day_audit_text(journal_path)]
   elif text=="/prematchstatus":
    from .prematch_status import prematch_status_text
    replies=[prematch_status_text()]
   elif text=="/livecheck":
    from .live_data_check import live_data_check_text
    replies=[live_data_check_text()]
   elif text=="/valuehunter":
    from .v4_value_hunter_delivery import value_hunter_report_text
    replies=[value_hunter_report_text(journal_path)]
   elif text=="/multisport":
    from .multisport_menu import multisport_status_text
    replies=[multisport_status_text()]
   elif text=="/sportreport":
    from .multisport_menu import multisport_report_text
    replies=[multisport_report_text()]
   elif text=="/sportjournal":
    from .multisport_menu import sport_journal_text
    replies=[sport_journal_text()]
   elif text in {"/hockeyjournal","📒 хоккей"}:
    from .multisport_menu import hockey_journal_text
    replies=[hockey_journal_text()]
   elif text in {"/basketjournal","📒 баскет"}:
    from .multisport_menu import basketball_journal_text
    replies=[basketball_journal_text()]
   elif text=="/hockeyreport":
    from .multisport_menu import sport_phase_report_text
    replies=[sport_phase_report_text("hockey")]
   elif text=="/basketreport":
    from .multisport_menu import sport_phase_report_text
    replies=[sport_phase_report_text("basketball")]
   elif text=="🏒 хоккей":
    from .multisport_menu import sport_overview_text
    replies=[sport_overview_text("hockey")]
   elif text=="🏀 баскетбол":
    from .multisport_menu import sport_overview_text
    replies=[sport_overview_text("basketball")]
   elif text=="🎟 ординары":
    replies=prematch_singles_sections(journal_path)
   elif text=="🔗 экспрессы":
    replies=prematch_parlays_sections(journal_path)
   elif text=="🔎 найти матч":
    _MATCH_SEARCH_WAITING.add(str(chat_id));replies=["🔎 <b>Поиск матча GOOL V4</b>\n\nНапиши название одной команды или обеих команд.\nНапример: <code>Арсенал</code> или <code>Арсенал — Манчестер Сити</code>.\n\nИщу только среди сегодняшних матчей."]
   else:
    replies=[analysis_text(_analysis_path(journal_path))]
   for reply in replies:
    if send_message(chat_id,reply,reply_markup=MENU_KEYBOARD):changed+=1
   continue
  if chat_id is not None and str(chat_id) in _MATCH_SEARCH_WAITING and raw_text and not raw_text.startswith("/"):
   from .manual_match_analysis import analyse_match_text,find_today_matches,match_choices
   matches=find_today_matches(raw_text,limit=6)
   if not matches:
    send_message(chat_id,"❌ Сегодня матч с таким названием не найден. Попробуй написать название короче.",reply_markup=MENU_KEYBOARD);changed+=1
   elif len(matches)==1:
    _MATCH_SEARCH_WAITING.discard(str(chat_id));send_message(chat_id,analyse_match_text(matches[0]),reply_markup=MENU_KEYBOARD);changed+=1
   else:
    send_message(chat_id,f"Нашёл совпадений: <b>{len(matches)}</b>. Выбери нужный матч:",reply_markup=match_choices(matches));changed+=1
   continue
  cb=update.get("callback_query") or {};data=str(cb.get("data") or "")
  if data.startswith("ma:"):
   from .manual_match_analysis import analyse_match_text,find_today_by_id
   event_id=data.split(":",1)[1];cm=cb.get("message") or {};cid=(cm.get("chat") or {}).get("id")
   match=find_today_by_id(event_id)
   if cid is not None:
    if match is None: send_message(cid,"❌ Матч уже не найден в сегодняшнем списке.",reply_markup=MENU_KEYBOARD)
    else: send_message(cid,analyse_match_text(match),reply_markup=MENU_KEYBOARD)
    _MATCH_SEARCH_WAITING.discard(str(cid));changed+=1
   answer_callback_query(str(cb.get("id") or ""),"Запускаю V4-анализ")
   continue
  if data.startswith("v4ig:"):
   entry_id=data.split(":",1)[1]
   cm=cb.get("message") or {};cid=(cm.get("chat") or {}).get("id");mid=cm.get("message_id")
   try:
    from .v4_prematch_delivery import mark_prematch_in_game,prematch_keyboard
    marked=mark_prematch_in_game(journal_path,entry_id,chat_id=cid)
   except Exception as exc:
    print(f"V4_PREMATCH_CALLBACK_ERROR {type(exc).__name__}:{exc}",flush=True);marked=False
   if marked:
    changed+=1
    if cid is not None and mid is not None:edit_message_reply_markup(cid,int(mid),prematch_keyboard(entry_id,entered=True))
    answer_callback_query(str(cb.get("id") or ""),"Отмечено: в игре")
   else:answer_callback_query(str(cb.get("id") or ""),"Ставка уже рассчитана или не найдена")
   continue
  if not data.startswith("ig:"):continue
  parts=data.split(":",2)
  if len(parts)!=3:continue
  _,code,match_id=parts;head=CODE_TO_HEAD.get(code)
  if head is None:continue
  cm=cb.get("message") or {};cid=(cm.get("chat") or {}).get("id");mid=cm.get("message_id")
  if mark_in_game(journal_path,match_id,head,chat_id=cid):
   changed+=1
   if cid is not None and mid is not None:edit_message_reply_markup(cid,int(mid),signal_keyboard(match_id,head,entered=True))
   answer_callback_query(str(cb.get("id") or ""),"Отмечено: в игре")
  else:answer_callback_query(str(cb.get("id") or ""),"Сигнал уже отмечен или не найден")
 return next_offset,changed
def poll_in_game_callbacks(journal_path:Path,offset:int=0,timeout:int=0)->tuple[int,int]:return poll_telegram_updates(journal_path,offset=offset,timeout=timeout)
