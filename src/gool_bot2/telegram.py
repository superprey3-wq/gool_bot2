from __future__ import annotations

import json
import os
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image

from .bot_menu import MENU_KEYBOARD, analysis_text, in_game_sections, report_text
from .journal import mark_in_game

HEAD_TO_CODE={"another_goal":"AG","goal_before_ht":"FH","over_2_5":"O25","both_teams_to_score":"BTTS","two_more_goals":"PLUS2"}
CODE_TO_HEAD={value:key for key,value in HEAD_TO_CODE.items()}
START_TEXT=(
    "🟢 <b>GOOL Bot 2 работает</b>\n\nАктивные стратегии:\n"
    "⚽ Ещё гол — обученная модель до 75'\n"
    "🔥 Ещё +2 гола — GOOL LIVE до 75'\n\n"
    "LIVE-сигналы приходят автоматически.\n"
    "Чтобы отключить сигналы: /stop"
)
STOP_TEXT="🔕 <b>Сигналы отключены</b>\n\nЭтот чат больше не получает автоматические сигналы GOOL Bot 2.\nЧтобы включить их снова: /start"

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
def send_startup_status()->int:return broadcast("🚀 <b>GOOL Bot 2 запущен</b>\nАктивные стратегии: Ещё гол + Ещё +2 гола ✅\nLIVE collector: ✅\nSignal worker: ✅\nTelegram: ✅",reply_markup=MENU_KEYBOARD)
def edit_message_reply_markup(chat_id:str|int,message_id:int,reply_markup:dict[str,Any])->bool:
 r=_api_call("editMessageReplyMarkup",{"chat_id":str(chat_id),"message_id":int(message_id),"reply_markup":reply_markup});return bool(r and r.get("ok"))
def answer_callback_query(callback_query_id:str,text:str="")->bool:
 payload={"callback_query_id":callback_query_id}
 if text:payload["text"]=text
 r=_api_call("answerCallbackQuery",payload);return bool(r and r.get("ok"))
def _analysis_path(journal_path:Path)->Path:
 explicit=os.getenv("SIGNAL_ANALYSIS_PATH","").strip();return Path(explicit) if explicit else journal_path.with_name("gool_bot2_analysis.jsonl")
def poll_telegram_updates(journal_path:Path,offset:int=0,timeout:int=0)->tuple[int,int]:
 result=_api_call("getUpdates",{"offset":offset,"timeout":timeout,"allowed_updates":["message","callback_query"]},timeout=max(5,timeout+5))
 if not result or not result.get("ok"):return offset,0
 changed=0;next_offset=offset
 for update in result.get("result") or []:
  next_offset=max(next_offset,int(update.get("update_id") or 0)+1);message=update.get("message") or {};raw_text=str(message.get("text") or "").strip();text=raw_text.split("@",1)[0].lower();chat_id=(message.get("chat") or {}).get("id")
  if chat_id is not None and text=="/stop":
   unsubscribe(chat_id)
   if send_message(chat_id,STOP_TEXT):changed+=1
   continue
  if chat_id is not None and text in {"/start","📊 отчёт","📊 отчет","🟢 в игре","🧠 анализ"}:
   if text=="/start":subscribe(chat_id)
   if text=="/start":
    replies=[START_TEXT]
   elif text in {"📊 отчёт","📊 отчет"}:
    replies=[report_text(journal_path)]
   elif text=="🟢 в игре":
    replies=in_game_sections(journal_path,_analysis_path(journal_path))
   else:
    replies=[analysis_text(_analysis_path(journal_path))]
   for reply in replies:
    if send_message(chat_id,reply,reply_markup=MENU_KEYBOARD):changed+=1
   continue
  cb=update.get("callback_query") or {};data=str(cb.get("data") or "")
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
