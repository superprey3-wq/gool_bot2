from __future__ import annotations

import asyncio
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import betdaq_exchange as exchange


# BETDAQ MarketType values from the External API specification.
# 13 (Unspecified) is kept as a legacy compatibility bucket because older/live
# AAPI payloads have exposed some soccer totals under that value.
MATCH_ODDS_MARKET_TYPES = frozenset({3})
GOAL_TOTAL_MARKET_TYPES = frozenset({4, 13, 17, 27, 40, 46})
GOOL_MARKET_TYPES = tuple(sorted(MATCH_ODDS_MARKET_TYPES | GOAL_TOTAL_MARKET_TYPES))


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
    """Parse the exact BETDAQ goal-total runner labels seen on the live AAPI."""
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


def event_hierarchy_fields(correlation_id: int = 301, *, fetch_only: bool = False) -> dict[int, Any]:
    """Build an anonymous football hierarchy request.

    Production keeps one persistent subscription alive. Discovery can also send a
    fetch-only replay on the same socket if the first burst is incomplete. BETDAQ
    sometimes populates the daily hierarchy in several phases, so relying on only
    one of those modes can produce a false empty board while the stream is healthy.
    """
    return {
        0: int(correlation_id),
        2: exchange.SOCCER_ID,
        3: False,
        4: False,
        5: bool(fetch_only),
        11: True,
        12: False,
        13: False,
        14: False,
    }


def market_information_fields(event_id: int, correlation_id: int) -> dict[int, Any]:
    """Build the AAPI SubscribeMarketInformation(9) request used by GOOL.

    Field 4 is marketTypesToInclude. Historically GOOL asked only for 3~13,
    incorrectly treating MarketType 13 as the totals family. Current BETDAQ market
    types use 4/17/27/40/46 for the relevant Over/Under/Total families. We keep 13
    only as a backwards-compatible bucket and validate totals by their runners.

    Field 7 is the deprecated fetchOnly flag for command 9 and the AAPI spec says
    it must always be false. The previous collector sent true here.
    """
    return {
        0: int(correlation_id),
        2: int(event_id),
        4: "~".join(str(value) for value in GOOL_MARKET_TYPES),
        5: False,
        7: False,
        8: True,
        9: True,
        11: False,
        12: False,
    }


