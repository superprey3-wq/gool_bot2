from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

from .bot_menu import MENU_KEYBOARD, analysis_text, in_game_text, report_text
from .journal import mark_in_game

HEAD_TO_CODE = {"another_goal":"AG","goal_before_ht":"FH","over_2_5":"O25","both_teams_to_score":"BTTS"}
CODE_TO_HEAD = {value:key for key,value in HEAD_TO_CODE.items()}
START_TEXT = (
    "🟢 <b>GOOL Bot 2 работает</b>\n\nАктивные системы:\n"
    "⚽ Ещё гол — при любом текущем счёте\n⏱ Гол до перерыва — только 1-й тайм при 0:0\n"
    "📈 ТБ 2.5 — пока в матче меньше 3 голов\n🤝 Обе забьют — пока обе команды ещё не забили\n\n"
    "Модели: 3/3 загружены.\nLIVE-сигналы приходят автоматически."
)

def _token()->str:
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    if token and ":" not in token:
        bot_id=os.getenv("TELEGRAM_BOT_ID","").strip()
        if bot_id.isdigit(): token=f"{bot_id}:{token}"
    return token

def _owner_chat_id()->str: return os.getenv("TELEGRAM_CHAT_ID","").strip()
def _extra_chat_ids()->set[str]:
    raw=os.getenv("TELEGRAM_EXTRA_CHAT_IDS",""); return {x for x in re.split(r"[\s,;]+",raw.strip()) if x}

def subscribers_file()->Path:
    explicit=os.getenv("TELEGRAM_SUBSCRIBERS_FILE","").strip()
    if explicit:return Path(explicit)
    runtime=os.getenv("RUNTIME_DATA_DIR","").strip()
    return Path(runtime)/"telegram_subscribers.json" if runtime else Path("telegram_subscribers.json")

def _read_saved()->set[str]:
    path=subscribers_file()
    if not path.exists():return set()
    try:
        rows=json.loads(path.read_text("utf-8")); return {str(x).strip() for x in rows if str(x).strip()} if isinstance(rows,list) else set()
    except Exception:return set()

def _write_saved(chat_ids:Iterable[str])->None:
    path=subscribers_file();path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(sorted(set(map(str,chat_ids))),ensure_ascii=False,indent=2),"utf-8")

def get_subscribers()->list[str]:
    rows=_read_saved()|_extra_chat_ids();owner=_owner_chat_id()
    if owner:rows.add(owner)
    return sorted(rows)

def subscribe(chat_id:str|int)->bool:
    chat_id=str(chat_id).strip();rows=_read_saved();before=len(rows);rows.add(chat_id);_write_saved(rows);return len(rows)>before

def unsubscribe(chat_id:str|int)->bool:
    chat_id=str(chat_id).strip();rows=_read_saved();existed=chat_id in rows;rows.discard(chat_id);_write_saved(rows);return existed

def _api_call(method:str,payload:dict[str,Any],timeout:int=15)->dict[str,Any]|None:
    token=_token()
    if not token:return None
    req=Request(f"https://api.telegram.org/bot{token}/{method}",data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"},method="POST")
    try:
        with urlopen(req,timeout=timeout) as response:
            body=json.loads(response.read().decode("utf-8"));return body if isinstance(body,dict) else None
    except Exception:return None

def signal_keyboard(match_id:str,head:str,entered:bool=False)->dict[str,Any]:
    code=HEAD_TO_CODE.get(head,"AG");text="✅ В игре" if entered else "🎯 В игре";return {"inline_keyboard":[[{"text":text,"callback_data":f"ig:{code}:{match_id}"}]]}

def send_message(chat_id:str|int,text:str,parse_mode:str="HTML",reply_markup:dict[str,Any]|None=None)->bool:
    payload:dict[str,Any]={"chat_id":str(chat_id),"text":text,"parse_mode":parse_mode,"disable_web_page_preview":True}
    if reply_markup:payload["reply_markup"]=reply_markup
    result=_api_call("sendMessage",payload);return bool(result and result.get("ok"))

def broadcast(text:str,parse_mode:str="HTML",reply_markup:dict[str,Any]|None=None)->int:
    return sum(int(send_message(chat_id,text,parse_mode=parse_mode,reply_markup=reply_markup)) for chat_id in get_subscribers())

def send_startup_status()->int:
    return broadcast("🚀 <b>GOOL Bot 2 запущен</b>\nМодели: 3/3 ✅\nLIVE collector: ✅\nSignal worker: ✅\nTelegram: ✅",reply_markup=MENU_KEYBOARD)

def edit_message_reply_markup(chat_id:str|int,message_id:int,reply_markup:dict[str,Any])->bool:
    result=_api_call("editMessageReplyMarkup",{"chat_id":str(chat_id),"message_id":int(message_id),"reply_markup":reply_markup});return bool(result and result.get("ok"))

def answer_callback_query(callback_query_id:str,text:str="")->bool:
    payload:dict[str,Any]={"callback_query_id":callback_query_id}
    if text:payload["text"]=text
    result=_api_call("answerCallbackQuery",payload);return bool(result and result.get("ok"))

def _analysis_path(journal_path:Path)->Path:
    explicit=os.getenv("SIGNAL_ANALYSIS_PATH","").strip();return Path(explicit) if explicit else journal_path.with_name("gool_bot2_analysis.jsonl")

def poll_telegram_updates(journal_path:Path,offset:int=0,timeout:int=0)->tuple[int,int]:
    result=_api_call("getUpdates",{"offset":offset,"timeout":timeout,"allowed_updates":["message","callback_query"]},timeout=max(5,timeout+5))
    if not result or not result.get("ok"):return offset,0
    changed=0;next_offset=offset
    for update in result.get("result") or []:
        next_offset=max(next_offset,int(update.get("update_id") or 0)+1);message=update.get("message") or {};raw_text=str(message.get("text") or "").strip();text=raw_text.split("@",1)[0].lower();chat_id=(message.get("chat") or {}).get("id")
        if chat_id is not None and text in {"/start","📊 отчёт","📊 отчет","🟢 в игре","🧠 анализ"}:
            subscribe(chat_id)
            if text=="/start": reply=START_TEXT
            elif text in {"📊 отчёт","📊 отчет"}: reply=report_text(journal_path)
            elif text=="🟢 в игре": reply=in_game_text(journal_path)
            else: reply=analysis_text(_analysis_path(journal_path))
            if send_message(chat_id,reply,reply_markup=MENU_KEYBOARD):changed+=1
            continue
        callback=update.get("callback_query") or {};data=str(callback.get("data") or "")
        if not data.startswith("ig:"):continue
        parts=data.split(":",2)
        if len(parts)!=3:continue
        _,code,match_id=parts;head=CODE_TO_HEAD.get(code)
        if head is None:continue
        cb_message=callback.get("message") or {};cb_chat_id=(cb_message.get("chat") or {}).get("id");message_id=cb_message.get("message_id")
        if mark_in_game(journal_path,match_id,head,chat_id=cb_chat_id):
            changed+=1
            if cb_chat_id is not None and message_id is not None:edit_message_reply_markup(cb_chat_id,int(message_id),signal_keyboard(match_id,head,entered=True))
            answer_callback_query(str(callback.get("id") or ""),"Отмечено: в игре")
        else:answer_callback_query(str(callback.get("id") or ""),"Сигнал уже отмечен или не найден")
    return next_offset,changed

def poll_in_game_callbacks(journal_path:Path,offset:int=0,timeout:int=0)->tuple[int,int]:return poll_telegram_updates(journal_path,offset=offset,timeout=timeout)
