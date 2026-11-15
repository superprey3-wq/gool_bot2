"""GOOL RADAR -> independent LIVE comparison of all valid TB/TM totals.

Paired live quotes and genuine Fonbet statistics are mandatory. The
probability heuristic is uncalibrated; it cannot guarantee a betting edge.
Existing first-brain ST/BL and settlement remain separate.
"""
from __future__ import annotations
import math
import sqlite3


def _pair_int(value):
    if not isinstance(value,(tuple,list)) or len(value)!=2:
        return None
    if any(type(x) is not int or x<0 or x>80 for x in value):
        return None
    return tuple(value)


class GoalPressure:
    """Durable observed shots and scores for LIVE pressure/quiet decisions."""
    def __init__(self,db:sqlite3.Connection):
        self.db=db
        db.execute("""CREATE TABLE IF NOT EXISTS goal_live_pressure(
            root_id TEXT NOT NULL,kickoff INTEGER NOT NULL,
            phase TEXT NOT NULL,bucket INTEGER NOT NULL,observed_at INTEGER NOT NULL,
            shots_home INTEGER NOT NULL,shots_away INTEGER NOT NULL,
            sot_home INTEGER NOT NULL,sot_away INTEGER NOT NULL,
            home INTEGER NOT NULL,away INTEGER NOT NULL,
            PRIMARY KEY(root_id,kickoff,phase,bucket)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS goal_live_pressure_time
            ON goal_live_pressure(observed_at)""")
        db.commit()

    def observe(self,*,root,kickoff,now,minute,phase,score,shots,on,
                early=False):
        score=_pair_int(score);shots=_pair_int(shots);on=_pair_int(on)
        if (not score or not shots or not on or
                any(a>b for a,b in zip(on,shots)) or
                phase not in ("1-й тайм","2-й тайм")):
            return None
        with self.db:
            self.db.execute("""INSERT INTO goal_live_pressure
                (root_id,kickoff,phase,bucket,observed_at,
                 shots_home,shots_away,sot_home,sot_away,home,away)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(root_id,kickoff,phase,bucket) DO UPDATE SET
                observed_at=excluded.observed_at,
                shots_home=excluded.shots_home,shots_away=excluded.shots_away,
                sot_home=excluded.sot_home,sot_away=excluded.sot_away,
                home=excluded.home,away=excluded.away""",
                (str(root),int(kickoff),str(phase),int(now)//30,int(now),
                 *shots,*on,*score))
            self.db.execute("""DELETE FROM goal_live_pressure
                WHERE observed_at<?""",(int(now)-7*86400,))
        # Independently observed trajectory in each half. A zero-shot
        # *snapshot* is not evidence of a five-minute quiet interval.
        rows=self.db.execute("""SELECT observed_at,shots_home,shots_away,
            sot_home,sot_away,home,away FROM goal_live_pressure
            WHERE root_id=? AND kickoff=? AND phase=?
              AND observed_at BETWEEN ? AND ?
            ORDER BY observed_at""",(str(root),int(kickoff),phase,
                                      int(now)-2*3600,int(now))).fetchall()
        # PROGRUZ1 can assess early-half pressure using two genuine
        # snapshots >=30s apart; the legacy engines retain 90s.
        evidence_age=30 if early else 90
        if len(rows)<2 or now-int(rows[0][0])<evidence_age:
            return {"shots":0,"sot":0,"sample_age":0,
                    "recent_shots":0,"recent_sot":0}
        first=rows[0]
        # If a provider corrects statistics downwards, do not derive a
        # negative attack rate or mistakenly treat it as a new attack.
        total_shots=sum(shots)-int(first[1])-int(first[2])
        total_sot=sum(on)-int(first[3])-int(first[4])
        if total_shots<0 or total_sot<0:
            return None
        window=8*60 if phase=="1-й тайм" else 12*60
        recent=next((row for row in rows if int(row[0])>=now-window),rows[-1])
        # A time-window baseline must be at least 90s old. Otherwise it
        # means we have insufficient evidence of pressure in that window.
        recent_age=now-int(recent[0])
        recent_shots=(sum(shots)-int(recent[1])-int(recent[2])
                      if recent_age>=evidence_age else 0)
        recent_sot=(sum(on)-int(recent[3])-int(recent[4])
                    if recent_age>=evidence_age else 0)
        # Real per-team shot deltas from an independently observed 8-10m
        # baseline. This is observational evidence, not an estimate of who
        # attacked. If no old enough same-half snapshot exists, report None
        # rather than manufacture zero shots for the trailing team.
        ten_min_baseline=next((
            row for row in rows
            if 480<=now-int(row[0])<=600
        ),None)
        ten_min_age=(now-int(ten_min_baseline[0])
                     if ten_min_baseline is not None else 0)
        ten_min_team_shots=None
        ten_min_team_sot=None
        if ten_min_baseline is not None:
            delta=(shots[0]-int(ten_min_baseline[1]),
                   shots[1]-int(ten_min_baseline[2]))
            on_delta=(on[0]-int(ten_min_baseline[3]),
                      on[1]-int(ten_min_baseline[4]))
            if all(v>=0 for v in delta+on_delta) and all(
                    a<=b for a,b in zip(on_delta,delta)):
                ten_min_team_shots=delta
                ten_min_team_sot=on_delta
        # Independently observed team-specific 4-6 minute trajectory.
        # Without two real snapshots, report UNKNOWN rather than
        # assuming the other team is attacking (or quiet).
        five_min_baseline=next((
            row for row in rows
            if 240<=now-int(row[0])<=360
        ),None)
        five_min_age=(now-int(five_min_baseline[0])
                      if five_min_baseline is not None else 0)
        five_min_team_shots=None
        five_min_team_sot=None
        if five_min_baseline is not None:
            delta=(shots[0]-int(five_min_baseline[1]),
                   shots[1]-int(five_min_baseline[2]))
            on_delta=(on[0]-int(five_min_baseline[3]),
                      on[1]-int(five_min_baseline[4]))
            if all(v>=0 for v in delta+on_delta) and all(
                    a<=b for a,b in zip(on_delta,delta)):
                five_min_team_shots=delta
                five_min_team_sot=on_delta
        # A valid zero-shot interval requires an actual old baseline;
        # a newly created snapshot must never masquerade as no attacks.
        fresh_window=bool(recent_age>=300)
        first_score=(int(first[5]),int(first[6]))
        goals_since_first=sum(score)-sum(first_score)
        # If the scoreboard records goals but sportscast counts fewer
        # shots on target, the counters are incomplete. Do not infer quiet.
        stats_consistent=(goals_since_first>=0 and
                          total_sot>=goals_since_first)
        # Count only observed score stability, not time since kickoff.
        # The score must be unchanged in ALL observations since at least
        # 20 minutes ago. HT goals alone do not establish 2H stability.
        stable_window=5*60 if phase=="1-й тайм" else 20*60
        cutoff=int(now)-stable_window
        older=[row for row in rows if int(row[0])<=cutoff]
        goal_free_seconds=0
        if older:
            anchor=older[-1]
            current_score=tuple(score)
            if all((int(row[5]),int(row[6]))==current_score
                   for row in rows if int(row[0])>=int(anchor[0])):
                goal_free_seconds=int(now)-int(anchor[0])
        # No quiet signal unless an older independent observation within
        # this SAME half has an unchanged score.
        stable=self.db.execute("""SELECT home,away FROM goal_live_pressure
            WHERE root_id=? AND kickoff=? AND phase=?
              AND observed_at BETWEEN ? AND ?
            ORDER BY observed_at DESC LIMIT 1""",
            (str(root),int(kickoff),phase,
             int(now)-(600 if phase=="2-й тайм" else 420),
             int(now)-(300 if phase=="2-й тайм" else 240))).fetchone()
        return {"shots":total_shots,"sot":total_sot,
                "shots_by_team":(shots[0]-int(first[1]),shots[1]-int(first[2])),
                "sot_by_team":(on[0]-int(first[3]),on[1]-int(first[4])),
                "sample_age":now-int(first[0]),
                "recent_shots":max(0,recent_shots),
                "recent_sot":max(0,recent_sot),
                "recent_age":recent_age,
                "recent_10m_shots_by_team":ten_min_team_shots,
                "recent_10m_sot_by_team":ten_min_team_sot,
                "recent_10m_age":ten_min_age,
                "recent_5m_shots_by_team":five_min_team_shots,
                "recent_5m_sot_by_team":five_min_team_sot,
                "recent_5m_age":five_min_age,
                "fresh_window":fresh_window,
                "stats_consistent":stats_consistent,
                "goals_since_first":goals_since_first,
                "goal_free_seconds":goal_free_seconds,
                "score_stable":bool(stable and score==
                    (int(stable[0]),int(stable[1])))}