def _hours(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


class BetdaqFlowHelper(exchange.MatchbookExchangeCollector):
    """Reuse the proven flow maths while keeping BETDAQ tuning independent."""

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

    def _events_from_topics(self) -> list[dict[str, Any]]:
        """Return football events in a rolling LIVE/upcoming window.

        The old base collector required the event's local calendar date to equal
        today's date. That is unnecessarily brittle around midnight, timezone
        conversion, delayed daily-board rollover and games already in progress.
        GOOL needs LIVE games plus enough upcoming events to keep subscriptions
        warm, so production uses an explicit UTC lookback/lookahead horizon.
        """
        labels: dict[int, str] = {}
        starts: dict[int, datetime] = {}
        for topic, attrs in self._topics.items():
            event = exchange._event_id(topic)
            if event is None:
                continue
            # Initial hierarchy contains no market/selection topics because field
            # 11 excludes market information. Accept the language label family
            # instead of hard-coding only one exact suffix.
            if "/EL/" in topic and attrs.get("1"):
                labels[event] = str(attrs["1"]).strip()
            if topic.endswith("/EEI") and attrs.get("3"):
                dt = exchange._parse_dt(attrs.get("3"))
                if dt is not None:
                    starts[event] = dt.astimezone(timezone.utc)

        now = datetime.now(timezone.utc)
        lower = now - timedelta(hours=_hours("BETDAQ_DISCOVERY_LOOKBACK_HOURS", 6.0))
        upper = now + timedelta(hours=_hours("BETDAQ_DISCOVERY_LOOKAHEAD_HOURS", 36.0))
        rows: list[dict[str, Any]] = []
        for event, start in starts.items():
            if start < lower or start > upper:
                continue
            name = labels.get(event, "").strip()
            if not name:
                continue
            home, away = exchange._split_teams(name)
            if not home or not away:
                continue
            rows.append({"event_id": event, "name": name, "home": home, "away": away, "start": start})
        rows.sort(key=lambda row: row["start"])
        return rows

    def _catalog(self) -> dict[int, dict[str, Any]]:
        """Build a resilient GOOL catalog from BETDAQ market metadata.

        MarketType is authoritative for Match Odds, so do not require the English
        display name to equal exactly 'Match Odds'. For totals, validate the actual
        Over/Under runner pair and line; this safely permits legacy type 13 while
        supporting the current total-related MarketType values.
        """
        names: dict[int, str] = {}
        types: dict[int, int] = {}
        events: dict[int, int] = {}
        selections: dict[int, dict[int, str]] = defaultdict(dict)

        for topic, attrs in self._topics.items():
            mid = exchange._market_id(topic)
            if mid is None:
                continue
            event = exchange._event_id(topic)
            if event is not None:
                events[mid] = event

            if (topic.endswith("/ML/en") or "/MEI/MEL/en" in topic) and attrs.get("1"):
                names[mid] = str(attrs["1"]).strip()
            elif topic.endswith("/MEI"):
                try:
                    types[mid] = int(attrs.get("2", "0"))
                except (TypeError, ValueError):
                    pass

            if (topic.endswith("/SL/en") or "/SEI/SEL/en" in topic) and attrs.get("1"):
                sid = exchange._selection_id(topic)
                if sid is not None:
                    selections[mid][sid] = str(attrs["1"]).strip()

        out: dict[int, dict[str, Any]] = {}
        for mid, event in events.items():
            if event not in self._tracked_events:
                continue

            name = names.get(mid, "")
            mtype = types.get(mid)
            runner_labels = selections.get(mid, {})

            if mtype in MATCH_ODDS_MARKET_TYPES:
                out[mid] = {
                    "event_id": event,
                    "id": mid,
                    "name": name or "Match Odds",
                    "type": mtype,
                    "kind": "match_odds",
                    "selections": dict(runner_labels),
                }
                continue

            # Only total-like market types (plus a partially delivered type) are
            # candidates. The actual runner labels are the final safety gate.
            if mtype is not None and mtype not in GOAL_TOTAL_MARKET_TYPES:
                continue
            total = exchange._goal_total_market(name, runner_labels)
            if total is None:
                continue
            period, line = total
            if mtype in {40, 46}:
                period = "1H"
            out[mid] = {
                "event_id": event,
                "id": mid,
                "name": name or (f"Half-time Total {line:g}" if period == "1H" else f"Total {line:g}"),
                "type": mtype,
                "kind": "total",
                "period": period,
                "line": float(line),
                "selections": dict(runner_labels),
            }
        return out

    def _observed_market_types(self) -> list[int]:
        values: set[int] = set()
        for topic, attrs in self._topics.items():
            if not topic.endswith("/MEI"):
                continue
            try:
                values.add(int(attrs.get("2", "0")))
            except (TypeError, ValueError):
                continue
        return sorted(values)

    def _discovery_counts(self) -> tuple[int, int]:
        labels: set[int] = set()
        starts: set[int] = set()
        for topic, attrs in self._topics.items():
            event = exchange._event_id(topic)
            if event is None:
                continue
            if "/EL/" in topic and attrs.get("1"):
                labels.add(event)
            if topic.endswith("/EEI") and attrs.get("3"):
                starts.add(event)
        return len(labels), len(starts)

    async def _discover_events(self, ws: Any) -> list[dict[str, Any]]:
        """Discover LIVE/upcoming football with persistent + snapshot recovery."""
        await self._send(ws, 12, event_hierarchy_fields(301, fetch_only=False))
        received = 0
        waits = (6.0, 6.0, 8.0)
        best: list[dict[str, Any]] = []
        for attempt, wait in enumerate(waits, 1):
            received += await self._recv_for(ws, wait)
            current = self._events_from_topics()
            if len(current) > len(best):
                best = current
            labels, starts = self._discovery_counts()
            print(
                f"BETDAQ_DISCOVERY attempt={attempt} labels={labels} starts={starts} "
                f"eligible={len(current)} messages={received} topics={len(self._topics)} "
                f"window=-{_hours('BETDAQ_DISCOVERY_LOOKBACK_HOURS', 6.0):g}h/+{_hours('BETDAQ_DISCOVERY_LOOKAHEAD_HOURS', 36.0):g}h",
                flush=True,
            )

            # After the first persistent burst, ask BETDAQ for a one-shot replay.
            # It merges into the same topic store and repairs a partially delivered
            # hierarchy without creating a second browser or a second worker.
            if attempt == 1:
                await self._send(ws, 12, event_hierarchy_fields(302, fetch_only=True))

        if best:
            print(
                f"BETDAQ_EXCHANGE discovery_ready events={len(best)} messages={received}",
                flush=True,
            )
            return best

        labels, starts = self._discovery_counts()
        raise RuntimeError(
            f"betdaq_no_football_events_in_window labels={labels} starts={starts} "
            f"messages={received} topics={len(self._topics)} "
            f"lookback_h={_hours('BETDAQ_DISCOVERY_LOOKBACK_HOURS', 6.0):g} "
            f"lookahead_h={_hours('BETDAQ_DISCOVERY_LOOKAHEAD_HOURS', 36.0):g}"
        )

    async def _bootstrap(self, ws: Any) -> list[dict[str, Any]]:
        self._topics.clear()
        self._markets.clear()
        self._tracked_events.clear()

        events = await self._discover_events(ws)
        self._tracked_events = {int(row["event_id"]) for row in events}

        corr = 1000
        for row in events:
            corr += 1
            await self._send(ws, 9, market_information_fields(int(row["event_id"]), corr))
            await asyncio.sleep(0.01)

        await self._recv_for(ws, 12.0)
        self._markets = self._catalog()
        if not self._markets:
            await self._recv_for(ws, 6.0)
            self._markets = self._catalog()
        if not self._markets:
            observed = ",".join(str(value) for value in self._observed_market_types()) or "none"
            raise RuntimeError(
                f"betdaq_no_relevant_markets observed_types={observed} "
                f"topics={len(self._topics)} requested_types={'~'.join(str(value) for value in GOOL_MARKET_TYPES)}"
            )

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
            raise RuntimeError(
                "betdaq_no_goal_total_markets "
                f"observed_types={','.join(str(value) for value in self._observed_market_types()) or 'none'}"
            )
        total_events = len({int(row["event_id"]) for row in totals})
        odds_events = len({int(row["event_id"]) for row in match_odds})
        print(
            "BETDAQ_EXCHANGE production_ready "
            f"events={len(events)} match_odds={len(match_odds)} totals={len(totals)} "
            f"events_with_match_odds={odds_events} events_with_totals={total_events}",
            flush=True,
        )
        return events


install_live_decoder()


__all__ = [
    "BetdaqFlowHelper",
    "GOAL_TOTAL_MARKET_TYPES",
    "GOOL_MARKET_TYPES",
    "MATCH_ODDS_MARKET_TYPES",
    "ProductionBetdaqExchangeCollector",
    "event_hierarchy_fields",
    "install_live_decoder",
    "market_information_fields",
    "runner_line",
]
