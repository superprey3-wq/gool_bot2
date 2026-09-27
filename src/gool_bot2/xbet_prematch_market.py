from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .providers.common import pair_score
from .xbet_market_pressure import _http_json, decode_markets


ROOTS = (
    "https://1xbet.com/service-api/LineFeed",
    "https://1xbet.com/LineFeed",
    "https://1xbet.fi/service-api/LineFeed",
    "https://1xbet.fi/LineFeed",
)
INDEX_QUERIES = (
    "sports=1&count=500&lng=en&cfview=2&mode=4&country=19&getEmpty=true",
    "sports=1&count=500&lng=en&cfview=2&mode=4&country=1&getEmpty=true",
    "sports=1&count=500&lng=en&cfview=2&mode=4&country=19&tf=2200000&tz=0&getEmpty=true",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Any) -> float | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        if raw > 1_000_000_000:
            return raw
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _event_start(event: dict[str, Any]) -> float | None:
    for key in ("S", "Start", "StartTime", "startTime", "D", "date"):
        ts = _parse_ts(event.get(key))
        if ts is not None:
            return ts
    return None


def _main_total(markets: dict[str, Any]) -> dict[str, Any] | None:
    rows = [dict(row) for row in (markets.get("match_total") or []) if isinstance(row, dict)]
    if not rows:
        return None

    def fair_over(row: dict[str, Any]) -> float | None:
        try:
            over = float(row.get("over"))
        except (TypeError, ValueError):
            return None
        if over <= 1.0:
            return None
        try:
            under = float(row.get("under"))
        except (TypeError, ValueError):
            under = 0.0
        a = 1.0 / over
        if under <= 1.0:
            return a
        b = 1.0 / under
        return a / (a + b) if a + b > 0 else None

    def rank(row: dict[str, Any]) -> tuple[float, float]:
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            line = 99.0
        paired = 0.0 if row.get("over") and row.get("under") else 1.0
        return abs(line - 2.5), paired

    row = min(rows, key=rank)
    return {
        "line": row.get("line"),
        "over": row.get("over"),
        "under": row.get("under"),
        "fair_over": fair_over(row),
    }


def _usable_snapshot(markets: dict[str, Any]) -> bool:
    """A candidate is usable when at least one supported market can be priced."""
    one_x_two = markets.get("match_1x2") or {}
    has_1x2 = all(one_x_two.get(name) for name in ("home", "draw", "away"))
    return bool(has_1x2 or _main_total(markets))


def _safe_team_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {"home", "away", "team 1", "team 2"}:
        return ""
    return text


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_prematch_market_state(path: Path | None = None) -> dict[str, Any]:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    target = path or Path(os.getenv("XBET_PREMATCH_STATE", str(runtime / "live" / "xbet_prematch_market.json")))
    return _load(target)


def find_prematch_market(
    home: str,
    away: str,
    *,
    path: Path | None = None,
    min_score: float | None = None,
) -> dict[str, Any] | None:
    state = load_prematch_market_state(path)
    rows = state.get("matches") or {}
    threshold = float(os.getenv("XBET_PREMATCH_MATCH_MIN_SCORE", "0.72")) if min_score is None else float(min_score)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in rows.values() if isinstance(rows, dict) else []:
        if not isinstance(row, dict):
            continue
        score = pair_score(home, away, str(row.get("home") or ""), str(row.get("away") or ""))
        if score > best_score:
            best = row
            best_score = score
    if best is None or best_score < threshold:
        return None
    return {**best, "match_score": round(best_score, 4)}


