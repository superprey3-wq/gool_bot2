from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import telegram
from . import xbet_market_pressure as market
from .multisport_journal import append_unique
from .providers.flashscore import FlashscoreProvider
from .storage_runtime import trim_file_tail
from .xbet_multisport_card import render_multisport_signal_card
from .xbet_multisport_steam import (
    SPORTS,
    SportConfig,
    _balanced_total,
    _event_allowed,
    _float_env,
    _int_env,
    _metric,
    _mode,
    _sport_enabled,
    map_xbet_to_flashscore,
    parse_flashscore_events,
)

ROOTS = (
    "https://1xbet.com/service-api/LineFeed",
    "https://1xbet.com/LineFeed",
    "https://1xbet.fi/service-api/LineFeed",
    "https://1xbet.fi/LineFeed",
)


def _event_start(event: dict[str, Any]) -> float:
    for key in ("S", "Start", "StartTime", "startTime", "D", "date"):
        raw = event.get(key)
        if raw in (None, "", 0, "0"):
            continue
        try:
            value = float(raw)
            if value > 10_000_000_000:
                value /= 1000.0
            if value > 1_000_000_000:
                return value
        except (TypeError, ValueError):
            pass
    return 0.0


def detect_prematch_signal(
    rows: list[dict[str, Any]],
    cfg: SportConfig,
    *,
    now: float,
) -> dict[str, Any] | None:
    window = max(300.0, _float_env("GOOL_MULTISPORT_PREMATCH_WINDOW_SECONDS", 6 * 3600.0))
    eligible = [row for row in rows if now - float(row.get("ts") or 0.0) <= window]
    if len(eligible) < 2:
        return None
    age = float(eligible[-1]["ts"]) - float(eligible[0]["ts"])
    if age < _float_env("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", 45.0):
        return None

    start, end = eligible[0], eligible[-1]
    raw_delta = float(end["metric"]) - float(start["metric"])
    direction = "over" if raw_delta > 0 else "under"
    metric_delta = abs(raw_delta)
    min_metric = (
        _float_env("GOOL_HOCKEY_PREMATCH_MIN_METRIC_DELTA", 0.30)
        if cfg.key == "hockey"
        else _float_env("GOOL_BASKETBALL_PREMATCH_MIN_METRIC_DELTA", 2.0)
    )
    extreme_metric = (
        _float_env("GOOL_HOCKEY_PREMATCH_EXTREME_METRIC_DELTA", 0.55)
        if cfg.key == "hockey"
        else _float_env("GOOL_BASKETBALL_PREMATCH_EXTREME_METRIC_DELTA", 4.0)
    )
    extreme = metric_delta >= extreme_metric
    if metric_delta < min_metric:
        return None

    one_way = 0
    epsilon = cfg.move_epsilon
    for left, right in zip(eligible, eligible[1:]):
        delta = float(right["metric"]) - float(left["metric"])
        if (direction == "over" and delta >= epsilon) or (direction == "under" and delta <= -epsilon):
            one_way += 1
    if one_way < 1 and not extreme:
        return None

    over_delta_pp = (float(end["probability"]) - float(start["probability"])) * 100.0
    probability_delta_pp = over_delta_pp if direction == "over" else -over_delta_pp
    raw_line_delta = float(end["line"]) - float(start["line"])
    line_delta = raw_line_delta if direction == "over" else -raw_line_delta
    min_prob = _float_env("GOOL_MULTISPORT_PREMATCH_MIN_FAIR_EDGE_PP", 2.0)
    if probability_delta_pp < min_prob and abs(line_delta) < (0.5 if cfg.key == "hockey" else 1.5) and not extreme:
        return None

    odd = float(end[direction])
    if not (_float_env("GOOL_MULTISPORT_MIN_ODD", 1.45) <= odd <= _float_env("GOOL_MULTISPORT_MAX_ODD", 3.25)):
        return None

    fair_probability = float(end["probability"]) if direction == "over" else 1.0 - float(end["probability"])
    strength = min(
        100.0,
        56.0
        + metric_delta / max(1e-6, min_metric) * 13.0
        + one_way * 4.0
        + min(10.0, max(0.0, probability_delta_pp))
        + (7.0 if extreme else 0.0),
    )
    return {
        "phase": "PREMATCH",
        "direction": direction,
        "line": float(end["line"]),
        "odd": odd,
        "fair_probability": round(fair_probability, 6),
        "metric_delta": round(metric_delta, 3),
        "probability_delta_pp": round(probability_delta_pp, 2),
        "line_delta": round(line_delta, 2),
        "moves": one_way,
        "age_seconds": round(age, 1),
        "strength": round(strength, 1),
        "extreme": extreme,
        "opening_line": float(start["line"]),
        "opening_odd": float(start[direction]),
        "start": start,
        "end": end,
    }


