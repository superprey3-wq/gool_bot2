from __future__ import annotations
import time
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.history_db import connect

def settle(trend, fh_home, fh_away, ft_home, ft_away):
    fh=fh_home+fh_away; ft=ft_home+ft_away; sh=ft-fh
    rules={
      "1H_OVER_0.5":fh>.5,"1H_OVER_1.5":fh>1.5,
      "2H_OVER_0.5":sh>.5,"2H_OVER_1.5":sh>1.5,
      "FT_OVER_2.5":ft>2.5,"FT_OVER_3.5":ft>3.5,
      "FT_UNDER_2.5":ft<2.5,
      "BTTS_YES":ft_home>0 and ft_away>0,
      "BTTS_NO":not(ft_home>0 and ft_away>0),
    }
    return "WIN" if rules.get(trend,False) else "LOSS"

def settle_finished(db_path="data/gool_history.sqlite"):
    fs=FlashscoreProvider(); db=connect(db_path)
    rows=db.execute("SELECT event_id,trend FROM picks WHERE result IS NULL AND kickoff_ts < ?",(time.time()-90*60,)).fetchall()
    states=fs.event_states({r[0] for r in rows}); done=0
    for event_id,trend in rows:
        st=states.get(event_id) or {}
        if not st.get("is_finished"): continue
        ft_h=int(st.get("home_score") or 0); ft_a=int(st.get("away_score") or 0)
        goals=fs.fetch_goal_timeline(event_id)
        fh_h=sum(1 for g in goals if g.get("period")=="1H" and g.get("side")=="home")
        fh_a=sum(1 for g in goals if g.get("period")=="1H" and g.get("side")=="away")
        result=settle(trend,fh_h,fh_a,ft_h,ft_a)
        db.execute("UPDATE picks SET result=?,final_home=?,final_away=?,settled_ts=? WHERE event_id=? AND trend=?",(result,ft_h,ft_a,time.time(),event_id,trend)); done+=1
    db.commit(); db.close(); return done

if __name__=="__main__":
    print("SETTLED",settle_finished(),flush=True)
