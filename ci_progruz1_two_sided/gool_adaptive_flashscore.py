"""Bounded Flashscore adapter for GOOL ADAPTIVE.

Uses original GOOL BOT 2 text feed parser with strict per-request budgets and
team-score matching. No fallback to fabricated xG / results.
"""
from __future__ import annotations
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher

from gool_flashscore_provider.flashscore import FlashscoreProvider
from gool_flashscore_provider.flashscore_stats_guard import parse_match_stats_body


RU={"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh",
    "з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o",
    "п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts",
    "ч":"ch","ш":"sh","щ":"shch","ы":"y","э":"e","ю":"yu","я":"ya",
    "ь":"","ъ":""}
ALIASES={"manchester siti":"manchester city","brayton":"brighton",
         "brayton houv":"brighton","bavariya":"bayern",
         "yuventus":"juventus","sanderlend":"sunderland",
         "brentford":"brentford","vulverhempton":"wolverhampton",
         "genoa":"genoa","dzhenoa":"genoa","fiorentino":"fiorentino",
         "frayburg":"freiburg","viffsburg":"wolfsburg",
         "portu":"porto","fiorentina":"fiorentina",
         # Fonbet Cyrillic labels vs Flashscore's Chinese League One names.
         # Explicit verified aliases, not broad team-name fuzzy stripping.
         "guansi henchen":"guangxi hengchen",
         "vuksi vugu":"wuxi wugou",
         "nankin siti":"nanjing city",
         "dalyan k un siti":"dalian k un city",
         "ninbo profeshnl":"ningbo professional",
         # Exact club-name transliterations observed in Fonbet PREMOVE.
         # Do not strip city/league/youth/gender tokens globally.
         "palmeyras sp":"palmeiras",
         "vasko da gama rzh":"vasco da gama",
         "vasko da gama":"vasco da gama",
         "boka huniors":"boca juniors",
         "river pleyt":"river plate",
         "sharlerua":"charleroi",
         "la luvyer":"la louviere",
         "la luvier":"la louviere",
         "la luver":"la louviere",
         # Observed *same live fixtures* in Fonbet and Flashscore. These
         # are exact club-name aliases; never strip arbitrary location,
         # youth, women's or reserve identity tokens globally.
         "tun":"thun","harts":"hearts",
         "omoniya mayu 29":"omonia 29th may",
         "aep polemidion":"polemidion",
         "montevideo uonderers":"wanderers",
         "urakan":"huracan",
         "kolon santa fe":"colon santa fe",
         "atletiko tukuman":"atl tucuman",
         "instituto kordoba":"instituto",
         "defensa i hustisiya":"defensa y justicia",
         "union santa fe":"union de santa fe",
         "taleres kordoba":"talleres cordoba",
         "rasing avelyaneda":"racing",
         "rosario sentral":"rosario central",
         "tsrvena zvezda":"crvena zvezda",
         "inter eskaldes":"inter escaldes",
         # Names witnessed with the same current kickoff, score and minute.
         "ferro karril oeste":"ferro",
         "san paulu":"sao paulo",
         "seara fortaleza":"ceara",
         # Confirmed bilingual names from paired UEFA / French LIVE feeds.
         # These are full club names, NOT suffix stripping or generic swaps.
         "bayer leverkuzen":"bayer leverkusen",
         "marsel":"marseille",
         "benfika":"benfica",
         "leh poznan":"lech poznan",
         "seltik":"celtic",
         "beshiktash":"besiktas",
         "hapoel beer sheva":"h beer sheva",
         "ofi":"ofi crete",
         "shturm":"sturm graz",
         "tsele":"celje",
         "dinamo zagreb":"din zagreb",
         "kopengagen":"copenhagen",
         "iberiya 1999":"iberia 1999",
         "hetafe":"getafe",
         "zhalgiris kaunas":"kauno zalgiris",
         "monako":"monaco",
         "norshellann":"nordsjaelland",
         "panatinaikos":"panathinaikos",
         "tsska sofiya":"cska sofia",
         "yablonets":"jablonec",
         "sent treyden":"st truiden",
         "tvente":"twente",
         "universitatya krayova":"univ craiova",
         "egnatiya rogozhina":"egnatia",
         "valansen":"valenciennes",
         "orlean":"orleans",
         "midtyullann":"midtjylland",
         "ayaks":"ajax",
         "estudiantes rio kuarte":"estudiantes rio cuarto",
         "portmor yun ayted":"portmore",
         "trezhe bich":"treasure beach",
         "portmor yunayted":"portmore",
         # Observed paired UEFA Europa match: Celta Vigo — Royale Union SG
         # is named Сельта — Унион Сент-Жиллуаз at Fonbet.
         "selta":"celta vigo",
         "union sent zhilluaz":"royale union sg"}

