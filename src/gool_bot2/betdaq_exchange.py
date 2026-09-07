from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import string
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .matchbook_exchange import MatchbookExchangeCollector
from .providers.common import pair_score

SOCCER_ID = 100003
RS = "\x01"
FS = "\x02"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _tz() -> timezone | ZoneInfo:
    try:
        return ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return timezone.utc


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _command(message_id: int, fields: dict[int, Any]) -> str:
    payload = FS + str(message_id) + RS
    for key, value in fields.items():
        if isinstance(value, bool):
            value = "T" if value else "F"
        payload += f"{key}{FS}{value}{FS}{RS}"
    return payload


def _tagged(message: str) -> tuple[str, dict[str, str]]:
    try:
        topic, tail = message.split(FS, 1)
    except ValueError:
        return message, {}
    attrs: dict[str, str] = {}
    for part in tail.split(RS):
        if FS not in part:
            continue
        key, value = part.split(FS, 1)
        if key:
            attrs[key] = value
    return topic, attrs


def _event_id(topic: str) -> int | None:
    values = re.findall(r"/E/E_(\d+)", topic)
    return int(values[-1]) if values else None


def _market_id(topic: str) -> int | None:
    match = re.search(r"/M/E_(\d+)", topic)
    return int(match.group(1)) if match else None


def _selection_id(topic: str) -> int | None:
    match = re.search(r"/S/E_(\d+)", topic)
    return int(match.group(1)) if match else None


def _split_teams(name: str) -> tuple[str, str]:
    text = str(name or "").strip()
    for token in (" v ", " vs ", " - "):
        if token in text:
            home, away = text.split(token, 1)
            return home.strip(), away.strip()
    return "", ""


def _mid_probability(runner: dict[str, Any]) -> float | None:
    values: list[float] = []
    for key in ("best_back", "best_lay"):
        row = runner.get(key) or {}
        odd = _number(row.get("odds") if isinstance(row, dict) else None)
        if odd is not None and odd > 1.0:
            values.append(1.0 / odd)
    if not values:
        return None
    return sum(values) / len(values)


def _fair_over(over: dict[str, Any], under: dict[str, Any]) -> float | None:
    po = _mid_probability(over)
    pu = _mid_probability(under)
    if po is None or pu is None or po + pu <= 0.0:
        return None
    return po / (po + pu)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _runner_line(label: str) -> tuple[str | None, float | None]:
    text = str(label or "").strip()
    match = re.match(r"^(Over|Under)\s*([0-9]+(?:\.[0-9]+)?)\b", text, re.I)
    if not match:
        return None, None
    try:
        return match.group(1).lower(), float(match.group(2))
    except Exception:
        return None, None


def _goal_total_market(name: str, selections: dict[int, str]) -> tuple[str, float] | None:
    low = str(name or "").casefold()
    blocked = (
        "corner",
        "team total",
        "match result",
        "both teams",
        "shot",
        "card",
        "booking",
        "offside",
        "throw-in",
        "handicap",
    )
    if any(token in low for token in blocked):
        return None
    pairs: dict[float, set[str]] = defaultdict(set)
    for label in selections.values():
        side, line = _runner_line(label)
        if side and line is not None:
            pairs[float(line)].add(side)
    valid = [line for line, sides in pairs.items() if {"over", "under"} <= sides]
    if not valid:
        return None
    # GOOL enters only half-goal lines; quarter/integer Asian lines are not needed.
    valid = [line for line in valid if abs((line % 1.0) - 0.5) < 0.01]
    if not valid:
        return None
    period = "1H" if any(token in low for token in ("half-time", "half time", "1st half", "first half")) else "FT"
    max_line = 3.5 if period == "1H" else 6.5
    valid = [line for line in valid if 0.5 <= line <= max_line]
    if not valid:
        return None
    return period, min(valid)


def _price_rows(attrs: dict[str, str], runner_index: int, side_group: int) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    prefix = f"1V{runner_index}-{side_group}V"
    levels: set[int] = set()
    for key in attrs:
        match = re.match(re.escape(prefix) + r"(\d+)-1$", key)
        if match:
            levels.add(int(match.group(1)))
    for level in levels:
        odd = _number(attrs.get(f"{prefix}{level}-1"))
        amount = _number(attrs.get(f"{prefix}{level}-2"))
        if odd is None or odd <= 1.0 or amount is None or amount < 0.0:
            continue
        rows.append({"odds": float(odd), "odd": float(odd), "available": float(amount)})
    return rows


