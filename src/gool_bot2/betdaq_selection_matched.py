from __future__ import annotations

import asyncio
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import betdaq_exchange as exchange
from .betdaq_production import (
    GOOL_MARKET_TYPES,
    ProductionBetdaqExchangeCollector,
    market_information_fields,
)


def selection_matched_fields(market_ids: list[int] | tuple[int, ...], correlation_id: int) -> dict[int, Any]:
    """SubscribeSelectionMatchedAmounts (AAPI command 13)."""
    ids = [int(value) for value in market_ids]
    return {
        0: int(correlation_id),
        1: "~".join(str(value) for value in ids),
        2: False,  # includeSelectionMatchDetail
        4: False,  # fetchOnly: keep the subscription live
    }


def _stream_limit() -> int:
    """Leave headroom below BETDAQ's commonly exposed 500-market price quota."""
    try:
        value = int(float(os.getenv("BETDAQ_STREAM_MARKET_LIMIT", "450")))
    except (TypeError, ValueError):
        value = 450
    return max(25, min(480, value))


def _start_utc(row: dict[str, Any]) -> datetime | None:
    value = row.get("start")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return exchange._parse_dt(value)


def _is_live_event(row: dict[str, Any], now: datetime) -> bool:
    start = _start_utc(row)
    return bool(start is not None and start <= now <= start + timedelta(hours=4))


def _total_sort_key(meta: dict[str, Any]) -> tuple[int, float, float, int]:
    period = str(meta.get("period") or "FT").upper()
    try:
        line = float(meta.get("line") or 0.0)
    except (TypeError, ValueError):
        line = 0.0
    # Prefer the normal FT central line first. Extra LIVE totals are added later.
    return (0 if period == "FT" else 1, abs(line - 2.5), line, int(meta.get("id") or 0))


