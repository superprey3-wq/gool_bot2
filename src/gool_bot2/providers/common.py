from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/137 Safari/537.36"


@dataclass(frozen=True)
class ProviderMatch:
    provider: str
    provider_match_id: str
    home: str
    away: str
    minute: int | None = None
    home_score: int | None = None
    away_score: int | None = None
    league: str | None = None
    is_halftime: bool = False
    stats: dict[str, tuple[float, float]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 10) -> tuple[int, dict]:
    req = Request(url, headers=headers or {"User-Agent": UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except Exception:
        return 0, {}


def http_text(url: str, headers: dict[str, str] | None = None, timeout: int = 12) -> tuple[int, str]:
    req = Request(url, headers=headers or {"User-Agent": UA, "Accept": "*/*"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except Exception:
        return 0, ""


def norm_team(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|sc|ac|afc|club|deportivo|deportes|women|w|femenil|femenino)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def pair_score(h1: str, a1: str, h2: str, a2: str) -> float:
    def sim(a: str, b: str) -> float:
        a, b = norm_team(a), norm_team(b)
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    direct = (sim(h1, h2) + sim(a1, a2)) / 2
    reverse = (sim(h1, a2) + sim(a1, h2)) / 2
    return max(direct, reverse * 0.88)
