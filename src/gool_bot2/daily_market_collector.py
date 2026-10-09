"""Separate low-priority bookmaker crawler for ALL hockey/basketball fixtures.

Intentionally does not run LIVE models, mutate signal journals or send Telegram.
This process can lag or fail without delaying GOOL's 20-second LIVE worker.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .xbet_multisport_steam import MultiSportSteamWorker, SPORTS, _sport_enabled


def run(interval: float = 40.0) -> None:
    interval = max(10.0, float(interval))
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    state_path = Path(os.getenv(
        "GOOL_DAILY_MARKET_STATE",
        str(runtime / "live" / "daily_market_archive_state.json"),
    ))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    worker = MultiSportSteamWorker(runtime)
    while True:
        started = time.monotonic()
        sports = {}
        for key, cfg in SPORTS.items():
            if not _sport_enabled(key):
                sports[key] = {"enabled": False}
                continue
            try:
                fs_today = worker._flashscore_today(cfg)
                has_future = any(
                    str(row.get("coarse_status") or "") == "1" and
                    float(row.get("start_ts") or 0) > time.time()
                    for row in fs_today
                )
                book = worker._xbet_prematch_index(cfg) if has_future else []
                sports[key] = worker._archive_all_day_markets(cfg, fs_today, book)
            except Exception as exc:
                sports[key] = {"enabled": True, "error": f"{type(exc).__name__}:{exc}"}
                print(f"GOOL_DAILY_MARKET_ERROR sport={key} error={type(exc).__name__}:{exc}", flush=True)
        state = {"captured_at": datetime.now(timezone.utc).isoformat(), "sports": sports}
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp.replace(state_path)
        print(
            "GOOL_DAILY_MARKET "
            + " ".join(
                f"{key}:matched={row.get('xbet_matched', 0)}/archived={row.get('archived_matches', 0)}"
                f"/quotes={row.get('latest_quotes', 0)}/error={row.get('error', '')}"
                for key, row in sports.items()
            ),
            flush=True,
        )
        time.sleep(max(1.0, interval - (time.monotonic() - started)))


def main() -> None:
    parser = argparse.ArgumentParser(description="GOOL all-day hockey/basketball odds archive")
    parser.add_argument(
        "--interval", type=float,
        default=float(os.getenv("GOOL_DAILY_MARKET_INTERVAL_SECONDS", "40")),
    )
    args = parser.parse_args()
    run(args.interval)


if __name__ == "__main__":
    main()
