from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .providers.common import UA, pair_score

MATCHBOOK_EVENTS_URL = "https://api.matchbook.com/edge/rest/events"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_prices(runner: dict[str, Any]) -> dict[str, Any]:
    backs: list[dict[str, float]] = []
    lays: list[dict[str, float]] = []
    for raw in runner.get("prices") or []:
        if not isinstance(raw, dict):
            continue
        odd = _number(raw.get("odds"))
        amount = _number(raw.get("available-amount"))
        side = str(raw.get("side") or "").lower()
        if odd is None or odd <= 1.0 or amount is None or amount < 0:
            continue
        row = {"odds": float(odd), "available": float(amount)}
        if side == "back":
            backs.append(row)
        elif side == "lay":
            lays.append(row)
    backs.sort(key=lambda row: row["odds"], reverse=True)
    lays.sort(key=lambda row: row["odds"])
    best_back = backs[0] if backs else None
    best_lay = lays[0] if lays else None
    return {
        "name": str(runner.get("name") or ""),
        "status": str(runner.get("status") or ""),
        "volume": float(_number(runner.get("volume")) or 0.0),
        "best_back": best_back,
        "best_lay": best_lay,
        "back_depth": round(sum(row["available"] for row in backs[:3]), 4),
        "lay_depth": round(sum(row["available"] for row in lays[:3]), 4),
        "prices": [
            {"side": "back", **row} for row in backs[:3]
        ] + [
            {"side": "lay", **row} for row in lays[:3]
        ],
    }


def _runner_line(name: str) -> tuple[str | None, float | None]:
    text = str(name or "").strip().upper()
    for prefix in ("OVER ", "UNDER "):
        if text.startswith(prefix):
            try:
                return prefix.strip().lower(), float(text[len(prefix):].strip())
            except (TypeError, ValueError):
                return None, None
    return None, None


def _period_from_market(name: str) -> str:
    text = str(name or "").lower()
    if "1st half" in text or "first half" in text or "1st-half" in text or "first-half" in text:
        return "1H"
    return "FT"


def _mid_probability(runner: dict[str, Any]) -> float | None:
    back = ((runner.get("best_back") or {}).get("odds"))
    lay = ((runner.get("best_lay") or {}).get("odds"))
    vals: list[float] = []
    for odd in (back, lay):
        try:
            odd_f = float(odd)
        except (TypeError, ValueError):
            continue
        if odd_f > 1.0:
            vals.append(1.0 / odd_f)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _fair_over(over: dict[str, Any], under: dict[str, Any]) -> float | None:
    po = _mid_probability(over)
    pu = _mid_probability(under)
    if po is None or pu is None or po + pu <= 0:
        return None
    return po / (po + pu)


def _event_teams(event: dict[str, Any]) -> tuple[str, str]:
    participants = [
        str(row.get("participant-name") or "").strip()
        for row in (event.get("event-participants") or [])
        if isinstance(row, dict) and str(row.get("participant-name") or "").strip()
    ]
    if len(participants) >= 2:
        return participants[0], participants[1]
    name = str(event.get("name") or "")
    if " vs " in name:
        home, away = name.split(" vs ", 1)
        return home.strip(), away.strip()
    if " v " in name:
        home, away = name.split(" v ", 1)
        return home.strip(), away.strip()
    return "", ""


