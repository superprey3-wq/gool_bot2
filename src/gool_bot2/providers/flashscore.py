from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from .common import ProviderMatch, UA, http_text, pair_score

FSIGN = os.getenv("FLASHSCORE_FSIGN", "SW9D1eZo")
FEED_BASES = ("https://local-global.flashscore.ninja/2/x/feed", "https://global.flashscore.ninja/2/x/feed", "https://2.flashscore.ninja/2/x/feed", "https://local-ruua.flashscore.ninja/46/x/feed")
MASTER_PATHS = ("f_1_0_3_en_1", "f_1_0_0_en_1")
SCHEDULE_DAY_OFFSETS = (-1, 0, 1, 2, 3, 4, 5, 6, 7)
LIVE_COARSE_STATUS = "2"
FINISHED_COARSE_STATUS = "3"
FIRST_HALF_STATUS = "12"
SECOND_HALF_STATUS = "13"
HALFTIME_STATUS = "38"

STAT_MAP = {
    "432": "xg", "499": "xgot", "12": "possession", "34": "shots",
    "13": "shots_on_target", "14": "shots_off_target", "158": "blocked_shots",
    "461": "shots_inside_box", "463": "shots_outside_box", "459": "big_chances",
    "16": "corners", "471": "touches_box", "23": "yellow_cards",
}


MULTISPORT_STAT_ALIASES = {
    "shots on goal": "shots_on_goal",
    "shots on target": "shots_on_goal",
    "total shots": "shots",
    "shots": "shots",
    "blocked shots": "blocked_shots",
    "saves": "saves",
    "faceoffs won": "faceoffs_won",
    "face-offs won": "faceoffs_won",
    "penalties": "penalties",
    "penalty minutes": "penalty_minutes",
    "2-minute penalties": "penalties_2m",
    "2 minute penalties": "penalties_2m",
    "powerplay goals": "powerplay_goals",
    "power play goals": "powerplay_goals",
    "powerplay opportunities": "powerplay_opportunities",
    "power play opportunities": "powerplay_opportunities",
    "field goals": "field_goals",
    "2 point field goals": "two_point_field_goals",
    "2-point field goals": "two_point_field_goals",
    "3 point field goals": "three_point_field_goals",
    "3-point field goals": "three_point_field_goals",
    "free throws": "free_throws",
    "rebounds": "rebounds",
    "total rebounds": "rebounds",
    "offensive rebounds": "offensive_rebounds",
    "defensive rebounds": "defensive_rebounds",
    "assists": "assists",
    "turnovers": "turnovers",
    "steals": "steals",
    "blocks": "blocks",
    "fouls": "fouls",
    "personal fouls": "fouls",
    "possessions": "possessions",
    "possession": "possession",
}


def _clean_stat_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ").strip()).casefold()


def _stat_key(label: str, stat_id: str = "") -> str:
    clean = _clean_stat_label(label)
    if clean in MULTISPORT_STAT_ALIASES:
        return MULTISPORT_STAT_ALIASES[clean]
    if stat_id and str(stat_id) in STAT_MAP:
        return STAT_MAP[str(stat_id)]
    key = re.sub(r"[^a-z0-9]+", "_", clean).strip("_")
    return key or (f"stat_{stat_id}" if stat_id else "unknown")