class MultiSportPrematchWorker:
    def __init__(self, runtime: Path | None = None) -> None:
        runtime = runtime or Path(os.getenv("RUNTIME_DATA_DIR", "data"))
        live = runtime / "live"
        self.state_path = Path(os.getenv("GOOL_MULTISPORT_PREMATCH_STATE", str(live / "gool_multisport_prematch_state.json")))
        self.history_path = Path(os.getenv("GOOL_MULTISPORT_PREMATCH_HISTORY", str(live / "gool_multisport_prematch_history.jsonl")))
        self.journal_path = Path(os.getenv("GOOL_MULTISPORT_JOURNAL", str(live / "gool_multisport_signals.json")))
        self._stop = threading.Event()
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=80))
        self._roots: dict[str, str] = {key: ROOTS[0] for key in SPORTS}
        self._flashscore = FlashscoreProvider()
        self._restore_history()

    def stop(self) -> None:
        self._stop.set()

    def _restore_history(self) -> None:
        try:
            size = self.history_path.stat().st_size
            with self.history_path.open("rb") as fh:
                fh.seek(max(0, size - 4 * 1024 * 1024))
                data = fh.read()
            if size > 4 * 1024 * 1024 and b"\n" in data:
                data = data.split(b"\n", 1)[1]
        except FileNotFoundError:
            return
        restored = 0
        for raw in data.splitlines():
            try:
                state = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            for key in SPORTS:
                for row in (((state.get("sports") or {}).get(key) or {}).get("matches") or []):
                    if isinstance(row, dict) and row.get("event_id") and row.get("ts") is not None:
                        self._history[f"{key}:{row['event_id']}"].append(dict(row))
                        restored += 1
        if restored:
            print(f"GOOL_MULTISPORT_PREMATCH_MEMORY restored_snapshots={restored}", flush=True)

    def _flashscore_scheduled(self, cfg: SportConfig) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        horizon = time.time() + max(2 * 3600, _int_env("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", 30 * 3600))
        for day in (0, 1):
            for suffix in ("3", "0"):
                body = self._flashscore._feed(f"f_{cfg.flashscore_id}_{day}_{suffix}_en_1")
                if not body:
                    continue
                for row in parse_flashscore_events(body):
                    if str(row.get("coarse_status") or "") != "1":
                        continue
                    start_ts = float(row.get("scheduled_start_ts") or 0)
                    if start_ts and start_ts <= horizon:
                        merged[str(row["flashscore_event_id"])] = row
        return list(merged.values())

    def _queries(self, cfg: SportConfig) -> list[str]:
        count = max(100, _int_env("GOOL_MULTISPORT_PREMATCH_INDEX_COUNT", 1000))
        base = {"sports": cfg.sport_id, "count": count, "lng": "en", "cfview": 2, "mode": 4, "getEmpty": "true"}
        return [
            urllib.parse.urlencode({**base, "country": 1}),
            urllib.parse.urlencode({**base, "country": 19}),
        ]

    def _index(self, cfg: SportConfig) -> list[dict[str, Any]]:
        roots = [self._roots[cfg.key], *[root for root in ROOTS if root != self._roots[cfg.key]]]
        for root in dict.fromkeys(roots):
            merged: dict[str, dict[str, Any]] = {}
            for query in self._queries(cfg):
                payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=8.0)
                values = payload.get("Value") if isinstance(payload, dict) else None
                if not isinstance(values, list):
                    continue
                for row in values:
                    if isinstance(row, dict) and row.get("I") and row.get("O1") and row.get("O2") and _event_allowed(row):
                        merged[str(row["I"])] = row
            if merged:
                self._roots[cfg.key] = root
                return list(merged.values())
        return []

    def _game(self, event_id: str, cfg: SportConfig) -> dict[str, Any] | None:
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
        roots = [self._roots[cfg.key], *[root for root in ROOTS if root != self._roots[cfg.key]]]
        for root in dict.fromkeys(roots):
            payload = market._http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=8.0)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self._roots[cfg.key] = root
                return value
        return None

    def _snapshot(
        self,
        event: dict[str, Any],
        fs: dict[str, Any],
        reversed_order: bool,
        match_score: float,
        cfg: SportConfig,
    ) -> dict[str, Any] | None:
        event_id = str(event.get("I") or "")
        game = self._game(event_id, cfg) or event
        total = _balanced_total(game)
        if total is None:
            return None
        now = time.time()
        scheduled = float(fs.get("scheduled_start_ts") or _event_start(event) or 0.0)
        if not scheduled or scheduled <= now + _float_env("GOOL_MULTISPORT_PREMATCH_MIN_LEAD_SECONDS", 120.0):
            return None
        return {
            "ts": now,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "phase": "PREMATCH",
            "sport": cfg.key,
            "event_id": event_id,
            "flashscore_event_id": str(fs.get("flashscore_event_id") or ""),
            "home": str(fs.get("home") or "?"),
            "away": str(fs.get("away") or "?"),
            "league": str(fs.get("league") or game.get("LE") or game.get("L") or ""),
            "scheduled_start_ts": scheduled,
            "line": float(total["line"]),
            "over": float(total["over"]),
            "under": float(total["under"]),
            "probability": float(total["probability"]),
            "metric": _metric(total, (0, 0), cfg),
            "flashscore_match_score": round(float(match_score), 4),
            "reversed_provider_order": bool(reversed_order),
        }

    def _message(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> str:
        direction = str(signal.get("direction") or "over")
        selection = ("ТБ" if direction == "over" else "ТМ") + f" {float(signal.get('line') or 0):g}"
        start_ts = float(row.get("scheduled_start_ts") or 0)
        start = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%d.%m %H:%M UTC") if start_ts else "—"
        return (
            f"{cfg.icon} <b>GOOL MULTI · PREMATCH · {cfg.title}</b>\n"
            f"<b>{row.get('home','?')} — {row.get('away','?')}</b>\n"
            f"🏆 {row.get('league') or 'PREMATCH'} · 🕐 {start}\n"
            f"🎯 <b>{selection} @ {float(signal.get('odd') or 0):.2f}</b>\n"
            f"📉 линия {float(signal.get('opening_line') or 0):g} → {float(signal.get('line') or 0):g}\n"
            f"🧠 сила {float(signal.get('strength') or 0):.0f}/100 · Δp {float(signal.get('probability_delta_pp') or 0):+.1f} п.п."
        )

    def _record_signal(self, row: dict[str, Any], signal: dict[str, Any], cfg: SportConfig) -> tuple[bool, int]:
        direction = str(signal.get("direction") or "over")
        selection = ("ТБ" if direction == "over" else "ТМ") + f" {float(signal.get('line') or 0):g}"
        mode = _mode()
        entry = {
            "entry_id": f"{cfg.key}:PREMATCH:{row.get('event_id')}:match_total",
            "journal_version": 2,
            "phase": "PREMATCH",
            "signal_type": "prematch_total_movement",
            "market_family": "match_total",
            "selection": selection,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sport": cfg.key,
            "event_id": row.get("event_id"),
            "flashscore_event_id": row.get("flashscore_event_id"),
            "home": row.get("home"),
            "away": row.get("away"),
            "league": row.get("league"),
            "scheduled_start_ts": row.get("scheduled_start_ts"),
            "direction": direction,
            "line": float(signal.get("line") or 0.0),
            "odd": float(signal.get("odd") or 0.0),
            "opening_line": float(signal.get("opening_line") or 0.0),
            "opening_odd": float(signal.get("opening_odd") or 0.0),
            "fair_probability": float(signal.get("fair_probability") or 0.0),
            "metric_delta": float(signal.get("metric_delta") or 0.0),
            "probability_delta_pp": float(signal.get("probability_delta_pp") or 0.0),
            "line_delta": float(signal.get("line_delta") or 0.0),
            "moves": int(signal.get("moves") or 0),
            "strength": float(signal.get("strength") or 0.0),
            "extreme": bool(signal.get("extreme")),
            "mapping_score": float(row.get("flashscore_match_score") or 0.0),
            "result": "pending",
            "profit_units": 0.0,
            "mode": mode,
            "telegram_sent": False,
        }
        if not append_unique(self.journal_path, entry):
            return False, 0
        sent = 0
        if mode == "active" and os.getenv("GOOL_MULTISPORT_PREMATCH_DELIVER", "1").strip().casefold() not in {"0", "false", "off", "no"}:
            try:
                png = render_multisport_signal_card(row, signal, cfg, phase="PREMATCH")
                sent = int(telegram.broadcast_photo(png, caption=self._message(row, signal, cfg)) or 0)
            except Exception as exc:
                print(f"GOOL_{cfg.key.upper()}_PREMATCH_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
                sent = int(telegram.broadcast(self._message(row, signal, cfg)) or 0)
        print(
            f"GOOL_MULTISPORT_PREMATCH_SIGNAL sport={cfg.key} match={row.get('home')}--{row.get('away')} "
            f"selection={selection} odd={float(signal.get('odd') or 0):.2f} strength={float(signal.get('strength') or 0):.0f} mode={mode}",
            flush=True,
        )
        return True, sent

    def _scan(self, cfg: SportConfig) -> dict[str, Any]:
        fs = self._flashscore_scheduled(cfg)
        xbet = self._index(cfg)
        mapped = map_xbet_to_flashscore(xbet, fs)[:max(1, _int_env("GOOL_MULTISPORT_PREMATCH_MAX_MAPPED", 160))]
        decoded = detected = delivered = 0
        latest: list[dict[str, Any]] = []
        workers = max(2, min(16, _int_env("GOOL_MULTISPORT_PREMATCH_WORKERS", 8)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._snapshot, event, frow, reversed_order, score, cfg) for event, frow, reversed_order, score in mapped]
            for future in as_completed(futures):
                try:
                    row = future.result(timeout=18)
                except Exception:
                    row = None
                if row is None:
                    continue
                decoded += 1
                key = f"{cfg.key}:{row['event_id']}"
                self._history[key].append(dict(row))
                signal = detect_prematch_signal(list(self._history[key]), cfg, now=float(row["ts"]))
                if signal is not None:
                    recorded, sent = self._record_signal(row, signal, cfg)
                    detected += int(recorded)
                    delivered += int(sent > 0)
                    row["signal"] = signal
                latest.append(row)
        return {
            "enabled": True,
            "flashscore_scheduled": len(fs),
            "xbet_prematch": len(xbet),
            "mapped": len(mapped),
            "decoded": decoded,
            "detected": detected,
            "delivered": delivered,
            "matches": latest[:100],
        }

    def collect_once(self) -> dict[str, Any]:
        started = time.time()
        sports: dict[str, Any] = {}
        for key, cfg in SPORTS.items():
            if not _sport_enabled(key):
                sports[key] = {"enabled": False}
                continue
            sports[key] = self._scan(cfg)
            row = sports[key]
            print(
                f"GOOL_{key.upper()}_PREMATCH fs={row['flashscore_scheduled']} xbet={row['xbet_prematch']} "
                f"mapped={row['mapped']} decoded={row['decoded']} signals={row['detected']} delivered={row['delivered']}",
                flush=True,
            )
        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": int((time.time() - started) * 1000),
            "mode": _mode(),
            "phase": "PREMATCH",
            "sports": sports,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(self.state_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        trim_file_tail(self.history_path, max(1024 * 1024, _int_env("GOOL_MULTISPORT_PREMATCH_HISTORY_KEEP_BYTES", 8 * 1024 * 1024)))
        return state

    def run(self, interval: float = 60.0) -> None:
        interval = max(30.0, float(interval))
        print(f"GOOL_MULTISPORT_PREMATCH started interval={interval:g}s mode={_mode()}", flush=True)
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.collect_once()
            except Exception as exc:
                print(f"GOOL_MULTISPORT_PREMATCH_ERROR {type(exc).__name__}:{exc}", flush=True)
            self._stop.wait(max(1.0, interval - (time.monotonic() - started)))


def main() -> None:
    parser = argparse.ArgumentParser(description="GOOL multisport prematch worker")
    parser.add_argument("--interval", type=float, default=_float_env("GOOL_MULTISPORT_PREMATCH_INTERVAL_SECONDS", 60.0))
    args = parser.parse_args()
    worker = MultiSportPrematchWorker()
    try:
        worker.run(args.interval)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
