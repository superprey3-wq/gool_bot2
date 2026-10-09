"""Durable, bookmaker-first market history, independent of PREMATCH signal limits.

One 1xBet quote is identified by raw (sport, event, scope, G, GS, T, P).
This intentionally includes markets the betting brain cannot yet price.
A full feed response is not guaranteed for every fixture; coverage reports
distinguish discovered, mapped, and actually archived matches.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def match_day(start_ts: float) -> str:
    return datetime.fromtimestamp(start_ts, _MSK).strftime("%Y-%m-%d")


def select_due_events(
    mapped: list[tuple[dict[str, Any], dict[str, Any], bool, float]],
    last_seen: dict[str, float],
    *,
    now: float,
    limit: int,
    refresh_seconds: float,
) -> list[tuple[dict[str, Any], dict[str, Any], bool, float]]:
    """New fixtures first, then oldest refreshed. No nearest-N truncation."""
    due = []
    for event, fs, reversed_order, quality in mapped:
        event_id = str(event.get("I") or "")
        start_ts = _number(fs.get("start_ts")) or 0
        if not event_id or start_ts <= now:
            continue
        last = float(last_seen.get(event_id) or 0)
        if last and now - last < refresh_seconds:
            continue
        due.append((0 if not last else 1, last, start_ts, event_id, (event, fs, reversed_order, quality)))
    due.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [entry[-1] for entry in due[:max(0, int(limit))]]


class DailyOddsArchive:
    """SQLite WAL: opening/current prices and an append-only change/heartbeat log."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=20)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=20000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS fixtures (
                sport TEXT NOT NULL,
                xbet_id TEXT NOT NULL,
                fs_id TEXT NOT NULL,
                day TEXT NOT NULL,
                start_ts REAL NOT NULL,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                last_scanned REAL NOT NULL,
                quote_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (sport, xbet_id)
            );
            CREATE INDEX IF NOT EXISTS fixtures_day ON fixtures(day,sport);
            CREATE TABLE IF NOT EXISTS current_prices (
                sport TEXT NOT NULL,
                xbet_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                market_key TEXT NOT NULL,
                first_ts REAL NOT NULL,
                first_odd REAL NOT NULL,
                last_ts REAL NOT NULL,
                odd REAL NOT NULL,
                blocked INTEGER NOT NULL,
                PRIMARY KEY (sport,xbet_id,scope,market_key)
            );
            CREATE TABLE IF NOT EXISTS price_history (
                sport TEXT NOT NULL,
                xbet_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                market_key TEXT NOT NULL,
                ts REAL NOT NULL,
                odd REAL NOT NULL,
                blocked INTEGER NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS price_history_match
                ON price_history(sport,xbet_id,scope,market_key,ts);
        """)

    def last_seen(self, sport: str, event_ids: list[str]) -> dict[str, float]:
        if not event_ids:
            return {}
        result: dict[str, float] = {}
        for i in range(0, len(event_ids), 400):
            sub = event_ids[i:i + 400]
            marks = ",".join("?" for _ in sub)
            for event_id, ts in self.db.execute(
                f"SELECT xbet_id,last_scanned FROM fixtures WHERE sport=? AND xbet_id IN ({marks})",
                [sport, *sub],
            ):
                result[str(event_id)] = float(ts)
        return result

    @staticmethod
    def _quotes(decoded_scopes: dict[str, dict[str, Any]]):
        """Retain every priced raw G/GS/T/P returned by 1xBet, including unknown groups."""
        for scope, decoded in decoded_scopes.items():
            for raw in decoded.get("raw") or []:
                try:
                    g, t = int(raw["G"]), int(raw["T"])
                    gs = raw.get("GS")
                    p = raw.get("P")
                    odd = float(raw["C"])
                    if odd <= 1.0:
                        continue
                    key = f"{g}:{'' if gs is None else int(gs)}:{t}:{'' if p is None else float(p):g}" if p is not None else f"{g}:{'' if gs is None else int(gs)}:{t}:"
                except (KeyError, TypeError, ValueError):
                    continue
                yield str(scope), key, odd, int(bool(raw.get("blocked")))

    def record(
        self,
        *,
        sport: str,
        event_id: str,
        fs: dict[str, Any],
        decoded: dict[str, dict[str, Any]],
        ts: float | None = None,
        heartbeat_seconds: float = 1800,
    ) -> dict[str, int]:
        now = float(time.time() if ts is None else ts)
        start = float(fs.get("start_ts") or 0)
        if not event_id or start <= now:
            return {"quotes": 0, "changes": 0}
        quotes = list(dict.fromkeys(self._quotes(decoded)))
        # Never mark an empty/failed market response as successfully scanned.
        if not quotes:
            return {"quotes": 0, "changes": 0}
        changed = 0
        with self.db:
            self.db.execute(
                """INSERT INTO fixtures (sport,xbet_id,fs_id,day,start_ts,home,away,last_scanned,quote_count)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(sport,xbet_id) DO UPDATE SET
                   fs_id=excluded.fs_id,day=excluded.day,start_ts=excluded.start_ts,
                   home=excluded.home,away=excluded.away,
                   last_scanned=excluded.last_scanned,quote_count=excluded.quote_count""",
                (sport, event_id, str(fs.get("flashscore_event_id") or ""), match_day(start),
                 start, str(fs.get("home") or ""), str(fs.get("away") or ""), now, len(quotes)),
            )
            for scope, key, odd, blocked in quotes:
                old = self.db.execute(
                    """SELECT first_ts,first_odd,last_ts,odd,blocked FROM current_prices
                       WHERE sport=? AND xbet_id=? AND scope=? AND market_key=?""",
                    (sport, event_id, scope, key),
                ).fetchone()
                reason = ("open" if old is None else
                          "change" if (abs(old[3] - odd) > 0.00001 or old[4] != blocked) else
                          "heartbeat" if now - old[2] >= heartbeat_seconds else None)
                if old is None:
                    self.db.execute(
                        "INSERT INTO current_prices VALUES (?,?,?,?,?,?,?,?,?)",
                        (sport, event_id, scope, key, now, odd, now, odd, blocked),
                    )
                else:
                    self.db.execute(
                        """UPDATE current_prices SET last_ts=?,odd=?,blocked=?
                           WHERE sport=? AND xbet_id=? AND scope=? AND market_key=?""",
                        (now, odd, blocked, sport, event_id, scope, key),
                    )
                if reason:
                    self.db.execute(
                        "INSERT INTO price_history VALUES (?,?,?,?,?,?,?,?)",
                        (sport, event_id, scope, key, now, odd, blocked, reason),
                    )
                    changed += int(reason == "change")
        return {"quotes": len(quotes), "changes": changed}

    def coverage(self, sport: str, day: str) -> dict[str, int]:
        matched, markets = self.db.execute(
            """SELECT COUNT(*),COALESCE(SUM(quote_count),0)
               FROM fixtures WHERE sport=? AND day=?""",
            (sport, day),
        ).fetchone()
        return {"archived_matches": matched, "latest_quotes": markets}

    def prune(self, retention_days: int = 14, *, now: float | None = None) -> None:
        cutoff = float(time.time() if now is None else now) - max(1, retention_days) * 86400
        with self.db:
            self.db.execute("DELETE FROM price_history WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM current_prices WHERE (sport,xbet_id) IN (SELECT sport,xbet_id FROM fixtures WHERE start_ts < ?)", (cutoff,))
            self.db.execute("DELETE FROM fixtures WHERE start_ts < ?", (cutoff,))

    def close(self) -> None:
        self.db.close()
