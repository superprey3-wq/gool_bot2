from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any
from urllib.request import Request, urlopen

from .providers.common import UA


def _json_any(url: str, *, headers: dict[str, str] | None = None, timeout: int = 12) -> Any:
    req = Request(url, headers=headers or {"User-Agent": UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def american_to_decimal(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price == 0:
        return None
    return round(1.0 + (price / 100.0 if price > 0 else 100.0 / abs(price)), 6)


class PinnacleReference:
    root = "https://guest.api.arcadia.pinnacle.com/0.1"

    def __init__(self) -> None:
        self.headers = {"User-Agent": UA, "Accept": "application/json", "Origin": "https://www.pinnacle.com", "Referer": "https://www.pinnacle.com/"}
        key = os.getenv("PINNACLE_PUBLIC_WEB_KEY", "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R").strip()
        if key: self.headers["X-API-Key"] = key

    def sports(self) -> list[dict[str, Any]]:
        data = _json_any(f"{self.root}/sports", headers=self.headers)
        return data if isinstance(data, list) else []

    def matchups(self, sport_id: int, *, live: bool = False) -> list[dict[str, Any]]:
        suffix = "/live" if live else ""
        data = _json_any(f"{self.root}/sports/{int(sport_id)}/matchups{suffix}", headers=self.headers)
        return data if isinstance(data, list) else []

    def markets(self, matchup_id: int) -> list[dict[str, Any]]:
        data = _json_any(f"{self.root}/matchups/{int(matchup_id)}/markets/related/straight", headers=self.headers)
        return data if isinstance(data, list) else []

    @staticmethod
    def decimal_prices(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for market in markets:
            row = dict(market); prices = []
            for raw in market.get("prices") or []:
                p = dict(raw); p["decimal"] = american_to_decimal(raw.get("price")); prices.append(p)
            row["prices"] = prices; out.append(row)
        return out


class NHLReference:
    root = "https://api-web.nhle.com/v1"

    def scores(self, date: str = "now") -> dict[str, Any]:
        data = _json_any(f"{self.root}/score/{urllib.parse.quote(str(date))}")
        return data if isinstance(data, dict) else {}

    def standings(self, date: str = "now") -> dict[str, Any]:
        data = _json_any(f"{self.root}/standings/{urllib.parse.quote(str(date))}")
        return data if isinstance(data, dict) else {}

    def game_landing(self, game_id: int) -> dict[str, Any]:
        data = _json_any(f"{self.root}/gamecenter/{int(game_id)}/landing")
        return data if isinstance(data, dict) else {}


class ESPNReference:
    root = "https://site.api.espn.com/apis/site/v2/sports"

    def scoreboard(self, sport: str, league: str, *, dates: str | None = None) -> dict[str, Any]:
        url = f"{self.root}/{urllib.parse.quote(sport)}/{urllib.parse.quote(league)}/scoreboard"
        if dates: url += "?" + urllib.parse.urlencode({"dates": dates})
        data = _json_any(url)
        return data if isinstance(data, dict) else {}

    def summary(self, sport: str, league: str, event_id: str) -> dict[str, Any]:
        url = f"{self.root}/{urllib.parse.quote(sport)}/{urllib.parse.quote(league)}/summary?" + urllib.parse.urlencode({"event": event_id})
        data = _json_any(url)
        return data if isinstance(data, dict) else {}


class BetfairReference:
    """Optional AU exchange feed. It must never be a hard dependency outside AU."""

    key = os.getenv("BETFAIR_PUBLIC_WEB_KEY", "nzIFcwyWhrlwYMrh")
    scan = "https://scan-inbf.betfair.com.au"
    ero = "https://ero.betfair.com.au"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"User-Agent": UA, "Accept": "application/json", "Origin": "https://www.betfair.com.au", "Referer": "https://www.betfair.com.au/"}

    def navigation(self, event_type: int, *, max_results: int = 200) -> dict[str, Any]:
        params = {"nodeIds": f"EVENT_TYPE:{int(event_type)}", "attachments": "MENU,EVENT,MARKET", "maxInDistance": 0, "maxOutDistance": 4, "maxResults": int(max_results), "currencyCode": "AUD", "locale": "en", "_ak": self.key, "alt": "json"}
        data = _json_any(self.scan + "/www/sports/navigation/v2/graph/bynode?" + urllib.parse.urlencode(params), headers=self._headers())
        return data if isinstance(data, dict) else {}

    def market_prices(self, market_ids: list[str]) -> dict[str, Any]:
        if not market_ids: return {}
        params = {"marketIds": ",".join(market_ids), "types": "MARKET_STATE,MARKET_RATES,MARKET_DESCRIPTION,EVENT,RUNNER_DESCRIPTION,RUNNER_STATE,RUNNER_EXCHANGE_PRICES_BEST", "currencyCode": "AUD", "locale": "en", "rollupLimit": 25, "rollupModel": "STAKE", "_ak": self.key, "alt": "json"}
        data = _json_any(self.ero + "/www/sports/exchange/readonly/v1/bymarket?" + urllib.parse.urlencode(params), headers=self._headers())
        return data if isinstance(data, dict) else {}
