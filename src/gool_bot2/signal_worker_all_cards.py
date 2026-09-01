from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import signal_worker_all as base
from . import signal_cards as trained_cards
from .gool_live_cards import render_gool_live_result_card, render_gool_live_signal_card
from .journal import append_analysis, save_signal_journal
from .match_context import provider_pair, provider_count, xg_or_proxy_pair
from .signal_cards import flashscore_meta, stats_snapshot
from .signal_policy import exposure_gate, post_goal_gate
from .telegram import broadcast, broadcast_photo, signal_keyboard

_LAST_TWO_MORE: dict[str, dict[str, Any]] = {}
_ORIG_TWO_MORE = base.analyze_two_more_goals
_ORIG_SETTLE_PENDING = base._settle_pending

def _match_id(record): return str(((record.get("match") or {}).get("flashscore_event_id") or ""))
def _capture_two_more(record):
    result=_ORIG_TWO_MORE(record); mid=_match_id(record)
    if mid: _LAST_TWO_MORE[mid]=dict(result or {})
    return result
def _disabled_btts(record): return {"passed":False,"pressure_score":None,"minimum":None,"confidence_score":None,"disabled":True}

def _settle_pending_finished(record,journal):
    settled=_ORIG_SETTLE_PENDING(record,journal); match=record.get("match") or {}
    if not bool(match.get("is_finished")): return settled
    match_id=str(match.get("flashscore_event_id") or "")
    if not match_id:return settled
    minute=int(match.get("minute") or 90); home_score,away_score=base._reconciled_score(record); already={(str(r.get("match_id") or ""),str(r.get("head") or ""),int(r.get("minute") or 0)) for r in settled}
    for row in journal:
        if str(row.get("match_id") or "")!=match_id or str(row.get("result") or "pending").lower()!="pending":continue
        head=str(row.get("head") or "")
        if head=="two_more_goals":continue
        result="won" if base._is_won(head,row.get("score") or [0,0],minute,home_score,away_score) else "lost"
        row.update({"result":result,"settled_at":datetime.now(timezone.utc).isoformat(),"settled_minute":minute,"settled_score":[home_score,away_score],"settlement_source":"flashscore_finished_reconcile"}); row.pop("terminal_seen_score",None);row.pop("terminal_seen_count",None)
        key=(match_id,head,int(row.get("minute") or 0))
        if key not in already:settled.append(dict(row));already.add(key)
    return settled

def _pct(value):
    try:return f"{float(value)*100:.1f}%"
    except Exception:return "n/a"
def _live_status(analyzer,min_strength):
    analyzer=analyzer or {};pressure=analyzer.get("pressure_score");strength=analyzer.get("confidence_score");passed=bool(analyzer.get("passed"))
    if strength is None:return "WAIT"
    try:strength_f=float(strength)
    except Exception:return "WAIT"
    pressure_text="n/a" if pressure is None else f"{float(pressure):.2f}";state="BLOCK_PRESSURE" if not passed else (f"BLOCK_CHANCE<{min_strength*100:.0f}" if strength_f<min_strength else "READY")
    return f"{state} pressure={pressure_text} chance={strength_f*100:.0f}/100"
def _pair_total(record,key):
    try:
        if key=="xg":
            home,away,_,_=xg_or_proxy_pair(record)
        else:
            home,away=provider_pair(record,key)
    except Exception:return None
    return None if home is None or away is None else float(home+away)
def _weighted_ratio(values,expectations):
    parts=[]
    for key,(expected,weight) in expectations.items():
        value=values.get(key)
        if value is None or expected<=0:continue
        parts.append((max(0.0,min(2.5,float(value)/expected)),weight))
    if not parts:return None,0
    tw=sum(w for _,w in parts);return sum(v*w for v,w in parts)/tw,len(parts)

def _form_stats(rows,team=None,venue=None):
    rows=list(rows or [])
    if not rows:return {"matches":0,"avg_total":None,"scored_rate":None,"conceded_rate":None,"over15_rate":None,"over25_rate":None}
    totals=[];scored=[];conceded=[];t=(team or "").casefold().strip()
    for r in rows:
        hs=int(r.get("home_score") or 0);aws=int(r.get("away_score") or 0);totals.append(hs+aws)
        if t:
            is_home=str(r.get("home") or "").casefold().strip()==t;gf=hs if is_home else aws;ga=aws if is_home else hs;scored.append(gf>0);conceded.append(ga>0)
    n=len(rows);return {"matches":n,"avg_total":sum(totals)/n,"scored_rate":sum(scored)/n if scored else None,"conceded_rate":sum(conceded)/n if conceded else None,"over15_rate":sum(x>=2 for x in totals)/n,"over25_rate":sum(x>=3 for x in totals)/n}