def _stat_section_key(label: str) -> str:
    clean = _clean_stat_label(label)
    aliases = {
        "match": "FULL_MATCH", "full match": "FULL_MATCH", "overall": "FULL_MATCH",
        "1st period": "PERIOD_1", "1 period": "PERIOD_1", "period 1": "PERIOD_1",
        "2nd period": "PERIOD_2", "2 period": "PERIOD_2", "period 2": "PERIOD_2",
        "3rd period": "PERIOD_3", "3 period": "PERIOD_3", "period 3": "PERIOD_3",
        "1st quarter": "QUARTER_1", "1 quarter": "QUARTER_1", "quarter 1": "QUARTER_1",
        "2nd quarter": "QUARTER_2", "2 quarter": "QUARTER_2", "quarter 2": "QUARTER_2",
        "3rd quarter": "QUARTER_3", "3 quarter": "QUARTER_3", "quarter 3": "QUARTER_3",
        "4th quarter": "QUARTER_4", "4 quarter": "QUARTER_4", "quarter 4": "QUARTER_4",
        "1st half": "FIRST_HALF", "1 half": "FIRST_HALF", "first half": "FIRST_HALF",
        "2nd half": "SECOND_HALF", "2 half": "SECOND_HALF", "second half": "SECOND_HALF",
        "overtime": "OVERTIME", "ot": "OVERTIME",
    }
    if clean in aliases:
        return aliases[clean]
    for name, key in aliases.items():
        if name and name in clean:
            return key
    return "FULL_MATCH" if not clean else re.sub(r"[^A-Z0-9]+", "_", clean.upper()).strip("_")


