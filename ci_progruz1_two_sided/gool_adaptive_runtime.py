"""GOOL ADAPTIVE continuous LIVE decision flow.

No bets are placed. NOTIFY mode sends a research-grade recommendation only
after source/score/market confirmation. SHADOW is the safe default.
"""
from __future__ import annotations
import math
import os
import sqlite3
import time

from gool_adaptive_market import live_markets, watch_live_quote
from gool_adaptive_decision import select_market
from gool_live_markets_v2 import add_verified_team_totals,market_label
from gool_adaptive_flashscore import FlashscoreContext
from gool_adaptive_outcomes import AdaptiveOutcomes
from gool_edge_stats_cache import FonbetEdgeStatsCache
from gool_edge_live import select_edge_live, format_edge_pick
from gool_goal_live import GoalPressure,select_goal_live,format_goal_pick
from progruz1_scenario_evidence import ScenarioEvidence
from progruz1_prematch_favourites import PrematchFavouriteQuotes
from gool_edge_lab import EdgeLab
from gool_adaptive_trajectory import (
    evaluate_trajectory,trajectory_text,scenario_for_pick,
)


def _stat_pair(stats:dict,key:str) -> str | None:
    """Expose only actually retrieved Flashscore numbers, never estimates."""
    pair=stats.get(key)
    if not isinstance(pair,(tuple,list)) or len(pair)!=2:
        return None
    try:
        first,second=float(pair[0]),float(pair[1])
    except (TypeError,ValueError):
        return None
    if not (0<=first<=100 and 0<=second<=100):
        return None
    if key=="xg":
        return f"{first:.2f}+{second:.2f}"
    if first.is_integer() and second.is_integer():
        return f"{int(first)}+{int(second)}"
    return None


def format_pick_message(pick:dict,scenario:str,baseline:tuple,
                        ctx:dict,name:str,pregame:dict|None=None,
                        *,market_count:int=0,shift_pp:float|None=None,
                        league:str|None=None,
                        trajectory:dict|None=None,
                        first_half_trajectory:dict|None=None) -> str:
    """Explain LIVE choice with the evidence used and its limitations."""
    period,line,direction=baseline
    tournament=" ".join(str(league or "").split())[:120]
    tournament_line=(f"🏆 Турнир: {tournament}\n" if tournament else
                     "🏆 Турнир: не указан в линии\n")
    base_side="ТБ" if direction=="TB" else "ТМ"
    side=market_label(pick)
    original=f"{period} · {base_side} {line:g}"
    move=(f" ({shift_pp:+.1f} п.п. по безмаржинальной оценке)"
          if shift_pp is not None else "")
    options=(f"🔎 Проверены актуальные пары ТБ/ТМ: {market_count} "
             "линий разных тоталов и периодов.\n"
             if market_count else "")
    stats=ctx.get("stats") or {}
    sources=[]
    for key,title in (("shots","удары"),("shots_on_target","в створ"),
                      ("xg","xG")):
        val=_stat_pair(stats,key)
        if val is not None:
            sources.append(f"{title} {val}")
    evidence=" · ".join(sources) if sources else "подробная статистика не показана"
    profile=pick.get("analysis_mode","FULL_LIVE")
    coverage=pick.get("history_coverage")
    coverage_text=(f", история {coverage[0]}/{coverage[1]} матчей"
                   if isinstance(coverage,(tuple,list)) and len(coverage)==2
                   else "")
    model_quality=("ПОЛНЫЙ LIVE (история и xG)"
                   if profile=="FULL_LIVE" else
                   "УПРОЩЁННЫЙ LIVE (история/статистика частичные)")
    quality_line=f"🧠 Режим анализа: {model_quality}{coverage_text}.\n"
    preparation=pregame or {}
    form=preparation.get("form") or {}
    h=form.get("home") or {}
    a=form.get("away") or {}
    if h.get("matches") and a.get("matches"):
        preform=(f"📚 До матча изучено: {h['matches']} игр хозяев, "
                 f"{a['matches']} игр гостей; средние общие голы "
                 f"{h.get('total','?')} / {a.get('total','?')}.\n")
    else:
        preform="📚 История команд: недостаточно проверенных данных.\n"
    lineup=preparation.get("lineups") or "NOT_CHECKED"
    if lineup=="PUBLISHED":
        lineup_text=("👥 Стартовые составы опубликованы во Flashscore; "
                     "отдельная численная поправка на игроков не откалибрована.\n")
    else:
        lineup_text=(f"👥 Составы: {lineup} — не учитываю "
                     "неподтверждённых игроков.\n")
    if scenario=="ПРОТИВ РЫНКА":
        decision="ПРОТИВ PREMOVE"
        why="LIVE-оценка не подтверждает исходное направление движения цены."
    elif scenario=="РАСХОЖДЕНИЕ":
        decision="С РЫНКОМ, НО ТРАЕКТОРИЯ СОМНИТЕЛЬНА"
        why="LIVE-оценка первоначальной линии противоречит рыночной гипотезе."
    elif scenario=="АЛЬТЕРНАТИВА":
        decision="ДРУГАЯ ЛИНИЯ"
        why=("После начала матча подобрана другая точная линия или период. "
             +("Направление ставки противоположно PREMOVE."
               if pick["side"]!=direction else "Направление PREMOVE сохранено."))
    else:
        decision="ПОДТВЕРЖДАЕТ PREMOVE"
        why="LIVE-оценка согласуется с исходным направлением, но не следует ему автоматически."
    motion_detail=(
        "🧭 Траектория исходного PREMOVE: "
        +trajectory_text(trajectory,(period,line,direction))+"\n"
        if trajectory is not None else "")
    half_detail=(
        "⏱ Отдельно первый тайм: "
        +trajectory_text(first_half_trajectory,
                         ("1-й тайм",first_half_trajectory.get("line",0.5),
                          first_half_trajectory.get("side","TB")))+"\n"
        if first_half_trajectory is not None else "")
    if pick["side"]=="TM":
        risk="Пропущенный гол может резко ухудшить вероятность ТМ."
    else:
        risk="Времени на необходимые голы становится меньше; котировка может быстро измениться."
    return (
        "🎯 GOOL ADAPTIVE · МОЯ СТАВКА НА МАТЧ (сигнал, не размещена)\n"
        f"⚽ {name}\n"
        +tournament_line+
        f"⏱ {pick['minute']}' · счёт {pick['score'][0]}:{pick['score'][1]}\n"
        f"🎯 {pick['period']} · {side} {pick['line']:g} @ {pick['odd']:.2f}\n"
        f"🧭 Решение: {decision}\n"
        f"📈 PREMOVE до матча: {original}{move} — только первоначальный ориентир.\n"
        +motion_detail+half_detail+options+
        f"💭 Мои мысли: {why}\n"
        f"📊 Сейчас по Flashscore: {evidence}.\n"
        +quality_line+preform+lineup_text+
        f"🧮 Экспериментальная оценка {pick['p_model']:.1%} "
        f"против безубыточного порога {pick['p_break_even']:.1%}; "
        f"расчётная разница {pick['edge']*100:+.1f} п.п. "
        "(не проверенное преимущество).\n"
        f"📡 Линия: Фонбет LIVE, подтверждена на двух срезах ≥8 сек; "
        f"модель: {pick['basis']}.\n"
        f"⚠️ Риск: {risk} Модель не откалибрована; "
        "доступность цены в момент ставки не гарантирована.\n"
        "⏳ ОЖИДАЕТ РЕЗУЛЬТАТА"
    )


