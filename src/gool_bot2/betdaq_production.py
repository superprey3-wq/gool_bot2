from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from . import betdaq_exchange as exchange


_FLOW_ENV = {
    "MATCHBOOK_FLOW_WOM_MIN": ("BETDAQ_FLOW_WOM_MIN", "0.54"),
    "MATCHBOOK_FLOW_SPOOF_SPIKE_MULTIPLIER": ("BETDAQ_FLOW_SPOOF_SPIKE_MULTIPLIER", "2.5"),
    "MATCHBOOK_FLOW_SPOOF_SPIKE_ABS_GBP": ("BETDAQ_FLOW_SPOOF_SPIKE_ABS_GBP", "250"),
    "MATCHBOOK_FLOW_SPOOF_MATCHED_RATIO": ("BETDAQ_FLOW_SPOOF_MATCHED_RATIO", "0.25"),
    "MATCHBOOK_FLOW_OFI_MIN": ("BETDAQ_FLOW_OFI_MIN", "0.10"),
    "MATCHBOOK_FLOW_PERSISTENCE_SNAPSHOTS": ("BETDAQ_FLOW_PERSISTENCE_SNAPSHOTS", "2"),
    "MATCHBOOK_FLOW_ORDERBOOK_MIN_CONFIRMATIONS": ("BETDAQ_FLOW_ORDERBOOK_MIN_CONFIRMATIONS", "2"),
    "MATCHBOOK_FLOW_MIN_VOLUME_DELTA": ("BETDAQ_FLOW_MIN_VOLUME_DELTA", "20"),
    "MATCHBOOK_FLOW_STRONG_VOLUME_DELTA": ("BETDAQ_FLOW_STRONG_VOLUME_DELTA", "75"),
}


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


def event_hierarchy_fields(correlation_id: int = 301) -> dict[int, Any]:
    """Fields for the persistent anonymous football hierarchy subscription.

    The successful live AAPI probe uses fetch_only=False. Production previously
    used fetch_only=True, which can leave the collector with an incomplete one-shot
    hierarchy and therefore zero events when the daily board is being populated.
    """
    return {
        0: int(correlation_id),
        2: exchange.SOCCER_ID,
        3: False,
        4: False,
        5: False,  # fetch_only=False: keep hierarchy subscription alive
        11: True,  # event discovery only; market metadata is requested per event
        12: False,
        13: False,
        14: False,
    }


class BetdaqFlowHelper(exchange.MatchbookExchangeCollector):
    """Reuse the proven flow maths while keeping BETDAQ tuning independent.

    ``MatchbookExchangeCollector._flow`` is a pure history/order-book calculation,
    but its knobs historically use MATCHBOOK_* names. The BETDAQ worker runs in a
    separate process, so alias the knobs only for the duration of one calculation
    and restore the process environment immediately afterwards.
    """

    def _flow(self, event_id: str, key: str, market: dict[str, Any], now: float) -> dict[str, Any]:
        saved = {name: os.environ.get(name) for name in _FLOW_ENV}
        try:
            for matchbook_name, (betdaq_name, default) in _FLOW_ENV.items():
                os.environ[matchbook_name] = os.getenv(betdaq_name, default)
            return super()._flow(event_id, key, market, now)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class ProductionBetdaqExchangeCollector(exchange.BetdaqExchangeCollector):
    """BETDAQ collector with strict health checks for GOOL goal-flow markets."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        install_live_decoder()
        super().__init__(*args, **kwargs)
        self._flow_helper = BetdaqFlowHelper(self.state_path)

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

    async def _discover_events(self, ws: Any) -> list[dict[str, Any]]:
        """Discover today's football board without failing on a partial first burst."""
        await self._send(ws, 12, event_hierarchy_fields())
        events: list[dict[str, Any]] = []
        received = 0
        waits = (6.0, 6.0, 8.0)
        for attempt, wait in enumerate(waits, 1):
            received += await self._recv_for(ws, wait)
            events = self._events_from_topics()
            if events:
                print(
                    f"BETDAQ_EXCHANGE discovery_ready attempt={attempt} "
                    f"events={len(events)} messages={received}",
                    flush=True,
                )
                return events
            print(
                f"BETDAQ_EXCHANGE discovery_wait attempt={attempt} "
                f"messages={received} topics={len(self._topics)}",
                flush=True,
            )

        labels = sum(1 for topic in self._topics if topic.endswith("/EL/en"))
        starts = sum(1 for topic in self._topics if topic.endswith("/EEI"))
        raise RuntimeError(
            f"betdaq_no_today_football_events labels={labels} starts={starts} "
            f"messages={received} topics={len(self._topics)}"
        )

    async def _bootstrap(self, ws: Any) -> list[dict[str, Any]]:
        # Do not call the base bootstrap here: its legacy hierarchy request used
        # fetch_only=True. Keep the proven market/catalog/price subscription path,
        # but start it from a persistent football hierarchy subscription.
        self._topics.clear()
        self._markets.clear()
        self._tracked_events.clear()

        events = await self._discover_events(ws)
        self._tracked_events = {int(row["event_id"]) for row in events}

        corr = 1000
        for row in events:
            corr += 1
            await self._send(
                ws,
                9,
                {
                    0: corr,
                    2: int(row["event_id"]),
                    4: "3~13",
                    5: False,
                    7: True,
                    8: True,
                    9: True,
                    11: False,
                    12: False,
                },
            )
            await asyncio.sleep(0.01)

        # Large daily boards can arrive in several bursts. Give metadata one extra
        # short window before declaring the source broken.
        await self._recv_for(ws, 12.0)
        self._markets = self._catalog()
        if not self._markets:
            await self._recv_for(ws, 6.0)
            self._markets = self._catalog()
        if not self._markets:
            raise RuntimeError("betdaq_no_relevant_markets")

        market_ids = sorted(self._markets)
        for offset in range(0, len(market_ids), 120):
            chunk = market_ids[offset : offset + 120]
            joined = "~".join(str(mid) for mid in chunk)
            corr += 1
            await self._send(ws, 10, {0: corr, 5: joined, 6: 3, 7: 3, 8: 0, 11: False})
            corr += 1
            await self._send(ws, 14, {0: corr, 5: joined, 7: False})
            await asyncio.sleep(0.03)
        await self._recv_for(ws, 8.0)

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


__all__ = [
    "BetdaqFlowHelper",
    "ProductionBetdaqExchangeCollector",
    "event_hierarchy_fields",
    "install_live_decoder",
    "runner_line",
]
