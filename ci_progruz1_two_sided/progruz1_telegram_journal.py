"""Private Telegram commands for PROGRUZ 1 journal.

This replaces legacy /start, /status ST/PREMOVE menu only in the dedicated
runner. Telegram history cursor is persisted in the active SQLite database.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path
import urllib.parse
import urllib.request

from progruz1_journal import journal_summary,export_journal_csv


def _read(token,offset):
    query=urllib.parse.urlencode({
        "offset":int(offset),"timeout":0,"limit":30,
        "allowed_updates":json.dumps(["message"])})
    req=urllib.request.Request(
        "https://api.telegram.org/bot"+token+"/getUpdates?"+query)
    with urllib.request.urlopen(req,timeout=9) as response:
        data=json.loads(response.read(120000))
    if not data.get("ok") or not isinstance(data.get("result"),list):
        raise ValueError("Telegram updates unavailable")
    return data["result"]


def _send(token,chat_id,body):
    payload=urllib.parse.urlencode({
        "chat_id":chat_id,"text":body[:4000]}).encode("utf-8")
    req=urllib.request.Request(
        "https://api.telegram.org/bot"+token+"/sendMessage",data=payload)
    with urllib.request.urlopen(req,timeout=9) as response:
        return bool(json.loads(response.read(2000)).get("ok"))


def _send_csv(token,chat_id,filepath,
              caption="ПРОГРУЗ 1 · журнал сигналов и результатов"):
    """Send private CSV file to Telegram without third-party libraries."""
    path=Path(filepath)
    content=path.read_bytes()
    if not content or len(content)>45_000_000:
        raise ValueError("Journal CSV is empty or exceeds Telegram upload limit")
    boundary="Progruz"+secrets.token_hex(12)
    marker=boundary.encode("ascii")
    def field(name,value):
        return (b"--"+marker+b"\r\n"+
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            +str(value).encode("utf-8")+b"\r\n")
    header=(b"--"+marker+b"\r\n"+
        f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'.encode("utf-8")+
        b"Content-Type: text/csv; charset=utf-8\r\n\r\n")
    body=(field("chat_id",chat_id)+
          field("caption",caption)+
          header+content+b"\r\n--"+marker+b"--\r\n")
    req=urllib.request.Request(
        "https://api.telegram.org/bot"+token+"/sendDocument",
        data=body,headers={
            "Content-Type":"multipart/form-data; boundary="+boundary})
    with urllib.request.urlopen(req,timeout=45) as response:
        return bool(json.loads(response.read(3000)).get("ok"))


class ProgruzTelegramJournal:
    def __init__(self,db:sqlite3.Connection,token:str,chat_id:str,
                 *,subscribers=None,widgets=None):
        self.db=db
        self.token=token
        self.chat_id=str(chat_id)
        self.subscribers=subscribers
        self.widgets=widgets
        db.execute("""CREATE TABLE IF NOT EXISTS progruz1_tg_offset(
            id INTEGER PRIMARY KEY CHECK(id=1), next_update INTEGER NOT NULL)""")
        db.execute("""INSERT OR IGNORE INTO progruz1_tg_offset VALUES(1,0)""")
        db.commit()

    def poll(self,*,now:int,latest:dict|None=None):
        if not self.token or not self.chat_id:
            return 0
        offset=self.db.execute("""SELECT next_update FROM
            progruz1_tg_offset WHERE id=1""").fetchone()[0]
        updates=_read(self.token,offset)
        answered=0
        for update in updates:
            try:
                u=int(update["update_id"])
            except (KeyError,TypeError,ValueError):
                continue
            if u<offset:
                continue
            # Persist consumed update before sending so network failures do
            # not cause duplicate journal replies.
            with self.db:
                self.db.execute("""UPDATE progruz1_tg_offset SET
                    next_update=? WHERE id=1""",(u+1,))
            offset=u+1
            msg=update.get("message") or {}
            chat=msg.get("chat") or {}
            target=str(chat.get("id") or "")
            raw_text=str(msg.get("text") or "").strip()
            pieces=raw_text.split(maxsplit=1)
            cmd=(pieces[0].split("@",1)[0].lower() if pieces else "")
            argument=(pieces[1].strip() if len(pieces)>1 else "")
            if target!=self.chat_id:
                # The same existing getUpdates cursor handles both owner
                # diagnostics and subscription requests. Never run another
                # Telegram poller against the same token.
                if self.subscribers is None or not target.isdecimal() or (
                        chat.get("type") not in (None,"private")):
                    continue
                if cmd=="/start":
                    person=msg.get("from") or {}
                    name=str(person.get("first_name") or
                             chat.get("first_name") or "Пользователь")[:80]
                    status=self.subscribers.request(target,name,now)
                    if status=="NEW_PENDING":
                        if _send(self.token,target,
                            "⚽ ПРОГРУЗ 1 · Запрос отправлен владельцу. "
                            "После подтверждения сюда будут приходить живые сигналы."):
                            answered+=1
                        if _send(self.token,self.chat_id,
                            f"👤 Новый запрос: {name} · Chat ID {target}\n"
                            f"Разрешить: /approve {target}\n"
                            f"Отказать: /remove {target}"):
                            answered+=1
                    elif status=="ACTIVE":
                        if _send(self.token,target,
                            "✅ Ты подключён к сигналам ПРОГРУЗ 1. "
                            "Для отключения отправь /stop."):
                            answered+=1
                    elif status=="PENDING":
                        if _send(self.token,target,
                            "⏳ Заявка уже отправлена. Ожидай подтверждения владельца."):
                            answered+=1
                elif cmd in {"/stop","/unsubscribe"}:
                    if self.subscribers.revoke(target,now)=="REVOKED":
                        if _send(self.token,target,
                            "🔕 Подписка отключена. Чтобы подключиться снова, нажми /start."):
                            answered+=1
                elif cmd=="/status":
                    status=self.subscribers.state(target)
                    message=("✅ Подписка активна." if status=="ACTIVE"
                             else "⏳ Ожидает подтверждения." if status=="PENDING"
                             else "Для подписки нажми /start.")
                    if _send(self.token,target,message):
                        answered+=1
                # Other commands (journal, files, diagnostics) are owner-only.
                continue
            if self.subscribers is not None and cmd in {
                    "/users","/approve","/remove","/revoke"}:
                if cmd=="/users":
                    response=self.subscribers.list_text()
                elif not argument.isdecimal():
                    response=("Укажи Chat ID цифрами, например "
                              "/approve 12345678 или /remove 12345678.")
                elif cmd=="/approve":
                    state=self.subscribers.approve(argument,now)
                    response={
                        "APPROVED":f"✅ Пользователь {argument} подключён. "
                                   "Новые сигналы придут ему в личный чат.",
                        "LIMIT_REACHED":"Лимит друзей исчерпан. "
                                        "Отключи старого через /remove CHAT_ID.",
                        "NOT_PENDING":"Нет ожидающей заявки с таким Chat ID.",
                    }.get(state,"Неверный Chat ID.")
                    if state=="APPROVED":
                        if _send(self.token,argument,
                            "✅ Владелец ПРОГРУЗ 1 одобрил подписку! "
                            "Следующие сигналы будут приходить с живым табло. "
                            "Для отключения: /stop."):
                            answered+=1
                else:
                    state=self.subscribers.revoke(argument,now)
                    response=(f"🔕 Доступ {argument} отключён."
                              if state=="REVOKED"
                              else "Нет активной или ожидающей заявки с таким Chat ID.")
                if _send(self.token,self.chat_id,response):
                    answered+=1
                continue
            if cmd in {"/version","/версия","/build"}:
                response=("⚽ ПРОГРУЗ 1 · ВЕРСИЯ ПРОЦЕССА\n"
                          "🔕 Результаты: только изменение исходного "
                          "сообщения\n"
                          "🧩 Режим: EDIT_ORIGINAL_ONLY_V3_NO_REPLY")
            elif cmd in {"/widgetstatus","/widgetcheck"} and self.widgets is not None:
                response=self.widgets.delivery_status()
            elif cmd=="/widgettest" and self.widgets is not None:
                response=self.widgets.test_widget(now)
            elif cmd=="/widgetprobe" and self.widgets is not None:
                response=self.widgets.probe_widget_permission(now)
            elif (cmd in {"/journal_all","/журнал_весь","/all_results",
                          "/journal_csv","/journal_file","/journal_3",
                          "/journal_4","/journal_7","/journal_week"}
                  or (cmd in {"/journal","/журнал","журнал"} and
                      argument in {"3","4","7"})):
                # The original /journal command stays a short daily
                # summary; every full/ranged export is a proper CSV file.
                day_commands={"/journal_3":3,"/journal_4":4,
                              "/journal_7":7,"/journal_week":7}
                days=day_commands.get(cmd)
                if cmd in {"/journal","/журнал","журнал"}:
                    days=int(argument)
                name=(f"PROGRUZ1_JOURNAL_{days}D.csv" if days is not None
                      else "PROGRUZ1_JOURNAL_ALL.csv")
                filename="reports/"+name
                caption=("ПРОГРУЗ 1 · полный журнал"
                         if days is None else
                         f"ПРОГРУЗ 1 · журнал за {days} дн. (МСК)")
                try:
                    count=export_journal_csv(
                        self.db,filename,days=days,now=now)
                    if _send_csv(self.token,self.chat_id,filename,
                                 caption=f"{caption} · {count} сигналов"):
                        answered+=1
                    elif _send(self.token,self.chat_id,
                               "Не удалось отправить CSV. Попробуй повторить команду."):
                        answered+=1
                except (OSError,ValueError,TimeoutError) as exc:
                    print("PROGRUZ1_JOURNAL_EXPORT_FAILED",
                          type(exc).__name__,str(exc)[:130],flush=True)
                    if _send(self.token,self.chat_id,
                             "Файл журнала не отправлен. Проверь логи хостинга "
                             "и повтори команду."):
                        answered+=1
                continue
            elif cmd in {"/journal","/журнал","журнал","/report","/отчёт","отчёт"}:
                response=journal_summary(self.db,now=now,period="today")
            elif cmd in {"/coverage_csv","/missing_csv"}:
                from progruz1_coverage_audit import export_coverage_csv
                filename,n_flash,n_fonbet=export_coverage_csv(
                    self.db,now=now)
                if _send_csv(self.token,self.chat_id,filename,
                             caption=f"Сопоставление LIVE: Flashscore {n_flash}, "
                                     f"Fonbet {n_fonbet}"):
                    answered+=1
                continue
            elif cmd in {"/funnel","/воронка","/diagnose"}:
                from progruz1_coverage_audit import CoverageAudit,format_funnel
                response=format_funnel(
                    CoverageAudit(self.db).funnel_summary(now=now))
            elif cmd in {"/coverage","/покрытие","/missing"}:
                from progruz1_coverage_audit import CoverageAudit,format_summary
                response=format_summary(CoverageAudit(self.db).summary(now=now))
            elif cmd in {"/oddscheck","/sourcecheck","/источники"}:
                # Owner-only and nonblocking. A bounded worker compares
                # all observed LIVE fixtures with public bookmaker prices.
                from progruz1_live_odds_telegram import start
                response=start(self.token,self.chat_id)
            elif cmd in {"/oddsgeo","/flashgeo","/геокэф"}:
                # Compare the same Flashscore IDs via regional bookmaker
                # catalogues without changing production quote selection.
                from progruz1_flashscore_geo_telegram import start
                response=start(self.token,self.chat_id)
            elif cmd in {"/fonbetfirst","/fonbet_first","/fbfirst"}:
                # Full football Fonbet LIVE catalogue -> Flashscore identity.
                # This is an independent read-only diagnostic, not a switch
                # in the production GOOL signal decision flow.
                from progruz1_fonbet_first_telegram import start
                response=start(self.token,self.chat_id)
            elif cmd in {"/joint","/отбор","/selection"}:
                from progruz1_joint_audit import JointAudit,text_summary
                response=text_summary(JointAudit(self.db).summary(now=now))
            elif cmd in {"/model","/модель","/scenarios"}:
                from progruz1_shadow_audit import analyze,format_summary
                response=format_summary(analyze(self.db))
            elif cmd in {"/status","/статус","/start"}:
                info=latest or {}
                response=(
                    "⚽ ПРОГРУЗ 1 · Flashscore LIVE / котировки\n"
                    f"📡 Flashscore: {info.get('fs_live','—')} LIVE\n"
                    f"🔗 Найдено в Fonbet: "
                    f"{info.get('fs_identity_found','—')}\n"
                    f"✅ Проверенный счёт: {info.get('matched','—')}\n"
                    f"⏳ Ждут синхронизации: "
                    f"{info.get('fs_wait_score_sync','—')}\n"
                    f"💰 Fonbet: пары тоталов в каталоге: "
                    f"{info.get('fonbet_priced_catalog','—')}\n"
                    f"🌍 Flashscore Odds: с ценой в цикле: "
                    f"{(info.get('quote_sources') or {}).get('with_flashscore_quote','—')}\n"
                    f"🔁 Fonbet: резервная цена в цикле: "
                    f"{(info.get('quote_sources') or {}).get('with_fonbet_fallback','—')}\n"
                    f"🎯 1xBet напрямую: дополнительно с ценой: "
                    f"{(info.get('quote_sources') or {}).get('with_1xbet_direct','—')}\n"
                    +("⚠️ Fonbet API недоступен; Flashscore продолжает работу\n"
                      if info.get("fonbet_feed_available") is False else "")
                    + f"🎯 Цены на отобранных для проверки матчах: "
                    f"{info.get('priced','—')}\n"
                    f"⏱ Всего матчей в окне входа: "
                    f"{info.get('fs_in_window','—')}\n"
                    f"👁 Проверено в этом цикле: {info.get('sampled','—')}\n"
                    f"📊 Flashscore: сняты LIVE-статистики до поиска цены: "
                    f"{info.get('fs_preobserved','—')}\n"
                    f"🔍 Снимки матчей без нужной цены: "
                    f"{info.get('fs_preobserved_unpriced','—')}\n"
                    f"⚠️ Ошибки подготовки статистики: "
                    f"{sum((info.get('fs_preobserve_failures') or {}).values())}\n"
                    + ("🔎 Причины ошибок: "
                       + ", ".join(
                           f"{key}={count}" for key,count in
                           sorted((info.get("fs_preobserve_failures") or {}).items(),
                                  key=lambda row: -row[1])[:2])
                       + "\n" if info.get("fs_preobserve_failures") else "")
                    + f"🔬 Проанализировано в последнем цикле: "
                    f"{info.get('evaluated','—')}\n"
                    f"📤 Режим сигналов: {info.get('live_signal_mode','—')}\n"
                    f"✅ Сильный LIVE при умеренно слабой форме: "
                    f"{info.get('high_grade_low_form_overrides',0)}\n"
                    f"🛡 TWO_SIDED отложено (нет свежих атак обеих команд): "
                    f"{sum(v for k,v in (info.get('waits') or {}).items() if k.startswith('TWO_SIDED_'))}\n"
                    f"🎯 Сильные кандидаты: "
                    f"Q_SURGE {(info.get('scenario_candidates') or {}).get('QUALITY_SURGE',0)}"
                    f" · CHASE {(info.get('scenario_candidates') or {}).get('TRAILING_CHASE',0)}"
                    f" · TWO_SIDED {(info.get('scenario_candidates') or {}).get('TWO_SIDED',0)}\n"
                    f"❓ Не сопоставлены: {info.get('fs_unmatched','—')}\n"
                    "🧠 Уверенность: 0–100 баллов; не вероятность гола.\n"
                    "🔕 Расчёт: только обновление табло (v3)\n"
                    "⚙️ /version — версия запущенного процесса\n"
                    "📒 /journal — кратко за сегодня\n"
                    "📁 /journal_all — полный журнал файлом CSV\n"
                    "📁 /journal_3 · /journal_4 · /journal_week — CSV за 3/4/7 дней\n"
                    "📁 /journal_csv — CSV с моделями и статистикой\n"
                    "🧪 /model — сравнение сценариев с результатами\n"
                    "🤝 /joint — отсеянные и отправленные сигналы\n"
                    "🔎 /coverage — причины потери матчей\n"
                    "🔬 /funnel — где пропадают сигналы\n"
                    "📁 /coverage_csv — оба списка матчей для проверки\n"
                    "🔎 /oddscheck — Fonbet / Flashscore Odds / 1xBet LIVE\n"
                    "🌍 /oddsgeo — все LIVE Flashscore, 12 регионов + резервные БК\n"
                    "🔁 /fonbetfirst — весь LIVE-футбол Fonbet → Flashscore"
                    +("\n👥 /users — подписчики и заявки"
                      if self.subscribers is not None else "")
                    +("\n🖥 /widgetstatus — причины сбоев табло"
                      "\n🧪 /widgettest — пробная карточка"
                      "\n🔎 /widgetprobe — проверка доступа Telegram"
                      if self.widgets is not None else ""))
            else:
                continue
            if _send(self.token,self.chat_id,response):
                answered+=1
        return answered