def prioritize_stream_market_ids(
    event_rows: list[dict[str, Any]],
    markets: dict[int, dict[str, Any]],
    limit: int,
    *,
    now: datetime | None = None,
) -> list[int]:
    """Choose the markets that deserve expensive AAPI live subscriptions.

    Market metadata can cover a very large football board, but Detailed Prices and
    Market Matched Amounts are quota-controlled subscriptions. We therefore keep
    complete metadata discovery while streaming a bounded high-value subset:

    1. LIVE Match Odds;
    2. one central LIVE total per event;
    3. the remaining LIVE totals (so the live main line can move with the score);
    4. upcoming Match Odds;
    5. one central upcoming total per event;
    6. any remaining totals while capacity remains.
    """
    cap = max(1, int(limit))
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    by_event: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for meta in markets.values():
        try:
            by_event[int(meta["event_id"])].append(meta)
        except (KeyError, TypeError, ValueError):
            continue

    known: dict[int, dict[str, Any]] = {}
    for row in event_rows:
        try:
            known[int(row["event_id"])] = row
        except (KeyError, TypeError, ValueError):
            continue

    def event_key(row: dict[str, Any]) -> tuple[int, float, int]:
        live = _is_live_event(row, moment)
        start = _start_utc(row)
        stamp = start.timestamp() if start is not None else float("inf")
        try:
            event_id = int(row.get("event_id") or 0)
        except (TypeError, ValueError):
            event_id = 0
        return (0 if live else 1, stamp, event_id)

    ordered_events = sorted(known.values(), key=event_key)
    live_events = [row for row in ordered_events if _is_live_event(row, moment)]
    future_events = [row for row in ordered_events if not _is_live_event(row, moment)]

    selected: list[int] = []
    seen: set[int] = set()

    def add(meta: dict[str, Any] | None) -> bool:
        if meta is None or len(selected) >= cap:
            return False
        try:
            mid = int(meta.get("id") or 0)
        except (TypeError, ValueError):
            return False
        if mid <= 0 or mid in seen:
            return False
        seen.add(mid)
        selected.append(mid)
        return True

    def match_odds(row: dict[str, Any]) -> dict[str, Any] | None:
        try:
            event_id = int(row["event_id"])
        except (KeyError, TypeError, ValueError):
            return None
        candidates = [meta for meta in by_event.get(event_id, []) if meta.get("kind") == "match_odds"]
        return min(candidates, key=lambda meta: int(meta.get("id") or 0)) if candidates else None

    def totals(row: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            event_id = int(row["event_id"])
        except (KeyError, TypeError, ValueError):
            return []
        return sorted(
            [meta for meta in by_event.get(event_id, []) if meta.get("kind") == "total"],
            key=_total_sort_key,
        )

    # LIVE gets full priority because the Money Board and sharp-flow alerts are
    # most time-sensitive there.
    for row in live_events:
        add(match_odds(row))
    for row in live_events:
        rows = totals(row)
        add(rows[0] if rows else None)
    for row in live_events:
        for meta in totals(row)[1:]:
            if len(selected) >= cap:
                return selected
            add(meta)

    # Preserve PREMATCH P1/X/P2 progруз detection for as many upcoming events as
    # possible, then give every event one representative total before filling
    # spare capacity with secondary total lines.
    for row in future_events:
        if len(selected) >= cap:
            return selected
        add(match_odds(row))
    for row in future_events:
        if len(selected) >= cap:
            return selected
        rows = totals(row)
        add(rows[0] if rows else None)
    for row in future_events:
        for meta in totals(row)[1:]:
            if len(selected) >= cap:
                return selected
            add(meta)
    return selected


def _price_rows_nonzero(attrs: dict[str, str], runner_index: int, side_group: int) -> list[dict[str, float]]:
    """Decode only executable BETDAQ ladder levels.

    AAPI sends amount=0 when a previously published price level has been cleared.
    Retaining it can make an old price look like the current best Back/Lay.
    """
    rows: list[dict[str, float]] = []
    prefix = f"1V{runner_index}-{side_group}V"
    levels: set[int] = set()
    for key in attrs:
        match = re.match(re.escape(prefix) + r"(\d+)-1$", key)
        if match:
            levels.add(int(match.group(1)))
    for level in levels:
        odd = exchange._number(attrs.get(f"{prefix}{level}-1"))
        amount = exchange._number(attrs.get(f"{prefix}{level}-2"))
        if odd is None or odd <= 1.0 or amount is None or amount <= 0.0:
            continue
        rows.append({"odds": float(odd), "odd": float(odd), "available": float(amount)})
    return rows


def _selection_matched_id(topic: str) -> int | None:
    match = re.search(r"/MMA/GBP/SMA/(?:E_)?(\d+)(?:/|$)", str(topic or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _runner_role(label: str, home: str, away: str) -> str | None:
    low = _clean(label)
    if low == _clean(home):
        return "P1"
    if low in {"draw", "tie", "the draw"}:
        return "X"
    if low == _clean(away):
        return "P2"
    return None


def _depth(runner: dict[str, Any], side: str) -> float:
    total = 0.0
    for row in runner.get("prices") or []:
        if not isinstance(row, dict) or str(row.get("side") or "") != side:
            continue
        amount = exchange._number(row.get("available"))
        if amount is not None and amount > 0.0:
            total += float(amount)
    return round(total, 2)


class SelectionMatchedBetdaqCollector(ProductionBetdaqExchangeCollector):
    """Production BETDAQ collector with real matched money per selection."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # _decode_price_ladder resolves this global at call time.
        exchange._price_rows = _price_rows_nonzero
        self._stream_market_ids: set[int] = set()
        self._subscription_last: dict[int, dict[str, Any]] = {}

    def _apply_message(self, message: str) -> None:
        """Capture subscription return codes before the generic topic cache merges them."""
        topic, attrs = exchange._tagged(message)
        if str(topic).endswith("/D"):
            for message_id in (9, 10, 13, 14):
                if str(message_id) not in attrs:
                    continue
                raw_code = str(attrs.get("1") or "").strip()
                if raw_code.isdigit():
                    return_code = f"RC{int(raw_code):03d}"
                else:
                    return_code = raw_code or "UNKNOWN"
                available = exchange._number(attrs.get("4"))
                row = {
                    "message_id": message_id,
                    "correlation_id": attrs.get("0"),
                    "return_code": return_code,
                    "available_markets": int(available) if available is not None else None,
                }
                self._subscription_last[message_id] = row
                if return_code != "RC000":
                    print(
                        f"BETDAQ_SUBSCRIPTION command={message_id} return={return_code} "
                        f"available={row['available_markets']}",
                        flush=True,
                    )
        super()._apply_message(message)

    def _catalog(self) -> dict[int, dict[str, Any]]:
        catalog = super()._catalog()

        # Prefer canonical SelectionLanguage names over exchange-language deltas.
        canonical: dict[int, dict[int, str]] = defaultdict(dict)
        fallback: dict[int, dict[int, str]] = defaultdict(dict)
        for topic, attrs in self._topics.items():
            mid = exchange._market_id(topic)
            sid = exchange._selection_id(topic)
            if mid is None or sid is None or not attrs.get("1"):
                continue
            label = str(attrs["1"]).strip()
            if topic.endswith("/SL/en"):
                canonical[mid][sid] = label
            elif "/SEI/SEL/en" in topic:
                fallback[mid][sid] = label

        for mid, meta in catalog.items():
            existing = dict(meta.get("selections") or {})
            ids = set(existing) | set(fallback.get(mid, {})) | set(canonical.get(mid, {}))
            meta["selections"] = {
                sid: canonical.get(mid, {}).get(sid)
                or fallback.get(mid, {}).get(sid)
                or existing.get(sid)
                or str(sid)
                for sid in ids
            }
        return catalog

    async def _bootstrap(self, ws: Any) -> list[dict[str, Any]]:
        """Discover the full board but stream only a quota-safe market subset.

        The previous implementation called the production bootstrap (which
        subscribed every discovered market to commands 10 and 14) and then added
        command 13 for every market. A 1,200+ market football board can exceed
        BETDAQ's concurrent Detailed Prices / Market Matched quotas, leaving the
        Money Board with metadata but no runner ladders. This bootstrap owns all
        three expensive subscriptions and keeps them on the same bounded set.
        """
        self._topics.clear()
        self._markets.clear()
        self._tracked_events.clear()
        self._stream_market_ids.clear()
        self._subscription_last.clear()

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

        limit = _stream_limit()
        stream_ids = prioritize_stream_market_ids(events, self._markets, limit)
        self._stream_market_ids = set(stream_ids)
        for mid, meta in self._markets.items():
            meta["streamed"] = int(mid) in self._stream_market_ids

        stream_match_odds = sum(
            1 for mid in stream_ids if (self._markets.get(int(mid)) or {}).get("kind") == "match_odds"
        )
        stream_totals = sum(
            1 for mid in stream_ids if (self._markets.get(int(mid)) or {}).get("kind") == "total"
        )
        print(
            f"BETDAQ_STREAM plan catalog={len(self._markets)} selected={len(stream_ids)} "
            f"match_odds={stream_match_odds} totals={stream_totals} limit={limit}",
            flush=True,
        )

        # Keep batches modest. The aggregate count, not the batch size, is the
        # quota-sensitive part. Use a spec-compliant MoneyValue for filterByVolume.
        for offset in range(0, len(stream_ids), 75):
            chunk = stream_ids[offset : offset + 75]
            if not chunk:
                continue
            joined = "~".join(str(mid) for mid in chunk)
            corr += 1
            await self._send(ws, 10, {0: corr, 5: joined, 6: 3, 7: 3, 8: "0.00", 11: False})
            corr += 1
            await self._send(ws, 14, {0: corr, 5: joined, 7: False})
            corr += 1
            await self._send(ws, 13, selection_matched_fields(chunk, corr))
            await asyncio.sleep(0.04)

        await self._recv_for(ws, 10.0)
        summary = " ".join(
            f"cmd{mid}={self._subscription_last.get(mid, {}).get('return_code', 'NO_RESPONSE')}"
            + (
                f"/avail{self._subscription_last[mid]['available_markets']}"
                if mid in self._subscription_last and self._subscription_last[mid].get("available_markets") is not None
                else ""
            )
            for mid in (10, 14, 13)
        )
        print(f"BETDAQ_STREAM subscribed={len(stream_ids)} {summary}", flush=True)
        return events

    def _market_topic_attrs(self, market_id: int, suffix: str) -> dict[str, str]:
        # Command 13 creates child topics under /MMA/GBP/SMA/<selectionId>.
        # Market-level matched must keep reading only the parent /MMA/GBP topic.
        if suffix == "/MMA/GBP":
            marker = f"/M/E_{int(market_id)}/"
            candidates = [
                attrs
                for topic, attrs in self._topics.items()
                if marker in topic and topic.endswith("/MMA/GBP")
            ]
            return max(candidates, key=len) if candidates else {}
        return super()._market_topic_attrs(market_id, suffix)

    def _selection_matched(self, market_id: int) -> dict[int, dict[str, float]]:
        marker = f"/M/E_{int(market_id)}/"
        result: dict[int, dict[str, float]] = {}
        for topic, attrs in self._topics.items():
            if marker not in topic or "/MMA/GBP/SMA/" not in topic or "/SMD/" in topic:
                continue
            sid = _selection_matched_id(topic)
            if sid is None:
                continue
            for_amount = exchange._number(attrs.get("1"))
            against_amount = exchange._number(attrs.get("2"))
            result[sid] = {
                "for": max(0.0, float(for_amount or 0.0)),
                "against": max(0.0, float(against_amount or 0.0)),
            }
        return result

    def _decode_market(self, meta: dict[str, Any]) -> dict[str, Any]:
        market = super()._decode_market(meta)
        market["streamed"] = bool(meta.get("streamed"))
        matched = self._selection_matched(int(meta["id"]))
        for runner in market.get("runners") or []:
            try:
                sid = int(str(runner.get("id") or "0"))
            except (TypeError, ValueError):
                sid = 0
            amounts = matched.get(sid)
            runner["selection_matched_ready"] = amounts is not None
            runner["matched_for_gbp"] = round(float((amounts or {}).get("for") or 0.0), 2)
            runner["matched_against_gbp"] = round(float((amounts or {}).get("against") or 0.0), 2)
            runner["back_depth_gbp"] = _depth(runner, "back")
            runner["lay_depth_gbp"] = _depth(runner, "lay")
        market["selection_matched_ready"] = bool(matched)
        return market

    def _build_state(self, event_rows: list[dict[str, Any]]) -> dict[str, Any]:
        state = super()._build_state(event_rows)
        valid_count = 0
        no_price_count = 0
        ambiguous_count = 0
        for event in state.get("events") or []:
            if not isinstance(event, dict):
                continue
            match_odds = event.get("match_odds") or {}
            runners = [row for row in (match_odds.get("runners") or []) if isinstance(row, dict)]
            roles: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for runner in runners:
                role = _runner_role(
                    str(runner.get("label") or runner.get("name") or ""),
                    str(event.get("home") or ""),
                    str(event.get("away") or ""),
                )
                if role:
                    runner["outcome"] = role
                    roles[role].append(runner)

            labels_valid = len(runners) == 3 and all(len(roles[key]) == 1 for key in ("P1", "X", "P2"))
            price_stream_ready = bool(runners)
            match_odds["price_stream_ready"] = price_stream_ready
            match_odds["labels_valid"] = labels_valid
            match_odds["observed_labels"] = [str(row.get("label") or row.get("name") or "") for row in runners]
            event["match_odds"] = match_odds
            event["runners"] = runners
            if labels_valid:
                valid_count += 1
            else:
                if not price_stream_ready:
                    no_price_count += 1
                    reason = "betdaq_match_odds_price_stream_unavailable"
                    level = "NO_PRICE_STREAM"
                else:
                    ambiguous_count += 1
                    reason = "betdaq_match_odds_labels_not_unique"
                    level = "INVALID_RUNNERS"
                invalid_flow = {"ready": False, "level": level, "reason": reason}
                match_odds["flow"] = invalid_flow
                event["flow"] = dict(invalid_flow)

        state["valid_match_odds_labels"] = valid_count
        state["match_odds_no_price_stream"] = no_price_count
        state["match_odds_ambiguous_labels"] = ambiguous_count
        state["stream_market_limit"] = _stream_limit()
        state["streamed_markets"] = len(self._stream_market_ids)
        state["streamed_match_odds_markets"] = sum(
            1
            for mid in self._stream_market_ids
            if (self._markets.get(int(mid)) or {}).get("kind") == "match_odds"
        )
        state["streamed_total_markets"] = sum(
            1
            for mid in self._stream_market_ids
            if (self._markets.get(int(mid)) or {}).get("kind") == "total"
        )
        state["betdaq_subscription_status"] = {
            str(mid): dict(row) for mid, row in self._subscription_last.items() if mid in {9, 10, 13, 14}
        }

        # Selection pushes are driven directly by the BETDAQ collector so they can
        # fire both PREMATCH and LIVE; the football-record signal loop is not needed.
        try:
            from .betdaq_selection_alerts import process_selection_alerts

            alerts = process_selection_alerts(state)
            state["selection_pushes"] = alerts
            if alerts:
                print(f"BETDAQ_SELECTION_PUSH emitted={len(alerts)}", flush=True)
        except Exception as exc:
            state["selection_pushes"] = []
            state["selection_push_error"] = f"{type(exc).__name__}:{exc}"
            print(f"BETDAQ_SELECTION_PUSH error={type(exc).__name__}:{exc}", flush=True)
        return state


__all__ = [
    "SelectionMatchedBetdaqCollector",
    "prioritize_stream_market_ids",
    "selection_matched_fields",
]