# Verified LIVE pairs on 2026-11-06: teams, kickoff and score observed
# in the same real Flashscore/Fonbet snapshot. Never strip youth/gender.
ALIASES.update({
    "neftchi baku": "neftci baku",
    "kyapaz": "kapaz",
    "lokomotiva zagreb": "lok zagreb",
    "rudesh": "rudes",
    "karlovats": "karlovac",
    "vukovar 91": "vukovar 1991",
    "aek larnaka": "aek larnaca",
    "nea salamina": "nea salamis",
    "omoniya aradipu": "omonia aradippou",
    "dukla praga": "dukla prague",
    "prshibram": "pribram",
    "viktoriya zhizhkov": "zizkov",
    "vayle": "vejle",
    "frederisiya": "fredericia",
    "naft maysan": "naft missan",
    "navruz": "newroz",
    "kerbela": "karbala",
    "al bakaa": "al buqaa",
    "al vehdat": "al wehdat",
    "dukadzhini": "dukagjini",
    "lapi": "llapi",
    "al shabab al ahmadi": "al shabab",
    "usfas bamako": "usfas bamako",
    "fut elit": "foot elite",
    "bokel": "bokelj",
    "motor lyublin": "motor lublin",
    "visla plotsk": "wisla plock",
    "al vakra": "al wakrah",
    "petrolul ploeshti": "petrolul",
    "lokomotiv moskva": "lokomotiv moscow",
    "rubin": "rubin kazan",
    "daymond starz": "diamond stars",
    "bay byureh uorriors": "bai bureh warriors",
    "nafta lendava": "nafta",
    "olimpiya lyublyana": "o ljubljana",
    "yurgorden": "djurgarden",
    "hekken": "hacken",
    "gaziantep bb": "gaziantep",
    "kasympasha": "kasimpasa",
    "independente avelyaneda": "independiente",
    "san lorenso almagro": "san lorenzo",
    # Actual same league/kickoff/clock/score pairs from today's misses.
    "atletiko grau": "grau",
    "vil 1900": "wil",
    "union breshiya": "brescia",
    "artsinyano valchiampo": "arzignano",
    "reyt rovers": "raith",
    "nansy": "nancy",
    "sosho": "sochaux",
    "denbi taun": "denbigh",
    "boka huniors kali": "boca juniors",
    "llandidno taun": "llandudno",
    "nyu seynts": "tns",
    # English league club names: verified transliteration variants,
    # with U19/women/reserve suffixes still handled separately.
    "birmingem": "birmingham",
    "birmingem siti": "birmingham",
    "birmingham city": "birmingham",
    "suonsi": "swansea",
    "suonsi siti": "swansea",
    "swansea city": "swansea",
    "hall": "hull",
    "hall siti": "hull",
    "hull city": "hull",
    "arsenal london": "arsenal",
    # Additional exact-name transliteration variants seen in the
    # unmatched English/French competitions. A name alias never skips
    # kickoff, league, home/away or scoreboard checks.
    "akkrington": "accrington",
    "uikomb": "wycombe",
    "uikomb uonderers": "wycombe",
    "borem vud": "boreham wood",
    "boremvud": "boreham wood",
    "uigan": "wigan",
    "kolchester": "colchester",
    "vuster": "worcester",
    "gayzli": "guiseley",
    "satton": "sutton",
    "hayd": "hyde",
    "redkar atletik": "redcar athletic",
    "burk an bres": "bourg en bresse",
    "le pyui an vele": "le puy en velay",
    # Real 13 Nov 2026 LIVE pairs: same ordered teams, kickoff,
    # live minute and score independently observed in BOTH feeds.
    "komorskie ostrova": "comoros",
    "chernogoriya": "montenegro",
    "shilong layong": "lajong",
    "ulyanik": "uljanik pula",
})
NEUTRAL=re.compile(r"\b(fc|cf|sc|afc|fk|club|football|soccer|calcio|fk|as|ac)\b",re.I)
AGE=re.compile(r"\b(?:u[12][0-9]|under\s*[12][0-9])\b",re.I)
WOMEN=re.compile(r"(?:\(ж\)|\bwomen\b|\bfemenin|\bdam\b|\bw\b)",re.I)


