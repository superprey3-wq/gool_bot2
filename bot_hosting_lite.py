"""GOOL Lite staging launcher for a single small bot-hosting container.

Stage 2: three-sport analysis in one process, lightweight Telegram menus.
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
    "GOOL_GLOBAL_SUPER10_ENABLED": "1",  # dry-run SUPER 10 in shadow mode
    "GOOL_MULTISPORT_INTERVAL_SECONDS": "90",
    "GOOL_LITE_FOOTBALL_ENABLED": "1",
    "GOOL_LITE_FOOTBALL_PREMATCH_MAX": "3",
    "GOOL_LITE_FOOTBALL_LIVE_MAX": "3",
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
    from gool_bot2 import global_super10

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
                        "⚽ Футбол: PREMATCH и LIVE-анализ\\n"
                        "🏒 Хоккей и 🏀 баскетбол: PREMATCH + LIVE\\n"
                        "Режим: " + os.getenv("GOOL_MULTISPORT_MODE", "shadow").upper() +
                        "\\nКоманды: /status /football /super10 /dayreport /parlays /hockeyjournal /basketjournal /stop"
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
                    f = {}
                    try:
                        f = json.loads((state / "gool_lite_football_state.json").read_text("utf-8"))
                    except (OSError, ValueError):
                        pass
                    response_text = (
                        "🏟 GOOL Lite\\n"
                        f"Режим: {os.getenv('GOOL_MULTISPORT_MODE', 'shadow').upper()}\\n"
                        f"Последний анализ: {s.get('captured_at') or 'ещё не выполнялся'}\\n"
                        f"Футбол: {'анализ есть' if f.get('captured_at') else 'ожидает'} "
                        f"(отправка ставок пока отключена)\\n"
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
                elif cmd in ("/football", "/footballprematch", "/footballlive"):
                    f = {}
                    try:
                        f = json.loads((state / "gool_lite_football_state.json").read_text("utf-8"))
                    except (OSError, ValueError):
                        pass
                    pre = f.get("prematch") or {}
                    live = f.get("live") or {}
                    response_text = (
                        "⚽ GOOL Lite · футбол\\n"
                        f"PREMATCH: проверено {pre.get('analysed', 0)}, сценариев {pre.get('scenario_matches', 0)}, "
                        f"с реальными ценами {pre.get('priced', 0)}\\n"
                        f"LIVE: матчей {live.get('live_total', 0)}, проверено {live.get('analysed', 0)}\\n"
                        "⚠️ Автоотправка футбольных ставок ещё не проверена и отключена."
                    )
                    if cmd == "/footballprematch":
                        for r in (f.get("prematch_candidates") or [])[:3]:
                            response_text += (f"\\n{r.get('home')} — {r.get('away')}: "
                                              f"{r.get('selection')} @{r.get('odd')} "
                                              f"[{r.get('bookmaker')}]")
                    elif cmd == "/footballlive":
                        for r in (live.get("decisions") or [])[:5]:
                            response_text += (f"\\n{r.get('match')} {r.get('minute')}′ "
                                              f"{r.get('market')} {r.get('decision')} "
                                              "(без котировки)")
                elif cmd in ("/super10", "/super"):
                    response_text = multisport_menu.super10_text()
                elif cmd in ("/parlays", "/express"):
                    response_text = (multisport_menu.sport_parlay_text("hockey") + "\\n" +
                                     multisport_menu.sport_parlay_text("basketball"))
                elif cmd in ("/dayreport", "/fullreport"):
                    from gool_bot2.full_report_export import build_daily_report
                    filename, data, mime = build_daily_report(
                        football_path=state / "gool_lite_football_journal.json"
                    )
                    # Do not send the same document as HTML text below.
                    ok = telegram.send_document(chat_id, filename, data,
                                                caption="📊 GOOL Lite · отчёт за день",
                                                content_type=mime)
                    if not ok:
                        telegram.send_message(chat_id, "⚠️ Не удалось отправить отчёт; проверь логи.")
                    continue
                else:
                    response_text = "Команды GOOL Lite: /start /status /football /footballlive /footballprematch /super10 /dayreport /parlays /hockeyjournal /basketjournal /stop"
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
        from gool_bot2.lite_football import LiteFootballWorker
        worker = MultiSportSteamWorker(Path(temp))
        football = LiteFootballWorker(Path(temp))
        assert football.state_path.parent == Path(temp) / "live"
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
    from gool_bot2.lite_football import LiteFootballWorker

    worker = MultiSportSteamWorker(Path(os.environ["RUNTIME_DATA_DIR"]))
    football = LiteFootballWorker(Path(os.environ["RUNTIME_DATA_DIR"])) if os.getenv("GOOL_LITE_FOOTBALL_ENABLED", "1") == "1" else None
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
        "sports": ["football", "hockey", "basketball"],
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
            if football is not None:
                football.collect_once()
        except Exception as exc:
            print(f"GOOL_LITE_FOOTBALL_ERROR {type(exc).__name__}:{exc}", flush=True)
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