def decode_event(event: dict[str, Any]) -> dict[str, Any] | None:
    home, away = _event_teams(event)
    if not home or not away:
        return None
    totals: dict[str, dict[str, Any]] = {}
    match_odds: dict[str, Any] | None = None
    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        name = str(market.get("name") or "")
        low = name.lower()
        runners = [_best_prices(row) for row in (market.get("runners") or []) if isinstance(row, dict)]
        base = {
            "id": str(market.get("id") or ""),
            "name": name,
            "status": str(market.get("status") or ""),
            "in_running": bool(market.get("in-running-flag")),
            "volume": float(_number(market.get("volume")) or 0.0),
            "runners": runners,
        }
        if "match odds" in low or low == "moneyline":
            match_odds = base
            continue
        pairs: dict[float, dict[str, dict[str, Any]]] = defaultdict(dict)
        for runner in runners:
            side, line = _runner_line(str(runner.get("name") or ""))
            if side and line is not None:
                pairs[float(line)][side] = runner
        if not pairs:
            continue
        period = _period_from_market(name)
        for line, pair in pairs.items():
            over = pair.get("over")
            under = pair.get("under")
            if not over or not under:
                continue
            key = f"{period}:{line:g}"
            totals[key] = {
                **base,
                "period": period,
                "line": float(line),
                "over": over,
                "under": under,
                "fair_over": _fair_over(over, under),
            }
    return {
        "event_id": str(event.get("id") or ""),
        "name": str(event.get("name") or ""),
        "home": home,
        "away": away,
        "start": event.get("start"),
        "status": str(event.get("status") or ""),
        "in_running": bool(event.get("in-running-flag")),
        "allow_live": bool(event.get("allow-live-betting")),
        "volume": float(_number(event.get("volume")) or 0.0),
        "match_odds": match_odds,
        "totals": totals,
    }


def _fetch_events() -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "tag-url-names": "soccer",
            "states": "open,suspended",
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "include-prices": "true",
            "price-depth": 3,
            "price-mode": "expanded",
            "currency": "GBP",
            "minimum-liquidity": 1,
            "include-event-participants": "true",
            "markets-limit": 40,
            "per-page": 100,
        }
    )
    req = urllib.request.Request(
        f"{MATCHBOOK_EVENTS_URL}?{params}",
        headers={"User-Agent": UA, "Accept": "application/json,*/*"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows: list[dict[str, Any]] = []
    for event in (payload or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        decoded = decode_event(event)
        if decoded is not None:
            rows.append(decoded)
    return rows


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


class MatchbookExchangeCollector:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self._stop = threading.Event()
        self._history: dict[str, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=12))

    def stop(self, *_: object) -> None:
        self._stop.set()

    @staticmethod
    def _hist_key(event_id: str, key: str) -> str:
        return f"{event_id}:{key}"

    def _flow(self, event_id: str, key: str, market: dict[str, Any], now: float) -> dict[str, Any]:
        fair = _number(market.get("fair_over"))
        volume = float(_number(market.get("volume")) or 0.0)
        hist = self._history[self._hist_key(event_id, key)]
        current = {"ts": now, "fair": float(fair) if fair is not None else math.nan, "volume": volume}

        def prior(seconds: float) -> dict[str, float] | None:
            candidates = [row for row in hist if now - float(row["ts"]) >= seconds]
            return candidates[-1] if candidates else None

        out: dict[str, Any] = {}
        ready: dict[str, bool] = {}
        for seconds, label in ((15.0, "15s"), (30.0, "30s"), (60.0, "60s")):
            old = prior(seconds)
            ready[label] = old is not None
            out[f"window_ready_{label}"] = ready[label]
            if old is None:
                out[f"volume_delta_{label}"] = 0.0
                out[f"fair_over_delta_pp_{label}"] = 0.0
                continue
            out[f"volume_delta_{label}"] = round(max(0.0, volume - float(old["volume"])), 4)
            old_fair = float(old["fair"])
            if fair is None or math.isnan(old_fair):
                delta_pp = 0.0
            else:
                delta_pp = (float(fair) - old_fair) * 100.0
            out[f"fair_over_delta_pp_{label}"] = round(delta_pp, 3)
        hist.append(current)

        usable_windows = [label for label in ("30s", "60s") if ready.get(label)]
        volume_delta = max(
            (float(out.get(f"volume_delta_{label}") or 0.0) for label in usable_windows),
            default=0.0,
        )
        delta_pp = float(out.get("fair_over_delta_pp_30s") or 0.0) if ready.get("30s") else 0.0
        if abs(delta_pp) < 0.25 and ready.get("60s"):
            delta_pp = float(out.get("fair_over_delta_pp_60s") or 0.0)
        min_volume = max(0.0, float(os.getenv("MATCHBOOK_FLOW_MIN_VOLUME_DELTA", "20")))
        strong_volume = max(min_volume, float(os.getenv("MATCHBOOK_FLOW_STRONG_VOLUME_DELTA", "75")))
        if delta_pp >= 3.0 and volume_delta >= strong_volume:
            level = "STRONG_SUPPORT"
        elif delta_pp >= 1.25 and volume_delta >= min_volume:
            level = "SUPPORT"
        elif delta_pp <= -3.0 and volume_delta >= strong_volume:
            level = "STRONG_OPPOSITION"
        elif delta_pp <= -1.25 and volume_delta >= min_volume:
            level = "OPPOSITION"
        else:
            level = "NEUTRAL"
        out.update({
            "level": level,
            "direction_pp": round(delta_pp, 3),
            "activity_volume": round(volume_delta, 4),
        })
        return out

    def collect_once(self) -> dict[str, Any]:
        events = _fetch_events()
        now = time.time()
        for event in events:
            event_id = str(event.get("event_id") or "")
            for key, market in (event.get("totals") or {}).items():
                market["flow"] = self._flow(event_id, str(key), market, now)
        payload = {
            "captured_at": _now_iso(),
            "source": "matchbook_public_exchange",
            "events": events,
        }
        _atomic_write(self.state_path, payload)
        return payload

    def run(self, interval: float) -> None:
        interval = max(8.0, float(interval))
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = self.collect_once()
                open_events = sum(1 for row in state.get("events") or [] if row.get("in_running"))
                print(
                    f"MATCHBOOK_EXCHANGE captured={len(state.get('events') or [])} live={open_events} "
                    f"state={self.state_path}",
                    flush=True,
                )
            except Exception as exc:
                print(f"MATCHBOOK_EXCHANGE error={type(exc).__name__}:{exc}", flush=True)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.5, interval - elapsed))