def normalize(name: str) -> str:
    raw=str(name or "").lower().strip()
    # Flashscore marks international club-friendlies by club country
    # (Kapfenberg (Aut), Varazdin (Cro)). These are geographic labels,
    # NOT team age/gender/reserve identities. Only known source codes.
    raw=re.sub(r"\s*\((?:aut|cro|cze|ger|slo)\)\s*$","",raw)
    # Fonbet Russian (р) = reserve, Flashscore convention = team "2".
    # Keep the suffix rather than merging reserves into the senior side.
    reserve=bool(re.search(r"\s*\((?:р|рез|резерв)\)\s*$",raw))
    women=bool(WOMEN.search(raw))
    raw=re.sub(r"\s*\((?:р|рез|резерв|ж)\)\s*$","",raw)
    translit="".join(RU.get(c,c) for c in raw)
    translit=unicodedata.normalize("NFKD",translit).encode("ascii","ignore").decode()
    translit=re.sub(r"[^a-z0-9]+"," ",translit).strip()
    # Accept "До 19 лет", "Under 19" and "U 19" as the same age
    # suffix without ever merging the U19 team with a senior/U21 side.
    translit=re.sub(
        r"\b(?:do|under|u)\s+(17|18|19|20|21|23)(?:\s+(?:let|goda|god))?\b",
        lambda match:"u"+match.group(1),translit)
    translit=NEUTRAL.sub(" ",translit)
    if women:
        translit=re.sub(r"\b(?:women|femenin|dam|ladies|female)\b"," ",translit)
    translit=re.sub(r"\s+"," ",translit).strip()
    # Preserve one age/gender/reserve suffix and expand aliases only on
    # the club name. Never equate unrelated U19/U20 or women/seniors.
    suffix=""
    m=re.search(r"\s+(u\d{2}|w|2)$",translit)
    if m:
        suffix=m.group(1)
        translit=translit[:m.start()].strip()
    club=ALIASES.get(translit,translit)
    if reserve: suffix="2"
    if women: suffix="w"
    return f"{club} {suffix}".strip()


def teams_compatible(a: str,b: str) -> bool:
    an,bn=normalize(a),normalize(b)
    # Reserve teams are not interchangeable with the parent senior teams;
    # same applies to women and different youth age brackets.
    def tags(v):
        return (bool(re.search(r"\bw$",v)),
                bool(re.search(r"\b2$",v)),
                tuple(AGE.findall(v)))
    return tags(an)==tags(bn)


def team_similarity(a: str,b: str) -> float:
    if not teams_compatible(a,b):
        return 0.0
    a=normalize(a);b=normalize(b)
    if not a or not b:
        return 0.0
    if a==b:
        return 1.0
    return SequenceMatcher(None,a,b).ratio()


def choose_fixture(home: str,away: str,fixtures: list, *, kickoff: int|None=None,
                   live_score: tuple[int,int]|None=None):
    scored=[]
    for m in fixtures:
        direct=(team_similarity(home,m.home)+team_similarity(away,m.away))/2
        reverse=(team_similarity(home,m.away)+team_similarity(away,m.home))/2
        if reverse>=direct*0.96:  # swapped home/away is not trustworthy
            continue
        if live_score and m.home_score is not None and m.away_score is not None:
            # scoreboard discrepancies often signal an incorrect fixture,
            # delayed data, or a recently overturned goal.
            if (int(m.home_score),int(m.away_score))!=tuple(live_score):
                continue
        if kickoff:
            fs_kick=int((m.meta or {}).get("scheduled_start_ts") or 0)
            if fs_kick and abs(fs_kick-kickoff)>1800:
                continue
        scored.append((direct,m))
    scored.sort(key=lambda v:v[0],reverse=True)
    if not scored or scored[0][0]<0.82:
        return None
    if len(scored)>1 and scored[0][0]-scored[1][0]<0.10:
        return None
    return scored[0][1]


