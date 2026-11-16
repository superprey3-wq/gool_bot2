"""Persistent Flashscore-first live watcher for ПРОГРУЗ 1.

Stage 1: register ALL genuine Flashscore LIVE fixtures, irrespective of odds.
Stage 2: rotate bounded LIVE context/statistics reads over every fixture.
Stage 3: remember promising games and give them frequent fresh observations.
Stage 4: request bookmakers' next-goal prices only for active WATCH/HOT games.

The watch score is an operational scheduling heuristic, NOT a goal
probability, price-edge estimate, or permission to send a signal. All
real picks still pass the independent existing goal-brain safety gates.
"""
from __future__ import annotations

import sqlite3

WATCH_SCORE=38
HOT_SCORE=66
WATCH_KEEP_SECONDS=15*60


def _match_id(fs):
    return str(getattr(fs,"provider_match_id","") or "").strip()


def _int_pair(value):
    if (not isinstance(value,(tuple,list)) or len(value)!=2
            or not all(type(x) is int and 0<=x<=100 for x in value)):
        return None
    return tuple(value)


def interest_score(*,minute,score,stats,pressure):
    """Rank which verified LIVE match needs a closer look; never bet on it."""
    if not isinstance(pressure,dict):
        return 0
    shots=_int_pair(stats.get("shots"))
    sot=_int_pair(stats.get("shots_on_target"))
    if shots is None or sot is None or any(sot[i]>shots[i] for i in (0,1)):
        return 0
    try:
        minute=int(minute)
    except (TypeError,ValueError):
        return 0
    total_shots=sum(shots)
    total_sot=sum(sot)
    fresh_shots=max(0,int(pressure.get("recent_shots") or 0))
    fresh_sot=max(0,int(pressure.get("recent_sot") or 0))
    verified=bool(pressure.get("stats_consistent"))
    if not verified:
        return 0
    # Historical match stats earn DISCOVERY priority, not a goal alert.
    score_pts=(9 if total_shots>=7 else 0)
    score_pts+=12 if total_shots>=12 else 0
    score_pts+=13 if total_sot>=3 else 0
    score_pts+=7 if total_sot>=5 else 0
    score_pts+=6 if min(sot)>=1 else 0
    # Genuine observed LIVE developments earn WATCH/HOT priority.
    if pressure.get("recent_age",0)>=90:
        score_pts+=12 if fresh_shots>=2 else 0
        score_pts+=12 if fresh_shots>=4 else 0
        score_pts+=16 if fresh_sot>=1 else 0
        score_pts+=8 if fresh_sot>=2 else 0
    shots5=_int_pair(pressure.get("recent_5m_shots_by_team"))
    sot5=_int_pair(pressure.get("recent_5m_sot_by_team"))
    if (shots5 is not None and sot5 is not None
            and 240<=int(pressure.get("recent_5m_age") or 0)<=360):
        score_pts+=10 if sum(shots5)>=3 else 0
        score_pts+=10 if sum(sot5)>=1 else 0
        score_pts+=7 if min(shots5)>=1 else 0
    if (minute>=73 and fresh_sot<2):
        score_pts=min(score_pts,64)
    return min(100,score_pts)


