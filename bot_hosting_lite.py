"""GOOL Lite staging launcher for a single small bot-hosting container.

Stage 1: hockey + basketball PREMATCH/LIVE, lightweight Telegram menu.
No training, browsers, exchange workers, or football trained ensemble.
The default is SHADOW: real feed processing, no unsolicited bet messages.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Set before importing gool_bot2: __init__ used to eagerly import the football
# ensemble/pandas/sklearn even for hockey and basketball-only workers.
DEFAULTS = {
    "GOOL_FOOTBALL_AUTOINSTALL": "0",
    "GOOL_BROWSER_ENABLE": "0",
    "GOOL_MATCHBOOK_ENABLED": "0",
    "GOOL_MULTISPORT_MODE": "shadow",
    "GOOL_HOCKEY_ENABLED": "1",
    "GOOL_BASKETBALL_ENABLED": "1",
    "GOOL_MULTISPORT_PREMATCH_ENABLED": "1",
    "GOOL_DAILY_ALL_MARKETS_ENABLED": "0",
    "GOOL_DAILY_MARKET_EMBEDDED_ENABLED": "0",
    "GOOL_GLOBAL_SUPER10_ENABLED": "0",  # stage 1 has no football
    "GOOL_MULTISPORT_INTERVAL_SECONDS": "90",
    "GOOL_MULTISPORT_PREMATCH_FS_BRAIN_SCAN_MAX": "30",
    "GOOL_MULTISPORT_PREMATCH_PRICE_MAX_PER_SPORT": "12",
    "GOOL_MULTISPORT_PREMATCH_MAX_MAPPED_PER_SPORT": "24",
    "GOOL_MULTISPORT_PREMATCH_BATCH_SIZE": "12",
    "GOOL_MULTISPORT_PREMATCH_GAME_WORKERS": "2",
    "GOOL_MULTISPORT_PREMATCH_FS_BRAIN_WORKERS": "2",
    "XBET_MULTISPORT_MAX_MAPPED_PER_SPORT": "24",
    "XBET_MULTISPORT_GAME_WORKERS": "2",
    "GOOL_MULTISPORT_INDEX_PREFETCH_WORKERS": "1",
    "GOOL_BASKETBALL_HISTORY_FETCH_WORKERS": "2",
    "GOOL_DAILY_ODDS_RETENTION_DAYS": "1",
    "GOOL_DAILY_ODDS_MAX_MB": "64",
    "XBET_MULTISPORT_HISTORY_KEEP_BYTES": str(1024 * 1024),
}
for key, value in DEFAULTS.items():
    os.environ.setdefault(key, value)


def _usage() -> dict[str, int]:
    # On Linux, ru_maxrss is peak resident KiB, *not* bytes.
    return {"peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}


def _settings() -> tuple[Path, Path]:
    data = Path(os.environ.get("RUNTIME_DATA_DIR", str(ROOT / "data-lite")))
    os.environ["RUNTIME_DATA_DIR"] = str(data)
    data.mkdir(parents=True, exist_ok=True)
    state = data / "live"
    state.mkdir(parents=True, exist_ok=True)
    return data, state


def _telegram_loop(state: Path, interval: int = 15):
    """Poll only Lite-supported commands. No legacy football model imported."""
    from gool_bot2 import telegram
    from gool_bot2 import multisport_menu

    offset_path = state / "telegram_offset.txt"
    try:
        offset = int(offset_path.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        offset = 0
    last_poll = 0.0

    def poll() -> None:
        nonlocal offset, last_poll
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            return
        if time.monotonic() - last_poll < interval:
            return
        last_poll = time.monotonic()
        response = telegram._api_call(
            "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message"]}, timeout=7
        )
        if not isinstance(response, dict) or not response.get("ok"):
            return
        for update in response.get("result") or []:
            if not isinstance(update, dict):
                continue
            update_id = int(update.get("update_id") or 0)
            offset = max(offset, update_id + 1)
            # Persist before dispatch so a restart cannot duplicate a command.
            offset_path.write_text(str(offset), "utf-8")
            message = update.get("message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id is None:
                continue
            raw = str(message.get("text") or "").strip()
            cmd = raw.split(maxsplit=1)[0].split("@", 1)[0].lower() if raw else ""
            try:
                if cmd == "/start":
                    telegram.subscribe(chat_id)
                    response_text = (
                        "🟢 GOOL Lite — тестовый запуск\\n"
                        "🏒 Хоккей и 🏀 баскетбол: PREMATCH + LIVE\\n"
                        "Режим: " + os.getenv("GOOL_MULTISPORT_MODE", "shadow").upper() +
                        "\\nКоманды: /status /hockeyjournal /basketjournal /hockeyprematch /basketprematch /stop"
                    )
                elif cmd == "/stop":
                    telegram.unsubscribe(chat_id)
                    response_text = "🔕 Автоматические уведомления отключены."
                elif cmd == "/status":
                    s = {}
                    try:
                        s = json.loads((state / "gool_multisport_state.json").read_text("utf-8"))
                    except (FileNotFoundError, ValueError):
                        pass
                    sports = s.get("sports") or {}
                    response_text = (
                        "🏟 GOOL Lite\\n"
                        f"Режим: {os.getenv('GOOL_MULTISPORT_MODE', 'shadow').upper()}\\n"
                        f"Последний анализ: {s.get('captured_at') or 'ещё не выполнялся'}\\n"
                        f"Хоккей: {'OK' if sports.get('hockey') else 'ожидает'}\\n"
                        f"Баскетбол: {'OK' if sports.get('basketball') else 'ожидает'}"
                    )
                elif cmd in ("/hockeyjournal", "/basketjournal"):
                    response_text = multisport_menu.sport_journal_text(
                        "hockey" if cmd == "/hockeyjournal" else "basketball"
                    )
                elif cmd in ("/hockeyprematch", "/basketprematch"):
                    sport = "hockey" if cmd == "/hockeyprematch" else "basketball"
                    sections = multisport_menu.sport_prematch_picks_sections(sport)
                    response_text = "\\n\\n".join(sections[:1]) if sections else "Ставок пока нет"
                else:
                    response_text = "Команды GOOL Lite: /start /status /hockeyjournal /basketjournal /hockeyprematch /basketprematch /stop"
                telegram.send_message(chat_id, str(response_text)[:3800])
            except Exception as exc:
                print(f"GOOL_LITE_TELEGRAM_ERROR command={cmd} error={type(exc).__name__}:{exc}", flush=True)

    return poll


def _smoke() -> int:
    # Ensure no secrets, network calls, or Telegram pushes in the CI smoke.
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["GOOL_MULTISPORT_MODE"] = "shadow"
    with tempfile.TemporaryDirectory(prefix="gool-lite-smoke-") as temp:
        os.environ["RUNTIME_DATA_DIR"] = temp
        from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker
        worker = MultiSportSteamWorker(Path(temp))
        assert worker.state_path.parent == Path(temp) / "live"
        poll = _telegram_loop(Path(temp) / "live")
        poll()  # token deliberately absent; no API request
        worker.stop()
        metrics = _usage()
        metrics["smoke"] = "passed"
        metrics["mode"] = "shadow"
        print("GOOL_LITE_SMOKE " + json.dumps(metrics), flush=True)
        if metrics["peak_rss_mib"] > 210:
            print("GOOL_LITE_SMOKE memory_headroom_warning=above_210_MiB", flush=True)
            return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--interval", type=int, default=90)
    args = parser.parse_args()
    if args.smoke:
        return _smoke()

    _, state = _settings()
    if os.getenv("GOOL_MULTISPORT_MODE", "shadow").strip().lower() == "active":
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            raise RuntimeError("Active GOOL Lite needs TELEGRAM_BOT_TOKEN from hosting environment")
        if not os.getenv("TELEGRAM_CHAT_ID"):
            raise RuntimeError("Active GOOL Lite needs TELEGRAM_CHAT_ID")
    from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker

    worker = MultiSportSteamWorker(Path(os.environ["RUNTIME_DATA_DIR"]))
    poll = _telegram_loop(state)
    stop = False

    def stop_handler(*_):
        nonlocal stop
        stop = True
        worker.stop()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    print("GOOL_LITE_STAGE1_STARTED " + json.dumps({
        "mode": os.getenv("GOOL_MULTISPORT_MODE", "shadow"),
        "sports": ["hockey", "basketball"],
        "interval": args.interval,
        "data_dir": str(state.parent),
        **_usage(),
    }), flush=True)
    interval = max(45, args.interval)
    while not stop:
        started = time.monotonic()
        try:
            worker.collect_once()
        except Exception as exc:
            print(f"GOOL_LITE_COLLECT_ERROR {type(exc).__name__}:{exc}", flush=True)
        try:
            poll()
        except Exception as exc:
            print(f"GOOL_LITE_POLL_ERROR {type(exc).__name__}:{exc}", flush=True)
        print("GOOL_LITE_HEALTH " + json.dumps(_usage()), flush=True)
        while not stop and time.monotonic() - started < interval:
            time.sleep(min(2, max(0.1, interval - (time.monotonic() - started))))
            try:
                poll()
            except Exception as exc:
                print(f"GOOL_LITE_POLL_ERROR {type(exc).__name__}:{exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