def _verify_saved_fixture_id(fixtures:list,fixture_id:str,kickoff:int,
                             now:int,score:tuple[int,int]|None):
    """Restore a previously confirmed PREMATCH Flashscore ID safely.

    This is NOT a loose match by clock or name; only the same unique source
    event may be reused and its present-day LIVE score must match Fonbet.
    """
    eid=str(fixture_id or "")
    if not re.fullmatch(r"[A-Za-z0-9]{8}",eid) or score is None:
        return None
    if not isinstance(score,(tuple,list)) or len(score)!=2:
        return None
    if not kickoff-600<=now<=kickoff+4*3600:
        return None
    matches=[m for m in fixtures if
             str(getattr(m,"provider_match_id",""))==eid]
    if len(matches)!=1:
        return None
    m=matches[0]
    try:
        fs_score=(int(m.home_score),int(m.away_score))
        minute=float(m.minute)
        start=int((m.meta or {}).get("scheduled_start_ts") or 0)
    except (ValueError,TypeError,AttributeError):
        return None
    if (fs_score!=tuple(score) or not 0<=minute<=125 or
            (start and abs(start-int(kickoff))>1800)):
        return None
    return m


class FlashscoreContext:
    def __init__(self, provider=None, *, ttl=65, network_enabled=True):
        self.provider=provider or FlashscoreProvider()
        self.ttl=max(35,int(ttl))
        # Fixture score/minute updates must not inherit the long
        # history/analysis cache. Keep LIVE master requests bounded.
        self.live_ttl=max(10,int(os.getenv("GOOL_FS_LIVE_TTL","12")))
        self.enabled=network_enabled
        self._list=(0,[],[])
        self._cache={}
        self._scout_cache={}
        self._history={}
        self._daily_cache={}
        self._alternate_live=(0,[])
        self.calls=0

    def _feed(self,path):
        self.calls+=1
        return self.provider._feed(path,timeout=2,max_hosts=1)

    def list_matches(self,now: int):
        if now-self._list[0]<self.live_ttl and self._list[0]:
            return self._list[1],self._list[2]
        if not self.enabled:
            return [],[]
        body=self._feed("f_1_0_3_en_1")
        if not body:
            return self._list[1],self._list[2]
        live=self.provider.parse_master_live(body)
        future=self.provider.parse_master_scheduled(body)
        self._list=(now,live,future)
        return live,future

    def _alternate_live_matches(self,now:int):
        """Bounded retry from the provider's alternative LIVE master view.

        Never fall back to scheduled games as if they were in progress.
        At most one extra HTTP read per 90 seconds across all candidates.
        """
        when,rows=self._alternate_live
        if when and now-when<90:
            return rows
        body=self._feed("f_1_0_0_en_1")
        rows=self.provider.parse_master_live(body) if body else []
        self._alternate_live=(now,rows)
        return rows

    def seed_history(self,event_id:str,history:dict)->None:
        """Warm the original FS adapter from verified persisted prematch data."""
        if (re.fullmatch(r"[A-Za-z0-9]{8}",str(event_id or ""))
                and isinstance(history,dict)):
            key=str(event_id)
            # Bounded for free/low-RAM bot hosting; SQLite retains the evidence.
            if key in self._history:
                self._history.pop(key)
            self._history[key]=history
            while len(self._history)>120:
                self._history.pop(next(iter(self._history)))

    def invalidate_history(self,event_id:str)->None:
        """Allow a bounded retry of incomplete historical evidence."""
        self._history.pop(str(event_id or ""),None)

    def _scheduled_day(self,offset:int,now:int):
        """One bounded daily fallback for games absent from the main feed."""
        cached=self._daily_cache.get(offset)
        if cached and now-cached[0]<900:
            return cached[1]
        if not self.enabled:
            return []
        body=self._feed(f"f_1_{offset}_3_en_1")
        matches=self.provider.parse_master_scheduled(body) if body else []
        self._daily_cache[offset]=(now,matches)
        return matches

    def scout_live(self,*,fixture,now:int)->dict:
        """One-stat-request scouting for ALL LIVE fixtures.

        The fixture must originate in the current Flashscore master
        (unique event ID and displayed score). No bookmaker, history,
        halftime or goal-timeline request runs at this stage; those are
        reserved for remembered candidates AFTER they turn interesting.
        """
        eid=str(getattr(fixture,"provider_match_id","") or "")
        try:
            score=(int(fixture.home_score),int(fixture.away_score))
            minute=int(fixture.minute)
        except (TypeError,ValueError,OverflowError):
            return {"ok":False,"reason":"SCOUT_SCORE_OR_MINUTE_UNVERIFIED"}
        if (not re.fullmatch(r"[A-Za-z0-9]{8}",eid)
                or any(v<0 or v>70 for v in score)
                or minute<0 or minute>130):
            return {"ok":False,"reason":"SCOUT_FIXTURE_ID_UNVERIFIED"}
        key=(eid,score)
        cached=self._scout_cache.get(key)
        # A short LIVE cache is economical without disguising an old
        # match-wide total as a fresh observed goal burst.
        if cached and 0<=now-cached[0]<30:
            return cached[1]
        if not self.enabled:
            return {"ok":False,"reason":"SCOUT_NETWORK_DISABLED"}
        body=self._feed(f"df_st_1_{eid}")
        stats=parse_match_stats_body(body) if body else {}
        result={"ok":bool(stats),"event_id":eid,"score":score,
                "minute":minute,"stats":stats,"updated_at":now,
                "reason":"" if stats else "SCOUT_LIVE_STATS_UNAVAILABLE",
                "source":"flashscore"}
        self._scout_cache[key]=(now,result)
        while len(self._scout_cache)>350:
            self._scout_cache.pop(next(iter(self._scout_cache)))
        return result

    def context(self,*,root: str,home: str,away: str,kickoff:int,
                now:int,score:tuple[int,int]|None=None,
                fixture_id:str|None=None,
                preobserved_stats:dict|None=None) -> dict:
        # The provider fixture ID is stable across Fonbet/live-odds
        # market roots. Reuse verified LIVE stats across those roots,
        # always separating score changes so a goal forces revalidation.
        stable_id=str(fixture_id or "").strip()
        key=(("FS:"+stable_id) if stable_id else root,
             score,stable_id)
        cached=self._cache.get(key)
        # Empty/unverified Flashscore statistics are transient. Keeping
        # them for the full positive TTL can suppress an otherwise
        # excellent attack for multiple LIVE cycles.
        cache_ttl=(min(self.ttl,15) if cached
                   and not cached[1].get("ok") else self.ttl)
        if cached and 0<=now-cached[0]<cache_ttl:
            if (score is not None and isinstance(preobserved_stats,dict)
                    and preobserved_stats and cached[1].get("ok")):
                updated=dict(cached[1],stats=preobserved_stats,
                             updated_at=now,ok=True,reason="")
                self._cache[key]=(now,updated)
                return updated
            if preobserved_stats is None:
                return cached[1]
        live,future=self.list_matches(now)
        fixture=choose_fixture(home,away,live if score is not None else future,
                               kickoff=kickoff,live_score=score)
        # Prefer a new reliable name+score match. Only if the current
        # LIVE name matching fails, reuse the independently verified
        # previous PREMATCH ID for the *same* Flashscore event.
        if fixture is None and score is not None and fixture_id:
            fixture=_verify_saved_fixture_id(live,fixture_id,kickoff,now,score)
        if fixture is None and score is not None:
            # Alternate Flashscore master can contain lower-tier fixtures
            # missing from the first LIVE list. This feed is parsed as
            # LIVE only; still require the same unique names/score or
            # previously verified ID and kickoff constraints.
            alternate=self._alternate_live_matches(now)
            fixture=choose_fixture(home,away,alternate,
                                   kickoff=kickoff,live_score=score)
            if fixture is None and fixture_id:
                fixture=_verify_saved_fixture_id(alternate,fixture_id,
                                                kickoff,now,score)
        if fixture is None and score is None:
            # A match selected many hours before kickoff may appear in
            # tomorrow's calendar but not the current master feed.
            for offset in (0,1):
                fixture=choose_fixture(home,away,self._scheduled_day(offset,now),
                                       kickoff=kickoff)
                if fixture is not None:
                    break
        if fixture is None:
            reason="FLASHSCORE_MATCH_UNVERIFIED"
            if score is not None and choose_fixture(
                    home,away,live,kickoff=kickoff,live_score=None):
                reason="FLASHSCORE_SCORE_CONFLICT"
            return {"ok":False,"reason":reason,
                    "updated_at":now,"source":"flashscore"}
        eid=str(fixture.provider_match_id)
        history=self._history.get(eid)
        if history is None:
            # Capture recent matches once; avoid repeated expensive H2H reads.
            feed=self._feed(f"df_hh_1_{eid}")
            # H2H contains multiple blocks: first team's form, second
            # team's form, then mutual meetings. Truncating at 50 BEFORE
            # filtering silently drops away-team fixtures on busy feeds.
            recent=(self.provider._parse_h2h_matches(feed,eid,limit=160)
                    if feed else [])
            def form(team):
                hits=[x for x in recent if team_similarity(team,x.get("home",""))>0.82
                      or team_similarity(team,x.get("away",""))>0.82]
                hits.sort(key=lambda row:int(row.get("timestamp") or 0),reverse=True)
                seen=set()
                result=[]
                for item in hits:
                    key=str(item.get("event_id") or "").strip()
                    if key and key in seen:continue
                    if key:seen.add(key)
                    result.append(item)
                    if len(result)==10:break
                return result
            # H2H must contain only meetings BETWEEN these exact teams,
            # not the first N rows from each team's unrelated recent matches.
            mutual=[x for x in recent if
                    ((team_similarity(home,x.get("home",""))>=0.82
                      and team_similarity(away,x.get("away",""))>=0.82) or
                     (team_similarity(away,x.get("home",""))>=0.82
                      and team_similarity(home,x.get("away",""))>=0.82))]
            history={"home":form(home),"away":form(away),
                     "h2h":mutual[:10],"available":bool(recent)}
            self.seed_history(eid,history)
        stats={}
        if score is not None:
            if isinstance(preobserved_stats,dict) and preobserved_stats:
                # Same verified fixture and current-score stat sample from
                # scout_live(), not a fresh extra network request.
                stats=preobserved_stats
            else:
                body=self._feed(f"df_st_1_{eid}")
                stats=parse_match_stats_body(body) if body else {}
        # PROGRUZ 1: verify 1H score from Flashscore itself before
        # proposing a 2H-only market. A match score cannot be used as
        # 2H goals; never borrow the bookmaker's scoreboard for this.
        first_half_score=None
        half_score_source=""
        if score is not None:
            try:
                current_minute=int(fixture.minute)
            except (TypeError,ValueError):
                current_minute=0
            started_2h=(current_minute>=46 or
                (current_minute==45 and str(
                    (getattr(fixture,"meta",None) or {}).get(
                        "status_code") or "")=="13"))
            if 1<=current_minute<=45 and not started_2h:
                first_half_score=tuple(score)
                half_score_source="flashscore_live_first_half"
            elif started_2h:
                # Flashscore's explicit 1st Half split is better than
                # reconstructing a potentially incomplete event timeline.
                if self.enabled and hasattr(self.provider,"fetch_football_halves"):
                    try:
                        halves=self.provider.fetch_football_halves(eid)
                        value=(halves or {}).get("HT")
                        if (isinstance(value,(list,tuple)) and len(value)==2
                                and all(type(n) is int and 0<=n<=70 for n in value)
                                and value[0]<=score[0] and value[1]<=score[1]):
                            first_half_score=tuple(value)
                            half_score_source="flashscore_1st_half_score"
                    except Exception:
                        pass
                if first_half_score is None and tuple(score)==(0,0):
                    first_half_score=(0,0)
                    half_score_source="flashscore_live_zero_goals"
                elif first_half_score is None and self.enabled and hasattr(self.provider,"fetch_goal_timeline"):
                    try:
                        goals=self.provider.fetch_goal_timeline(eid)
                        # Some LIVE games have a complete score but their
                        # incidents feed is empty or temporarily unavailable.
                        # Never index an empty timeline; no reliable HT means
                        # no 2H-specific pick until history is verifiable.
                        last=(tuple(goals[-1].get("score") or ())
                              if isinstance(goals,list) and goals else ())
                        if len(last)==2 and last==tuple(score) and all(
                                g.get("period") in {"1H","2H"} and
                                g.get("side") in {"home","away"}
                                for g in goals):
                            hh=sum(1 for g in goals if
                                   g["period"]=="1H" and g["side"]=="home")
                            ha=sum(1 for g in goals if
                                   g["period"]=="1H" and g["side"]=="away")
                            if hh<=score[0] and ha<=score[1]:
                                first_half_score=(hh,ha)
                                half_score_source="flashscore_goal_timeline"
                    except (TypeError,ValueError,KeyError,AttributeError,TimeoutError):
                        pass  # No verified HT evidence means WAIT.
        result={"ok":bool(stats) if score is not None else bool(history["available"]),
                "source":"flashscore","event_id":eid,"updated_at":now,
                "minute":fixture.minute,"score":(
                    fixture.home_score,fixture.away_score),
                "first_half_score":first_half_score,
                "half_score_source":half_score_source,
                "stats":stats,"history":history,
                "reason":"" if stats or (score is None and history["available"])
                         else "FLASHSCORE_STATS_UNAVAILABLE"}
        self._cache[key]=(now,result)
        # Each score change would otherwise grow the cache for weeks.
        while len(self._cache)>256:
            self._cache.pop(next(iter(self._cache)))
        return result