def select_goal_live(markets, ctx, *, pressure=None, min_odd=1.50,
                     allow_under=True):
    """Select the best supported TB/TM among every real paired LIVE line."""
    from gool_total_choice import select_best_total
    return select_best_total(markets, ctx, pressure=pressure,
                             min_odd=min_odd, allow_under=allow_under)


def format_goal_pick(pick, *, name, league="", baseline=None):
    """Short, explicitly *uncalibrated* comparison of the chosen total."""
    from gool_live_markets_v2 import market_label
    direction = baseline[2] if baseline else None
    move = ("ТБ" if direction == "TB" else
            "ТМ" if direction == "TM" else "неизвестно")
    origin_line=("📡 Источник: ST2 одного голевого исхода; "
                 "это НЕ направление прогруза.\n"
                 if pick.get("selection_origin")=="ST2_GOAL" else
                 f"📈 RADAR: {move} (не диктует выбор).\n")
    league = " ".join(str(league).split())[:120] or "не указан"
    chosen = market_label(pick)
    push = (f" · возврат {pick.get('p_push', 0):.1%}"
            if pick.get("p_push", 0) > 0.005 else "")
    scenario=pick.get("live_scenario") or "LIVE и движение линии"
    scenario_line=f"🔎 Сценарий: {scenario}.\n"
    return (
        "🎯 GOOL · LIVE · ЛУЧШИЙ ТОТАЛ (эксперимент)\n"
        f"⚽ {name}\n🏆 {league}\n"
        f"⏱ {pick['minute']}' · {pick['score'][0]}:{pick['score'][1]}\n"
        f"🎯 {pick['period']} · {chosen} {pick['line']:g} @{pick['odd']:.2f}\n"
        f"📊 Fonbet: {pick['pressure_detail']}\n"
        +scenario_line+
        origin_line+
        f"🔎 ТБ/ТМ проверено: {pick.get('directions_examined', {}).get('TB', 0)}/"
        f"{pick.get('directions_examined', {}).get('TM', 0)}, допущено: "
        f"{pick.get('directions_passing', {}).get('TB', 0)}/"
        f"{pick.get('directions_passing', {}).get('TM', 0)}.\n"
        f"💰 Безубыточный порог при этом кэфе: "
        f"{pick['p_break_even']:.1%}.\n"
        "⚠️ Вероятность модели НЕ откалибрована; её внутренние "
        "оценки нельзя считать достоверным шансом прохода. "
        "Ставка не размещена.\n"
        "⏳ ОЖИДАЕТ ПОДТВЕРЖДЁННОГО РЕЗУЛЬТАТА"
    )
