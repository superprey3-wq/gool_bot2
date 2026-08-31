from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

HEAD_LABELS = {
    "another_goal": "⚽ Ещё гол",
    "goal_before_ht": "⏱ Гол до перерыва",
    "over_2_5": "📈 ТБ 2.5",
    "both_teams_to_score": "🤝 Обе забьют",
    "prefilter": "🔎 Предфильтр",
    "model": "🧠 Модель",
}

MENU_KEYBOARD: dict[str, Any] = {
    "keyboard": [[{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}], [{"text": "🧠 Анализ"}]],
    "resize_keyboard": True, "is_persistent": True,
}

def _pct(won:int,lost:int)->str:
    total=won+lost; return "—" if not total else f"{won/total*100:.1f}%"
def _load_rows(path:Path)->list[dict[str,Any]]:
    try: rows=json.loads(path.read_text("utf-8")) if path.exists() else []
    except Exception: rows=[]
    return rows if isinstance(rows,list) else []
def _dedupe_signals(rows):
    latest={}
    for row in rows:
        score=row.get("score") or [0,0]
        try: hs,aws=int(score[0] or 0),int(score[1] or 0)
        except Exception: hs,aws=0,0
        latest[(str(row.get("match_id") or ""),str(row.get("head") or ""),int(row.get("minute") or 0),hs,aws)]=row
    return list(latest.values())
def _latest_live_states(path):
    if path is None or not path.exists(): return {}
    latest={}
    try:
        with path.open("r",encoding="utf-8") as h:
            for line in h:
                try:r=json.loads(line)
                except Exception:continue
                mid=str(r.get("match_id") or "")
                if mid and (mid not in latest or str(r.get("captured_at") or "")>=str(latest[mid].get("captured_at") or "")):latest[mid]=r
    except Exception:return {}
    return latest
def _already_resolved_from_live(signal,live):
    if not live:return False
    try:
        score=live.get("score") or [0,0];home,away=int(score[0] or 0),int(score[1] or 0);minute=int(live.get("minute") or 0);entry=signal.get("score") or [0,0];eh,ea=int(entry[0] or 0),int(entry[1] or 0)
    except Exception:return False
    head=str(signal.get("head") or "");total=home+away;et=eh+ea
    if head=="another_goal":return total>et or minute>=90
    if head=="goal_before_ht":return total>et or minute>45
    if head=="over_2_5":return total>=3 or minute>=90
    if head=="both_teams_to_score":return (home>0 and away>0) or minute>=90
    return False

def report_text(journal_path:Path)->str:
    rows=_dedupe_signals(_load_rows(journal_path));lines=["📊 <b>ОТЧЁТ GOOL Bot 2</b>",""];tw=tl=tp=0
    for head in ("another_goal","goal_before_ht","over_2_5","both_teams_to_score"):
        selected=[r for r in rows if str(r.get("head"))==head];c=Counter(str(r.get("result") or "pending").lower() for r in selected);w,l,p=c["won"],c["lost"],c["pending"];tw+=w;tl+=l;tp+=p;suffix=""
        if not selected:
            if head=="goal_before_ht":suffix=" · система активна: 0–25' при 0:0"
            elif head=="over_2_5":suffix=" · система активна: перерыв 1:0/0:1, нужны ещё 2 гола"
            elif head=="both_teams_to_score":suffix=" · система активна: оценка на перерыве"
        lines.append(f"{HEAD_LABELS[head]}: ✅ {w} · ❌ {l} · ⏳ {p} · {_pct(w,l)}{suffix}")
    lines += ["",f"Всего закрыто: <b>{tw+tl}</b> · проход: <b>{_pct(tw,tl)}</b>",f"Ожидают результата: <b>{tp}</b>","<i>Повторы одного и того же сигнала после старых Restart в отчёте не считаются.</i>"]
    return "\n".join(lines)