_STATE_CACHE: dict[str, Any] = {"path": None, "mtime": None, "payload": {}}


def load_matchbook_state(path: Path | None = None) -> dict[str, Any]:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    state_path = path or Path(os.getenv("MATCHBOOK_MARKET_STATE", str(runtime / "live" / "matchbook_market_state.json")))
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
    home = str(
        match.get("home")
        or match.get("home_team")
        or match.get("home_name")
        or match.get("home_team_name")
        or ""
    )
    away = str(
        match.get("away")
        or match.get("away_team")
        or match.get("away_name")
        or match.get("away_team_name")
        or ""
    )
    return home, away


def _best_event(record: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any] | None, float]:
    home, away = _record_teams(record)
    if not home or not away:
        return None, 0.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    for event in state.get("events") or []:
        if not isinstance(event, dict):
            continue
        score = pair_score(home, away, str(event.get("home") or ""), str(event.get("away") or ""))
        if score > best_score:
            best = event
            best_score = score
    threshold = float(os.getenv("MATCHBOOK_MATCH_MIN_SCORE", "0.72"))
    return (best, best_score) if best is not None and best_score >= threshold else (None, best_score)


def _target_context(event: dict[str, Any], period: str, line: float) -> dict[str, Any]:
    key = f"{period}:{float(line):g}"
    market = dict(((event.get("totals") or {}).get(key)) or {})
    if not market:
        return {"available": False, "period": period, "line": float(line), "level": "NO_MARKET"}
    min_volume = max(0.0, float(os.getenv("MATCHBOOK_MIN_MARKET_VOLUME", "50")))
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


def matchbook_context(record: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = state if isinstance(state, dict) else load_matchbook_state()
    event, score = _best_event(record, payload)
    if event is None:
        return {"available": False, "match_score": round(score, 4), "source": "matchbook"}
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    total = hs + aws
    systems = {
        "goal_before_ht": _target_context(event, "1H", total + 0.5) if 1 <= minute <= 30 else {"available": False},
        "another_goal": _target_context(event, "FT", total + 0.5) if 46 <= minute <= 75 else {"available": False},
    }
    return {
        "available": True,
        "source": "matchbook",
        "captured_at": payload.get("captured_at"),
        "match_score": round(score, 4),
        "event": {
            "id": event.get("event_id"),
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