class LiveWatchlist:
    """All LIVE fixtures, persisted watch states, fair observation schedule."""

    def __init__(self,db:sqlite3.Connection):
        self.db=db
        db.execute("""CREATE TABLE IF NOT EXISTS progruz1_live_watchlist (
            fs_id TEXT PRIMARY KEY,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            league TEXT NOT NULL,
            kickoff INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            score_home INTEGER NOT NULL,
            score_away INTEGER NOT NULL,
            last_observed INTEGER NOT NULL DEFAULT 0,
            observations INTEGER NOT NULL DEFAULT 0,
            interest INTEGER NOT NULL DEFAULT 0,
            focus_until INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'TRACKING',
            last_price_at INTEGER NOT NULL DEFAULT 0,
            last_reason TEXT NOT NULL DEFAULT ''
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS
            progruz1_live_watchlist_active
            ON progruz1_live_watchlist(last_seen,status,interest)""")
        db.commit()

    def catalog(self,live,*,now):
        """Store every LIVE fixture with verified fixture ID and score."""
        count=0
        with self.db:
            for fs in live:
                fid=_match_id(fs)
                score=_int_pair((getattr(fs,"home_score",None),
                                 getattr(fs,"away_score",None)))
                if not fid or score is None:
                    continue
                try:
                    minute=int(fs.minute)
                    kickoff=int((getattr(fs,"meta",None) or {}).get(
                        "scheduled_start_ts") or 0)
                except (ValueError,TypeError,OverflowError):
                    continue
                if minute<0 or minute>130:
                    continue
                # Keep the stable provider ID even when a bookmaker
                # market/root changes or a fixture lacks a kickoff.
                self.db.execute("""INSERT INTO progruz1_live_watchlist
                    (fs_id,first_seen,last_seen,home,away,league,
                     kickoff,minute,score_home,score_away)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(fs_id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    home=excluded.home,away=excluded.away,
                    league=excluded.league,
                    kickoff=CASE WHEN excluded.kickoff>0
                        THEN excluded.kickoff
                        ELSE progruz1_live_watchlist.kickoff END,
                    minute=excluded.minute,
                    score_home=excluded.score_home,
                    score_away=excluded.score_away""",
                    (fid,int(now),int(now),str(fs.home),str(fs.away),
                     str(getattr(fs,"league","") or ""),kickoff,minute,
                     score[0],score[1]))
                count+=1
            self.db.execute(
                "DELETE FROM progruz1_live_watchlist WHERE last_seen<?",
                (int(now)-3*86400,))
        return count

    def schedule(self,universe,*,now,capacity,confirmed_ids=()):
        """Choose details WITHOUT restricting universe to priced games.

        A slice is always reserved for overlooked/unobserved fixtures.
        Hot/confirmed fixtures are revisited faster without starving
        new matches, and recent reads aren't needlessly repeated.
        """
        limit=max(1,int(capacity))
        fid_rows={}
        for row in universe:
            fid=_match_id(row[2])
            if fid:
                fid_rows[fid]=row
        if not fid_rows:
            return []
        ids=tuple(fid_rows)
        qs=",".join("?" for _ in ids)
        rows=self.db.execute(
            "SELECT fs_id,last_observed,interest,focus_until,status "
            "FROM progruz1_live_watchlist WHERE fs_id IN ("+qs+")",
            ids).fetchall()
        seen={r[0]:(int(r[1]),int(r[2]),int(r[3]),str(r[4]))
              for r in rows}
        confirmed=set(str(x) for x in confirmed_ids)
        targeted=[]
        exploratory=[]
        for fid,item in fid_rows.items():
            observed,interest,focus,status=seen.get(
                fid,(0,0,0,"TRACKING"))
            age=(int(now)-observed) if observed else 10**9
            focused=focus>=now and (
                status in ("WATCH","HOT","WAIT_PRICE","WAIT_ENTRY",
                           "WAIT_SIGNAL","PRICE_CONFIRM"))
            urgent=fid in confirmed
            # Do not requery a cached minute-old Flashscore response.
            min_gap=30 if urgent else 35 if focused else 65
            if age<min_gap:
                continue
            if status=="SENT":
                continue
            if focused or urgent:
                targeted.append((int(urgent),interest,age,fid,item))
            else:
                exploratory.append((age,fid,item))
        targeted.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
        exploratory.sort(key=lambda x:x[0],reverse=True)
        # At least 1/3 of detail reads explore the rest of LIVE football.
        reserve=min(limit,max(1,(limit+2)//3))
        chosen=[]
        for _,_,_,fid,item in targeted[:max(0,limit-reserve)]:
            chosen.append(item)
        for _,fid,item in exploratory[:limit-len(chosen)]:
            chosen.append(item)
        if len(chosen)<limit:
            present={_match_id(item[2]) for item in chosen}
            for _,_,_,fid,item in targeted:
                if fid not in present:
                    chosen.append(item)
                    present.add(fid)
                if len(chosen)>=limit:
                    break
        return chosen

    def record(self,fs,*,now,stats,pressure):
        fid=_match_id(fs)
        if not fid:
            return None
        match_stats={}
        for k in ("shots","shots_on_target"):
            pair=stats.get(k) if isinstance(stats,dict) else None
            if (isinstance(pair,(tuple,list)) and len(pair)==2
                    and all(type(v) in (int,float) and float(v).is_integer()
                            and 0<=v<=100 for v in pair)):
                match_stats[k]=tuple(int(v) for v in pair)
        level=interest_score(
            minute=fs.minute,score=(fs.home_score,fs.away_score),
            stats=match_stats,pressure=pressure)
        now=int(now)
        row=self.db.execute("""SELECT interest,focus_until,status
            FROM progruz1_live_watchlist WHERE fs_id=?""",
            (fid,)).fetchone()
        if row is None:
            return None
        old_interest,focus,old_status=int(row[0]),int(row[1]),str(row[2])
        if old_status=="SENT":
            status="SENT"
        elif level>=HOT_SCORE and bool(pressure.get("recent_sot")):
            status="HOT"
            focus=max(focus,now+WATCH_KEEP_SECONDS)
        elif level>=WATCH_SCORE:
            status="WATCH"
            focus=max(focus,now+WATCH_KEEP_SECONDS)
        elif focus>=now:
            status="WAIT_SIGNAL"
        else:
            status="TRACKING"
        with self.db:
            self.db.execute("""UPDATE progruz1_live_watchlist
                SET last_observed=?,observations=observations+1,
                    interest=?,focus_until=?,status=?,last_reason=?
                WHERE fs_id=?""",
                (now,level,focus,status,
                 "VERIFIED_LIVE_STATS" if level else
                 "LOW_LIVE_INTEREST",fid))
        return {"fs_id":fid,"interest":level,"status":status,
                "focus_until":focus,"previous_interest":old_interest}

    def price_candidates(self,batch_rows,prepared,*,now,confirmed_ids=()):
        """Only followed, fresh and in-window games reach bookmakers."""
        confirmed=set(str(x) for x in confirmed_ids)
        chosen=[]
        for item in batch_rows:
            root,kick,fs,book=item
            fid=_match_id(fs)
            observed=prepared.get(root)
            if observed is None:
                continue
            # A fixture with unknown kickoff stays monitored, but cannot
            # become a correctly settled bookmaker bet.
            if int(kick)<=0:
                continue
            try:
                minute=int(fs.minute)
            except (TypeError,ValueError):
                continue
            second=minute>=46 or (minute==45 and str(
                (getattr(fs,"meta",None) or {}).get(
                    "status_code") or "")=="13")
            if not (45<=minute<=80 if second else 1<=minute<=35):
                continue
            r=self.db.execute(
                "SELECT status,interest FROM progruz1_live_watchlist "
                "WHERE fs_id=?",(fid,)).fetchone()
            if (r and r[0]!="SENT"
                    and (r[0] in ("WATCH","HOT","WAIT_PRICE","WAIT_ENTRY",
                                  "WAIT_SIGNAL","PRICE_CONFIRM")
                         or fid in confirmed)):
                chosen.append(item)
        return chosen

    def note_price(self,fs_id,*,now,has_price):
        with self.db:
            self.db.execute("""UPDATE progruz1_live_watchlist SET
                last_price_at=?,status=CASE
                    WHEN status='SENT' THEN 'SENT'
                    WHEN ? THEN 'WAIT_SIGNAL'
                    ELSE 'WAIT_PRICE' END,
                last_reason=? WHERE fs_id=?""",
                (int(now),int(bool(has_price)),
                 "PRICE_SEEN_WAIT_SIGNAL" if has_price
                 else "WAITING_FOR_EXACT_NEXT_GOAL_PRICE",str(fs_id)))

    def note_decision(self,fs_id,*,reason,send=False):
        with self.db:
            self.db.execute("""UPDATE progruz1_live_watchlist
                SET status=CASE WHEN ? THEN 'SENT'
                    WHEN status='SENT' THEN 'SENT'
                    ELSE 'WAIT_SIGNAL' END,last_reason=?
                WHERE fs_id=?""",
                (int(bool(send)),str(reason)[:90],str(fs_id)))

    def metrics(self,*,now):
        active=self.db.execute("""SELECT status,COUNT(*)
            FROM progruz1_live_watchlist
            WHERE last_seen>=? GROUP BY status""",
            (int(now)-90,)).fetchall()
        counted=dict(active)
        total=sum(counted.values())
        watched=sum(n for status,n in active if status in (
            "WATCH","HOT","WAIT_PRICE","WAIT_ENTRY","WAIT_SIGNAL",
            "PRICE_CONFIRM"))
        return {"registered":total,"watched":watched,
                "hot":counted.get("HOT",0),
                "awaiting_price":counted.get("WAIT_PRICE",0),
                "statuses":counted}

    def summary(self,*,now,limit=12):
        """Human-readable watched LIVE fixtures; interest is not probability."""
        metrics=self.metrics(now=now)
        rows=self.db.execute("""SELECT home,away,minute,score_home,
                score_away,interest,status,last_observed,last_reason
            FROM progruz1_live_watchlist
            WHERE last_seen>=? AND status!='SENT'
            ORDER BY CASE status
                WHEN 'HOT' THEN 0 WHEN 'WAIT_PRICE' THEN 1
                WHEN 'PRICE_CONFIRM' THEN 2 WHEN 'WAIT_SIGNAL' THEN 3
                WHEN 'WATCH' THEN 4 ELSE 5 END,
                interest DESC,last_observed DESC
            LIMIT ?""",(int(now)-90,max(1,min(20,int(limit))))).fetchall()
        lines=[
            "👁 ПРОГРУЗ 1 · НАБЛЮДЕНИЕ FLASHScore LIVE",
            f"⚽ Матчей в LIVE-каталоге: {metrics['registered']}",
            f"🎯 Перспективных в памяти: {metrics['watched']}",
            f"🔥 Горячих: {metrics['hot']}",
            f"💰 Ожидают цену: {metrics['awaiting_price']}",
        ]
        titles={"HOT":"🔥 горячий","WATCH":"👁 наблюдаем",
                "WAIT_PRICE":"💰 ждём кэф",
                "WAIT_ENTRY":"⏱ ждём вход",
                "WAIT_SIGNAL":"📊 ждём момент",
                "PRICE_CONFIRM":"💰 проверка цены",
                "TRACKING":"🔎 сканируем"}
        for home,away,minute,h,a,interest,status,observed,reason in rows:
            age=f"{max(0,(int(now)-observed)//60)}м" if observed else "—"
            lines.append(
                f"• {home} — {away} | {int(minute)}' {h}:{a}"
                f" | {titles.get(status,status)}"
                f" | интерес {interest}/100 | статистика {age} назад")
        lines.append("ℹ️ Интерес — приоритет наблюдения, НЕ шанс гола.")
        lines.append("⚙️ Полный анализ/ставка только при подтверждённых"
                     " LIVE-атаках и актуальном кэфе ≥1.50.")
        return "\n".join(lines)
