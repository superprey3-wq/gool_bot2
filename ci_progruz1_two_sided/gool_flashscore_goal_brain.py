"""PROGRUZ 1, single brain: Flashscore LIVE + team history -> another goal.

No RADAR/PREMOVE, external LLM, fabricated xG, or Fonbet sportscast.
Fonbet supplies ONLY available, paired prices for exact MATCH TB (next
goal wins); Flashscore supplies current match identity, score, minute,
shots / shots on target, optional actual xG, and last completed games.
The historical/pressure screen is not a calibrated win-probability.
"""
from __future__ import annotations

import math
import os

from gool_adaptive_flashscore import team_similarity
from gool_adaptive_early import _history_rows


BASIS="flashscore_history_live_next_goal_qualitative"


def _pair(value):
    if not isinstance(value,(tuple,list)) or len(value)!=2:
        return None
    try:
        v=tuple(float(x) for x in value)
        if any(not math.isfinite(x) or x<0 for x in v):
            return None
        return v
    except (ValueError,TypeError,OverflowError):
        return None


def _score(value):
    p=_pair(value)
    if p is None or any(x>40 or x!=int(x) for x in p):
        return None
    return tuple(int(x) for x in p)


def match_flashscore_live(fs_matches,fonbet_roots,diagnostics=None,
                          pending=None,known_links=None):
    """Flashscore-first, competition-aware LIVE fixture identity lookup.

    A fixture with an asynchronously different scoreboard is FOUND but is
    NOT priceable. It is retried on the next Flashscore/Fonbet cycle and
    becomes actionable only once the published scores agree.
    """
    from collections import Counter
    from gool_match_identity import (
        competition_relation,competition_country,football_team_similarity)

    bookmaker=[]
    for root in fonbet_roots:
        if (root.get("sport")!="football" or root.get("phase")!="LIVE"
                or not root.get("kickoff")):
            continue
        parts=str(root.get("match") or "").split(" — ",1)
        if len(parts)!=2:
            continue
        try:
            fb_kick=int(root["kickoff"])
        except (ValueError,TypeError):
            continue
        fm=None
        try:
            if root.get("elapsed_seconds") is not None:
                value=int(float(root["elapsed_seconds"])//60)
                if 1<=value<=96:
                    fm=value
        except (TypeError,ValueError,OverflowError):
            pass
        bookmaker.append((root,parts,fb_kick,fm,
                          _score((root.get("score_home"),
                                  root.get("score_away")))))

    fs_counts=Counter(str(fs.provider_match_id) for fs in fs_matches)
    seen=set()
    claims=[]
    status={}
    for fs in fs_matches:
        key=str(fs.provider_match_id)
        if key in seen:
            continue
        seen.add(key)
        if fs_counts[key]>1:
            status[key]="FLASHSCORE_EVENT_DUPLICATED"
            continue
        # The fixture can be identified at HT, but its odds may NOT be
        # used while the half is over (see read_fonbet_goal_prices).
        score=_score((fs.home_score,fs.away_score))
        try:
            minute=int(fs.minute)
            kickoff=int((fs.meta or {}).get("scheduled_start_ts") or 0)
        except (TypeError,ValueError,AttributeError):
            status[key]="FLASHSCORE_CLOCK_UNVERIFIED"
            continue
        if score is None or not 1<=minute<=96:
            status[key]="FLASHSCORE_SCORE_OR_CLOCK_UNVERIFIED"
            continue

        candidates=[]
        # Only a plausible pair of BOTH ordered clubs may explain why a
        # fixture did not match. Otherwise foreign games in a large Fonbet
        # catalogue must not be reported as a "country conflict".
        rejected=[]
        # Once an actual match was verified on a previous scan, keep its
        # stable FS event ID -> Fonbet root and kickoff. Team spelling or
        # league labels may change in the feeds; don't rediscover them
        # from fuzzy text on every 8-second refresh.
        saved=(known_links or {}).get(key)
        if saved:
            try:
                saved_root,saved_kick=saved
                saved_root=str(saved_root)
                saved_kick=int(saved_kick)
            except (TypeError,ValueError):
                saved_root=None
                saved_kick=0
            for book,parts,fb_kick,fb_minute,fb_score in bookmaker:
                if (str(book.get("root_id"))!=saved_root or
                        fb_kick!=saved_kick or
                        (kickoff and abs(fb_kick-kickoff)>1800) or
                        (fb_minute is not None and
                         abs(minute-fb_minute)>10)):
                    continue
                if competition_relation(
                        str(getattr(fs,"league","") or ""),
                        str(book.get("league") or ""))=="CONFLICT":
                    continue
                stage=("FOUND_WAIT_BOOK_SCORE" if fb_score is None
                       else "FOUND_WAIT_LIVE_TIMER" if
                            book.get("identity_only") else
                            "FOUND_ON_FONBET" if fb_score==score
                            else "FOUND_WAIT_SCORE_SYNC")
                candidates.append((
                    1.0,1.0,1,str(book["root_id"]),book,fs,stage))
                break
        for book,parts,fb_kick,fb_minute,fb_score in ([] if candidates else bookmaker):
            home=football_team_similarity(parts[0],fs.home)
            away=football_team_similarity(parts[1],fs.away)
            reverse=football_team_similarity(parts[0],fs.away)
            # No match could pass the later bilingual/normal team gates with
            # such weak ordered names; this is an unrelated fixture, not a
            # competition/country mismatch for the requested match.
            if min(home,away)<0.43 or home+away<1.02:
                continue
            fs_league=str(getattr(fs,"league","") or "")
            fb_league=str(book.get("league") or "")
            relation=competition_relation(fs_league,fb_league)
            fs_country=competition_country(fs_league)
            fb_country=competition_country(fb_league)
            country_aligned=bool(fs_country and fs_country==fb_country)
            # A known DIFFERENT country proves a different fixture.
            if (fs_country and fb_country and fs_country!=fb_country):
                rejected.append((min(home,away),(home+away)/2,
                                 "COUNTRY_CONFLICT"))
                continue
            # Known different competitions or team classes cannot be merged.
            if relation=="CONFLICT":
                if min(home,away)>=0.65:
                    rejected.append((min(home,away),(home+away)/2,
                                 "TOURNAMENT_CONFLICT"))
                continue
            # The corresponding competition and kickoff provide extra
            # evidence for legitimate bilingual club-name differences.
            min_sim=0.82 if relation=="CONFIRMED" else 0.88
            # If the SAME tournament, exact kickoff, live minute and
            # scoreboard all agree, a visibly different transliteration
            # should not hide a fixture. Still require evidence in BOTH
            # ordered team names; reject weak or swapped team identities.
            same_league_kickoff=(
                relation=="CONFIRMED" and kickoff
                and abs(kickoff-fb_kick)<=300)
            clock_gap=(abs(minute-fb_minute)
                       if fb_minute is not None else None)
            # Reuse COUNTRY, BOTH ordered clubs, exact start, score and
            # minute to prove a fixture even if its LEAGUE TRANSLATION
            # has never appeared in our alias catalog before.
            country_fixture_proof=(
                country_aligned and relation=="UNVERIFIED"
                and kickoff and abs(kickoff-fb_kick)<=300
                and fb_score==score and clock_gap is not None
                and clock_gap<=3
                and min(home,away)>=0.50
                and home+away>=1.15)
            # Some bookmaker indexes report scheduled kickoffs one hour
            # differently. Allow that ONLY with both near-exact teams,
            # same country, same score and same running minute.
            kickoff_shift_proof=(
                country_aligned and relation!="CONFLICT"
                and kickoff and 1800<abs(kickoff-fb_kick)<=3600
                and fb_score==score and clock_gap is not None
                and clock_gap<=2 and min(home,away)>=0.90)
            country_score_lag_identity=(
                country_aligned and relation=="UNVERIFIED"
                and kickoff and abs(kickoff-fb_kick)<=300
                and fb_score is not None and fb_score!=score
                and clock_gap is not None and clock_gap<=3
                and min(home,away)>=0.52
                and home+away>=1.35)
            verified_league_live=(
                same_league_kickoff and fb_score==score
                and clock_gap is not None and clock_gap<=3)
            # We can identify a game with stale score or delayed minute
            # WITHOUT trusting its quoted total yet. A strong bilingual
            # name pair, competition and kickoff are sufficient to keep
            # following the ID and waiting for the provider to catch up.
            strong_stale_identity=(
                same_league_kickoff and clock_gap is not None
                and clock_gap<=3 and min(home,away)>=0.65
                and home+away>=1.45)
            delayed_clock_identity=(
                same_league_kickoff and fb_score==score
                and clock_gap is not None and 7<clock_gap<=20
                and min(home,away)>=0.65 and home+away>=1.50)
            no_book_state_identity=(
                same_league_kickoff and
                (fb_score is None or fb_minute is None)
                and min(home,away)>=0.72 and home+away>=1.52)
            bilingual_names=(
                (verified_league_live and min(home,away)>=0.48
                 and max(home,away)>=0.58 and home+away>=1.06)
                or strong_stale_identity or delayed_clock_identity
                or no_book_state_identity or country_fixture_proof
                or kickoff_shift_proof or country_score_lag_identity)
            if (reverse>=min(home,away)*0.96
                    or (min(home,away)<min_sim and not bilingual_names)):
                if verified_league_live:
                    rejected.append((min(home,away),(home+away)/2,
                                 "TEAM_TRANSLITERATION_UNVERIFIED"))
                continue
            if kickoff and abs(kickoff-fb_kick)>1800 and not kickoff_shift_proof:
                rejected.append((min(home,away),(home+away)/2,
                                 "KICKOFF_CONFLICT"))
                continue
            if fb_minute is not None and abs(minute-fb_minute)>7:
                if delayed_clock_identity:
                    candidates.append((
                        min(home,away),(home+away)/2,1,
                        str(book["root_id"]),book,fs,
                        "FOUND_WAIT_CLOCK_SYNC"))
                else:
                    rejected.append((min(home,away),(home+away)/2,
                                 "CLOCK_CONFLICT"))
                continue
            if not kickoff and (fb_score is None or fb_minute is None):
                rejected.append((min(home,away),(home+away)/2,
                                 "UNVERIFIED_KICKOFF_AND_LIVE_STATE"))
                continue
            if (relation!="CONFIRMED"
                    and not country_fixture_proof
                    and not kickoff_shift_proof
                    and not country_score_lag_identity
                    and (min(home,away)<0.95
                    or (kickoff and abs(kickoff-fb_kick)>600)
                    or (not kickoff and (fb_score is None
                        or fb_minute is None or abs(minute-fb_minute)>3)))):
                rejected.append((min(home,away),(home+away)/2,
                                 "TOURNAMENT_UNVERIFIED"))
                continue

            synced=fb_score==score
            if not synced and fb_score is not None:
                # Keep the same match on watch; never give an alert for
                # differing scoreboards, even with exact teams and league.
                # A lagging Fonbet scoreboard is not absence of the
                # fixture. Keep a *non-priceable* identity link when club
                # names, league and kickoff establish the game, but never
                # issue an alert until the scores agree again.
                if (not country_score_lag_identity and
                        (relation!="CONFIRMED" or min(home,away)<0.65
                         or not kickoff or abs(kickoff-fb_kick)>300
                         or (fb_minute is not None and
                             abs(minute-fb_minute)>3))):
                    rejected.append((min(home,away),(home+away)/2,
                                 "SCORE_CONFLICT_UNVERIFIED"))
                    continue
            # Fonbet is only a PRICE source. A missing bookmaker score
            # must not veto exact teams + kickoff + competition identity;
            # Flashscore is the authoritative live scoreboard here.
            if fb_score is None and (
                    not kickoff or abs(kickoff-fb_kick)>600
                    or (not no_book_state_identity and min(home,away)<0.95)):
                rejected.append((min(home,away),(home+away)/2,
                                 "BOOKMAKER_SCORE_UNVERIFIED"))
                continue
            # Being listed in the Fonbet live catalogue != a confirmed
            # betting state. Count it as FOUND, but do not price before
            # the bookmaker supplies its LIVE clock and synchronized score.
            stage=("FOUND_WAIT_BOOK_SCORE" if fb_score is None
                   else "FOUND_WAIT_LIVE_TIMER" if
                        book.get("identity_only") else
                        "FOUND_ON_FONBET" if synced else
                        "FOUND_WAIT_SCORE_SYNC")
            candidates.append((
                min(home,away),(home+away)/2,
                1 if relation=="CONFIRMED" else 0,
                str(book["root_id"]),book,fs,stage))

        candidates.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
        if not candidates:
            # Choose the rejection tied to the most credible ordered team
            # pair; do not let numerous irrelevant foreign fixtures win
            # by sheer count.
            status[key]=(max(rejected)[2] if rejected else
                         "NOT_LISTED_ON_FONBET_OR_NAME_UNKNOWN")
            continue
        # Require a larger uniqueness margin for fuzzy bilingual names;
        # never force a bet when two clubs of the same league are plausible.
        margin=(0.20 if candidates[0][0]<0.82 else 0.12)
        if (len(candidates)>1 and
                candidates[0][1]-candidates[1][1]<margin):
            status[key]="AMBIGUOUS_FONBET_FIXTURE"
            continue
        claims.append(candidates[0])
        status[key]=candidates[0][6]

    # Two Flashscore fixtures cannot own the same Fonbet root.
    root_counts=Counter(c[3] for c in claims)
    matched={}
    for _,_,_,root,book,fs,stage in claims:
        key=str(fs.provider_match_id)
        if root_counts[root]!=1:
            status[key]="FONBET_ROOT_AMBIGUOUS"
            continue
        if stage=="FOUND_ON_FONBET":
            matched[root]=(book,fs)
        elif pending is not None:
            pending[key]={"root_id":root,"book":book,"fixture":fs,
                          "reason":stage}
    if diagnostics is not None:
        diagnostics.update(status)
    return matched


def _second_half_started(minute,source_status=None):
    """2H is live from 46' or at 45' ONLY on verified Flashscore AC=13.

    45' in the first half, added time or HT does not become 2H merely
    because the minute reached 45.
    """
    try:
        m=int(minute)
    except (TypeError,ValueError):
        return False
    return m>=46 or (m==45 and str(source_status or "")=="13")


def read_fonbet_goal_prices(snapshot,matched):
    """Read exactly priced next-goal bets in the current LIVE half.

    1H requires an actual 1H market. In 2H, a match total set exactly
    0.5 above the LIVE match score is mathematically the same as one more
    goal during the remaining 2H; either 2H or full-match markets can be
    offered, but they remain distinctly labeled for settlement.
    """
    from collections import defaultdict
    import re
    from fonbet_live_scanner import factor_ids,state
    from gool_scanner_lite import HALF
    second_half=re.compile(
        r"^\s*(?:2[-– ]?(?:й|я|ый)\s*тайм|второй\s+тайм|"
        r"2nd\s+half|second\s+half)\s*$",re.I)
    events={}
    for e in snapshot.get("events",[]):
        if isinstance(e,dict) and e.get("id") is not None:
            events[str(e["id"])]=e
    blocks={str(b["eventId"]):b for b in snapshot.get("eventBlocks",[])
            if isinstance(b,dict) and b.get("eventId") is not None}
    factors=defaultdict(list)
    for g in snapshot.get("customFactors",[]):
        if isinstance(g,dict) and g.get("e") is not None:
            factors[str(g["e"])].extend(g.get("factors") or [])
    rows=[]
    for root,(fb,fs) in matched.items():
        # A half-time match is still part of the Flashscore catalogue,
        # but the first-half next-goal market is no longer actionable.
        if getattr(fs,"is_halftime",False):
            continue
        root_block=blocks.get(str(root))
        if fb.get("root_blocked") or state(root_block)=="blocked":
            continue
        score=_score((fs.home_score,fs.away_score))
        if score is None:
            continue
        try:
            minute=int(fs.minute)
        except (TypeError,ValueError):
            continue
        if not 1<=minute<=96:
            continue
        wanted=("2-й тайм" if _second_half_started(
            minute,(getattr(fs,"meta",None) or {}).get("status_code"))
            else "1-й тайм")
        for eid,event in events.items():
            if str(eid)==str(root) and wanted=="2-й тайм":
                # From now to FT, any new match goal necessarily belongs
                # to 2H. A whole-match next-goal market is thus legitimate.
                period="Матч"
            else:
                if str(event.get("parentId"))!=str(root):
                    continue
                name=str(event.get("name") or "")
                period=("1-й тайм" if HALF.fullmatch(name) else
                        "2-й тайм" if second_half.fullmatch(name) else None)
                if period!=wanted:
                    continue
            block=blocks.get(eid)
            if state(block)=="blocked":
                continue
            blocked=factor_ids(block) if state(block)=="partial" else set()
            if blocked & {"930","931"}:
                continue
            pairs=defaultdict(dict)
            conflicts=set()
            for f in factors.get(eid,[]):
                if not isinstance(f,dict):
                    continue
                fid=str(f.get("f") or "")
                if fid not in {"930","931"}:
                    continue
                try:
                    line=float(f["pt"]);odd=float(f["v"])
                except (KeyError,TypeError,ValueError):
                    continue
                if (not math.isfinite(line) or not math.isfinite(odd)
                        or not 0.5<=line<=14.5 or not 1.01<odd<100
                        or abs(line*2-round(line*2))>1e-6):
                    continue
                key=round(line,3)
                if fid in pairs[key] and pairs[key][fid]!=odd:
                    conflicts.add(key)
                pairs[key][fid]=odd
            for line,both in pairs.items():
                if line in conflicts or set(both)!={"930","931"}:
                    continue
                over,under=both["930"],both["931"]
                margin=1/over+1/under-1
                if not 0<=margin<=0.14:
                    continue
                rows.append({
                    "root_id":str(root),"feed_root_id":str(root),
                    "kickoff":int(fb["kickoff"]),"event_id":eid,
                    "match":str(fb.get("match") or ""),
                    "period":period,"line":line,"over":over,"under":under,
                    "match_score":score,"minute":minute,
                    "current_half":wanted,"market_kind":"general_total"})
    return rows

def _venue(rows,team,at_home,now):
    out=[]
    for row in (rows or [])[:30]:
        if not isinstance(row,dict):continue
        try:
            h=int(row["home_score"]);a=int(row["away_score"])
            when=int(row.get("timestamp") or 0)
        except (KeyError,ValueError,TypeError):continue
        if min(h,a)<0 or h+a>16 or (when and
                (when>now or when<now-3*365*86400)):
            continue
        hs=team_similarity(team,row.get("home") or "")
        aws=team_similarity(team,row.get("away") or "")
        if (max(hs,aws)<0.82 or abs(hs-aws)<0.12 or
                (at_home and hs<=aws) or
                (not at_home and aws<=hs)):
            continue
        out.append((h,a) if hs>aws else (a,h))
        if len(out)>=10:break
    return out


def profile(history,home,away,kickoff,*,min_games=4):
    """A conservative, auditable goal-form summary; no fake samples."""
    hist=history if isinstance(history,dict) else {}
    h=_history_rows(hist.get("home"),home,int(kickoff))
    a=_history_rows(hist.get("away"),away,int(kickoff))
    n1,n2=len(h),len(a)
    mutual=[]
    seen_ids=set()
    for row in (hist.get("h2h") or [])[:30]:
        if not isinstance(row,dict):
            continue
        try:
            gh=int(row["home_score"]);ga=int(row["away_score"])
            when=int(row.get("timestamp") or 0)
        except (TypeError,ValueError,KeyError):
            continue
        if (min(gh,ga)<0 or gh+ga>16 or
                (when and (when>kickoff or when<kickoff-3*365*86400))):
            continue
        meeting=((team_similarity(home,row.get("home") or "")>=0.82
                  and team_similarity(away,row.get("away") or "")>=0.82)
                 or (team_similarity(home,row.get("away") or "")>=0.82
                  and team_similarity(away,row.get("home") or "")>=0.82))
        eid=str(row.get("event_id") or "")
        if meeting and (not eid or eid not in seen_ids):
            mutual.append(gh+ga)
            if eid:
                seen_ids.add(eid)
        if len(mutual)>=10:
            break
    h2h_avg=(sum(mutual)/len(mutual) if mutual else None)
    ans={"state":"MISSING","home_games":n1,"away_games":n2,
         "home_for":None,"home_against":None,
         "away_for":None,"away_against":None,
         "avg_total":None,"home_venue":0,"away_venue":0,
         "h2h_games":len(mutual),
         "h2h_avg_total":round(h2h_avg,2) if h2h_avg is not None else None}
    if min(n1,n2)<max(3,int(min_games)):
        return ans
    hf=sum(x[0] for x in h)/n1
    ha=sum(x[1] for x in h)/n1
    af=sum(x[0] for x in a)/n2
    aa=sum(x[1] for x in a)/n2
    hv=_venue(hist.get("home"),home,True,int(kickoff))
    av=_venue(hist.get("away"),away,False,int(kickoff))
    # Venue is only a modest adjustment when at least 3 actual games exist.
    if len(hv)>=3:
        hf=0.75*hf+0.25*sum(x[0] for x in hv)/len(hv)
        ha=0.75*ha+0.25*sum(x[1] for x in hv)/len(hv)
    if len(av)>=3:
        af=0.75*af+0.25*sum(x[0] for x in av)/len(av)
        aa=0.75*aa+0.25*sum(x[1] for x in av)/len(av)
    # Two recent attacks against the opponents' observed defences.
    projected=(hf+aa)/2+(af+ha)/2
    # Real mutual meetings can inform the estimate but never replace the
    # two independent teams' form; small H2H samples are highly noisy.
    if len(mutual)>=3:
        projected=0.85*projected+0.15*h2h_avg
    ans.update(state="READY",home_for=round(hf,2),
               home_against=round(ha,2),away_for=round(af,2),
               away_against=round(aa,2),avg_total=round(projected,2),
               home_venue=len(hv),away_venue=len(av))
    return ans


def evaluate_next_goal(quotes,ctx,pressure,history,*,min_odd=1.50,
                       decision_sink=None):
    """Only reasonably timed next-goal OVER, paired LIVE odds 1.50–3.00.

    The system has no calibrated goal probability, so do not infer that
    @7–8 injury-time shots are value bets from a low break-even threshold.
    """
    score=_score(ctx.get("score"))
    try:minute=int(ctx["minute"])
    except (KeyError,ValueError,TypeError):return None,"FS_CLOCK_MISSING"
    if score is None:
        return None,"FS_GOAL_WINDOW_OR_SCORE"
    second_half=_second_half_started(
        minute,ctx.get("flashscore_status_code"))
    # User-defined entry windows: 1H 1–35', 2H 45–80'. Ordinary Flashscore
    # calls the first minute of 2H 46'; a displayed 45' is 2H only when
    # Flashscore AC=13 explicitly confirms play has restarted.
    in_window=(45<=minute<=80 if second_half else 1<=minute<=35)
    if not in_window:
        return None,"FS_GOAL_TOO_LATE_OR_HALF_TRANSITION"
    if history.get("state")!="READY":
        return None,"FS_TEAM_HISTORY_UNVERIFIED"
    if (history.get("avg_total") is None or
            float(history["avg_total"])<2.15):
        return None,"FS_TEAM_GOAL_FORM_LOW"
    stats=ctx.get("stats") or {}
    shots=_pair(stats.get("shots"))
    sot=_pair(stats.get("shots_on_target"))
    xg=_pair(stats.get("xg"))
    if (shots is None or sot is None or
            any(a>b for a,b in zip(sot,shots))):
        return None,"FS_SHOTS_UNVERIFIED"
    # In the first minutes of each half the old 3.5-minute observation
    # requirement made a nominal 1' entry impossible. Accept two REAL,
    # independent 30-second snapshots in the opening 5 minutes, never a
    # single cumulative-shots snapshot or guessed momentum.
    half_opening=(minute<=5 if not second_half else minute<=50)
    sample_min=30 if half_opening else 210
    recent_min=30 if half_opening else 180
    if (not isinstance(pressure,dict) or
            pressure.get("sample_age",0)<sample_min or
            pressure.get("recent_age",0)<recent_min or
            not pressure.get("stats_consistent")):
        return None,"FS_LIVE_MOMENTUM_NOT_OBSERVED"
    recent=int(pressure.get("recent_shots") or 0)
    recent_on=int(pressure.get("recent_sot") or 0)
    # Real independent observations, not a 0-shot second-half snapshot.
    if (recent<3 or recent_on<1 or
            sum(shots)<6 or sum(sot)<2):
        return None,"FS_NO_RECENT_GOAL_PRESSURE"
    if minute>=73 and recent_on<2:
        return None,"FS_LATE_GOAL_PRESSURE_WEAK"
    if (second_half and pressure.get("sample_age",0)>
            max(0,(minute-45))*60+240):
        # A buggy provider clock must not turn first-half observations
        # into a supposed 30-minute 2H signal.
        return None,"FS_HALF_MOMENTUM_CLOCK_CONFLICT"
    period="2-й тайм" if second_half else "1-й тайм"
    ht=_score(ctx.get("first_half_score"))
    if ht is None or ht[0]>score[0] or ht[1]>score[1]:
        return None,"FS_FIRST_HALF_SCORE_UNVERIFIED"
    if not second_half and ht!=score:
        return None,"FS_FIRST_HALF_SCORE_CONFLICT"
    period_score=(score if period=="1-й тайм" else
                  (score[0]-ht[0],score[1]-ht[1]))
    goals=sum(period_score)
    wanted=goals+0.5
    possible=[]
    for q in quotes:
        offered=str(q.get("period") or "")
        if (offered!=period and not(period=="2-й тайм" and
                                      offered=="Матч")):
            continue
        # Match TB total is not the 2H TB total; they differ by 1H goals.
        exact=(sum(score)+0.5 if offered=="Матч" else wanted)
        if (_score(q.get("match_score"))!=score or
                abs(float(q.get("line",-99))-exact)>1e-6):
            continue
        try:
            odd=float(q["over"]);under=float(q["under"])
            margin=1/odd+1/under-1
        except (ValueError,TypeError,ZeroDivisionError):
            continue
        if not (math.isfinite(odd) and math.isfinite(under) and
                max(1.50,float(min_odd))<=odd<=3.00 and
                0<=margin<=0.14):
            continue
        possible.append((odd,q))
    if not possible:
        return None,"NEXT_GOAL_LIVE_ODDS_UNAVAILABLE"
    from progruz1_confidence import confidence_score,minimum_confidence
    strength=confidence_score(
        minute=minute,shots=shots,sot=sot,xg=xg,
        recent_shots=recent,recent_sot=recent_on,
        history=history,score=score,period_score=period_score,
        second_half_started=second_half)
    # Confidence ranks the Flashscore situation only. Price sets the
    # evidence threshold, NOT a fabricated probability of winning.
    eligible=[(o,q) for o,q in possible
              if strength["score"]>=minimum_confidence(o)]
    if not eligible:
        return None,"FS_CONFIDENCE_BELOW_THRESHOLD"
    odd,m=max(eligible,key=lambda x:x[0])
    from progruz1_scenarios import classify_scenario
    scenario=classify_scenario(
        minute=minute,score=score,second_half=second_half,
        shots=shots,sot=sot,recent_shots=recent,recent_sot=recent_on,
        odd=odd,xg_available=xg is not None,
        avg_total=history.get("avg_total"),
        recent_10m_shots_by_team=pressure.get("recent_10m_shots_by_team"),
        recent_10m_sot_by_team=pressure.get("recent_10m_sot_by_team"),
        recent_10m_age=pressure.get("recent_10m_age",0),
        recent_5m_shots_by_team=pressure.get("recent_5m_shots_by_team"),
        recent_5m_sot_by_team=pressure.get("recent_5m_sot_by_team"),
        recent_5m_age=pressure.get("recent_5m_age",0),
        opening_shots_by_team=pressure.get("shots_by_team"),
        opening_sot_by_team=pressure.get("sot_by_team"),
        opening_sample_age=pressure.get("sample_age",0),
        recent_10m_xg_by_team=ctx.get("recent_10m_xg_by_team"),
        recent_10m_xg_age=ctx.get("recent_10m_xg_age",0),
        first_half_score=ht,
        first_half_sot_total=ctx.get("first_half_sot_total"),
        first_half_xg_total=ctx.get("first_half_xg_total"),
        second_half_sot_by_team=ctx.get("second_half_sot_by_team"),
        second_half_xg_delta=ctx.get("second_half_xg_delta"),
        prematch_odds=ctx.get("prematch_odds"),
        first_goal_side=ctx.get("first_goal_side"),
        red_cards=ctx.get("red_cards"))
    # Store the mandatory TWO_SIDED safety veto before an optional OFF
    # setting clears experimental scenario details. OFF may disable
    # experiments but must NEVER disable verified-pressure safety.
    live_two_sided_veto=next((
        flag for flag in (scenario or {}).get("risk_flags",[])
        if str(flag).startswith("TWO_SIDED_")),None)
    # Existing ad-hoc scenario ENFORCE stays compatible outside the new
    # JOINT profile. JOINT itself performs the complete three-brain review.
    decision_mode=os.getenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW").upper()
    scenario_mode=os.getenv("GOOL_NEXT_GOAL_SCENARIOS","SHADOW").upper()
    if scenario_mode=="ENFORCE" and decision_mode!="JOINT" and scenario["risk_flags"]:
        return None,"FS_SCENARIO_RISK"
    if scenario_mode=="OFF" and decision_mode!="JOINT":
        scenario=None
    model_shadow=None
    if (decision_mode=="JOINT" or
            os.getenv("GOOL_NEXT_GOAL_POISSON_SHADOW","1")=="1"):
        from progruz1_poisson_shadow import estimate_next_goal
        model_shadow=estimate_next_goal(
            history=history,minute=minute,score=score,
            second_half=second_half,
            recent_shots=recent,recent_sot=recent_on,
            recent_age=pressure.get("recent_age",0),
            shots=shots,sot=sot,odd=odd,under_odd=m["under"],
            xg=xg,scenario=scenario)
    from progruz1_joint_decision import joint_decision
    joint=joint_decision(
        scenario=scenario,model=model_shadow,odd=odd,mode=decision_mode)
    # Preserve a structured snapshot for accepted AND vetoed old-brain
    # candidates. The runtime writes this once per fixture/minute to SQLite.
    if isinstance(decision_sink,dict):
        decision_sink.update({
            "minute":minute,"score":score,"scope":period,
            "market_period":str(m["period"]),"line":float(m["line"]),
            "odd":odd,"model":model_shadow,"scenario":scenario,
            "joint":joint,"fs_id":str(ctx.get("event_id") or "")})
    # A TWO_SIDED pulse without verified *per-team* recent attacks must
    # NEVER become a public signal, even while other scenario/Poisson
    # systems are in experimental SHADOW or OFF mode. SHADOW previously
    # allowed all scenario vetoes to become real alerts.
    if live_two_sided_veto:
        if isinstance(decision_sink,dict):
            decision_sink["joint"]={
                **joint,"approved":False,
                "reason":live_two_sided_veto,
                "would_approve":False,
                "would_reason":live_two_sided_veto}
        return None,live_two_sided_veto
    if decision_mode=="JOINT" and not joint["approved"]:
        return None,joint["reason"]
    xg_label=(f" · xG {sum(xg):.2f}" if xg is not None else "")
    detail=(f"Flashscore: удары {int(shots[0])}:{int(shots[1])}, "
            f"створы {int(sot[0])}:{int(sot[1])}{xg_label}; "
            f"недавно +{recent} ударов / +{recent_on} в створ")
    # When verified 10m team deltas exist, show them for auditability,
    # without suggesting that cumulative match shots are fresh attacks.
    recent_teams=pressure.get("recent_10m_shots_by_team")
    if (abs(score[0]-score[1])==1 and minute>=65
            and abs(shots[0]-shots[1])>=4
            and isinstance(recent_teams,(tuple,list))
            and len(recent_teams)==2
            and 480<=pressure.get("recent_10m_age",0)<=600):
        detail+=f"; удары за ~10 мин +{recent_teams[0]}:+{recent_teams[1]}"
    if isinstance(scenario,dict) and scenario.get("id")=="TWO_SIDED":
        if isinstance(pressure.get("recent_10m_shots_by_team"),(tuple,list)):
            hs,as_=pressure["recent_10m_shots_by_team"]
            detail+=f"; за ~10м удары {hs}:{as_}"
        if isinstance(pressure.get("recent_5m_shots_by_team"),(tuple,list)):
            hs,as_=pressure["recent_5m_shots_by_team"]
            detail+=f"; за ~5м удары {hs}:{as_}"
    if isinstance(scenario,dict) and scenario.get("id") in (
            "TWO_SIDED","TRAILING_CHASE","QUALITY_SURGE"):
        detail+=f"; сценарий {scenario['label']}"
    return {
        "quote_provider":str(m.get("quote_provider") or "Fonbet"),
        "quote_region":str(m.get("quote_region") or ""),
        "quote_label":str(m.get("quote_label") or "Фонбет LIVE"),
        "quote_source":str(m.get("quote_source") or "FONBET_LIVE"),
        "root_id":m["root_id"],"kickoff":int(m["kickoff"]),
        "event_id":m["event_id"],"period":str(m["period"]),
        "signal_scope":period,"line":float(m["line"]),
        "side":"TB","odd":odd,"score":score,
        "period_score":period_score,"first_half_score":ht,
        "half_score_source":str(ctx.get("half_score_source") or ""),
        "minute":minute,"match":m.get("match") or "",
        "p_model":0.0,"edge":0.0,"basis":BASIS,
        "market_kind":"general_total","team_index":None,
        "pressure_detail":detail,"history":history,
        "confidence_score":strength["score"],
        "confidence_label":strength["label"],
        "confidence_factors":strength["factors"],
        "confidence_adjustment":strength["adjustment"],
        "confidence_xg_available":strength["xg_available"],
        "minutes_remaining_regular":strength["minutes_remaining_regular"],
        "fs_event_id":str(ctx.get("event_id") or ""),
        "scenario":scenario,
        "model_shadow":model_shadow,
        "joint_decision":joint,
    },"FS_NEXT_GOAL_CANDIDATE"




def format_goal(pick,name,league):
    h=pick["history"]
    strength=int(pick["confidence_score"])
    filled=min(10,max(0,(strength+5)//10))
    confidence_bar="▰"*filled+"▱"*(10-filled)
    return (
        "⚽ ПРОГРУЗ 1 · LIVE · ЕЩЁ 1 ГОЛ\n"
        f"🏟 {name}\n🏆 {league or 'турнир не указан'}\n"
        f"⏱ {pick['minute']}' · {pick['score'][0]}:{pick['score'][1]}\n"
        f"🎯 {pick['period']} · ТБ {pick['line']:g} @{pick['odd']:.2f} "
        f"· {pick.get('quote_label') or 'Фонбет LIVE'}\n"
        f"⏱ Цель: ещё один гол до конца "
        f"{'1-го' if pick['signal_scope']=='1-й тайм' else '2-го'} тайма\n"
        f"⚽ Счёт тайма {pick['period_score'][0]}:{pick['period_score'][1]}\n"
        f"📚 История: хозяева {h['home_games']}, гости "
        f"{h['away_games']} игр; средний тотал по форме "
        f"{h['avg_total']:.2f}; личные встречи {h['h2h_games']} "
        f"(средний голевой тотал {h['h2h_avg_total'] if h['h2h_avg_total'] is not None else 'нет данных'})\n"
        f"📊 {pick['pressure_detail']}\n"
        f"🧠 Уверенность: {confidence_bar} "
        f"{pick['confidence_score']}/100 — {pick['confidence_label']}\n"
        f"🔍 Факторы: "
        f"(время {pick['confidence_factors']['time']:.0f}/20, "
        f"удары {pick['confidence_factors']['shots']:.0f}/18, "
        f"створы {pick['confidence_factors']['sot']:.0f}/16, "
        f"xG {pick['confidence_factors']['xg']:.0f}/14, "
        f"темп {pick['confidence_factors']['recent']:.0f}/16, "
        f"форма {pick['confidence_factors']['teams']:.0f}/10)\n"
        + (f"🎬 Сценарий: {pick['scenario']['label']}"
         + (" · ⚠️ риск" if pick['scenario']['risk_flags'] else "")
         + " (исследовательский)\n"
         if pick.get("scenario") else "")
        + ("🤝 Совместный отбор трёх систем: ОДОБРЕНО (эксперимент)\n"
           if (pick.get("joint_decision") or {}).get("mode")=="JOINT" else "")
        + f"⚠️ Шкала 0–100 — рейтинг ситуации, НЕ вероятность гола.\n"
        f"📌 Прогноз: ещё минимум один гол в текущем тайме; "
        f"порог окупаемости {1/pick['odd']:.1%}.\n"
        "⚠️ Анализ формы и активности не является "
        "откалиброванной вероятностью. Ставка не размещена.\n"
        "⏳ ОЖИДАЕТ ПОДТВЕРЖДЁННОГО РЕЗУЛЬТАТА"
    )
