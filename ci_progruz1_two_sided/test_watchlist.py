"""Full LIVE watchlist regression tests: no odds needed to follow matches."""
import sqlite3
from types import SimpleNamespace
from progruz1_live_watchlist import LiveWatchlist


NOW=2_000_090_000
KICK=NOW-60*60

def game(i, minute=60, score=(1,1), kickoff=KICK):
    return SimpleNamespace(
        provider_match_id=f"Ab{i:06d}",home=f"Home {i}",
        away=f"Away {i}",league="Football",minute=minute,
        home_score=score[0],away_score=score[1],
        is_halftime=False,
        meta={"status_code":"13" if minute>=46 else "12",
              "scheduled_start_ts":kickoff})


def observations(n=40, minute=60, kickoff=KICK):
    return [(f"FS:Ab{i:06d}",kickoff,game(i,minute,kickoff=kickoff),{})
            for i in range(n)]


def active_stats():
    return {
        "shots":(12,8),"shots_on_target":(4,3)}


def active_pressure():
    return {"sample_age":650,"recent_age":300,
            "recent_shots":6,"recent_sot":3,
            "stats_consistent":True,
            "recent_5m_shots_by_team":(3,2),
            "recent_5m_sot_by_team":(2,1),
            "recent_5m_age":300}


def test_catalogs_all_live_and_persists_watched_without_any_price():
    db=sqlite3.connect(":memory:")
    watch=LiveWatchlist(db)
    games=[game(i) for i in range(75)]
    games+=[game(75,minute=38),game(76,minute=83)]
    assert watch.catalog(games,now=NOW)==77
    assert watch.metrics(now=NOW)["registered"]==77
    prepared={}
    row=observations(1)[0]
    assert watch.price_candidates([row],prepared,now=NOW)==[]
    assert watch.record(row[2],now=NOW,
        stats=active_stats(),pressure=active_pressure())["status"]=="HOT"
    prepared[row[0]]=({"ok":True},active_pressure(),{},active_stats())
    assert watch.price_candidates([row],prepared,now=NOW)==[row]
    watch.note_price(row[2].provider_match_id,now=NOW,has_price=False)
    assert watch.metrics(now=NOW)["awaiting_price"]==1
    # Simulate restart: remembered match keeps status and priority.
    restarted=LiveWatchlist(db)
    assert restarted.metrics(now=NOW)["watched"]==1
    selected=restarted.schedule(observations(40),now=NOW+40,capacity=6)
    assert row[2].provider_match_id in {
        x[2].provider_match_id for x in selected}


def test_all_matches_get_eventual_context_despite_hot_focus():
    db=sqlite3.connect(":memory:")
    watch=LiveWatchlist(db)
    all_rows=observations(48)
    watch.catalog([r[2] for r in all_rows],now=NOW)
    visited=set()
    # Repeated cycles: reserve 2/6 reads for new and unobserved
    # games, so a HOT fixture cannot starve the rest of LIVE football.
    for tick in range(36):
        ts=NOW+tick*70
        rows=watch.schedule(all_rows,now=ts,capacity=6)
        for row in rows:
            visited.add(row[2].provider_match_id)
            pressure=(active_pressure() if row[2].provider_match_id==
                      all_rows[0][2].provider_match_id
                      else {"stats_consistent":True,"recent_shots":0,
                            "recent_sot":0,"recent_age":300})
            stats=(active_stats() if pressure["recent_shots"]
                   else {"shots":(1,0),"shots_on_target":(0,0)})
            watch.record(row[2],now=ts,stats=stats,pressure=pressure)
    assert len(visited)==48
    assert watch.metrics(now=ts)["watched"]>=1


def test_noninteresting_and_unknown_kickoff_are_never_priced():
    db=sqlite3.connect(":memory:")
    watch=LiveWatchlist(db)
    good=game(1,minute=62)
    unknown=game(2,minute=62,kickoff=0)
    watch.catalog([good,unknown],now=NOW)
    a=("FS:"+good.provider_match_id,KICK,good,{})
    b=("FS:"+unknown.provider_match_id,0,unknown,{})
    for row in (a,b):
        watch.record(row[2],now=NOW,
                     stats=active_stats(),pressure=active_pressure())
    prepared={a[0]:({},active_pressure(),{},active_stats()),
              b[0]:({},active_pressure(),{},active_stats())}
    assert watch.price_candidates([a,b],prepared,now=NOW)==[a]
    # This match is in the catalog but not an entry-window price target.
    good.minute=38
    assert watch.price_candidates([a],prepared,now=NOW)==[]


def test_sent_fixture_never_retriggers_price_scanner():
    db=sqlite3.connect(":memory:")
    watch=LiveWatchlist(db)
    fs=game(1)
    watch.catalog([fs],now=NOW)
    row=("FS:"+fs.provider_match_id,KICK,fs,{})
    watch.record(fs,now=NOW,stats=active_stats(),
                 pressure=active_pressure())
    prepared={row[0]:({},active_pressure(),{},active_stats())}
    watch.note_decision(fs.provider_match_id,reason="SENT",send=True)
    watch.record(fs,now=NOW+40,stats=active_stats(),
                 pressure=active_pressure())
    assert watch.metrics(now=NOW+40)["statuses"]["SENT"]==1
    assert watch.price_candidates([row],prepared,now=NOW+40)==[]
    assert watch.schedule([row],now=NOW+80,capacity=6)==[]


def test_weak_snapshot_is_only_tracking_not_a_goal_prediction():
    db=sqlite3.connect(":memory:")
    w=LiveWatchlist(db)
    fs=game(1,minute=77,score=(0,0))
    w.catalog([fs],now=NOW)
    answer=w.record(fs,now=NOW,
        stats={"shots":(11,4),"shots_on_target":(6,1)},
        pressure={"stats_consistent":True,"recent_shots":0,
                  "recent_sot":0,"recent_age":300})
    assert answer["status"]!="HOT"
    # WATCH/interest is only a queue priority, never a bet!
    # The actual goal-brain will still demand fresh shots/SOT.