def in_game_text(journal_path:Path,analysis_path:Path|None=None)->str:
    rows=_dedupe_signals(_load_rows(journal_path));states=_latest_live_states(analysis_path);pending=[]
    for row in rows:
        if bool(row.get("in_game")) or str(row.get("result") or "pending").lower()!="pending":continue
        live=states.get(str(row.get("match_id") or ""))
        if not _already_resolved_from_live(row,live):pending.append(row)
    pending.sort(key=lambda r:str(r.get("created_at") or ""),reverse=True)
    if not pending:return "🟢 <b>В ИГРЕ</b>\n\nНеподтверждённых и ещё не сыгравших LIVE-сигналов сейчас нет."
    lines=["🟢 <b>В ИГРЕ — ЖДУТ ПОДТВЕРЖДЕНИЯ</b>",f"Неподтверждённых сигналов: <b>{len(pending)}</b>","Показываются только ещё не рассчитанные сигналы.",""]
    for row in pending[:12]:
        ss=row.get("score") or [0,0];live=states.get(str(row.get("match_id") or "")) or {};ls=live.get("score") or ss;lm=int(live.get("minute") or row.get("minute") or 0);league=str(row.get("league") or "").strip();ll=f" · {league}" if league else ""
        lines.append(f"{HEAD_LABELS.get(str(row.get('head')),str(row.get('head')))}{ll}\n{row.get('home','?')} — {row.get('away','?')} · сейчас {lm}' · {ls[0]}:{ls[1]} · P <b>{float(row.get('probability') or 0)*100:.1f}%</b>\n↳ сигнал был: {row.get('minute',0)}' · {ss[0]}:{ss[1]}")
    return "\n\n".join(lines)

def _short_block(reason:str)->str:
    mapping={"prefilter_rejected":"не прошёл предфильтр","model_unavailable":"модель не загрузилась","model_output_missing":"нет выхода модели","warmup_until_10":"до 10'","second_half_warmup_until_55":"до 55' во 2Т","first_half_signal_window_closed_25":"окно 1Т закрыто","halftime_model_requires_halftime":"только перерыв","first_half_zero_zero_only":"1Т только 0:0","over25_ht_1_0_or_0_1_only":"ТБ2.5 только HT 1:0/0:1","btts_already_won":"ОЗ уже сыграл","duplicate_pending_signal":"уже есть сигнал"}
    if reason in mapping:return mapping[reason]
    if reason.startswith("score=") or reason.startswith("probability="):return "ниже порога "+reason
    if reason.startswith("model_disagreement="):return "модели расходятся"
    if reason.startswith("post_goal_cooldown_"):return "пауза после гола"
    if reason.startswith("entry_window_closed_"):return "окно входа закрыто"
    if reason.startswith("max_open=") or reason.startswith("max_entries="):return "лимит входов"
    return reason

def analysis_text(analysis_path:Path)->str:
    if not analysis_path.exists():return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nДанные анализа ещё не накоплены."
    latest={}
    try:
        with analysis_path.open("r",encoding="utf-8") as h:
            for line in h:
                try:r=json.loads(line)
                except Exception:continue
                key=(str(r.get("match_id") or ""),str(r.get("head") or ""))
                if key[0] and key[1]:latest[key]=r
    except Exception:return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nНе удалось прочитать текущий анализ."
    all_rows=sorted(latest.values(),key=lambda r:str(r.get("captured_at") or ""),reverse=True)
    if not all_rows:return "🧠 <b>LIVE-АНАЛИЗ</b>\n\nПока нет LIVE-данных."
    funnel=[r for r in all_rows if str(r.get("head"))=="prefilter"];live_matches=len({str(r.get("match_id")) for r in funnel});pp=sum(1 for r in funnel if r.get("decision")=="PASS");models=[r for r in all_rows if str(r.get("head")) in {"another_goal","goal_before_ht","over_2_5","both_teams_to_score"}];sc=sum(1 for r in models if r.get("decision")=="SIGNAL");bc=Counter()
    for row in all_rows:
        for reason in row.get("blocks") or []:bc[_short_block(str(reason))]+=1
    top=sorted(models,key=lambda r:float(r.get("probability") or 0),reverse=True)[:8];lines=["🧠 <b>LIVE-АНАЛИЗ</b>",f"LIVE матчей в воронке: <b>{live_matches}</b>",f"Прошли предфильтр: <b>{pp}</b>",f"Модельных оценок: <b>{len(models)}</b> · SIGNAL: <b>{sc}</b>"]
    if bc:
        lines += ["","<b>Что чаще всего блокирует:</b>"]+[f"• {r}: {n}" for r,n in bc.most_common(7)]
    if top:
        lines += ["","<b>Самые близкие к входу:</b>"]
        for row in top:
            s=row.get("score") or [0,0];decision="🔥 SIGNAL" if row.get("decision")=="SIGNAL" else "⏳ WAIT";blocks=[_short_block(str(x)) for x in row.get("blocks") or []];bt=", ".join(blocks[:2]) if blocks else "нет блоков";lines.append(f"{HEAD_LABELS.get(str(row.get('head')),str(row.get('head')))} · {decision}\n{row.get('home','?')} — {row.get('away','?')} · {row.get('minute',0)}' · {s[0]}:{s[1]} · P <b>{float(row.get('probability') or 0)*100:.1f}%</b>\n↳ {bt}")
    return "\n\n".join(lines)