def _prematch_confirmation(record):
    ctx=record.get("prematch_context") or {};match=record.get("match") or {};home=str(match.get("home") or "");away=str(match.get("away") or "")
    hf=_form_stats(ctx.get("home_recent"),home);af=_form_stats(ctx.get("away_recent"),away);hv=_form_stats(ctx.get("home_at_home"),home);av=_form_stats(ctx.get("away_away"),away);hh=_form_stats(ctx.get("h2h"))
    min_team=int(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_TEAM_MATCHES","5")); enough=hf["matches"]>=min_team and af["matches"]>=min_team;components=[]
    for stats,weight in ((hf,.24),(af,.24),(hv,.16),(av,.16),(hh,.20)):
        if stats["matches"]<=0:continue
        goal_rate=max(0.0,min(1.0,float(stats["avg_total"] or 0)/3.0));over15=float(stats["over15_rate"] or 0);over25=float(stats["over25_rate"] or 0);components.append(((.45*goal_rate+.35*over15+.20*over25),weight))
    score=sum(v*w for v,w in components)/sum(w for _,w in components) if components else None;minimum=float(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_SCORE","0.55"));passed=bool(enough and score is not None and score>=minimum)
    return {"passed":passed,"score":score,"minimum":minimum,"enough_history":enough,"home_form":hf,"away_form":af,"home_at_home":hv,"away_away":av,"h2h":hh,"source":ctx.get("source")}

def _another_goal_live_confirmation(record):
    """LIVE gate using consensus plus shot quality from all available providers."""
    match=record.get("match") or {};minute=int(match.get("minute") or 0);momentum=record.get("live_momentum") or {};xh,xa,xg_source,xg_evidence=xg_or_proxy_pair(record)
    cumulative={
        "xg":None if xh is None or xa is None else float(xh+xa),"xgot":_pair_total(record,"xgot"),"shots":_pair_total(record,"shots"),"sot":_pair_total(record,"shots_on_target"),
        "inside":_pair_total(record,"shots_inside_box"),"big":_pair_total(record,"big_chances"),"high_xg":_pair_total(record,"high_xg_shots"),
        "danger":_pair_total(record,"dangerous_attacks"),"touches":_pair_total(record,"touches_box"),"corners":_pair_total(record,"corners"),
    }
    progress=max(.03,min(1.0,float(minute)/90.0))
    cp,ce=_weighted_ratio(cumulative,{
        "xg":(2.30*progress,.22),"xgot":(1.65*progress,.13),"shots":(24*progress,.08),"sot":(8*progress,.13),
        "inside":(14*progress,.12),"big":(3.2*progress,.10),"high_xg":(2.4*progress,.08),"danger":(96*progress,.06),"touches":(42*progress,.04),"corners":(10*progress,.04),
    })
    r5={"xg":momentum.get("xg_total_last_5m"),"shots":momentum.get("shots_total_last_5m"),"sot":momentum.get("sot_total_last_5m"),"big":momentum.get("big_total_last_5m"),"danger":momentum.get("danger_total_last_5m")}
    r10={"xg":momentum.get("xg_total_last_10m"),"shots":momentum.get("shots_total_last_10m"),"sot":momentum.get("sot_total_last_10m"),"big":momentum.get("big_total_last_10m"),"danger":momentum.get("danger_total_last_10m")}
    p5,e5=_weighted_ratio(r5,{"xg":(.13,.34),"shots":(1.35,.16),"sot":(.45,.24),"big":(.18,.14),"danger":(5.3,.12)});p10,e10=_weighted_ratio(r10,{"xg":(.26,.34),"shots":(2.7,.16),"sot":(.9,.24),"big":(.36,.14),"danger":(10.6,.12)})
    mc=float(os.getenv("ANOTHER_GOAL_LIVE_MIN_CUMULATIVE","0.90"));m5=float(os.getenv("ANOTHER_GOAL_LIVE_MIN_5M","1.05"));m10=float(os.getenv("ANOTHER_GOAL_LIVE_MIN_10M","1.00"));me=int(os.getenv("ANOTHER_GOAL_LIVE_MIN_EVIDENCE","3"));enough=e5>=me and e10>=me
    recent_threat=(r5.get("xg") is not None and float(r5.get("xg") or 0)>=.12) or (r5.get("sot") is not None and float(r5.get("sot") or 0)>=1) or (r5.get("big") is not None and float(r5.get("big") or 0)>=1)
    quality_threat=(cumulative.get("xgot") is not None and float(cumulative.get("xgot") or 0)>=.35) or (cumulative.get("inside") is not None and float(cumulative.get("inside") or 0)>=3) or (cumulative.get("big") is not None and float(cumulative.get("big") or 0)>=1) or (cumulative.get("high_xg") is not None and float(cumulative.get("high_xg") or 0)>=1)
    passed=bool(enough and cp is not None and cp>=mc and p5 is not None and p5>=m5 and p10 is not None and p10>=m10 and recent_threat and quality_threat)
    ps=[x for x in (cp,p5,p10) if x is not None];combined=sum(ps)/len(ps) if ps else None
    return {"passed":passed,"pressure_score":combined,"combined_pressure":combined,"cumulative_pressure":cp,"pressure_5m":p5,"pressure_10m":p10,"minimum_5m":m5,"minimum_10m":m10,"minimum_cumulative":mc,"evidence_5m":e5,"evidence_10m":e10,"enough_history":enough,"direct_threat":recent_threat,"quality_threat":quality_threat,"recent_5m":r5,"recent_10m":r10,"cumulative":cumulative,"providers":provider_count(record),"xg_sources":provider_count(record,"xg"),"xg_source":xg_source,"xg_proxy_evidence":xg_evidence,"shot_sources":provider_count(record,"shots")}

def _minute_aware_goal_timing(match,probability,model_result):
    minute=int(match.get("minute") or 0)
    try:p_any=max(.01,min(.99,float(probability)))
    except Exception:return None,None
    if bool(match.get("is_halftime")) or minute>=46:return None,100*p_any
    try:p_ht=float((model_result.get("trained_probability") or {}).get("goal_before_ht"))
    except Exception:return None,100*p_any
    return 100*max(0.0,min(p_any,p_ht)),100*p_any

def _send_two_more_results(rows):
    for row in rows:
        result=str(row.get("result") or "lost");score=row.get("settled_score") or [0,0];icon="✅" if result=="won" else "❌";caption=f"{icon} <b>{'ЗАШЁЛ' if result=='won' else 'НЕ ЗАШЁЛ'}</b> · ЕЩЁ +2 ГОЛА";sent=0
        try:sent=broadcast_photo(render_gool_live_result_card(row,result),caption=caption)
        except Exception as exc:print(f"gool_two_more_result_card_error={type(exc).__name__}:{exc}",flush=True)
        if sent==0:broadcast(caption+f"\n{row.get('home','?')} — {row.get('away','?')} · {int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}")

class CardAllMatchSignalWorker(base.AllMatchSignalWorker):
    def __init__(self,journal_path:Path,max_disagreement:float=.20,analysis_path:Path|None=None):super().__init__(journal_path,max_disagreement=max_disagreement,analysis_path=analysis_path);self._diag_model_result={};self._diag_predict_wrapped=False
    def _ensure_model(self):
        ok=super()._ensure_model()
        if not ok or self.model is None or self._diag_predict_wrapped:return ok
        original_predict=self.model.predict
        def predict_with_capture(record):
            result=original_predict(record);prematch=_prematch_confirmation(record);live=_another_goal_live_confirmation(record);result["prematch_analysis"]=prematch;result["another_goal_live"]=live;an=result.setdefault("gool_analyzer",{});combined_pass=bool(prematch.get("passed") and live.get("passed"));an["another_goal"]={"required":True,"name":"prematch_model_live_consensus","score":live.get("combined_pressure"),"minimum":live.get("minimum_5m"),"passed":combined_pass,"details":{"prematch":prematch,"live":live}}
            if not combined_pass:result.setdefault("blended",{})["another_goal"]=None
            self._diag_model_result=dict(result or {});return result
        self.model.predict=predict_with_capture;self._diag_predict_wrapped=True;return ok
    def _print_system_status(self,record):
        match=record.get("match") or {};mid=_match_id(record);minute=int(match.get("minute") or 0);trained=self._diag_model_result.get("trained_probability") or {};blended=self._diag_model_result.get("blended") or {};live=self._diag_model_result.get("another_goal_live") or {};pre=self._diag_model_result.get("prematch_analysis") or {};another=blended.get("another_goal");lp=live.get("combined_pressure");ps=pre.get("score");ms=float(os.getenv("GOOL_LIVE_MIN_STRENGTH",".70"));two=_live_status(_LAST_TWO_MORE.get(mid),ms)
        print(f"GOOL_SYSTEMS match={match.get('home','?')} - {match.get('away','?')} stage={'HT' if match.get('is_halftime') else minute} score={int(match.get('home_score') or 0)}:{int(match.get('away_score') or 0)} | DATA={live.get('providers',provider_count(record))}/3 xG={live.get('xg_source','unavailable')} src={live.get('xg_sources',0)} | PREMATCH={'PASS' if pre.get('passed') else 'WAIT'} {'n/a' if ps is None else f'{float(ps)*100:.0f}/100'} | MODEL={_pct(trained.get('another_goal'))} | LIVE={'PASS' if live.get('passed') else 'WAIT'} {'n/a' if lp is None else f'{float(lp):.2f}x'} | ANOTHER_GOAL={'READY '+_pct(another) if another is not None else 'WAIT'} | PLUS2={two}",flush=True)
    def _process(self,record):
        self._diag_model_result={};emitted=super()._process(record);match=record.get("match") or {};minute=int(match.get("minute") or 0)
        if not bool(match.get("is_finished")) and (bool(match.get("is_halftime")) or 0<minute<=75):self._print_system_status(record)
        return emitted
    def _emit_gool_live_signal(self,record,journal,head,confidence,analyzer,model_result,cards):
        if head!="two_more_goals":return 0
        match=record.get("match") or {};match_id=str(match.get("flashscore_event_id") or "");minute=int(match.get("minute") or 0);home_score,away_score=base._reconciled_score(record);reasons=[]
        if minute<10:reasons.append("warmup_until_10")
        if minute>75:reasons.append("entry_window_closed_75")
        if not bool(analyzer.get("passed")):reasons.append("gool_pressure_not_confirmed")
        min_strength=float(os.getenv("GOOL_LIVE_MIN_STRENGTH",".70"))
        if confidence<min_strength:reasons.append("gool_strength_low")
        reasons.extend(exposure_gate(match_id,journal,max_entries=2,max_open=2).reasons);reasons.extend(post_goal_gate(minute,base._last_goal_minute(record),cooldown_minutes=5).reasons)
        if any(str(r.get("match_id"))==match_id and str(r.get("head"))==head and str(r.get("result") or "pending").lower()=="pending" for r in journal):reasons.append("duplicate_pending_signal")
        append_analysis(self.analysis_path,{**self._base_analysis(record,match_id),"head":head,"probability":None,"gool_confidence":confidence,"gool_live_analysis":analyzer,"decision":"SIGNAL" if not reasons else "WAIT","blocks":reasons})
        if reasons:return 0
        pressure=float(analyzer.get("pressure_score") or 0);label=base.HEAD_LABELS[head];sent=0
        try:sent=broadcast_photo(render_gool_live_signal_card(record,head,confidence,pressure,cards),caption=f"🔥 <b>{label}</b> · шанс события {confidence*100:.0f}/100 · pressure {pressure:.2f}",reply_markup=signal_keyboard(match_id,head))
        except Exception as exc:print(f"gool_live_card_error={type(exc).__name__}:{exc}",flush=True)
        if sent==0:sent=broadcast(f"🔥 <b>{label}</b>\n{match.get('home','?')} — {match.get('away','?')}\n{minute}' · {home_score}:{away_score}\nШанс события: <b>{confidence*100:.0f}/100</b> · GOOL pressure {pressure:.2f}",reply_markup=signal_keyboard(match_id,head))
        journal.append({"created_at":datetime.now(timezone.utc).isoformat(),"match_id":match_id,"head":head,"minute":minute,"home":match.get("home"),"away":match.get("away"),"league":match.get("league"),"score":[home_score,away_score],"probability":confidence,"signal_source":"gool_live_analyzer","gool_pressure":pressure,"provider_count":len(record.get("providers") or {}),"flashscore_meta":flashscore_meta(record),"stats_snapshot":stats_snapshot(record),"result":"pending","in_game":False});save_signal_journal(self.journal_path,journal);return int(sent>0)

base.TRAINED_HEADS=("another_goal",);base.analyze_two_more_goals=_capture_two_more;base.analyze_live_btts=_disabled_btts;base._settle_pending=_settle_pending_finished;trained_cards._goal_timing_split=_minute_aware_goal_timing;base.AllMatchSignalWorker=CardAllMatchSignalWorker;base._send_two_more_results=_send_two_more_results

def main():base.main()
if __name__=="__main__":main()
