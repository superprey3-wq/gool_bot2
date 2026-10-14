"""Exact allowlist for automatically delivered GOOL tournament-wide coupons.

Competition labels originate from Flashscore, not a trusted canonical ID.
Never treat every Flashscore tournament as a public accumulator.
"""
from __future__ import annotations

import re
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    s = s.replace("’", "'")
    return " ".join(s.split())


def _excluded(label: str) -> bool:
    return bool(re.search(
        r"\b(women|woman|female|feminin|feminine|feminina|femminile|"
        r"женщин(?:ы|ы?\b|ский|ская)?|women's|womens|"
        r"u-?\d{2}|under\s?\d{2}|youth|junior|reserve|reserves|"
        r"development|academy|amateur|futsal|футзал|молод[её]ж|юнош)\b",
        label,
    ))


def elite_tournament_tier(league: str) -> int | None:
    """0: UEFA men's Champions League; 1-5: domestic Big Five top divisions.

    Use a country/UEFA prefix when available; unprefixed labels are allowed
    only if unambiguous. Premier League 2, Serie A2, LaLiga2, youth/women,
    AFC/CAF/CONCACAF Champions League and second divisions are excluded.
    """
    raw = _norm(league)
    if not raw or _excluded(raw):
        return None
    country, _, rest = raw.partition(":")
    country, rest = country.strip(), rest.strip()
    if not rest:
        country, rest = "", raw
    league_name = re.split(r"\s+[-–—|]\s+", rest, maxsplit=1)[0].strip()
    uefa_origin = country in {"", "europe", "uefa", "европа"}
    if (uefa_origin
        and (re.search(r"\bchampions league\b", league_name)
             or re.search(r"\bлига чемпионов\b", league_name))
        and not re.search(r"\b(afc|caf|concacaf|asian|africa|asia|oceania)\b", raw)
    ):
        return 0
    leagues = {
        "england": (1, r"^premier league\b(?!\s*(?:2|u\d{2}|cup|women))"),
        "англия": (1, r"^премьер[- ]лига\b"),
        "spain": (2, r"^(?:laliga|la liga|primera division)\b(?!\s*2)"),
        "испания": (2, r"^(?:лалиг[а-я]*|примера)\b"),
        "italy": (3, r"^serie a\b(?!\s*(?:2|women))"),
        "италия": (3, r"^серия а\b"),
        "germany": (4, r"^bundesliga\b(?!\s*(?:2|3|women))"),
        "германия": (4, r"^бундеслига\b"),
        "france": (5, r"^ligue 1\b(?!\s*(?:2|women))"),
        "франция": (5, r"^лига 1\b"),
    }
    item = leagues.get(country)
    return item[0] if item and re.search(item[1], league_name) else None


def is_elite_tournament(league: str) -> bool:
    return elite_tournament_tier(league) is not None