def halftime_priority_batch(choices:list, cursor:int, limit:int)->tuple[list,int]:
    """Give the 42'-65' recheck a reserved evaluation slot, with fair rotation.

    PREMOVE-selected fixtures remain eligible all match long. A half-time
    candidate gets a slot when it has an actual, unblocked LIVE price pair;
    missing pairs cannot be promoted into a fabricated betting quote.
    Non-halftime fixtures keep at least one slot to avoid starvation.
    """
    if not choices:
        return [],int(cursor)
    size=len(choices)
    count=max(1,int(limit))
    offset=int(cursor)%size
    rotated=choices[offset:]+choices[:offset]
    if count==1:
        # A single worker cannot reserve a permanent HT slot without
        # starving every other fixture. Prioritize via normal fast rotation.
        return rotated[:1],(offset+1)%size
    halftime=[]
    other=[]
    for fixture in rotated:
        rows=fixture[1]
        try:
            minute=int(rows[0].get("minute") or 0)
        except (ValueError,TypeError,IndexError):
            minute=-1
        if 42<=minute<=65:
            halftime.append(fixture)
        else:
            other.append(fixture)
    # Reserve half the multi-fixture budget for the HT/2H window.
    reserved=min(len(halftime), max(1,(count+1)//2))
    picked=halftime[:reserved]
    picked.extend((other+halftime[reserved:])[:max(0,count-len(picked))])
    return picked, (offset+max(1,len(picked)))%size


class AdaptiveRuntime:
    def __init__(self,db:sqlite3.Connection,*,flashscore=None):
        self.db=db
        self.fs=flashscore if flashscore is not None else FlashscoreContext()
        self.last_check={}
        self._live_scan_cursor=0  # Fair round-robin across active LIVE fixtures.
        self.fonbet_live_stats=FonbetEdgeStatsCache()
        self.edge_lab=EdgeLab(db)
        self.goal_pressure=GoalPressure(db)
        self.scenario_evidence=ScenarioEvidence(db)
        self.prematch_favourites=PrematchFavouriteQuotes(db)
        self.pregame=None  # Attached by scanner main; optional in offline tests.
        self.value=None  # Third-brain research. Never a Telegram sender/gate.
        self.outcomes=AdaptiveOutcomes(db)
        # Verified Flashscore event IDs survive server restarts and let the
        # independent FT worker close outcomes even for transliterated teams.
        db.execute("""CREATE TABLE IF NOT EXISTS flashscore_goal_fixture_links(
            root_id TEXT NOT NULL,kickoff INTEGER NOT NULL,
            fs_id TEXT NOT NULL,match_name TEXT NOT NULL,
            first_seen INTEGER NOT NULL,
            PRIMARY KEY(root_id,kickoff))""")
        db.execute("""CREATE TABLE IF NOT EXISTS adaptive_picks (
            root_id TEXT NOT NULL,kickoff INTEGER NOT NULL,
            created_at INTEGER NOT NULL,mode TEXT NOT NULL,
            match_name TEXT NOT NULL,period TEXT NOT NULL,
            side TEXT NOT NULL,line REAL NOT NULL,odds REAL NOT NULL,
            score TEXT NOT NULL,minute INTEGER NOT NULL,
            scenario TEXT NOT NULL,p_estimate REAL NOT NULL,
            edge_estimate REAL NOT NULL,basis TEXT NOT NULL,
            message_id INTEGER,PRIMARY KEY(root_id,kickoff))""")
        db.execute("""CREATE TABLE IF NOT EXISTS adaptive_quote_confirm (
            root_id TEXT PRIMARY KEY,market_key TEXT NOT NULL,
            score TEXT NOT NULL,first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,last_odd REAL NOT NULL)""")
        db.execute("""CREATE INDEX IF NOT EXISTS adaptive_picks_time
            ON adaptive_picks(created_at)""")
        db.execute("""CREATE TABLE IF NOT EXISTS adaptive_trajectory_checks(
            root_id TEXT NOT NULL,kickoff INTEGER NOT NULL,bucket INTEGER NOT NULL,
            scope TEXT NOT NULL,period TEXT NOT NULL,line REAL NOT NULL,
            side TEXT NOT NULL,minute INTEGER NOT NULL,
            status TEXT NOT NULL,goals INTEGER,needed INTEGER,
            remaining INTEGER,expected_more REAL,p_direction REAL,
            model_basis TEXT,PRIMARY KEY(root_id,kickoff,bucket,scope))""")
        db.execute("""CREATE TABLE IF NOT EXISTS adaptive_watch_notices (
            root_id TEXT NOT NULL,kickoff INTEGER NOT NULL,
            sent_at INTEGER NOT NULL,message_id INTEGER NOT NULL,
            match_name TEXT,league TEXT,period TEXT,line REAL,side TEXT,
            PRIMARY KEY(root_id,kickoff))""")
        # Preserve old rows without inventing historical choices. New notices
        # freeze the exact period, side, and line shown in Telegram.
        columns={r[1] for r in db.execute("PRAGMA table_info(adaptive_watch_notices)")}
        for col,sql_type in (("match_name","TEXT"),("league","TEXT"),
                             ("period","TEXT"),("line","REAL"),("side","TEXT")):
            if col not in columns:
                db.execute(f"ALTER TABLE adaptive_watch_notices ADD COLUMN {col} {sql_type}")
        db.commit()

    def _send_watch_notices(self,snapshot:dict,active:dict,now:int,*,sender,
                            quotes:list[dict]|None=None,limit:int=4) -> int:
        """One confirmed LIVE kickoff notice per RADAR match and kickoff.

        Sending fails open for the analysis: no marker is inserted on failure,
        so the next scan can retry. An intro never creates a bet or PENDING.
        """
        if sender is None:
            return 0
        from gool_scanner_lite import extract_targets
        _,meta=extract_targets(snapshot,now,allowed_sports={"football"})
        online={str(r["root_id"]):r for r in meta["_roots"]
                if r["phase"]=="LIVE" and r.get("score_home") is not None
                and r.get("score_away") is not None}
        from gool_adaptive_fixture_match import live_links
        matched=live_links([(root,kick,name) for root,(kick,_,_,_,name)
                            in active.items()],list(online.values()))
        for canonical,ctx in matched.items():
            online[canonical]=ctx
        sent=0
        for root,(kick,period,line,direction,name) in active.items():
            if sent>=limit:
                break
            observed=online.get(root)
            if observed is None or not kick-120<=now<=kick+4*3600:
                continue
            existing=self.db.execute(
                "SELECT 1 FROM adaptive_watch_notices WHERE root_id=? AND kickoff=?",
                (root,kick)).fetchone()
            if existing:
                continue
            # Do not send a retroactive 'watch started' after an older
            # deployed version already delivered a real ADAPTIVE pick.
            old_pick=self.db.execute("""SELECT 1 FROM adaptive_picks
                WHERE root_id=? AND kickoff=? AND mode='NOTIFY'""",
                (root,kick)).fetchone()
            if old_pick:
                continue
            radar=self.db.execute("""SELECT league FROM adaptive_radar
                WHERE root_id=? AND kickoff=?""",(root,kick)).fetchone()
            league=(str(radar[0]).strip() if radar and radar[0] else
                    str(observed.get("league") or "").strip())
            tournament=" ".join(league.split())[:120]
            tournament_line=(f"🏆 Турнир: {tournament}\n" if tournament else
                             "🏆 Турнир: не указан в линии\n")
            side="ТБ" if direction=="TB" else "ТМ"
            # Never use an unrelated total, other period, opposite side or
            # stale prematch price as though it were a current LIVE quote.
            # A valid quote is the exact, unblocked paired Fonbet market
            # from this snapshot, already checked by live_markets().
            relevant=[q for q in (quotes or ())
                if str(q.get("root_id"))==str(root)
                and int(q.get("kickoff") or 0)==int(kick)
                and q.get("period")==period
                and abs(float(q.get("line") or 0)-float(line))<0.001]
            quote_text=None
            if len(relevant)==1:
                current=relevant[0].get("over" if direction=="TB" else "under")
                try:
                    odd=float(current)
                    if math.isfinite(odd) and 1<odd<100:
                        quote_text=f"LIVE @{odd:.2f}"
                except (ValueError,TypeError):
                    pass
            # A bookmaker can publish one side without its opposite.
            # Show the real quoted price, but do NOT treat it as a vetted
            # candidate: the separate decision engine still needs BOTH sides.
            display=({"status":"EXACT"} if quote_text else
                     watch_live_quote(snapshot,str(observed["root_id"]),
                                      period,line,direction,
                                      current_half=observed.get("current_half")))
            if quote_text is None and display["status"]=="EXACT":
                quote_text=f"LIVE @{display['odd']:.2f} (без подтверждённой пары)"
            if quote_text is None and display["status"]=="ALTERNATE":
                quote_text=(f"LIVE {side} {display['line']:g} "
                            f"@{display['odd']:.2f} (другая линия)")
            if quote_text is None:
                # Last saved PREMOVE price is historical, never current LIVE.
                # Use the identical fixture, market/period and direction.
                pre=self.db.execute("""SELECT over_last,under_last
                    FROM premove_cards
                    WHERE root_id=? AND kickoff=? AND period=?
                      AND ABS(line-?)<0.001 AND last_at<=?
                      AND last_at<kickoff
                    ORDER BY last_at DESC LIMIT 1""",
                    (root,kick,period,line,now)).fetchone()
                if pre:
                    try:
                        odd=float(pre[0] if direction=="TB" else pre[1])
                        if math.isfinite(odd) and 1<odd<100:
                            why={
                                "BLOCKED":"LIVE рынок заблокирован",
                                "AMBIGUOUS":"противоречивые котировки LIVE",
                                "NO_PERIOD":"тотал периода не опубликован в LIVE",
                                "NO_SIDE":"нет этой стороны в LIVE",
                                "NO_LIVE_EVENT":"нет подтверждённой LIVE-линии"
                            }.get(display["status"],"нет этой линии в LIVE")
                            quote_text=f"PREMATCH @{odd:.2f} ({why})"
                    except (ValueError,TypeError):
                        pass
            if quote_text is None:
                reasons={"BLOCKED":"рынок LIVE заблокирован",
                         "AMBIGUOUS":"противоречивые котировки LIVE",
                         "NO_PERIOD":"тотал периода не опубликован в LIVE",
                         "NO_SIDE":"этой стороны тотала нет в LIVE",
                         "NO_LIVE_EVENT":"матч пока не найден в линии LIVE",
                         "UNSUPPORTED":"рынок не поддерживается"}
                quote_text=reasons.get(display["status"],"нет цены в LIVE")
            score=f'{observed["score_home"]}:{observed["score_away"]}'
            clock=str(observed.get("clock") or "время пока не подтверждено")
            # This is only an opening watch notice, not a betting pick.
            # Keep it compact; actual selections retain the full evidence.
            msg=(f"🧠 GOOL ADAPTIVE · НАЧАЛ СЛЕДИТЬ ЗА МАТЧЕМ\n"
                 f"⚽ {name}\n"
                 +tournament_line+
                 f"⏱ {clock} · счёт {score}\n"
                 f"📈 PREMOVE: {side} {line:g} · {period} · {quote_text}")
            try:
                mid=sender(msg)
            except Exception as exc:
                print("ADAPTIVE_WATCH_DELIVERY_ERROR",type(exc).__name__,flush=True)
                continue
            if mid is None:
                continue
            with self.db:
                self.db.execute("""INSERT OR IGNORE INTO adaptive_watch_notices
                    (root_id,kickoff,sent_at,message_id,
                     match_name,league,period,line,side)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (root,kick,now,mid,name,league,period,float(line),direction))
            sent+=1
        return sent

    def _confirm(self,pick:dict,now:int)->bool:
        root=str(pick["root_id"])
        key=f'{pick["event_id"]}:{pick["period"]}:{pick["side"]}:{pick["line"]:.3f}'
        score=f'{pick["score"][0]}:{pick["score"][1]}'
        prev=self.db.execute("""SELECT market_key,score,first_seen,last_seen,
            last_odd FROM adaptive_quote_confirm WHERE root_id=?""",(root,)).fetchone()
        if (prev is None or prev[0]!=key or prev[1]!=score or
                now-prev[3]>90 or now<=prev[3] or
                abs(pick["odd"]/prev[4]-1)>0.10):
            self.db.execute("""INSERT INTO adaptive_quote_confirm VALUES (?,?,?,?,?,?)
                ON CONFLICT(root_id) DO UPDATE SET market_key=excluded.market_key,
                score=excluded.score,first_seen=excluded.first_seen,
                last_seen=excluded.last_seen,last_odd=excluded.last_odd""",
                (root,key,score,now,now,float(pick["odd"])))
            self.db.commit()
            return False
        self.db.execute("""UPDATE adaptive_quote_confirm SET last_seen=?,
              last_odd=? WHERE root_id=?""",(now,pick["odd"],root))
        self.db.commit()
        return now-prev[2]>=8

    def _record(self,pick:dict,now:int,mode:str,scenario:str,message_id):
        self.db.execute("""INSERT INTO adaptive_picks VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(root_id,kickoff) DO UPDATE SET
              created_at=excluded.created_at,mode=excluded.mode,
              period=excluded.period,side=excluded.side,line=excluded.line,
              odds=excluded.odds,score=excluded.score,minute=excluded.minute,
              scenario=excluded.scenario,p_estimate=excluded.p_estimate,
              edge_estimate=excluded.edge_estimate,basis=excluded.basis,
              message_id=excluded.message_id
            WHERE adaptive_picks.mode='SHADOW'""",
            (pick["root_id"],pick["kickoff"],now,mode,pick["match"],
             pick["period"],pick["side"],pick["line"],pick["odd"],
             f'{pick["score"][0]}:{pick["score"][1]}',pick["minute"],scenario,
             pick["p_model"],pick["edge"],pick["basis"],message_id))
        self.db.commit()

    def _scenario(self,pick:dict,baseline:tuple)->str:
        period,line,direction=baseline
        if pick["period"]!=period or pick["line"]!=line:return "АЛЬТЕРНАТИВА"
        if pick["side"]!=direction:return "ПРОТИВ РЫНКА"
        return "С РЫНКОМ"

    def _record_evaluation(self,root:str,kick:int,now:int,status:str,
                           reason:str,market:str|None=None):
        """Durable, per-minute decision trace for the existing RADAR report.

        The scanner has already written the WATCH_LIVE row this minute.
        Overwrite with the actual independent model decision/rejection.
        """
        with self.db:
            self.db.execute("""INSERT INTO adaptive_evaluations
                (root_id,kickoff,bucket,observed_at,status,reason,
                 candidates,preferred_market)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(root_id,kickoff,bucket) DO UPDATE SET
                observed_at=excluded.observed_at,status=excluded.status,
                reason=excluded.reason,candidates=excluded.candidates,
                preferred_market=excluded.preferred_market""",
                (root,int(kick),now//60,now,status,str(reason)[:160],
                 int(market is not None),market))

    def _record_trajectory(self,root:str,kick:int,now:int,scope:str,
                           baseline:tuple,trajectory:dict) -> None:
        """Persist factual and estimated trajectory separately, per minute."""
        period,line,side=baseline
        with self.db:
            self.db.execute("""INSERT INTO adaptive_trajectory_checks
                (root_id,kickoff,bucket,scope,period,line,side,minute,
                 status,goals,needed,remaining,expected_more,p_direction,model_basis)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(root_id,kickoff,bucket,scope) DO UPDATE SET
                minute=excluded.minute,status=excluded.status,
                goals=excluded.goals,needed=excluded.needed,
                remaining=excluded.remaining,expected_more=excluded.expected_more,
                p_direction=excluded.p_direction,model_basis=excluded.model_basis""",
                (root,int(kick),now//60,scope,period,float(line),side,
                 int(trajectory.get("minute") or 0),
                 str(trajectory.get("status") or "NO_EVIDENCE"),
                 trajectory.get("goals"),trajectory.get("needed"),
                 trajectory.get("remaining"),trajectory.get("expected_more"),
                 trajectory.get("p_direction"),trajectory.get("model_basis")))

    def _goal_fixture(self,root,rows,now,*,mode,sender,chat_id,
                      baseline,name,league):
        """Pure goal watcher route; NO Flashscore probability/trajectory gate.

        RADAR previously chose the fixture independently of movement side.
        Inspect actual Fonbet shot pressure, select goal TB or quiet-game
        TM from real paired quotes, and reconfirm unchanged price >=8 seconds.
        """
        score=rows[0].get("match_score")
        try:
            minute=int(rows[0].get("minute") or 0)
        except (ValueError,TypeError):
            minute=0
        current_half=str(rows[0].get("current_half") or "")
        # Some valid Fonbet LIVE events omit a per-half score split.
        # A current MATCH score + confirmed 49' clock is enough to use
        # a MATCH total for a next goal in 2H. It does NOT assert an HT.
        if not current_half and 49<=minute<=82:
            current_half="2-й тайм"
        ctx={"score":score,"minute":minute,"updated_at":now,"stats":{},
             "league":league,"match_name":name}
        try:
            ctx=self.fonbet_live_stats.enrich(
                ctx,feed_root_id=str(rows[0].get("feed_root_id") or root),
                current_half=current_half,now=now)
        except Exception as exc:
            print("GOAL_FONBET_STATS_ERROR",type(exc).__name__,flush=True)
        pressure=None
        proof=ctx.get("fonbet_stat_evidence") or {}
        stats=ctx.get("stats") or {}
        if (proof.get("source")=="fonbet_sportscast" and
                proof.get("scope")==("first_half" if
                    current_half=="1-й тайм" else "match") and
                0<=now-int(proof.get("observed_at") or 0)<=120):
            pressure=self.goal_pressure.observe(
                root=root,kickoff=rows[0]["kickoff"],now=now,
                minute=minute,phase=current_half,score=score,
                shots=stats.get("shots"),on=stats.get("shots_on_target"))
        # PROGRUZ 1: choose internally from observed PREMOVE movement,
        # verified LIVE game state and real paired Fonbet totals.
        # No external neural network, API key or research job.
        radar_origin=self.db.execute("""SELECT base_move_pp,premove_verdict
            FROM adaptive_radar WHERE root_id=? AND kickoff=?""",
            (str(root),int(rows[0]["kickoff"]))).fetchone()
        from_st=(bool(radar_origin) and
                 str(radar_origin[1] or "").startswith("ST2_GOAL_WATCH"))
        ctx["radar_direction"]=("" if from_st else
                                baseline[2] if baseline else "")
        ctx["radar_move_pp"]=(0.0 if from_st else
            float(radar_origin[0]) if radar_origin and
            radar_origin[0] is not None else 0.0)
        pick,reason=select_goal_live(rows,ctx,pressure=pressure,
            min_odd=float(os.getenv("GOOL_LIVE_BRAIN_MIN_ODD","1.50")),
            allow_under=os.getenv("GOOL_GOAL_ALLOW_UNDER","1")=="1")
        if pick is None:
            # Record the *actual* dominant model/scenario failure in the
            # existing per-minute RADAR journal. No extra SQLite writes.
            # Do not conflate a failed price quote with a rejected scenario.
            rejects=ctx.get("_goal_rejections") or {}
            dominant=(sorted(rejects.items(),key=lambda r:(-r[1],r[0]))[0][0]
                      if rejects else "")
            detailed_reason=(reason+":"+dominant if dominant and
                             reason=="GOAL_NO_SAFE_TOTAL" else reason)
            self._record_evaluation(root,rows[0]["kickoff"],now,
                                    "WAIT",detailed_reason)
            return False,reason
        pick["match"]=name
        pick["selection_origin"]="ST2_GOAL" if from_st else "PREMOVE"
        if not self._confirm(pick,now):
            reason="GOAL_WAIT_PRICE_CONFIRM"
            self._record_evaluation(root,rows[0]["kickoff"],now,
                                    "WAIT",reason)
            return False,reason
        # Price confirmation does not imply goal probability or bookmaker
        # execution. A previously NOTIFIED match must never be notified again.
        if self.db.execute("""SELECT 1 FROM adaptive_picks
            WHERE root_id=? AND kickoff=? AND mode='NOTIFY'""",
            (str(root),int(pick["kickoff"]))).fetchone():
            return False,"GOAL_ALREADY_NOTIFIED"
        text=format_goal_pick(pick,name=name,league=league,baseline=baseline)
        origin="ST2→LIVE" if from_st else "PREMOVE→LIVE"
        scenario=f"{origin} · {pick['side']}"
        if mode=="SHADOW":
            self._record_evaluation(root,pick["kickoff"],now,
                                    "SHADOW_PICK",scenario)
            self._record(pick,now,mode,scenario,None)
            print("GOAL_LIVE_SHADOW",text.replace("\n"," | "),flush=True)
            return True,scenario
        if sender is None or not chat_id:
            self._record_evaluation(root,pick["kickoff"],now,
                                    "WAIT","NOT_CONFIGURED")
            return False,"NOT_CONFIGURED"
        msg_id=sender(text)
        if msg_id is None:
            self._record_evaluation(root,pick["kickoff"],now,
                                    "WAIT","DELIVERY_FAILED")
            return False,"DELIVERY_FAILED"
        # Existing HT/FT outcome pipeline, no ST3/first-brain coupling.
        self.outcomes.track(pick,now,message_id=msg_id,message_text=text)
        self._record(pick,now,"NOTIFY",scenario,msg_id)
        self._record_evaluation(root,pick["kickoff"],now,"SIGNAL",
                                reason,
                                f"{pick['period']} {pick['side']} {pick['line']:g}")
        return True,"SENT"

    def scan_flashscore_live(self,snapshot:dict,now:int,*,mode:str="SHADOW",
                            sender=None,chat_id:str="",limit:int=3,
                            live_matches=None,widgets_observed=False)->dict:
        """The single PROGRUZ 1 brain: all available Flashscore LIVE football.

        No adaptive_radar, PREMOVE or Fonbet sportscast data is used.
        The Fonbet snapshot contributes only exact live HALF TB odds,
        plus defensive identity/score cross-validation. Network requests
        to Flashscore are bounded to a small fair rotating batch.
        """
        from gool_scanner_lite import extract_targets
        from gool_flashscore_goal_brain import (
            match_flashscore_live,read_fonbet_goal_prices,profile,
            evaluate_next_goal,format_goal,_pair,_score,_second_half_started)
        decision_mode=os.getenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW").upper()
        joint_audit=None
        if decision_mode=="JOINT":
            from progruz1_joint_audit import JointAudit
            joint_audit=JointAudit(self.db)
        state={"fs_live":0,"fonbet_live":0,"matched":0,
               "priced":0,"evaluated":0,"picked":0,"waits":{},
               "live_signal_mode":mode,
               "high_grade_low_form_overrides":0,
               "scenario_candidates":{},
               "prematch_favourite_captured":0,
               "prematch_favourite_matched":0,
               "flashscore_incidents_verified":0,
               "fs_unmatched":0,"fs_match_reasons":{},
               "fs_identity_found":0,"fs_wait_score_sync":0,
               "fs_wait_book_score":0,"fs_wait_clock_sync":0,
               "fs_wait_live_timer":0}
        live=[]
        fb=[]
        identity_reasons={}
        def done():
            # Real measured scan coverage, independent of the old RADAR.
            with self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS
                    flashscore_goal_scan_metrics(
                    bucket INTEGER PRIMARY KEY,observed_at INTEGER NOT NULL,
                    fs_live INTEGER NOT NULL,fonbet_live INTEGER NOT NULL,
                    matched INTEGER NOT NULL,priced INTEGER NOT NULL,
                    evaluated INTEGER NOT NULL,picked INTEGER NOT NULL)""")
                self.db.execute("""INSERT INTO flashscore_goal_scan_metrics
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(bucket) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    fs_live=excluded.fs_live,
                    fonbet_live=excluded.fonbet_live,
                    matched=excluded.matched,priced=excluded.priced,
                    evaluated=flashscore_goal_scan_metrics.evaluated+
                        excluded.evaluated,
                    picked=flashscore_goal_scan_metrics.picked+
                        excluded.picked""",
                    (now//60,now,state["fs_live"],state["fonbet_live"],
                     state["matched"],state["priced"],
                     state["evaluated"],state["picked"]))
            if live:
                # Dedicated provider coverage audit. This never gates
                # price confirmation or the live decision.
                try:
                    from progruz1_coverage_audit import CoverageAudit
                    CoverageAudit(self.db).record(
                        live=live,reasons=identity_reasons,now=now,
                        fb_count=state["fonbet_live"],
                        found=state["fs_identity_found"],
                        synced=state["matched"],priced=state["priced"],
                        funnel=state,fonbet_roots=fb)
                except (sqlite3.Error,ValueError,TypeError,OverflowError) as exc:
                    print("PROGRUZ1_COVERAGE_AUDIT_ERROR",
                          type(exc).__name__,str(exc)[:120],flush=True)
            return state
        if mode=="OFF":
            return done()
        # Observe authentic 1X2 before kickoff in the SAME full Fonbet
        # snapshot. Persist across restarts; never use LIVE 1X2 as pregame.
        try:
            state["prematch_favourite_captured"]=(
                self.prematch_favourites.capture(snapshot,now))
        except (sqlite3.Error,ValueError,TypeError,OverflowError,KeyError) as exc:
            print("PROGRUZ1_PREMATCH_CAPTURE_ERROR",
                  type(exc).__name__,str(exc)[:140],flush=True)
        def rejected(reason,root=None,kick=None):
            state["waits"][reason]=state["waits"].get(reason,0)+1
            if root is not None:
                try:
                    self._record_evaluation(root,kick,now,"WAIT",reason)
                except sqlite3.OperationalError:
                    pass  # no RADAR tables in isolated/legacy deployments
        try:
            if live_matches is not None:
                live=list(live_matches)
            else:
                live,_=self.fs.list_matches(now)
                # Secondary LIVE master includes lower and youth leagues.
                if hasattr(self.fs,"_alternate_live_matches"):
                    extra=self.fs._alternate_live_matches(now)
                    by_id={str(m.provider_match_id):m for m in live}
                    for m in extra:
                        by_id.setdefault(str(m.provider_match_id),m)
                    live=list(by_id.values())
        except Exception as exc:
            rejected("FLASHSCORE_LIVE_LIST_"+type(exc).__name__)
            return done()
        state["fs_live"]=len(live)
        # Update previously sent LIVE scoreboards even when the match no
        # longer has Fonbet odds or is outside today's entry windows.
        goal_widgets=getattr(self,"signal_widgets",None)
        if goal_widgets is not None and live and not widgets_observed:
            try:
                state["goal_widgets_updated"]=goal_widgets.observe_live(live,now)
            except (sqlite3.Error,ValueError,TypeError,OSError) as exc:
                print("PROGRUZ1_WIDGET_LIVE_ERROR",
                      type(exc).__name__,str(exc)[:130],flush=True)
        follower_delivery=getattr(self,"follower_delivery",None)
        if follower_delivery is not None and live and not widgets_observed:
            try:
                state["follower_widgets_updated"]=follower_delivery.observe_live(
                    live,now)
            except (sqlite3.Error,ValueError,TypeError,OSError) as exc:
                print("PROGRUZ1_FOLLOWER_LIVE_ERROR",
                      type(exc).__name__,str(exc)[:130],flush=True)
        if not live:
            rejected("FLASHSCORE_LIVE_UNAVAILABLE")
            return done()
        from gool_scanner_lite import extract_live_football_identity_roots
        fb,fonbet_diag=extract_live_football_identity_roots(snapshot,now)
        if not fb:
            # A strict legacy decoder remains a safe fallback for feeds
            # without a usable sports tree (and for legacy snapshots).
            # Never narrow a non-empty full catalog to priced-only roots.
            try:
                _,meta=extract_targets(
                    snapshot,now,allowed_sports={"football"})
                fb=[row for row in meta.get("_roots",[])
                    if row.get("sport")=="football"
                    and row.get("phase")=="LIVE"]
            except (KeyError,ValueError,TypeError):
                fb=[]
            fonbet_diag["legacy_catalog_fallback"]=bool(fb)
        state["fonbet_live"]=len(fb)
        state["fonbet_catalog_diagnostics"]=fonbet_diag
        # Fonbet's COMPLETE LIVE football tournament tree is the entry
        # point for fixture identity matching. Flashscore remains the
        # source of truth for score/stats; no price can bypass score sync.
        score_pending={}
        saved_links={
            str(fs_id):(str(root_id),int(kickoff))
            for root_id,kickoff,fs_id in self.db.execute(
                """SELECT root_id,kickoff,fs_id FROM
                flashscore_goal_fixture_links
                WHERE kickoff>=? AND kickoff<=?""",
                (now-2*86400,now+86400)).fetchall()}
        from progruz1_fonbet_first_runtime import match_fonbet_first_live
        first_stats={}
        linked=match_fonbet_first_live(
            live,fb,diagnostics=identity_reasons,
            pending=score_pending,known_links=saved_links,
            stats=first_stats)
        state["fonbet_first"] = first_stats
        state["fonbet_first_added"] = first_stats.get(
            "fonbet_first_additional",0)
        state["matched"]=len(linked)  # synced identity only; safe to check prices
        state["fs_wait_score_sync"]=sum(
            value["reason"]=="FOUND_WAIT_SCORE_SYNC"
            for value in score_pending.values())
        state["fs_wait_book_score"]=sum(
            value["reason"]=="FOUND_WAIT_BOOK_SCORE"
            for value in score_pending.values())
        state["fs_wait_clock_sync"]=sum(
            value["reason"]=="FOUND_WAIT_CLOCK_SYNC"
            for value in score_pending.values())
        state["fs_wait_live_timer"]=sum(
            value["reason"]=="FOUND_WAIT_LIVE_TIMER"
            for value in score_pending.values())
        state["fs_identity_found"]=len(linked)+len(score_pending)
        state["fs_unmatched"]=max(
            0,len(live)-state["fs_identity_found"])
        from collections import Counter
        state["fs_match_reasons"]=dict(Counter(
            reason for reason in identity_reasons.values()
            if reason!="FOUND_ON_FONBET"))
        if linked:
            with self.db:
                for root,(fb,fs) in linked.items():
                    self.db.execute("""INSERT OR IGNORE INTO
                        flashscore_goal_fixture_links
                        (root_id,kickoff,fs_id,match_name,first_seen)
                        VALUES(?,?,?,?,?)""",(root,int(fb["kickoff"]),
                         str(fs.provider_match_id),
                         str(fb.get("match") or ""),now))
        # Fonbet remains a FREE backup quote catalogue; failure to match
        # a Flashscore fixture to Fonbet must NOT suppress its independent
        # Flashscore Odds prices, which are already keyed by Flashscore ID.
        fonbet_quotes=read_fonbet_goal_prices(snapshot,linked) if linked else []
        byroot={}
        for q in fonbet_quotes:
            if q.get("period") in ("1-й тайм","2-й тайм","Матч"):
                byroot.setdefault(str(q["root_id"]),[]).append(q)
        state["fonbet_priced_catalog"]=len(byroot)

        linked_by_fs={
            str(fs.provider_match_id):(root,book,fs)
            for root,(book,fs) in linked.items()}
        known=[]
        for fs in live:
            fs_id=str(fs.provider_match_id or "").strip()
            if not fs_id:
                continue
            try:
                minute=int(fs.minute)
                score=_score((fs.home_score,fs.away_score))
                start_ts=int((getattr(fs,"meta",None) or {}).get(
                    "scheduled_start_ts") or 0)
            except (ValueError,TypeError,OverflowError):
                continue
            if (score is None or getattr(fs,"is_halftime",False)
                    or start_ts<=0 or abs(start_ts-now)>86400):
                continue
            code=(getattr(fs,"meta",None) or {}).get("status_code")
            second=_second_half_started(minute,code)
            if not (45<=minute<=80 if second else 1<=minute<=35):
                continue
            if fs_id in linked_by_fs:
                root,book,_=linked_by_fs[fs_id]
                kick=int(book["kickoff"])
            else:
                root="FS:"+fs_id
                kick=start_ts
                book={"root_id":root,"kickoff":kick,
                      "match":f"{fs.home} — {fs.away}",
                      "league":str(fs.league or ""),
                      "sport":"football","phase":"LIVE"}
            known.append((str(root),int(kick),fs,book))

        if not known:
            rejected("FLASHSCORE_NO_GOAL_ENTRY_WINDOW")
            return done()
        # Fair rotation across ALL Flashscore LIVE candidates, not just
        # games that Fonbet already priced. A modest per-cycle batch avoids
        # blocking the Telegram scheduler on 12-region bulk scans.
        if not hasattr(self,"_flash_goal_cursor"):
            self._flash_goal_cursor=0
        cursor=self._flash_goal_cursor%len(known)
        rotated=known[cursor:]+known[:cursor]
        confirmed_roots=set()
        for root,kick,fs,book in known:
            row=self.db.execute("""SELECT first_seen,last_seen FROM
                adaptive_quote_confirm WHERE root_id=?""",(root,)).fetchone()
            if row and 0<=now-int(row[1])<=80 and now-int(row[0])<=90:
                confirmed_roots.add(root)
        # Two-snapshot quote confirmation must be revisited promptly.
        priority=[v for v in known if v[0] in confirmed_roots]
        batch_rows=list(dict((v[0],v) for v in priority+rotated).values())
        batch_rows=batch_rows[:max(1,min(8,int(limit)))]
        self._flash_goal_cursor=(
            cursor+max(1,len(batch_rows)-len(priority)))%len(known)
        state["fs_in_window"]=len(known)
        state["sampled"]=len(batch_rows)

        # LIVE snapshots are collected for all selected Flashscore
        # fixtures BEFORE the bookmaker quote lookup. Strong attacks
        # cannot lose 5/10-minute history when a price appears late.
        # The exact same verified observations are reused for scoring.
        from progruz1_flashscore_preobserve import observe_live_batch
        prepared,observe_stats=observe_live_batch(
            batch_rows,now=now,flashscore=self.fs,
            goal_pressure=self.goal_pressure,
            scenario_evidence=self.scenario_evidence)
        state["fs_preobserved"]=observe_stats["observed"]
        state["fs_preobserve_failures"]=observe_stats["failures"]

        from progruz1_live_quote_router import priced_round,_regions
        # Check ALL regions over successive fixture visits without
        # performing 12 serial API requests for every match every 8 seconds.
        # Four core geos are always checked; two fallback geos rotate
        # independently for each Flashscore fixture.
        all_geos=_regions()
        core=all_geos[:4]
        extra_geos=all_geos[4:]
        geo_limit=max(1,min(12,int(os.getenv(
            "GOOL_FS_ODDS_REGIONS_PER_CYCLE","6"))))
        if not hasattr(self,"_flash_geo_offsets"):
            self._flash_geo_offsets={}
        regions_by_root={}
        for root,kick,fs,book in batch_rows:
            offset=self._flash_geo_offsets.get(root,0)
            extras=(extra_geos[offset:]+extra_geos[:offset]
                    if extra_geos else ())
            regions_by_root[root]=core+extras
            if extra_geos:
                self._flash_geo_offsets[root]=(offset+max(
                    1,geo_limit-len(core)))%len(extra_geos)
        state["geo_regions_per_cycle"]=geo_limit
        try:
            priced_batch,quote_stats=priced_round(
                [(root,kick,fs) for root,kick,fs,_ in batch_rows],
                fallback_by_root=byroot,max_workers=4,
                regions_by_root=regions_by_root,
                max_regions=geo_limit)
        except Exception as exc:
            print("PROGRUZ1_LIVE_ODDS_ROUTER_ERROR",
                  type(exc).__name__,str(exc)[:140],flush=True)
            priced_batch={
                root:{"quotes":byroot.get(root,[]),
                      "source":"FONBET_LIVE" if byroot.get(root) else "UNPRICED",
                      "meta":{}} for root,kick,fs,book in batch_rows}
            quote_stats={"checked":len(batch_rows),
                         "with_flashscore_quote":0,
                         "with_fonbet_fallback":sum(
                             bool(byroot.get(root)) for root,_,_,_ in batch_rows),
                         "unpriced":sum(
                             not bool(byroot.get(root))
                             for root,_,_,_ in batch_rows),
                         "geo_requests":0}
            rejected("FLASHSCORE_ODDS_ROUTER_"+type(exc).__name__)
        # Direct 1xBet is the final, separately score-verified fallback.
        # Its whole index is relatively expensive, so retry on a cooldown;
        # NEVER use any cached odds between cycles. All returned prices
        # still pass the same two-snapshot quote confirmer.
        quote_stats["with_1xbet_direct"]=0
        quote_stats["xbet_status"]="NOT_NEEDED"
        missing=[(root,kick,fs) for root,kick,fs,book in batch_rows
                 if not priced_batch.get(root,{}).get("quotes")]
        interval=max(45,min(900,int(os.getenv(
            "GOOL_1XBET_FALLBACK_INTERVAL","120"))))
        if missing and now>=getattr(self,"_xbet_direct_next",0):
            self._xbet_direct_next=now+interval
            try:
                from progruz1_live_xbet_fallback import get_direct_xbet_quotes
                retrieved,xfb=get_direct_xbet_quotes(missing[:3])
                for root,offers in retrieved.items():
                    if root in priced_batch and offers:
                        priced_batch[root]={
                            "quotes":offers,"source":"1XBET_DIRECT",
                            "meta":{"requests":xfb.get("detail_checked",0)}}
                quote_stats["with_1xbet_direct"]=len(retrieved)
                quote_stats["xbet_status"]=xfb
            except Exception as exc:
                quote_stats["xbet_status"]=(
                    "ERROR_"+type(exc).__name__)
                print("PROGRUZ1_DIRECT_XBET_FALLBACK_ERROR",
                      type(exc).__name__,str(exc)[:120],flush=True)
        state["quote_sources"]=quote_stats
        state["priced"]=sum(
            bool(r.get("quotes")) for r in priced_batch.values())
        state["in_window_priced"]=state["priced"]
        state["fs_preobserved_unpriced"]=sum(
            root in prepared
            and not priced_batch.get(root,{}).get("quotes")
            for root,kick,fs,book in batch_rows)
        # Synthetic roots are saved to the SAME table used by result
        # settlement and widgets. No confirmed Flashscore fixture can
        # create a second Telegram signal solely by changing quote source.
        with self.db:
            for root,kick,fs,book in batch_rows:
                if not priced_batch.get(root,{}).get("quotes"):
                    continue
                if root.startswith("FS:"):
                    self.db.execute("""INSERT OR IGNORE INTO
                        flashscore_goal_fixture_links
                        (root_id,kickoff,fs_id,match_name,first_seen)
                        VALUES(?,?,?,?,?)""",
                        (root,kick,str(fs.provider_match_id),
                         f"{fs.home} — {fs.away}",now))
        # Keep the original per-root price/history/scenario/notification
        # pipeline; it now accepts both Fonbet and Flashscore-sourced
        # quotes with identical strict selection and result settlement.
        batch=[(root,kick,fs,book) for root,kick,fs,book in batch_rows
               if priced_batch.get(root,{}).get("quotes")]
        if not batch:
            rejected("NO_VERIFIED_LIVE_TOTAL_FROM_FLASH_OR_FONBET")
            return done()
        for root,kick,fs,fb_root in batch:
            if self.db.execute("""SELECT 1 FROM adaptive_picks
                WHERE root_id=? AND kickoff=? AND mode='NOTIFY'""",
                (root,kick)).fetchone():
                continue
            # The SAME Flashscore fixture may be represented by a different
            # Fonbet root after market re-opening. Deduplicate by the stable
            # Flashscore match ID as well, across the entire fixture.
            if self.db.execute("""SELECT 1 FROM adaptive_results r
                JOIN flashscore_goal_fixture_links l
                  ON l.root_id=r.root_id AND l.kickoff=r.kickoff
                WHERE l.fs_id=? AND r.signal_source=?
                  AND r.message_id IS NOT NULL LIMIT 1""",
                (str(fs.provider_match_id),
                 "flashscore_history_live_next_goal_qualitative")
            ).fetchone():
                continue
            score=_score((fs.home_score,fs.away_score))
            observation=prepared.get(root)
            if observation is None:
                rejected("FLASHSCORE_PREOBSERVED_STATS_UNAVAILABLE",root,kick)
                continue
            context,pressure,scenario_evidence,stats=observation
            # The snapshots were already collected before searching for
            # prices; do not reset the attack clock when a market appears.
            history=profile(
                context.get("history"),str(fs.home),str(fs.away),kick,
                min_games=max(3,int(os.getenv("GOOL_FS_HISTORY_MIN_GAMES","4"))))
            pregame_price=self.prematch_favourites.lookup(
                root=root,kickoff=kick,
                match=str(fb_root.get("match") or ""),
                league=str(fb_root.get("league") or ""),now=now)
            incidents={}
            if pregame_price is not None:
                state["prematch_favourite_matched"]+=1
                # Event incidents come from Flashscore and are evaluated
                # separately from Fonbet scores/prices. Unknown = NOT zero.
                try:
                    incidents=self.fs.provider.fetch_live_event_evidence(
                        str(fs.provider_match_id),score)
                except (AttributeError,OSError,ValueError,TimeoutError,
                        TypeError) as exc:
                    print("PROGRUZ1_INCIDENTS_ERROR",
                          type(exc).__name__,str(exc)[:140],flush=True)
                if incidents.get("events_verified") is True:
                    state["flashscore_incidents_verified"]+=1
            selection_ctx=dict(
                context,score=score,minute=minute,
                flashscore_status_code=status_code,
                prematch_odds=pregame_price,
                first_goal_side=incidents.get("first_goal_side"),
                red_cards=incidents.get("red_cards"),
                **scenario_evidence)
            review={} if joint_audit is not None else None
            candidate,reason=evaluate_next_goal(
                priced_batch[root]["quotes"],selection_ctx,pressure,history,
                min_odd=float(os.getenv("GOOL_LIVE_BRAIN_MIN_ODD","1.50")),
                decision_sink=review)
            state["evaluated"]+=1
            # Preserve the old-brain eligible opportunity even if a new
            # scenario/Poisson gate rejects it. One row per fixture/minute.
            if review:
                joint_audit.observe(
                    root=root,kickoff=kick,now=now,
                    match=str(fb_root.get("match") or
                              f"{fs.home} — {fs.away}"),info=review)
            if candidate is None:
                rejected(reason,root,kick)
                continue
            candidate["match"]=str(fb_root.get("match") or
                                   f"{fs.home} — {fs.away}")
            scenario_name=str((candidate.get("scenario") or {}).get("id")
                              or "WITHOUT_SCENARIO")
            if "VERIFIED_5M_PRESSURE_OVERRIDES_MILD_LOW_FORM" in (
                    (candidate.get("scenario") or {}).get("notes") or []):
                state["high_grade_low_form_overrides"]+=1
            sc=state["scenario_candidates"]
            sc[scenario_name]=sc.get(scenario_name,0)+1
            if not self._confirm(candidate,now):
                rejected("FS_WAIT_PRICE_CONFIRM",root,kick)
                continue
            scenario="Flashscore history + recent goal pressure"
            if mode=="SHADOW":
                if not self.db.execute("""SELECT 1 FROM adaptive_picks
                    WHERE root_id=? AND kickoff=? AND mode='SHADOW'""",
                    (root,kick)).fetchone():
                    self._record(candidate,now,"SHADOW",scenario,None)
                    state["picked"]+=1
                continue
            if sender is None or not chat_id:
                rejected("FS_TELEGRAM_NOT_CONFIGURED",root,kick)
                continue
            message=format_goal(candidate,candidate["match"],
                                str(fs.league or fb_root.get("league") or ""))
            msg_id=None
            if goal_widgets is not None:
                try:
                    msg_id=goal_widgets.send_signal(
                        candidate,home=str(fs.home),away=str(fs.away),
                        stats=stats,now=now,
                        league=str(fs.league or fb_root.get("league") or ""),
                        minute_text=(getattr(fs,"meta",None) or {}).get("display_minute"),
                        logo_meta=(getattr(fs,"meta",None) or {}))
                except (sqlite3.Error,ValueError,TypeError,OSError) as exc:
                    print("PROGRUZ1_WIDGET_SEND_ERROR",
                          type(exc).__name__,str(exc)[:130],flush=True)
            # Older Telegram clients/chats can use the original text alert.
            if msg_id is None:
                msg_id=sender(message)
            if msg_id is None:
                rejected("FS_DELIVERY_FAILED",root,kick)
                continue
            self.outcomes.track(candidate,now,message_id=msg_id,
                                message_text=message)
            # The owner remains the sole source of journal entries and
            # analysis. Fan out only after the primary signal is delivered,
            # without producing duplicate picks in adaptive_results.
            if follower_delivery is not None:
                try:
                    follower_delivery.broadcast(
                        candidate,home=str(fs.home),away=str(fs.away),
                        stats=stats,now=now,
                        league=str(fs.league or fb_root.get("league") or ""),
                        minute_text=(getattr(fs,"meta",None) or {}).get(
                            "display_minute"),
                        logo_meta=(getattr(fs,"meta",None) or {}),
                        message=message)
                except (sqlite3.Error,ValueError,TypeError,OSError) as exc:
                    print("PROGRUZ1_FOLLOWER_SEND_ERROR",
                          type(exc).__name__,str(exc)[:130],flush=True)
            self._record(candidate,now,"NOTIFY",scenario,msg_id)
            if joint_audit is not None:
                joint_audit.mark_sent(
                    root=root,kickoff=kick,minute=candidate["minute"])
            state["picked"]+=1
            try:
                self._record_evaluation(root,kick,now,"SIGNAL",
                                        "FS_NEXT_GOAL_CANDIDATE",
                                        f"{candidate['period']} TB {candidate['line']:g}")
            except sqlite3.OperationalError:
                pass
        return done()

    def scan(self,snapshot:dict,now:int,*,mode:str="SHADOW",
             sender=None,settlement=None,chat_id:str="",limit:int=4,
             notify_watch:bool=True)->dict:
        if mode=="OFF":return {"active":0,"picked":0}
        fixtures=self.db.execute("""SELECT root_id,kickoff,
            base_period,base_line,base_direction,match_name
            FROM adaptive_radar WHERE kickoff BETWEEN ? AND ?
            AND status='WATCH_LIVE'""",(now-5*3600,now+300)).fetchall()
        active={str(x[0]):(int(x[1]),str(x[2]),float(x[3]),str(x[4]),str(x[5]))
                for x in fixtures}
        if not active:
            return {"active":0,"picked":0}
        # Radar.observe already verified the LIVE status and persisted any
        # bookmaker ID handoff, before this runtime scans the same snapshot.
        aliases={}
        try:
            for root,kick,provider_root in self.db.execute(
                """SELECT root_id,kickoff,live_root_id FROM adaptive_live_links
                   WHERE kickoff BETWEEN ? AND ?""",(now-5*3600,now+300)):
                if root in active and int(kick)==active[root][0]:
                    aliases[str(root)]=str(provider_root)
        except sqlite3.OperationalError:
            pass  # research fixtures without Shadow initialization
        # Parse current paired LIVE totals once and reuse exactly this same
        # snapshot for the opening notice and subsequent model evaluation.
        market_diagnostics={}
        prices=live_markets(snapshot,now,active,live_aliases=aliases,
                            diagnostics=market_diagnostics)
        # Adds only paired, unblocked ITB1/ITM1/ITB2/ITM2 markets whose
        # source F-ID mapping has been explicitly verified. Unknown F-IDs
        # remain raw ST/BL observations, not model recommendations.
        try:
            prices=add_verified_team_totals(
                snapshot,now,active,prices,live_aliases=aliases)
        except ValueError as exc:
            print("GOOL_INDIVIDUAL_CATALOG_ERROR",str(exc)[:120],flush=True)
        # A tracked fixture is NOT a bet. Production two-brain mode keeps
        # every RADAR/watch record internal and only notifies a selected,
        # quote-confirmed LIVE bet. Older opt-in flows remain testable.
        watch_enabled=(notify_watch and
            os.getenv("GOOL_ADAPTIVE_WATCH_NOTICES","1")=="1")
        watch_notices=0
        if (watch_enabled and mode=="NOTIFY" and sender is not None
                and chat_id):
            watch_notices=self._send_watch_notices(
                snapshot,active,now,sender=sender,quotes=prices,limit=limit)
        byroot={}
        for market in prices:
            byroot.setdefault(market["root_id"],[]).append(market)
        selected=sent=halftime_rechecks=0
        errors={}
        goal_mode=(os.getenv("GOOL_EDGE_LIVE_ENGINE","0")=="1" and
                   os.getenv("GOOL_GOAL_LIVE_MODE","0")=="1")
        for root,(kick,*_) in active.items():
            if root not in byroot:
                already_notified=self.db.execute("""SELECT 1 FROM adaptive_picks
                    WHERE root_id=? AND kickoff=? AND mode='NOTIFY'""",
                    (root,kick)).fetchone()
                if already_notified:
                    continue
                # Distinguish an actually absent current LIVE fixture from
                # blocked/one-sided/missing totals. Old WATCH_LIVE flags
                # persist across cycles and must not inflate missing-quote
                # totals for matches no longer in the current LIVE feed.
                reason=market_diagnostics.get(root,"NO_LIVE_PAIRED_MARKETS")
                errors[reason]=errors.get(reason,0)+1
                self._record_evaluation(root,kick,now,"WAIT",reason)
        # Rotate all games fairly while giving the half-time and early
        # second-half window priority for a concrete LIVE re-evaluation.
        # A verified selection still needs live paired quotes, independent
        # statistics/history and the existing min-odd/edge checks.
        choices=list(byroot.items())
        batch,self._live_scan_cursor=halftime_priority_batch(
            choices,self._live_scan_cursor,limit)
        for root,rows in batch:
            selected+=1
            if 42<=int(rows[0].get("minute") or 0)<=65:
                halftime_rechecks+=1
            if self.value is not None:
                # Bounded snapshot of both TB/TM prices, even without a model
                # choice. Low I/O: 4 paired quotes per fixture / 5min bucket.
                try:
                    closest=sorted(rows,key=lambda m:
                                   abs(float(m["line"])-active[root][2]))[:4]
                    for quote in closest:
                        self.value.observe_market(quote,now,phase="LIVE")
                except Exception as exc:
                    print("GOOL_VALUE_OBSERVE_ERROR",type(exc).__name__,flush=True)
            kickoff,period,line,direction,name=active[root]
            # Separate movement brain: ST/BL outcomes never veto a
            # PREMOVE-derived LIVE pick. This brain has its own one-pick guard.
            if "-" not in name and "—" not in name:
                errors["MALFORMED_TEAMS"]=errors.get("MALFORMED_TEAMS",0)+1
                self._record_evaluation(root,kickoff,now,"REJECT","MALFORMED_TEAMS")
                continue
            home,_,away=name.partition(" — ")
            if not away:
                home,_,away=name.partition(" - ")
            if not away:
                errors["MALFORMED_TEAMS"]=errors.get("MALFORMED_TEAMS",0)+1
                self._record_evaluation(root,kickoff,now,"REJECT","MALFORMED_TEAMS")
                continue
            if goal_mode:
                # No experimental Poisson model or Flashscore strict stats
                # matching in the agreed simple "RADAR -> LIVE goal" path.
                # The match was already verified against current Fonbet LIVE
                # by Radar.observe. Exact shots and odds still fail closed.
                done,reason=self._goal_fixture(
                    root,rows,now,mode=mode,sender=sender,chat_id=chat_id,
                    baseline=(period,line,direction),name=name,
                    league=str(rows[0].get("league") or ""))
                if done:
                    sent+=1
                else:
                    errors[reason]=errors.get(reason,0)+1
                continue
            score=rows[0]["match_score"]
            # PREMATCH preparation already matched the Flashscore fixture
            # independently and persisted its source ID. Feed that ID to
            # the LIVE adapter as an anchored fallback, never as automatic
            # proof of today's score or game state.
            fixture_id=None
            if self.pregame is not None:
                found=self.db.execute("""SELECT fixture_id
                    FROM adaptive_prematch_preparation
                    WHERE root_id=? AND kickoff=?""",(root,kickoff)).fetchone()
                if found and found[0]:fixture_id=str(found[0])
            try:
                options=dict(root=root,home=home,away=away,
                             kickoff=kickoff,now=now,score=score)
                if fixture_id is not None:
                    options["fixture_id"]=fixture_id
                ctx=self.fs.context(**options)
            except Exception as e:
                errors[type(e).__name__]=errors.get(type(e).__name__,0)+1
                self._record_evaluation(root,kickoff,now,"ERROR",
                                        "FLASHSCORE_"+type(e).__name__)
                continue
            # Enrich ONLY genuinely verified LIVE fixture statistics; do not
            # use bookmaker events to assert an independent final score.
            # Full-match and 1H shot counters are kept in separate scopes.
            # No extra HTTP request when the Fonbet timeline cache is fresh.
            try:
                ctx=self.fonbet_live_stats.enrich(
                    ctx,feed_root_id=str(rows[0].get("feed_root_id") or root),
                    current_half=str(rows[0].get("current_half") or ""),
                    now=now)
            except Exception as exc:
                print("PROGRUZ_FONBET_STATS_ERROR",type(exc).__name__,flush=True)
            # LIVE 1H goals must use independently verified 1H scores.
            # Never reinterpret full-match goals as a half-time sample.
            if (self.pregame is not None and
                    any(row.get("period")=="1-й тайм" for row in rows)
                    and isinstance(ctx.get("history"),dict)):
                ctx=dict(ctx,history=self.pregame.half.enrich(ctx["history"]))
            # Independent VALUE probability evaluation happens before every
            # ADAPTIVE notification/edge gate, including existing sent picks.
            if self.value is not None:
                try:
                    from gool_value_model import live_probability,journal_pair
                    closest=sorted(rows,key=lambda m:
                                   abs(float(m["line"])-active[root][2]))[:4]
                    for quote in closest:
                        p_over,basis=live_probability(quote,ctx,home,away)
                        if p_over is None:
                            self.value.mark_unavailable(quote,now,phase="LIVE",
                                                        reason=basis)
                        else:
                            journal_pair(self.value,quote,p_over,basis,now,"LIVE")
                except Exception as exc:
                    print("GOOL_VALUE_LIVE_PROB_ERROR",type(exc).__name__,flush=True)
            old=self.db.execute("""SELECT mode,created_at FROM adaptive_picks
                WHERE root_id=? AND kickoff=?""",(root,kickoff)).fetchone()
            if old and old[0]=="NOTIFY":
                continue
            if (watch_enabled and mode=="NOTIFY" and not self.db.execute(
                    "SELECT 1 FROM adaptive_watch_notices WHERE root_id=? AND kickoff=?",
                    (root,kickoff)).fetchone()):
                errors["WAIT_WATCH_NOTICE"]=errors.get("WAIT_WATCH_NOTICE",0)+1
                self._record_evaluation(root,kickoff,now,"WAIT","WAIT_WATCH_NOTICE")
                continue
            if old and mode=="SHADOW" and now-int(old[1])<60:
                continue
            # Link the hour-by-hour PREMOVE hypothesis to the actual score,
            # available time and independently checked LIVE attack evidence.
            baseline=(period,line,direction)
            motion_row=self.db.execute("""SELECT base_move_pp FROM adaptive_radar
                WHERE root_id=? AND kickoff=?""",(root,kickoff)).fetchone()
            move_pp=float(motion_row[0]) if motion_row and motion_row[0] is not None else None
            matching=next((r for r in rows if r.get("period")==period
                and abs(float(r["line"])-line)<0.001),rows[0])
            trajectory=evaluate_trajectory(
                baseline,matching,ctx,home=home,away=away,move_pp=move_pp)
            self._record_trajectory(root,kickoff,now,"PREMOVE",baseline,trajectory)
            # The 1H is separately analysed even when PREMOVE moved a
            # whole-match line; do not falsely describe this as a 1H
            # *premove* movement when the original market was full time.
            half_trajectory=None
            if period!="1-й тайм" and any(
                    r.get("period")=="1-й тайм" for r in rows):
                half=next((r for r in rows if r.get("period")=="1-й тайм"),None)
                if half is not None:
                    hbase=("1-й тайм",float(half["line"]),direction)
                    half_trajectory=evaluate_trajectory(
                        hbase,half,ctx,home=home,away=away,move_pp=None)
                    self._record_trajectory(
                        root,kickoff,now,"FIRST_HALF",hbase,half_trajectory)
            ctx=dict(ctx,premove_trajectory=trajectory,premove_baseline=baseline)
            edge_enabled=os.getenv("GOOL_EDGE_LIVE_ENGINE","0")=="1"
            if edge_enabled:
                # Every PREMOVE-selected LIVE fixture is recorded even when
                # it fails the model/price checks; this is the research set.
                try:
                    self.edge_lab.observe(root=root,kickoff=kickoff,now=now,
                        markets=rows,ctx=ctx,premove_direction=direction,
                        premove_shift=move_pp)
                    # Store TM as a separate research-only quote, with
                    # real LIVE price/time and no Telegram notification.
                    # This includes unattractive TM quotes to build a
                    # negative control cohort rather than just winners.
                    under_candidate,under_reason=select_edge_live(
                        rows,ctx,home=home,away=away,min_odd=1.50,
                        allow_under=True,only_side="TM")
                    if under_candidate:
                        self.edge_lab.observe_under_shadow(under_candidate,now)
                except Exception as exc:
                    print("PROGRUZ_EDGE_LAB_OBSERVE_ERROR",type(exc).__name__,flush=True)
            # PREMOVE is only a match selector; LIVE decides the offered
            # market and requires a currently available LIVE price.
            try:
                min_live_odd=float(os.getenv("GOOL_LIVE_BRAIN_MIN_ODD","1.50"))
            except (ValueError,TypeError):
                raise ValueError("GOOL_LIVE_BRAIN_MIN_ODD must be numeric")
            if not math.isfinite(min_live_odd) or min_live_odd<1.50:
                raise ValueError("GOOL_LIVE_BRAIN_MIN_ODD must be >= 1.50")
            over_diagnostic={}
            if edge_enabled:
                pick,reason=select_edge_live(
                    rows,ctx,home=home,away=away,min_odd=min_live_odd,
                    diagnostics=over_diagnostic)
                # The uncalibrated TB model may reject an entire night of
                # valid bookmaker pairs; log whether the bottleneck was
                # missing quotes/statistics or simply its model-price gate.
                self.edge_lab.record_over_diagnostic(
                    root=root,kickoff=kickoff,now=now,
                    detail=over_diagnostic,reason=reason)
            else:
                pick,reason=select_market(
                    rows,ctx,home=home,away=away,min_odd=min_live_odd)
            if not pick:
                if edge_enabled:
                    self.edge_lab.decide(root=root,kickoff=kickoff,now=now,
                                         decision="HOLD",reason=reason)
                errors[reason]=errors.get(reason,0)+1
                self._record_evaluation(root,kickoff,now,"REJECT",reason)
                continue
            scenario=scenario_for_pick(pick,baseline,trajectory)
            if not self._confirm(pick,now):
                errors["WAIT_QUOTE_CONFIRM"]=errors.get("WAIT_QUOTE_CONFIRM",0)+1
                self._record_evaluation(root,kickoff,now,"WAIT",
                                        "WAIT_QUOTE_CONFIRM",
                                        f"{pick['side']} {pick['line']:g} · {pick['period']}")
                if edge_enabled:
                    self.edge_lab.record_over_diagnostic(
                        root=root,kickoff=kickoff,now=now,
                        detail=over_diagnostic,reason="WAIT_QUOTE_CONFIRM")
                continue
            if mode=="NOTIFY" and (sender is None or not chat_id):
                errors["NOT_CONFIGURED"]=errors.get("NOT_CONFIGURED",0)+1
                continue
            if self.value is not None:
                try:
                    self.value.assess_pick(pick,rows,now,phase="LIVE")
                except Exception as exc:
                    # VALUE is isolated: a journal error never turns a
                    # confirmed ADAPTIVE selection into a different signal.
                    print("GOOL_VALUE_ASSESS_ERROR",type(exc).__name__,flush=True)
            prep=(self.pregame.summary(root,kickoff) if self.pregame else {})
            shift_row=self.db.execute("""SELECT base_move_pp,league
                FROM adaptive_radar WHERE root_id=? AND kickoff=?""",
                (root,kickoff)).fetchone()
            league=(shift_row[1] if shift_row and shift_row[1] else
                    rows[0].get("league") or "")
            if edge_enabled:
                text=format_edge_pick(
                    pick,name=name,league=league,
                    baseline=(period,line,direction),scenario=scenario)
                self.edge_lab.decide(
                    root=root,kickoff=kickoff,now=now,decision="EDGE_CANDIDATE",
                    reason=pick.get("basis",""))
            else:
                text=format_pick_message(
                    pick,scenario,(period,line,direction),ctx,name,prep,
                    market_count=len(rows),shift_pp=(
                        float(shift_row[0]) if shift_row else None),
                    league=league,trajectory=trajectory,
                    first_half_trajectory=half_trajectory)
            if mode=="SHADOW":
                self._record_evaluation(root,kickoff,now,"SHADOW_PICK",scenario,
                         f"{pick['side']} {pick['line']:g} · {pick['period']}")
                self._record(pick,now,mode,scenario,None)
                print("GOOL_ADAPTIVE_SHADOW_PICK",text.replace("\n"," | "),flush=True)
                sent+=1
                continue
            msg_id=sender(text)
            if msg_id is None:
                errors["DELIVERY_FAILED"]=errors.get("DELIVERY_FAILED",0)+1
                continue
            # Freeze only the selected market in the second-brain journal.
            # Never insert ADAPTIVE into signal_settlement (ST/BL's journal).
            self.outcomes.track(pick,now,message_id=msg_id,message_text=text)
            if edge_enabled:
                self.edge_lab.record_over_diagnostic(
                    root=root,kickoff=kickoff,now=now,
                    detail=over_diagnostic,reason="SENT")
                self.edge_lab.decide(
                    root=root,kickoff=kickoff,now=now,decision="SENT",
                    reason=scenario)
            self._record_evaluation(root,kickoff,now,"SIGNAL",scenario,
                    f"{pick['side']} {pick['line']:g} · {pick['period']}")
            self._record(pick,now,mode,scenario,msg_id)
            sent+=1
        return {"active":len(active),"markets":len(prices),
                "fixtures_with_prices":len(byroot),
                "deferred_for_next_cycle":max(0,len(byroot)-selected),
                "watch_notices":watch_notices,
                "halftime_rechecks":halftime_rechecks,
                "evaluated":selected,"picked":sent,"reasons":errors}