def _stat_value(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    percent = text.endswith("%")
    clean = text[:-1].strip() if percent else text
    made = attempts = None
    if "/" in clean:
        left, right = clean.split("/", 1)
        try: made = float(left.strip())
        except (TypeError, ValueError): made = None
        try: attempts = float(right.strip())
        except (TypeError, ValueError): attempts = None
    try:
        value = float(clean.replace(",", ".")) if "/" not in clean else made
    except (TypeError, ValueError):
        value = None
    return {
        "raw": text,
        "value": value,
        "percent": bool(percent),
        "made": made,
        "attempts": attempts,
    }


def _fields(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in raw.split("¬"):
        if "÷" in token:
            key, value = token.split("÷", 1)
            if key and key not in out:
                out[key] = value
    return out


def _as_int(value: Any, default: int = 0) -> int:
    try: return int(float(str(value)))
    except (TypeError, ValueError): return default


def _minute(fields: dict[str, str], now: int) -> tuple[int, bool]:
    ac = fields.get("AC", ""); ao = _as_int(fields.get("AO")); ad = _as_int(fields.get("AD"))
    if ac == HALFTIME_STATUS: return 45, True
    if ac == FIRST_HALF_STATUS:
        base = ao or ad; elapsed = max(0, now - base) if base else 0
        return max(1, min(45, elapsed // 60 + 1)), False
    if ac == SECOND_HALF_STATUS:
        base = ao or ad; elapsed = max(0, now - base) if base else 0
        return max(46, min(90, 45 + elapsed // 60 + 1)), False
    if ao:
        elapsed = max(0, now - ao) // 60 + 1
        if ac == "6": return max(91, min(130, 90 + elapsed)), False
    if ad:
        elapsed = max(1, (now - ad) // 60 + 1)
        if elapsed > 60: elapsed -= 15
        return max(1, min(130, elapsed)), False
    return 1, False


def _to_number(value: Any) -> float:
    try: return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError): return 0.0


class FlashscoreProvider:
    name = "flashscore"

    def _feed(self, path: str, *, timeout: int = 12, max_hosts: int | None = None) -> str:
        headers = {"User-Agent": UA, "x-fsign": FSIGN, "Origin": "https://www.flashscore.com", "Referer": "https://www.flashscore.com/", "Accept": "*/*", "Cache-Control": "no-cache"}
        bases = FEED_BASES if max_hosts is None else FEED_BASES[:max(1, max_hosts)]
        for base in bases:
            code, body = http_text(f"{base}/{path}", headers=headers, timeout=timeout)
            if code == 200 and body.strip() and not body.lstrip().lower().startswith("<"): return body
        return ""

    def _h2h_feed(self, event_id: str) -> str:
        landing = f"https://www.flashscore.com/match/{event_id}/#/h2h/overall"
        headers = {"User-Agent": UA, "x-fsign": FSIGN, "Origin": "https://www.flashscore.com", "Referer": "https://www.flashscore.com/", "Accept": "*/*", "Cache-Control": "no-cache", "Pragma": "no-cache", "x-requested-with": "XMLHttpRequest", "x-referer": landing, "x-geoip": "1"}
        path = f"df_hh_1_{event_id}"
        for base in FEED_BASES:
            code, body = http_text(f"{base}/{path}", headers=headers, timeout=15)
            if code == 200 and body.strip() and not body.lstrip().lower().startswith("<"): return body
        return ""

    @staticmethod
    @lru_cache(maxsize=1024)
    def team_logo_url(team_slug: str, team_id: str) -> str | None:
        slug = str(team_slug or "").strip().strip("/"); team_id = str(team_id or "").strip()
        if not slug or not team_id: return None
        url = f"https://www.flashscore.com/team/{quote(slug)}/{quote(team_id)}/"
        headers = {"User-Agent": UA, "Accept": "text/html,*/*", "Referer": "https://www.flashscore.com/"}
        code, body = http_text(url, headers=headers, timeout=10)
        if code != 200 or not body: return None
        patterns = (r'"(?:image_path|small_image_path|imagePath|smallImagePath)"\s*:\s*"(https?:\\?/\\?/static\.flashscore\.com\\?/res\\?/image\\?/data\\?/[^"\\]+\.png)"', r'(https://static\.flashscore\.com/res/image/data/[A-Za-z0-9_-]+\.png)', r'(//static\.flashscore\.com/res/image/data/[A-Za-z0-9_-]+\.png)')
        for pattern in patterns:
            m = re.search(pattern, body)
            if m:
                value = m.group(1).replace("\\/", "/"); return "https:" + value if value.startswith("//") else value
        return None

    def _parse_master_states(self, body: str) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for chunk in (body or "").split("~"):
            if not chunk.startswith("AA÷"): continue
            event_id, sep, rest = chunk[3:].partition("¬")
            if not sep or len(event_id) != 8 or not event_id.isalnum(): continue
            f = _fields(rest); coarse = str(f.get("AB") or "")
            states[event_id] = {"event_id": event_id, "coarse_status": coarse, "status_code": str(f.get("AC") or ""), "is_live": coarse == LIVE_COARSE_STATUS, "is_finished": coarse == FINISHED_COARSE_STATUS, "home_score": _as_int(f.get("AG"), _as_int(f.get("AT"))), "away_score": _as_int(f.get("AH"), _as_int(f.get("AU")))}
        return states

    def event_states(self, event_ids: set[str] | list[str]) -> dict[str, dict[str, Any]]:
        wanted = {str(x) for x in event_ids if str(x)}
        if not wanted: return {}
        merged: dict[str, dict[str, Any]] = {}
        for path in MASTER_PATHS:
            body = self._feed(path)
            if not body: continue
            for event_id, state in self._parse_master_states(body).items():
                if event_id in wanted: merged[event_id] = state
        return merged

    def parse_master_live(self, body: str) -> list[ProviderMatch]:
        now = int(time.time()); matches: list[ProviderMatch] = []; league = ""
        for chunk in (body or "").split("~"):
            if not chunk: continue
            if chunk.startswith("ZA÷"): league = _fields(chunk).get("ZA", "").strip(); continue
            if not chunk.startswith("AA÷"): continue
            event_id, sep, rest = chunk[3:].partition("¬")
            if not sep or len(event_id) != 8 or not event_id.isalnum(): continue
            f = _fields(rest)
            if f.get("AB") != LIVE_COARSE_STATUS: continue
            home = (f.get("AE") or f.get("CX") or "").strip(); away = (f.get("AF") or "").strip()
            if not home or not away: continue
            minute, is_ht = _minute(f, now)
            meta = {"status_code": f.get("AC", ""), "coarse_status": f.get("AB", ""), "home_team_id": (f.get("JA") or "").strip(), "away_team_id": (f.get("JB") or "").strip(), "home_team_slug": (f.get("WU") or "").strip(), "away_team_slug": (f.get("WV") or "").strip(), "home_short": (f.get("WM") or "").strip(), "away_short": (f.get("WN") or "").strip(), "round": (f.get("ER") or "").strip(), "home_logo_file": (f.get("OA") or "").strip(), "away_logo_file": (f.get("OB") or "").strip()}
            matches.append(ProviderMatch(provider=self.name, provider_match_id=event_id, home=home, away=away, minute=minute, home_score=_as_int(f.get("AG"), _as_int(f.get("AT"))), away_score=_as_int(f.get("AH"), _as_int(f.get("AU"))), league=league, is_halftime=is_ht, meta=meta))
        return list({m.provider_match_id: m for m in matches}.values())

    def parse_master_scheduled(self, body: str) -> list[ProviderMatch]:
        """Parse upcoming football fixtures from the same Flashscore master feed used by LIVE."""
        matches: list[ProviderMatch] = []; league = ""
        for chunk in (body or "").split("~"):
            if not chunk: continue
            if chunk.startswith("ZA÷"): league = _fields(chunk).get("ZA", "").strip(); continue
            if not chunk.startswith("AA÷"): continue
            event_id, sep, rest = chunk[3:].partition("¬")
            if not sep or len(event_id) != 8 or not event_id.isalnum(): continue
            f = _fields(rest); coarse = str(f.get("AB") or "")
            # Flashscore master uses coarse status 1 for scheduled/not-started events.
            if coarse != "1": continue
            home = (f.get("AE") or f.get("CX") or "").strip(); away = (f.get("AF") or "").strip()
            if not home or not away: continue
            start_ts = _as_int(f.get("AD") or f.get("AO"), 0)
            meta = {"status_code": f.get("AC", ""), "coarse_status": coarse, "scheduled_start_ts": start_ts, "home_team_id": (f.get("JA") or "").strip(), "away_team_id": (f.get("JB") or "").strip(), "home_team_slug": (f.get("WU") or "").strip(), "away_team_slug": (f.get("WV") or "").strip(), "round": (f.get("ER") or "").strip(), "home_logo_file": (f.get("OA") or "").strip(), "away_logo_file": (f.get("OB") or "").strip()}
            matches.append(ProviderMatch(provider=self.name, provider_match_id=event_id, home=home, away=away, league=league, meta=meta))
        return list({m.provider_match_id: m for m in matches}.values())

    def scheduled_matches_for_day(self, day: int = 0) -> list[ProviderMatch]:
        body = self._feed(f"f_1_{day}_3_en_1", timeout=4, max_hosts=1)
        if not body:
            return []
        matches = self.parse_master_scheduled(body)
        return sorted(matches, key=lambda m: (int((m.meta or {}).get("scheduled_start_ts") or 0), m.league or "", m.home))

    def scheduled_matches(self) -> list[ProviderMatch]:
        merged: dict[str, ProviderMatch] = {}
        # Flashscore daily football feed: f_1_{day}_3_en_1.  Day 0 is today,
        # positive offsets are future dates. Unlike the LIVE/master feed this
        # endpoint returns the complete calendar day (all competitions).
        for day in SCHEDULE_DAY_OFFSETS:
            body = self._feed(f"f_1_{day}_3_en_1")
            if not body: continue
            for match in self.parse_master_scheduled(body):
                merged[match.provider_match_id] = match
        return sorted(merged.values(), key=lambda m: (int((m.meta or {}).get("scheduled_start_ts") or 0), m.league or "", m.home))

    def live_matches(self) -> list[ProviderMatch]:
        merged: dict[str, ProviderMatch] = {}
        for path in MASTER_PATHS:
            body = self._feed(path)
            if not body: continue
            for match in self.parse_master_live(body): merged[match.provider_match_id] = match
        return sorted(merged.values(), key=lambda m: ((m.minute or 0), m.league or "", m.home))

    @staticmethod
    def parse_stats_detailed(body: str) -> dict[str, Any]:
        """Parse every Flashscore stats section without sport-specific loss.

        Flashscore encodes section/set in HA, category in SF, stat label in SG,
        optional numeric id in SD and home/away values in SH/SI. Unknown rows are
        preserved in raw so new hockey/basketball metrics can be learned later.
        """
        sections: dict[str, dict[str, Any]] = {}
        current_section_label = "Full match"
        current_category = ""

        def section(label: str) -> dict[str, Any]:
            key = _stat_section_key(label)
            return sections.setdefault(key, {
                "key": key,
                "label": label or "Full match",
                "stats": {},
                "raw": [],
            })

        for chunk in (body or "").split("~"):
            if not chunk:
                continue
            fields = _fields(chunk)
            if fields.get("HA"):
                current_section_label = str(fields.get("HA") or current_section_label).strip()
            if fields.get("SF"):
                current_category = str(fields.get("SF") or "").strip()

            home_raw = fields.get("SH")
            away_raw = fields.get("SI")
            if home_raw is None or away_raw is None:
                continue

            stat_id = str(fields.get("SD") or "").strip()
            label = str(
                fields.get("SG")
                or fields.get("SE")
                or fields.get("SN")
                or STAT_MAP.get(stat_id)
                or (f"stat_{stat_id}" if stat_id else "unknown")
            ).strip()
            key = _stat_key(label, stat_id)
            home = _stat_value(home_raw)
            away = _stat_value(away_raw)
            item = {
                "id": stat_id or None,
                "name": label,
                "key": key,
                "category": current_category or None,
                "home": home,
                "away": away,
            }
            target = section(current_section_label)
            target["raw"].append(item)
            target["stats"][key] = {
                "home": home.get("value"),
                "away": away.get("value"),
                "home_raw": home.get("raw"),
                "away_raw": away.get("raw"),
                "home_made": home.get("made"),
                "away_made": away.get("made"),
                "home_attempts": home.get("attempts"),
                "away_attempts": away.get("attempts"),
                "percent": bool(home.get("percent") or away.get("percent")),
                "name": label,
                "id": stat_id or None,
                "category": current_category or None,
            }

        # Some feeds do not emit an explicit HA row before full-match stats.
        if not sections and body:
            sections["FULL_MATCH"] = {"key": "FULL_MATCH", "label": "Full match", "stats": {}, "raw": []}
        return {
            "sections": sections,
            "section_keys": list(sections),
            "raw_present": bool(body),
        }

    def fetch_stats_detailed(self, event_id: str) -> dict[str, Any]:
        body = self._feed(f"df_st_1_{event_id}")
        return self.parse_stats_detailed(body)

    def fetch_stats(self, event_id: str) -> dict[str, tuple[float, float]]:
        body = self._feed(f"df_st_1_{event_id}"); out: dict[str, tuple[float, float]] = {}
        for chunk in (body or "").split("~"):
            m = re.search(r"SD(?:÷|¬)(\d+).*?SH(?:÷|¬)([^¬~]+).*?SI(?:÷|¬)([^¬~]+)", chunk)
            if not m: continue
            stat_id, home, away = m.groups(); name = STAT_MAP.get(stat_id)
            if name: out[name] = (_to_number(home), _to_number(away))
        return out

    def fetch_goal_timeline(self, event_id: str) -> list[dict[str, Any]]:
        """Return only confirmed goal incidents from the Flashscore summary feed.

        `df_sui` also contains cards, substitutions and VAR incidents. Some of
        those rows carry the *current* INX/IOX scoreboard, so treating every row
        with a score as a goal can back-date a later goal to an unrelated event
        minute. That was the source of the Manila Digger result being settled at
        35' although the second goal arrived in first-half stoppage time.
        """
        body = self._feed(f"df_sui_1_{event_id}")
        incidents: list[dict[str, Any]] = []
        for order, chunk in enumerate((body or "").split("~III")):
            if not chunk:
                continue
            event_type = re.search(r"(?:^|¬)IA(?:÷|¬)(\d+)", chunk)
            if not event_type or event_type.group(1) != "1":
                continue
            mm = re.search(r"(?:IB|IBX)(?:÷|¬)(\d{1,3})(?:\+(\d{1,2}))?(?:'|\\')?", chunk)
            hm = re.search(r"INX(?:÷|¬)(\d+)", chunk)
            am = re.search(r"IOX(?:÷|¬)(\d+)", chunk)
            if not mm or (not hm and not am):
                continue
            base_minute = int(mm.group(1))
            added = int(mm.group(2) or 0)
            incidents.append({
                "minute": base_minute + added,
                "base_minute": base_minute,
                "added_time": added,
                "home": None if hm is None else int(hm.group(1)),
                "away": None if am is None else int(am.group(1)),
                "order": order,
            })

        # The incident feed is not guaranteed to be oldest-first. Reconstruct
        # cumulative scores chronologically so a later current score cannot be
        # attached to an earlier event row.
        incidents.sort(key=lambda row: (int(row["minute"]), int(row["order"])))
        goals: list[dict[str, Any]] = []
        last = (0, 0)
        for item in incidents:
            home = last[0] if item["home"] is None else int(item["home"])
            away = last[1] if item["away"] is None else int(item["away"])
            if home < last[0] or away < last[1]:
                continue
            if home == last[0] and away == last[1]:
                continue
            # A football goal changes exactly one side by one. Ignore malformed
            # scoreboard jumps rather than fabricating a goal timestamp.
            dh, da = home - last[0], away - last[1]
            if (dh, da) not in {(1, 0), (0, 1)}:
                continue
            base = int(item["base_minute"])
            added = int(item["added_time"])
            goals.append({
                "minute": int(item["minute"]),
                "display_minute": f"{base}+{added}" if added else str(base),
                "period": "1H" if base <= 45 else "2H",
                "event_type": "goal",
                "side": "home" if dh else "away",
                "score": [home, away],
            })
            last = (home, away)
        return goals

    @staticmethod
    def _parse_h2h_matches(body: str, current_event_id: str, limit: int = 80) -> list[dict[str, Any]]:
        """Parse current Flashscore H2H feed.

        The live H2H endpoint currently uses KB section headers and KC match rows,
        not the AA master-feed rows used by older code. KC carries timestamp, KP
        event id, KJ/KK teams and KU/KT final score (KL is the display score).
        """
        rows: list[dict[str, Any]] = []
        section = "unknown"
        section_index = 0
        for chunk in (body or "").split("~"):
            if not chunk: continue
            f = _fields(chunk)
            if chunk.startswith("KB÷"):
                label = str(f.get("KB") or "").strip().lower()
                section_index += 1
                if "head" in label or "h2h" in label or "mutual" in label:
                    section = "h2h"
                elif "last matches" in label or "last games" in label or "recent" in label:
                    # Flashscore emits two consecutive 'Last matches: TEAM' blocks.
                    section = "home_form" if section_index == 1 else "away_form"
                elif "home" in label:
                    section = "home_form"
                elif "away" in label:
                    section = "away_form"
                continue
            if chunk.startswith("KC÷"):
                event_id = str(f.get("KP") or "").strip()
                if not event_id or event_id == current_event_id: continue
                home = str(f.get("KJ") or f.get("FH") or "").replace("*", "").strip()
                away = str(f.get("KK") or f.get("FK") or "").replace("*", "").strip()
                if not home or not away: continue
                hs = _as_int(f.get("KU"), -1); aws = _as_int(f.get("KT"), -1)
                if hs < 0 or aws < 0:
                    score = str(f.get("KL") or "").strip()
                    m = re.match(r"^(\d+)\s*:\s*(\d+)$", score)
                    if m: hs, aws = int(m.group(1)), int(m.group(2))
                if hs < 0 or aws < 0: continue
                rows.append({"event_id": event_id, "home": home, "away": away, "home_score": hs, "away_score": aws, "timestamp": _as_int(f.get("KC")), "section": section, "source": "flashscore"})
                if len(rows) >= limit: break
                continue
            # Backward compatibility with the previous AA-style H2H schema.
            if chunk.startswith("ZA÷") or chunk.startswith("ZB÷"):
                label = " ".join(f.values()).lower()
                if "head" in label or "h2h" in label or "mutual" in label: section = "h2h"
                elif "home" in label: section = "home_form"
                elif "away" in label: section = "away_form"
                continue
            if chunk.startswith("AA÷"):
                event_id, sep, rest = chunk[3:].partition("¬")
                if not sep or event_id == current_event_id: continue
                ff = _fields(rest); coarse = str(ff.get("AB") or "")
                if coarse and coarse != FINISHED_COARSE_STATUS: continue
                home = (ff.get("AE") or ff.get("CX") or "").strip(); away = (ff.get("AF") or "").strip()
                hs = _as_int(ff.get("AG"), _as_int(ff.get("AT"), -1)); aws = _as_int(ff.get("AH"), _as_int(ff.get("AU"), -1))
                if home and away and hs >= 0 and aws >= 0:
                    rows.append({"event_id": event_id, "home": home, "away": away, "home_score": hs, "away_score": aws, "timestamp": _as_int(ff.get("AD")), "section": section, "source": "flashscore"})
                    if len(rows) >= limit: break
        return rows

    @staticmethod
    def _same_team(name: str, target: str) -> bool:
        name = str(name or "").strip(); target = str(target or "").strip()
        if not name or not target: return False
        if name.casefold() == target.casefold(): return True
        return pair_score(target, target, name, name) >= 0.62

    def fetch_match_history(self, event_id: str, home: str, away: str, limit: int = 10) -> dict[str, Any]:
        body = self._h2h_feed(event_id)
        matches = self._parse_h2h_matches(body, event_id, limit=max(80, limit * 8))
        def has_team(row: dict[str, Any], team: str) -> bool:
            return self._same_team(str(row.get("home") or ""), team) or self._same_team(str(row.get("away") or ""), team)
        def latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            seen: set[str] = set(); out: list[dict[str, Any]] = []
            for row in sorted(rows, key=lambda x: int(x.get("timestamp") or 0), reverse=True):
                key = str(row.get("event_id") or "")
                if key and key in seen: continue
                if key: seen.add(key)
                out.append(row)
                if len(out) >= limit: break
            return out

        by_home_section = [r for r in matches if str(r.get("section")) == "home_form"]
        by_away_section = [r for r in matches if str(r.get("section")) == "away_form"]
        by_h2h_section = [r for r in matches if str(r.get("section")) == "h2h"]
        matched_home = [r for r in matches if has_team(r, home)]
        matched_away = [r for r in matches if has_team(r, away)]
        home_recent = latest(by_home_section or matched_home)
        away_recent = latest(by_away_section or matched_away)
        h2h = latest(by_h2h_section or [r for r in matches if has_team(r, home) and has_team(r, away)])
        if len(home_recent) < min(5, limit): home_recent = latest(matched_home)
        if len(away_recent) < min(5, limit): away_recent = latest(matched_away)
        home_at_home = latest([r for r in home_recent if self._same_team(str(r.get("home") or ""), home)])
        away_away = latest([r for r in away_recent if self._same_team(str(r.get("away") or ""), away)])
        sections = {name: sum(1 for r in matches if str(r.get("section")) == name) for name in ("home_form", "away_form", "h2h", "unknown")}
        return {"source": "flashscore_h2h", "event_id": event_id, "home_recent": home_recent, "away_recent": away_recent, "home_at_home": home_at_home, "away_away": away_away, "h2h": h2h, "raw_matches": len(matches), "feed_present": bool(body), "matched_home": len(home_recent), "matched_away": len(away_recent), "section_counts": sections}
