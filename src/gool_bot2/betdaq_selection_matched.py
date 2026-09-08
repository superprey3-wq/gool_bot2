from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any

from . import betdaq_exchange as exchange
from .betdaq_production import ProductionBetdaqExchangeCollector


def selection_matched_fields(market_ids: list[int] | tuple[int, ...], correlation_id: int) -> dict[int, Any]:
    """SubscribeSelectionMatchedAmounts (AAPI command 13)."""
    ids = [int(value) for value in market_ids]
    return {
        0: int(correlation_id),
        1: "~".join(str(value) for value in ids),
        2: False,  # includeSelectionMatchDetail
        4: False,  # fetchOnly: keep the subscription live
    }


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
        events = await super()._bootstrap(ws)
        market_ids = sorted(int(value) for value in self._markets)
        corr = 50000
        for offset in range(0, len(market_ids), 120):
            chunk = market_ids[offset : offset + 120]
            if not chunk:
                continue
            corr += 1
            await self._send(ws, 13, selection_matched_fields(chunk, corr))
            await asyncio.sleep(0.02)
        await self._recv_for(ws, 5.0)
        print(f"BETDAQ_SELECTION_MATCHED subscribed_markets={len(market_ids)}", flush=True)
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
            match_odds["labels_valid"] = labels_valid
            event["match_odds"] = match_odds
            event["runners"] = runners
            if labels_valid:
                valid_count += 1
            else:
                invalid_flow = {
                    "ready": False,
                    "level": "INVALID_RUNNERS",
                    "reason": "betdaq_match_odds_labels_not_unique",
                }
                match_odds["flow"] = invalid_flow
                event["flow"] = dict(invalid_flow)
        state["valid_match_odds_labels"] = valid_count
        return state


__all__ = ["SelectionMatchedBetdaqCollector", "selection_matched_fields"]
