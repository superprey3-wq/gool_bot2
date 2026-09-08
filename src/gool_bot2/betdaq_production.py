from __future__ import annotations

import re
from typing import Any

from . import betdaq_exchange as exchange


def runner_line(label: str) -> tuple[str | None, float | None]:
    """Parse the exact BETDAQ goal-total runner labels seen on the live AAPI.

    BETDAQ currently sends labels such as ``Over (0.5)`` and ``Under (2.5)``.
    Keep support for the older/plain ``Over 0.5`` representation as well, but
    require the whole label to match so team/corner props cannot be misread.
    """
    text = str(label or "").strip()
    match = re.match(
        r"^(Over|Under)\s*(?:\(\s*)?([0-9]+(?:\.[0-9]+)?)(?:\s*\))?\s*$",
        text,
        re.I,
    )
    if not match:
        return None, None
    try:
        return match.group(1).lower(), float(match.group(2))
    except (TypeError, ValueError):
        return None, None


def install_live_decoder() -> None:
    """Install the production decoder used by BetdaqExchangeCollector globals."""
    exchange._runner_line = runner_line


class ProductionBetdaqExchangeCollector(exchange.BetdaqExchangeCollector):
    """BETDAQ collector with strict health checks for GOOL goal-flow markets."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        install_live_decoder()
        super().__init__(*args, **kwargs)

    def _build_state(self, event_rows: list[dict[str, Any]]) -> dict[str, Any]:
        state = super()._build_state(event_rows)
        match_odds = [row for row in self._markets.values() if row.get("kind") == "match_odds"]
        totals = [row for row in self._markets.values() if row.get("kind") == "total"]
        state.update(
            {
                "tracked_match_odds_markets": len(match_odds),
                "tracked_total_markets": len(totals),
                "events_with_match_odds": len({int(row["event_id"]) for row in match_odds}),
                "events_with_totals": len({int(row["event_id"]) for row in totals}),
            }
        )
        return state

    async def _bootstrap(self, ws: Any) -> list[dict[str, Any]]:
        events = await super()._bootstrap(ws)
        match_odds = [row for row in self._markets.values() if row.get("kind") == "match_odds"]
        totals = [row for row in self._markets.values() if row.get("kind") == "total"]
        if not totals:
            # A Match Odds-only bootstrap looks superficially healthy but can never
            # feed GOOL BETDAQ MONEY FLOW. Fail loudly so the supervisor reconnects.
            raise RuntimeError("betdaq_no_goal_total_markets")
        total_events = len({int(row["event_id"]) for row in totals})
        odds_events = len({int(row["event_id"]) for row in match_odds})
        print(
            "BETDAQ_EXCHANGE production_ready "
            f"events={len(events)} match_odds={len(match_odds)} totals={len(totals)} "
            f"events_with_match_odds={odds_events} events_with_totals={total_events}",
            flush=True,
        )
        return events


# Install on import as well: helpers such as _goal_total_market and _decode_market
# resolve _runner_line dynamically from betdaq_exchange's module globals.
install_live_decoder()


__all__ = ["ProductionBetdaqExchangeCollector", "install_live_decoder", "runner_line"]