def _decode_price_ladder(attrs: dict[str, str], labels: dict[int, str]) -> list[dict[str, Any]]:
    runners: list[dict[str, Any]] = []
    indices: list[tuple[int, int]] = []
    for key, value in attrs.items():
        match = re.match(r"1V(\d+)-1$", key)
        if not match:
            continue
        try:
            indices.append((int(match.group(1)), int(value)))
        except Exception:
            continue
    for index, selection in sorted(indices):
        backs = _price_rows(attrs, index, 2)
        lays = _price_rows(attrs, index, 3)
        backs.sort(key=lambda row: row["odds"], reverse=True)
        lays.sort(key=lambda row: row["odds"])
        prices = [{"side": "back", **row} for row in backs] + [{"side": "lay", **row} for row in lays]
        runners.append(
            {
                "id": str(selection),
                "name": labels.get(selection, str(selection)),
                "label": labels.get(selection, str(selection)),
                "best_back": backs[0] if backs else None,
                "best_lay": lays[0] if lays else None,
                "prices": prices,
            }
        )
    return runners


class BetdaqExchangeCollector:
    """Anonymous BETDAQ AAPI collector for today's football board.

    One lightweight WebSocket discovers all football events, then subscribes only
    to Match Odds and half-goal FT/1H totals. Market volume and ladders are kept in
    a normalized state consumed by GOOL's existing independent MONEY FLOW logic.
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self._stop = threading.Event()
        self._topics: dict[str, dict[str, str]] = {}
        self._tracked_events: set[int] = set()
        self._markets: dict[int, dict[str, Any]] = {}
        # Reuse only the proven pure flow-memory calculation; no Matchbook network.
        self._flow_helper = MatchbookExchangeCollector(state_path)
        self._match_history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=64))
        self._last_state: dict[str, Any] = {}

    def stop(self, *_: object) -> None:
        self._stop.set()

    def _apply_message(self, message: str) -> None:
        topic, attrs = _tagged(message)
        if not topic or not attrs:
            return
        current = self._topics.setdefault(topic, {})
        current.update(attrs)

    def _apply_frame(self, frame: str | bytes) -> int:
        text = frame.decode("utf-8", errors="replace") if isinstance(frame, bytes) else str(frame)
        if not text.startswith("a["):
            return 0
        try:
            messages = json.loads(text[1:])
        except Exception:
            return 0
        count = 0
        for message in messages if isinstance(messages, list) else []:
            if isinstance(message, str):
                self._apply_message(message)
                count += 1
        return count

    async def _recv_for(self, ws: Any, seconds: float, limit: int = 60000) -> int:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, seconds)
        count = 0
        while loop.time() < deadline and count < limit and not self._stop.is_set():
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=min(0.65, max(0.05, deadline - loop.time())))
            except asyncio.TimeoutError:
                continue
            count += self._apply_frame(frame)
        return count

    @staticmethod
    async def _send(ws: Any, message_id: int, fields: dict[int, Any]) -> None:
        await ws.send(json.dumps([_command(message_id, fields)]))

    def _events_from_topics(self) -> list[dict[str, Any]]:
        labels: dict[int, str] = {}
        starts: dict[int, datetime] = {}
        for topic, attrs in self._topics.items():
            event = _event_id(topic)
            if event is None:
                continue
            if topic.endswith("/EL/en") and attrs.get("1"):
                labels[event] = attrs["1"].strip()
            elif topic.endswith("/EEI") and attrs.get("3"):
                dt = _parse_dt(attrs.get("3"))
                if dt is not None:
                    starts[event] = dt
        now = datetime.now(timezone.utc)
        local_today = now.astimezone(_tz()).date()
        rows: list[dict[str, Any]] = []
        for event, start in starts.items():
            name = labels.get(event, "").strip()
            if not name:
                continue
            local_date = start.astimezone(_tz()).date()
            possibly_live = start <= now <= start + timedelta(hours=4)
            if local_date != local_today and not possibly_live:
                continue
            home, away = _split_teams(name)
            if not home or not away:
                continue
            rows.append({"event_id": event, "name": name, "home": home, "away": away, "start": start})
        rows.sort(key=lambda row: row["start"])
        return rows

    def _catalog(self) -> dict[int, dict[str, Any]]:
        names: dict[int, str] = {}
        types: dict[int, int] = {}
        events: dict[int, int] = {}
        selections: dict[int, dict[int, str]] = defaultdict(dict)
        for topic, attrs in self._topics.items():
            mid = _market_id(topic)
            if mid is None:
                continue
            event = _event_id(topic)
            if event is not None:
                events[mid] = event
            if topic.endswith("/MEI/MEL/en") or topic.endswith("/ML/en"):
                if attrs.get("1"):
                    names[mid] = attrs["1"].strip()
            elif topic.endswith("/MEI"):
                try:
                    types[mid] = int(attrs.get("2", "0"))
                except Exception:
                    pass
            if topic.endswith("/SEI/SEL/en") or topic.endswith("/SL/en"):
                sid = _selection_id(topic)
                if sid is not None and attrs.get("1"):
                    selections[mid][sid] = attrs["1"].strip()
        out: dict[int, dict[str, Any]] = {}
        for mid, event in events.items():
            if event not in self._tracked_events:
                continue
            name = names.get(mid, "")
            mtype = types.get(mid)
            runner_labels = selections.get(mid, {})
            if mtype == 3 and name.casefold() == "match odds":
                out[mid] = {"event_id": event, "id": mid, "name": name, "type": mtype, "kind": "match_odds", "selections": dict(runner_labels)}
                continue
            if mtype != 13:
                continue
            total = _goal_total_market(name, runner_labels)
            if total is None:
                continue
            period, line = total
            out[mid] = {
                "event_id": event,
                "id": mid,
                "name": name,
                "type": mtype,
                "kind": "total",
                "period": period,
                "line": float(line),
                "selections": dict(runner_labels),
            }
        return out

    def _market_topic_attrs(self, market_id: int, suffix: str) -> dict[str, str]:
        marker = f"/M/E_{market_id}/"
        candidates = [attrs for topic, attrs in self._topics.items() if marker in topic and suffix in topic]
        if not candidates:
            return {}
        # AAPI uses a stable topic per subscription; merged attrs already contain deltas.
        return max(candidates, key=len)

    def _decode_market(self, meta: dict[str, Any]) -> dict[str, Any]:
        mid = int(meta["id"])
        price_attrs = self._market_topic_attrs(mid, "/MDP/")
        matched_attrs = self._market_topic_attrs(mid, "/MMA/GBP")
        runners = _decode_price_ladder(price_attrs, dict(meta.get("selections") or {}))
        volume = max(0.0, float(_number(matched_attrs.get("1")) or 0.0))
        market: dict[str, Any] = {
            "id": str(mid),
            "name": meta.get("name"),
            "status": "open" if runners else "suspended",
            "volume": volume,
            "matched_gbp": volume,
            "against_liability_gbp": max(0.0, float(_number(matched_attrs.get("2")) or 0.0)),
            "runners": runners,
        }
        if meta.get("kind") == "total":
            line = float(meta.get("line") or 0.0)
            pair: dict[str, dict[str, Any]] = {}
            for runner in runners:
                side, runner_line = _runner_line(str(runner.get("label") or runner.get("name") or ""))
                if side and runner_line is not None and abs(runner_line - line) < 0.01:
                    pair[side] = runner
            market.update({"period": meta.get("period"), "line": line, "over": pair.get("over") or {}, "under": pair.get("under") or {}})
            market["fair_over"] = _fair_over(market["over"], market["under"])
        return market

    def _match_flow(self, event_id: int, market: dict[str, Any], now: float) -> dict[str, Any]:
        hist = self._match_history[str(event_id)]
        current_probs: dict[str, float] = {}
        current_odds: dict[str, float] = {}
        for runner in market.get("runners") or []:
            label = str(runner.get("label") or runner.get("name") or "?")
            prob = _mid_probability(runner)
            odd = _number(((runner.get("best_back") or {}).get("odds")))
            if prob is not None:
                current_probs[label] = prob
            if odd is not None:
                current_odds[label] = odd
        current = {"ts": now, "volume": float(market.get("volume") or 0.0), "probs": current_probs, "odds": current_odds}

        def prior(seconds: float) -> dict[str, Any] | None:
            rows = [row for row in hist if now - float(row.get("ts") or 0.0) >= seconds]
            return rows[-1] if rows else None

        old = prior(30.0) or prior(15.0)
        hist.append(current)
        if old is None:
            return {"ready": False, "level": "WARMING", "matched_delta_gbp": 0.0}
        delta_volume = max(0.0, float(current["volume"]) - float(old.get("volume") or 0.0))
        best_label = ""
        best_pp = 0.0
        for label, prob in current_probs.items():
            old_prob = _number((old.get("probs") or {}).get(label))
            if old_prob is None:
                continue
            delta_pp = (prob - old_prob) * 100.0
            if delta_pp > best_pp:
                best_pp = delta_pp
                best_label = label
        min_delta = float(os.getenv("BETDAQ_BOARD_FLOW_MIN_DELTA_GBP", "100"))
        strong_delta = float(os.getenv("BETDAQ_BOARD_FLOW_STRONG_DELTA_GBP", "500"))
        if best_label and delta_volume >= strong_delta and best_pp >= 2.5:
            level = "STRONG_FLOW"
        elif best_label and delta_volume >= min_delta and best_pp >= 1.0:
            level = "FLOW"
        else:
            level = "NEUTRAL"
        return {
            "ready": True,
            "level": level,
            "outcome": best_label or None,
            "matched_delta_gbp": round(delta_volume, 2),
            "implied_delta_pp": round(best_pp, 3),
            "old_odd": _number((old.get("odds") or {}).get(best_label)) if best_label else None,
            "new_odd": current_odds.get(best_label) if best_label else None,
        }

    def _build_state(self, event_rows: list[dict[str, Any]]) -> dict[str, Any]:
        now_ts = time.time()
        now_dt = datetime.now(timezone.utc)
        by_event: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for meta in self._markets.values():
            by_event[int(meta["event_id"])].append(meta)
        events: list[dict[str, Any]] = []
        for base in event_rows:
            event_id = int(base["event_id"])
            match_odds: dict[str, Any] | None = None
            totals: dict[str, dict[str, Any]] = {}
            for meta in by_event.get(event_id, []):
                market = self._decode_market(meta)
                if meta.get("kind") == "match_odds":
                    match_odds = market
                    continue
                period = str(meta.get("period") or "FT")
                line = float(meta.get("line") or 0.0)
                key = f"{period}:{line:g}"
                market["flow"] = self._flow_helper._flow(str(event_id), key, market, now_ts)
                totals[key] = market
            if match_odds is not None:
                match_odds["flow"] = self._match_flow(event_id, match_odds, now_ts)
            start = base["start"]
            in_running = bool(start <= now_dt <= start + timedelta(hours=4))
            matched = float((match_odds or {}).get("volume") or 0.0)
            events.append(
                {
                    "id": str(event_id),
                    "event_id": str(event_id),
                    "name": base["name"],
                    "home": base["home"],
                    "away": base["away"],
                    "start": start.isoformat(),
                    "status": "open",
                    "in_running": in_running,
                    "allow_live": True,
                    "volume": matched,
                    "matched_gbp": matched,
                    "match_odds": match_odds,
                    "runners": list((match_odds or {}).get("runners") or []),
                    "flow": dict((match_odds or {}).get("flow") or {}),
                    "totals": totals,
                }
            )
        return {
            "captured_at": _now_iso(),
            "available": True,
            "source": "betdaq_public_aapi",
            "currency": "GBP",
            "events": events,
            "tracked_events": len(event_rows),
            "tracked_markets": len(self._markets),
        }

    async def _bootstrap(self, ws: Any) -> list[dict[str, Any]]:
        self._topics.clear()
        self._markets.clear()
        self._tracked_events.clear()
        await self._send(ws, 12, {0: 301, 2: SOCCER_ID, 3: False, 4: False, 5: True, 11: True, 12: False, 13: False, 14: False})
        await self._recv_for(ws, 6.0)
        events = self._events_from_topics()
        self._tracked_events = {int(row["event_id"]) for row in events}
        if not events:
            raise RuntimeError("betdaq_no_today_football_events")

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
        await self._recv_for(ws, 12.0)
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
        print(
            f"BETDAQ_EXCHANGE bootstrap events={len(events)} markets={len(self._markets)} "
            f"match_odds={sum(1 for row in self._markets.values() if row.get('kind') == 'match_odds')} "
            f"totals={sum(1 for row in self._markets.values() if row.get('kind') == 'total')}",
            flush=True,
        )
        return events

    async def _session(self, interval: float) -> None:
        import websockets

        server = f"{random.randrange(1000):03d}"
        session = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        uri = f"wss://aapi-service.betdaq.com/AAPI/{server}/{session}/websocket"
        async with websockets.connect(
            uri,
            origin="https://www.betdaq.com",
            user_agent_header="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            open_timeout=15,
            close_timeout=5,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
        ) as ws:
            first = await asyncio.wait_for(ws.recv(), 10)
            if first != "o":
                raise RuntimeError(f"betdaq_sockjs_open_failed={first!r}")
            await self._send(
                ws,
                1,
                {
                    0: 0,
                    1: "GBP",
                    2: "en",
                    3: 1,
                    4: False,
                    5: 0,
                    6: "2.2",
                    7: str(uuid.uuid4()),
                    8: 5,
                    9: "GEP-exchange-D~Linux~Browser-3.18|",
                    10: "getClientIdentifier",
                },
            )
            await self._recv_for(ws, 1.0)
            events = await self._bootstrap(ws)
            state = self._build_state(events)
            _atomic_write(self.state_path, state)
            self._last_state = state

            loop = asyncio.get_running_loop()
            started = loop.time()
            last_write = loop.time()
            last_ping = loop.time()
            while not self._stop.is_set() and loop.time() - started < 900.0:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=0.8)
                    self._apply_frame(frame)
                except asyncio.TimeoutError:
                    pass
                now = loop.time()
                if now - last_ping >= 15.0:
                    stamp = _now_iso().replace("+00:00", "Z")
                    await self._send(ws, 22, {0: 1, 1: stamp, 2: 0, 3: stamp})
                    last_ping = now
                if now - last_write >= interval:
                    state = self._build_state(events)
                    _atomic_write(self.state_path, state)
                    self._last_state = state
                    live = sum(1 for row in state.get("events") or [] if row.get("in_running"))
                    print(
                        f"BETDAQ_EXCHANGE captured={len(state.get('events') or [])} live={live} "
                        f"markets={state.get('tracked_markets')} state={self.state_path}",
                        flush=True,
                    )
                    last_write = now

    async def _run_forever(self, interval: float) -> None:
        interval = max(8.0, float(interval))
        while not self._stop.is_set():
            try:
                await self._session(interval)
            except Exception as exc:
                payload = {
                    "captured_at": _now_iso(),
                    "available": False,
                    "source": "betdaq_public_aapi",
                    "currency": "GBP",
                    "error": f"{type(exc).__name__}:{exc}",
                    "events": list((self._last_state or {}).get("events") or []),
                }
                _atomic_write(self.state_path, payload)
                print(f"BETDAQ_EXCHANGE error={type(exc).__name__}:{exc}", flush=True)
                await asyncio.sleep(5.0)

    def run(self, interval: float) -> None:
        asyncio.run(self._run_forever(interval))


_STATE_CACHE: dict[str, Any] = {"path": None, "mtime": None, "payload": {}}


def load_betdaq_state(path: Path | None = None) -> dict[str, Any]:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    state_path = path or Path(os.getenv("BETDAQ_MARKET_STATE", str(runtime / "live" / "betdaq_market_state.json")))
    try:
        mtime = state_path.stat().st_mtime_ns
    except OSError:
        return {}
    if _STATE_CACHE.get("path") == str(state_path) and _STATE_CACHE.get("mtime") == mtime:
        return dict(_STATE_CACHE.get("payload") or {})
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    _STATE_CACHE.update({"path": str(state_path), "mtime": mtime, "payload": payload})
    return dict(payload)


def _record_teams(record: dict[str, Any]) -> tuple[str, str]:
    match = record.get("match") or {}
    return str(match.get("home") or match.get("home_team") or ""), str(match.get("away") or match.get("away_team") or "")


def _best_event(record: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any] | None, float]:
    home, away = _record_teams(record)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for event in state.get("events") or []:
        if not isinstance(event, dict):
            continue
        score = pair_score(home, away, str(event.get("home") or ""), str(event.get("away") or ""))
        if score > best_score:
            best = event
            best_score = score
    threshold = float(os.getenv("BETDAQ_MATCH_MIN_SCORE", os.getenv("MATCHBOOK_MATCH_MIN_SCORE", "0.72")))
    return (best, best_score) if best is not None and best_score >= threshold else (None, best_score)


def _target_context(event: dict[str, Any], period: str, line: float) -> dict[str, Any]:
    key = f"{period}:{float(line):g}"
    market = dict(((event.get("totals") or {}).get(key)) or {})
    if not market:
        return {"available": False, "period": period, "line": float(line), "level": "NO_MARKET"}
    min_volume = max(0.0, float(os.getenv("BETDAQ_MIN_MARKET_VOLUME", os.getenv("MATCHBOOK_MIN_MARKET_VOLUME", "50"))))
    volume = float(_number(market.get("volume")) or 0.0)
    over = dict(market.get("over") or {})
    under = dict(market.get("under") or {})
    two_sided = bool(over.get("best_back") and over.get("best_lay") and under.get("best_back") and under.get("best_lay"))
    liquid = volume >= min_volume and two_sided
    flow = dict(market.get("flow") or {})
    level = str(flow.get("level") or "NEUTRAL") if liquid else "LOW_LIQUIDITY"
    return {
        "available": True,
        "liquid": liquid,
        "period": period,
        "line": float(line),
        "market_id": market.get("id"),
        "market_name": market.get("name"),
        "market_status": market.get("status"),
        "volume": volume,
        "fair_over": market.get("fair_over"),
        "over": over,
        "under": under,
        "flow": flow,
        "level": level,
        "support": liquid and level in {"SUPPORT", "STRONG_SUPPORT"},
        "strong_support": liquid and level == "STRONG_SUPPORT",
        "opposition": liquid and level in {"OPPOSITION", "STRONG_OPPOSITION"},
        "strong_opposition": liquid and level == "STRONG_OPPOSITION",
    }


def betdaq_context(record: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = state if isinstance(state, dict) else load_betdaq_state()
    captured = _parse_dt(payload.get("captured_at"))
    max_age = max(20.0, float(os.getenv("BETDAQ_STATE_MAX_AGE_SECONDS", "45")))
    if captured is None or (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() > max_age or not bool(payload.get("available", True)):
        return {"available": False, "match_score": 0.0, "source": "betdaq", "reason": "betdaq_state_unavailable_or_stale"}
    event, score = _best_event(record, payload)
    if event is None:
        return {"available": False, "match_score": round(score, 4), "source": "betdaq"}
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    total = hs + aws
    money_flow = {"available": False}
    if minute > 0 and not bool(match.get("is_finished")):
        period = "1H" if minute <= 45 and not bool(match.get("is_halftime")) else "FT"
        money_flow = _target_context(event, period, total + 0.5)
        if period == "1H" and not bool(money_flow.get("available")):
            money_flow = _target_context(event, "FT", total + 0.5)
    systems = {
        "goal_before_ht": _target_context(event, "1H", total + 0.5) if 1 <= minute <= 35 else {"available": False},
        "another_goal": _target_context(event, "FT", total + 0.5) if 46 <= minute <= 75 else {"available": False},
        "money_flow": money_flow,
    }
    return {
        "available": True,
        "source": "betdaq",
        "captured_at": payload.get("captured_at"),
        "match_score": round(score, 4),
        "event": {
            "id": event.get("event_id") or event.get("id"),
            "name": event.get("name"),
            "home": event.get("home"),
            "away": event.get("away"),
            "status": event.get("status"),
            "in_running": event.get("in_running"),
            "volume": event.get("volume"),
        },
        "match_odds": event.get("match_odds"),
        "systems": systems,
    }


__all__ = ["BetdaqExchangeCollector", "betdaq_context", "load_betdaq_state"]
