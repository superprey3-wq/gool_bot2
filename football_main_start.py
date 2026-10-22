"""Start original GOOL's football pipeline directly from the MAIN checkout.

This is NOT GOOL Lite. Workers below are the unchanged production modules
from main. Unrelated sports/services are disabled through environment only.

Commands:
    python football_main_start.py --plan       # no credentials/network/files
    python football_main_start.py --check      # validate real hosting prerequisites
    python football_main_start.py --run        # opt-in original send-capable GOOL

Never run it beside Lite with the same Telegram token: getUpdates would compete.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_FILES = {
    "ARCHIVE_FOUNDATION_MODEL": "archive_foundation.pkl",
    "ARCHIVE_HAZARD_MODEL": "archive_hazard.pkl",
    "FOOTBALL_DATA_GOAL_MODEL": "football_data_goal_models.pkl",
}
# All modules and their algorithms below are FROM MAIN, not reimplementations.
BASE_COMMANDS = {
    "live": ("gool_bot2.storage_live_collector", "--interval", "90"),
    "xbet": ("gool_bot2.xbet_market_worker", "--interval", "45"),
    "brain": ("gool_bot2.storage_market_signal_worker_var",),
    "prematch": ("gool_bot2.v4_prematch_daemon",),
}
DEFAULTS = {
    "GOOL_BROWSER_ENABLE": "0",
    "GOOL_MATCHBOOK_ENABLED": "0",
    "GOOL_HOCKEY_ENABLED": "0",
    "GOOL_BASKETBALL_ENABLED": "0",
    "GOOL_MULTISPORT_ENABLED": "0",
    "GOOL_MULTISPORT_EMBEDDED_ENABLED": "0",
    "XBET_MULTISPORT_STEAM_ENABLED": "0",
    "GOOL_MULTISPORT_MODE": "shadow",
    "GOOL_MULTI_TELEGRAM_MODE": "shadow",
    "GOOL_GLOBAL_SUPER10_ENABLED": "0",
    "GOOL_DAILY_MARKET_EMBEDDED_ENABLED": "0",
    "GOOL_FOOTBALL_AUTOINSTALL": "1",
    "GOOL_FOOTBALL_V5_ACTIVE": "1",
    "GOOL_PREMATCH_FULL_MARKET_ACTIVE": "1",
    "GOOL_PREMATCH_DELIVER": "1",
    "GOOL_LIVE_V4_MODE": "active",
    "XBET_MARKET_REQUIRED": "1",
    "XBET_GAME_WORKERS": "2",
    "LIVE_DETAIL_WORKERS": "2",
    "LIVE_HISTORY_WORKERS": "1",
    "LIVE_INTERVAL_SECONDS": "90",
    "XBET_MARKET_INTERVAL_SECONDS": "45",
    "XBET_PREMATCH_INTERVAL_SECONDS": "300",
    "XBET_PREMATCH_FETCH_EVENTS": "15",
    "XBET_PREMATCH_MAX_DUE_PER_CYCLE": "4",
    "XBET_HISTORY_RUNTIME_KEEP_BYTES": str(2 * 1024 * 1024),
    "XBET_MARKET_MEMORY_MAX_BYTES": str(8 * 1024 * 1024),
    "XBET_MARKET_MEMORY_RETENTION_DAYS": "1",
    "STORAGE_CLEANUP_EVERY_CYCLES": "3",
    "GOOL_PREMATCH_INTERVAL_SECONDS": "10800",
}


def _deployment_models() -> dict[str, str | None]:
    root = Path(os.getenv("GOOL_MODEL_DIRECTORY", str(ROOT / "gool_bot2_deploy")))
    out: dict[str, str | None] = {}
    for key, name in MODEL_FILES.items():
        supplied = os.getenv(key) or os.getenv({
            "ARCHIVE_FOUNDATION_MODEL": "ARCHIVE_FOUNDATION_MODEL_PATH",
            "ARCHIVE_HAZARD_MODEL": "ARCHIVE_HAZARD_MODEL_PATH",
            "FOOTBALL_DATA_GOAL_MODEL": "FOOTBALL_DATA_GOAL_MODELS_PATH",
        }[key])
        if supplied:
            file = Path(supplied)
        else:
            # Original monkey_start.find_model searched the entire deploy tree.
            # Keep that behavior; models may be in nested release directories.
            matches = list(root.rglob(name)) if root.is_dir() else []
            file = matches[0] if matches else root / name
        out[key] = str(file.resolve()) if file.is_file() else None
    return out


def fetch_original_release_models() -> dict[str, str | None]:
    """Reuse original Raven model installer, including manifest SHA-256 checks.

    This is the repo's existing raven_start.ensure_models(), not a re-training
    step or an unverified third-party model download.
    """
    import raven_start
    raven_start.MODEL_DIR = Path(os.getenv(
        "GOOL_MODEL_DIRECTORY", str(ROOT / "gool_bot2_deploy" / "models")
    ))
    raven_start.ensure_models()
    result = _deployment_models()
    if not all(result.values()):
        raise RuntimeError("GOOL_RAVEN_MODEL_BUNDLE_INCOMPLETE")
    return result


def make_env() -> dict[str, str]:
    env = os.environ.copy()
    # Hard-off by design: no multisport process, cards, or bookmaker crawler.
    for key, value in DEFAULTS.items():
        if key in {
            "GOOL_BROWSER_ENABLE", "GOOL_MATCHBOOK_ENABLED",
            "GOOL_HOCKEY_ENABLED", "GOOL_BASKETBALL_ENABLED",
            "GOOL_MULTISPORT_ENABLED", "GOOL_MULTISPORT_EMBEDDED_ENABLED",
            "XBET_MULTISPORT_STEAM_ENABLED", "GOOL_MULTISPORT_MODE",
            "GOOL_MULTI_TELEGRAM_MODE", "GOOL_GLOBAL_SUPER10_ENABLED",
            "GOOL_DAILY_MARKET_EMBEDDED_ENABLED", "GOOL_FOOTBALL_AUTOINSTALL"
        }:
            env[key] = value
        else:
            env.setdefault(key, value)
    runtime = Path(env.get("RUNTIME_DATA_DIR", str(ROOT / "gool-football-main-data")))
    env["RUNTIME_DATA_DIR"] = str(runtime)
    env["RAW_LIVE_DIR"] = str(runtime / "raw" / "live")
    env["GOOL_INBOX_DIR"] = env["RAW_LIVE_DIR"]
    env["SIGNAL_JOURNAL"] = str(runtime / "live" / "signal_journal.json")
    env["SIGNAL_JOURNAL_PATH"] = env["SIGNAL_JOURNAL"]
    env["SIGNAL_ANALYSIS_PATH"] = str(runtime / "live" / "gool_analysis.jsonl")
    env["GOOL_MULTI_JOURNAL_PATH"] = str(runtime / "live" / "gool_multi_journal.json")
    env["XBET_MARKET_STATE"] = str(runtime / "live" / "xbet_market_state.json")
    env["XBET_MARKET_HISTORY"] = str(runtime / "live" / "xbet_market_history.jsonl")
    env["XBET_PREMATCH_STATE"] = str(runtime / "live" / "xbet_prematch_state.json")
    env["TELEGRAM_SUBSCRIBERS_FILE"] = str(runtime / "telegram_subscribers.json")
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    for key, value in _deployment_models().items():
        if value:
            env[key] = value
    if env.get("ARCHIVE_FOUNDATION_MODEL"):
        env["ARCHIVE_FOUNDATION_MODEL_PATH"] = env["ARCHIVE_FOUNDATION_MODEL"]
    if env.get("ARCHIVE_HAZARD_MODEL"):
        env["ARCHIVE_HAZARD_MODEL_PATH"] = env["ARCHIVE_HAZARD_MODEL"]
    if env.get("FOOTBALL_DATA_GOAL_MODEL"):
        env["FOOTBALL_DATA_GOAL_MODELS_PATH"] = env["FOOTBALL_DATA_GOAL_MODEL"]
    return env


def commands(env: dict[str, str]) -> dict[str, list[str]]:
    # The original daemon assumes /home/container/scripts/gool_flashscore_today.py.
    # Skip it on other layouts, rather than spawning a restart loop.
    cmd = {name: [sys.executable, "-m", *args] for name, args in BASE_COMMANDS.items()}
    cmd["live"][-1] = env["LIVE_INTERVAL_SECONDS"]
    cmd["xbet"][-1] = env["XBET_MARKET_INTERVAL_SECONDS"]
    if not (ROOT / "scripts" / "gool_flashscore_today.py").is_file() or str(ROOT) != "/home/container":
        cmd.pop("prematch")
    return cmd


def checks(env: dict[str, str]) -> dict[str, object]:
    models = _deployment_models()
    required = ("numpy", "pandas", "sklearn", "pydantic", "yaml", "PIL", "websockets")
    packages = {name: importlib.util.find_spec(name) is not None for name in required}
    # Original signal-worker models are published in Raven release assets,
    # but are not tracked in the source Git tree.
    main_present = (ROOT / "src" / "gool_bot2" / "storage_market_signal_worker_var.py").is_file()
    telegram_ok = bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"))
    missing = [name for name, path in models.items() if not path]
    missing += [name for name, available in packages.items() if not available]
    if not main_present:
        missing.append("main_original_worker")
    if not telegram_ok:
        missing.append("telegram_configuration")
    return {
        "original_main": main_present,
        "models_found": {name: bool(path) for name, path in models.items()},
        "packages_found": packages,
        "telegram_configured": telegram_ok,
        "prematch_layout_supported": "prematch" in commands(env),
        "ready": not missing,
        "missing": missing,
    }


def plan() -> dict[str, object]:
    env = make_env()
    return {
        "branch": "main",
        "implementation": "UNCHANGED_ORIGINAL_MAIN_WORKERS",
        "commands": {k: v[2] for k, v in commands(env).items()},
        "disabled": ["hockey", "basketball", "multisport", "chromium", "matchbook", "super10"],
        "safe_default": "PLAN_ONLY",
    }


def run() -> None:
    if os.getenv("GOOL_MAIN_FOOTBALL_ALLOW_SEND") != "1":
        raise SystemExit(
            "REFUSED: original main broadcasts live bets; set GOOL_MAIN_FOOTBALL_ALLOW_SEND=1 "
            "only after validating real data, models and hosting resources"
        )
    env = make_env()
    state = checks(env)
    missing = [
        k for k, available in state["models_found"].items() if not available
    ]
    missing += [
        k for k, available in state["packages_found"].items() if not available
    ]
    if not state["telegram_configured"]:
        missing.append("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
    if not state["original_main"]:
        missing.append("main checkout")
    if missing:
        raise SystemExit("MAIN_FOOTBALL_PREFLIGHT_FAILED missing=" + ",".join(missing))
    run_commands = commands(env)
    runtime = Path(env["RUNTIME_DATA_DIR"])
    (runtime / "raw" / "live").mkdir(parents=True, exist_ok=True)
    (runtime / "live").mkdir(parents=True, exist_ok=True)
    children: dict[str, subprocess.Popen] = {}
    stopping = False

    def shutdown(*_args: object) -> None:
        nonlocal stopping
        stopping = True
        for child in children.values():
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print("GOOL_ORIGINAL_MAIN_FOOTBALL_START workers=" + ",".join(run_commands), flush=True)
    try:
        while not stopping:
            for name, cmd in run_commands.items():
                child = children.get(name)
                if child is None:
                    children[name] = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
                    print(f"GOOL_MAIN_FOOTBALL_CHILD name={name} pid={children[name].pid}", flush=True)
                elif child.poll() is not None:
                    print(f"GOOL_MAIN_FOOTBALL_EXIT name={name} rc={child.returncode}", flush=True)
                    raise SystemExit(f"original {name} worker failed: refusing infinite restarts")
            time.sleep(3)
    finally:
        shutdown()
        for child in children.values():
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="show original MAIN modules; zero side effects")
    action.add_argument("--check", action="store_true", help="check models and required packages")
    action.add_argument("--fetch-models", action="store_true",
                        help="download SHA256-checked original GOOL Raven release models")
    action.add_argument("--run", action="store_true", help="launch ORIGINAL MAIN; explicit approval required")
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, sort_keys=True))
    elif args.check:
        state = checks(make_env())
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    elif args.fetch_models:
        models = fetch_original_release_models()
        print("GOOL_MAIN_MODELS_READY " + ",".join(models), flush=True)
    else:
        run()


if __name__ == "__main__":
    main()