class XBetPrematchCollector:
    """Persist closing-ish 1xBet LineFeed snapshots before matches go live.

    This process is intentionally slow and bounded. It polls upcoming football,
    refreshes only a limited number of near-front LineFeed events, and preserves
    the latest prematch snapshot after the event disappears from LineFeed so the
    live GOOL process can still recover the kickoff market by team names.
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.active_root = ROOTS[0]
        self._stop = threading.Event()

    def stop(self, *_: object) -> None:
        self._stop.set()

    def _index(self) -> tuple[str | None, list[dict[str, Any]]]:
        roots = [self.active_root, *[root for root in ROOTS if root != self.active_root]]
        for root in roots:
            for query in INDEX_QUERIES:
                payload = _http_json(f"{root}/Get1x2_VZip?{query}")
                value = payload.get("Value") if isinstance(payload, dict) else None
                if isinstance(value, list) and value:
                    rows = [row for row in value if isinstance(row, dict)]
                    if rows:
                        self.active_root = root
                        return root, rows
        return None, []

    @staticmethod
    def _search_rows(payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                if obj.get("I") and obj.get("O1") and obj.get("O2"):
                    rows.append(obj)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)
        walk(payload)
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            unique[str(row.get("I"))] = row
        return list(unique.values())

    def _search_target(self, target: Any) -> dict[str, Any] | None:
        home = str(getattr(target, "home", "") or "").strip()
        away = str(getattr(target, "away", "") or "").strip()
        if not home or not away:
            return None
        roots = [self.active_root, *[root for root in ROOTS if root != self.active_root]]
        best: dict[str, Any] | None = None
        best_score = 0.0
        target_ts = float((getattr(target, "meta", {}) or {}).get("scheduled_start_ts") or 0)
        # Address 1xBet from the Flashscore fixture instead of downloading the
        # whole bookmaker catalogue. One query is normally enough; away is a fallback.
        for term in (home, away):
            params = urllib.parse.urlencode({"text": term, "limit": 50, "lng": "en"})
            for root in roots:
                payload = _http_json(f"{root}/Web_SearchZip?{params}")
                for event in self._search_rows(payload):
                    eh = _safe_team_name(event.get("O1")); ea = _safe_team_name(event.get("O2"))
                    if not eh or not ea:
                        continue
                    names = pair_score(home, away, eh, ea)
                    xb_ts = _event_start(event) or 0.0
                    if target_ts and xb_ts:
                        delta = abs(target_ts - xb_ts)
                        if delta > 3 * 3600:
                            continue
                        score = 0.85 * names + 0.15 * max(0.0, 1.0 - delta / (3 * 3600))
                    else:
                        score = names
                    if score > best_score:
                        best_score = score
                        best = {"event": event, "event_id": str(event.get("I")), "home": eh, "away": ea, "root": root, "match_score": score}
                if best_score >= 0.82:
                    self.active_root = root
                    return best
        return best if best_score >= 0.68 else None

    def _game(self, root: str, event_id: str) -> dict[str, Any] | None:
        params = {
            "id": event_id,
            "lng": "en",
            "cfview": 0,
            "isSubGames": "true",
            "GroupEvents": "true",
            "allEventsGroupSubGames": "true",
            "countevents": 250,
            "grMode": 4,
            "marketType": 1,
            "isNewBuilder": "true",
        }
        payload = _http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}")
        value = payload.get("Value") if isinstance(payload, dict) else None
        return value if isinstance(value, dict) else None

    def collect_once(self, targets: list[Any] | None = None) -> dict[str, Any]:
        started = time.time()
        root, index = self._index()
        previous = _load(self.state_path)
        stored = dict(previous.get("matches") or {}) if isinstance(previous.get("matches"), dict) else {}
        limit = max(40, min(120, int(os.getenv("XBET_PREMATCH_FETCH_EVENTS", "80"))))
        workers = max(2, min(16, int(os.getenv("XBET_PREMATCH_WORKERS", "8"))))
        now = time.time()
        candidates: list[dict[str, Any]] = []
        for event in index:
            event_id = str(event.get("I") or "").strip()
            home = _safe_team_name(event.get("O1"))
            away = _safe_team_name(event.get("O2"))
            if not event_id or not home or not away:
                continue
            candidates.append({"event": event, "event_id": event_id, "home": home, "away": away})

        if targets:
            # Fixture discovery is already complete before this stage. Resolve bookmaker
            # addresses concurrently so pricing cannot serialize the full daily field.
            selected: list[dict[str, Any]] = []
            seen: set[str] = set()
            search_workers = max(4, min(24, int(os.getenv("XBET_PREMATCH_SEARCH_WORKERS", "12"))))

            def resolve_target(target: Any) -> dict[str, Any] | None:
                row = self._search_target(target)
                if row:
                    return row
                target_ts = float((getattr(target, "meta", {}) or {}).get("scheduled_start_ts") or 0)
                best = None; best_score = 0.0
                for candidate in candidates:
                    names = pair_score(getattr(target, "home", ""), getattr(target, "away", ""), candidate["home"], candidate["away"])
                    xb_ts = _event_start(candidate["event"]) or 0.0
                    if target_ts and xb_ts and abs(target_ts - xb_ts) > 3 * 3600:
                        continue
                    if names > best_score:
                        best = candidate; best_score = names
                if best and best_score >= 0.68:
                    return {**best, "root": root}
                return None

            search_total = len(targets)
            search_done = 0
            print(f"XBET_SEARCH_PROGRESS start total={search_total} workers={search_workers}", flush=True)
            with ThreadPoolExecutor(max_workers=search_workers, thread_name_prefix="xbet-search") as pool:
                futures = [pool.submit(resolve_target, target) for target in targets]
                for future in as_completed(futures):
                    try:
                        row = future.result()
                    except Exception:
                        row = None
                    if row and row["event_id"] not in seen:
                        seen.add(row["event_id"])
                        selected.append(row)
                    search_done += 1
                    if search_done % 25 == 0 or search_done == search_total:
                        print(
                            f"XBET_SEARCH_PROGRESS checked={search_done}/{search_total} matched={len(selected)}",
                            flush=True,
                        )
            candidates = selected  # every brain-selected fixture is processed; display limits belong elsewhere
        else:
            candidates = [{**row, "root": root} for row in candidates[:limit]]

        refreshed = 0
        if candidates:
            price_total = len(candidates)
            price_done = 0
            price_failures = 0
            print(f"XBET_PRICE_PROGRESS start total={price_total} workers={workers}", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._game, row.get("root") or root, row["event_id"]): row
                    for row in candidates
                }
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        game = future.result(timeout=12)
                    except Exception:
                        game = None
                    if not game:
                        price_failures += 1
                    else:
                        markets = decode_markets(game)
                        if not _usable_snapshot(markets):
                            price_failures += 1
                            game = None
                    price_done += 1
                    if price_done % 25 == 0 or price_done == price_total:
                        print(
                            f"XBET_PRICE_PROGRESS checked={price_done}/{price_total} usable={refreshed} failures={price_failures}",
                            flush=True,
                        )
                    if not game:
                        continue
                    event = row["event"]
                    event_id = row["event_id"]
                    old = stored.get(event_id) or {}
                    snapshot = {
                        "event_id": event_id,
                        "home": row["home"],
                        "away": row["away"],
                        "scheduled_start_ts": _event_start(event),
                        "source": "1xbet:LineFeed",
                        "root": row.get("root") or root,
                        "first_captured_at": old.get("first_captured_at") or _iso_now(),
                        "captured_at": _iso_now(),
                        "match_1x2": dict(markets.get("match_1x2") or {}),
                        "main_total": _main_total(markets),
                        "match_totals": [dict(x) for x in (markets.get("match_total") or []) if isinstance(x, dict)],
                    }
                    stored[event_id] = snapshot
                    refreshed += 1

        # Retain disappeared prematch snapshots long enough for a live match to
        # use them, but do not let a persistent server accumulate months of data.
        keep_seconds = max(6 * 3600, int(os.getenv("XBET_PREMATCH_RETENTION_SECONDS", str(36 * 3600))))
        kept: dict[str, Any] = {}
        for event_id, row in stored.items():
            if not isinstance(row, dict):
                continue
            stamp = _parse_ts(row.get("captured_at"))
            if stamp is None or now - stamp <= keep_seconds:
                kept[str(event_id)] = row

        state = {
            "captured_at": _iso_now(),
            "root": root,
            "latency_ms": int((time.time() - started) * 1000),
            "index_events": len(index),
            "refreshed": refreshed,
            "matches": kept,
        }
        _write(self.state_path, state)
        return state

    def run(self, interval: float = 300.0) -> None:
        interval = max(60.0, float(interval))
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = self.collect_once()
                print(
                    f"XBET_PREMATCH stored={len(state.get('matches') or {})} refreshed={state.get('refreshed')} "
                    f"index={state.get('index_events')} latency_ms={state.get('latency_ms')} root={state.get('root')}",
                    flush=True,
                )
            except Exception as exc:
                # A prematch provider outage must not terminate the whole GOOL bot.
                print(f"XBET_PREMATCH_ERROR {type(exc).__name__}:{exc}", flush=True)
            self._stop.wait(max(1.0, interval - (time.monotonic() - started)))


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL 1xBet prematch market collector")
    parser.add_argument("--state", default=os.getenv("XBET_PREMATCH_STATE", str(runtime / "live" / "xbet_prematch_market.json")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("XBET_PREMATCH_INTERVAL_SECONDS", "300")))
    args = parser.parse_args()
    collector = XBetPrematchCollector(Path(args.state))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(f"XBET_PREMATCH started interval={args.interval}s state={args.state}", flush=True)
    collector.run(args.interval)


if __name__ == "__main__":
    main()
