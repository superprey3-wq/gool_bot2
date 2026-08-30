from __future__ import annotations

import os
import re
import time
from typing import Any

from .common import ProviderMatch, UA, http_text

FSIGN = os.getenv("FLASHSCORE_FSIGN", "SW9D1eZo")
FEED_HOSTS = ("global", "2", "46")
MASTER_PATHS = ("f_1_0_3_en_1", "f_1_0_0_en_1")
LIVE_COARSE_STATUS = "2"
FIRST_HALF_STATUS = "12"
SECOND_HALF_STATUS = "13"
HALFTIME_STATUS = "38"

STAT_MAP = {
    "432": "xg",
    "499": "xgot",
    "12": "possession",
    "34": "shots",
    "13": "shots_on_target",
    "14": "shots_off_target",
    "158": "blocked_shots",
    "461": "shots_inside_box",
    "463": "shots_outside_box",
    "459": "big_chances",
    "16": "corners",
    "471": "touches_box",
    "23": "yellow_cards",
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
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _minute(fields: dict[str, str], now: int) -> tuple[int, bool]:
    ac = fields.get("AC", "")
    ao = _as_int(fields.get("AO"))
    ad = _as_int(fields.get("AD"))
    if ac == HALFTIME_STATUS:
        return 45, True
    if ac == FIRST_HALF_STATUS:
        base = ao or ad
        elapsed = max(0, now - base) if base else 0
        return max(1, min(45, elapsed // 60 + 1)), False
    if ac == SECOND_HALF_STATUS:
        base = ao or ad
        elapsed = max(0, now - base) if base else 0
        return max(46, min(90, 45 + elapsed // 60 + 1)), False
    if ao:
        elapsed = max(0, now - ao) // 60 + 1
        if ac == "6":
            return max(91, min(130, 90 + elapsed)), False
    if ad:
        elapsed = max(1, (now - ad) // 60 + 1)
        if elapsed > 60:
            elapsed -= 15
        return max(1, min(130, elapsed)), False
    return 1, False


def _to_number(value: Any) -> float:
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


class FlashscoreProvider:
    name = "flashscore"

    def _feed(self, path: str) -> str:
        headers = {
            "User-Agent": UA,
            "x-fsign": FSIGN,
            "Origin": "https://www.flashscore.com",
            "Referer": "https://www.flashscore.com/",
            "Accept": "*/*",
            "Cache-Control": "no-cache",
        }
        for host in FEED_HOSTS:
            code, body = http_text(f"https://{host}.flashscore.ninja/2/x/feed/{path}", headers=headers, timeout=12)
            if code == 200 and body.strip() and not body.lstrip().lower().startswith("<"):
                return body
        return ""

    def parse_master_live(self, body: str) -> list[ProviderMatch]:
        now = int(time.time())
        matches: list[ProviderMatch] = []
        league = ""
        for chunk in (body or "").split("~"):
            if not chunk:
                continue
            if chunk.startswith("ZA÷"):
                league = _fields(chunk).get("ZA", "").strip()
                continue
            if not chunk.startswith("AA÷"):
                continue
            event_id, sep, rest = chunk[3:].partition("¬")
            if not sep or len(event_id) != 8 or not event_id.isalnum():
                continue
            f = _fields(rest)
            if f.get("AB") != LIVE_COARSE_STATUS:
                continue
            home = (f.get("AE") or f.get("CX") or "").strip()
            away = (f.get("AF") or "").strip()
            if not home or not away:
                continue
            minute, is_ht = _minute(f, now)
            matches.append(
                ProviderMatch(
                    provider=self.name,
                    provider_match_id=event_id,
                    home=home,
                    away=away,
                    minute=minute,
                    home_score=_as_int(f.get("AG"), _as_int(f.get("AT"))),
                    away_score=_as_int(f.get("AH"), _as_int(f.get("AU"))),
                    league=league,
                    is_halftime=is_ht,
                    meta={"status_code": f.get("AC", "")},
                )
            )
        return list({m.provider_match_id: m for m in matches}.values())

    def live_matches(self) -> list[ProviderMatch]:
        merged: dict[str, ProviderMatch] = {}
        for path in MASTER_PATHS:
            body = self._feed(path)
            if not body:
                continue
            for match in self.parse_master_live(body):
                merged[match.provider_match_id] = match
        return sorted(merged.values(), key=lambda m: ((m.minute or 0), m.league or "", m.home))

    def fetch_stats(self, event_id: str) -> dict[str, tuple[float, float]]:
        body = self._feed(f"df_st_1_{event_id}")
        out: dict[str, tuple[float, float]] = {}
        for chunk in (body or "").split("~"):
            match = re.search(r"SD(?:÷|¬)(\d+).*?SH(?:÷|¬)([^¬~]+).*?SI(?:÷|¬)([^¬~]+)", chunk)
            if not match:
                continue
            stat_id, home, away = match.groups()
            name = STAT_MAP.get(stat_id)
            if name:
                out[name] = (_to_number(home), _to_number(away))
        return out

    def fetch_goal_timeline(self, event_id: str) -> list[dict[str, Any]]:
        body = self._feed(f"df_sui_1_{event_id}")
        goals: list[dict[str, Any]] = []
        last = (0, 0)
        for chunk in (body or "").split("~III"):
            if not chunk:
                continue
            mm = re.search(r"(?:IB|IBX)(?:÷|¬)(\d{1,3})(?:'|\\')?", chunk)
            hm = re.search(r"INX(?:÷|¬)(\d+)", chunk)
            am = re.search(r"IOX(?:÷|¬)(\d+)", chunk)
            if not mm or (not hm and not am):
                continue
            home = int(hm.group(1)) if hm else last[0]
            away = int(am.group(1)) if am else last[1]
            if home > last[0] or away > last[1]:
                goals.append({"minute": int(mm.group(1)), "side": "home" if home > last[0] else "away", "score": [home, away]})
                last = (home, away)
        return goals
