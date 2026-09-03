from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/container")
ENV_FILE = ROOT / "gool.env"
DEPLOY_ROOT = ROOT / "gool_bot2_deploy"
RUNTIME_ROOT = ROOT / "gool_bot2_data"
PIP_TMP = ROOT / ".pip-tmp"


def load_env(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"missing_env={path}")
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def ensure_deps() -> None:
    packages = {
        "numpy": "numpy>=1.26",
        "pandas": "pandas>=2.2",
        "sklearn": "scikit-learn>=1.5",
        "pydantic": "pydantic>=2.8",
        "yaml": "pyyaml>=6.0",
        "PIL": "pillow>=10.0",
    }
    missing = [pkg for module, pkg in packages.items() if importlib.util.find_spec(module) is None]
    if not missing:
        print("GOOL_BOOT dependencies=ok", flush=True)
        return
    PIP_TMP.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"TMPDIR": str(PIP_TMP), "TMP": str(PIP_TMP), "TEMP": str(PIP_TMP), "PIP_NO_CACHE_DIR": "1"})
    print(f"GOOL_BOOT installing_dependencies tmp={PIP_TMP}", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *missing], env=env)


def find_model(filename: str) -> Path:
    matches = list(DEPLOY_ROOT.rglob(filename)) if DEPLOY_ROOT.exists() else []
    if not matches:
        raise RuntimeError(f"missing_model={filename} under={DEPLOY_ROOT}")
    return matches[0]


def main() -> None:
    load_env(ENV_FILE)
    os.environ["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")

    runtime = Path(os.environ.get("RUNTIME_DATA_DIR", str(RUNTIME_ROOT)))
    raw_live = Path(os.environ.get("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    journal = Path(os.environ.get("SIGNAL_JOURNAL", os.environ.get("SIGNAL_JOURNAL_PATH", str(runtime / "live" / "signal_journal.json"))))
    analysis = Path(os.environ.get("SIGNAL_ANALYSIS_PATH", str(runtime / "live" / "gool_bot2_analysis.jsonl")))
    shadow_journal = Path(os.environ.get("SHADOW_MARKET_JOURNAL", str(runtime / "live" / "gool_bot2_shadow_markets.json")))
    shadow_analysis = Path(os.environ.get("SHADOW_MARKET_ANALYSIS", str(runtime / "live" / "gool_bot2_shadow_analysis.jsonl")))
    shadow_cards = Path(os.environ.get("SHADOW_MARKET_CARDS", str(runtime / "live" / "shadow_cards")))
    prematch_cache = Path(os.environ.get("PREMATCH_CACHE_DIR", str(runtime / "live" / "prematch_cache")))
    xbet_state = Path(os.environ.get("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    xbet_history = Path(os.environ.get("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))

    os.environ["RUNTIME_DATA_DIR"] = str(runtime)
    os.environ["RAW_LIVE_DIR"] = str(raw_live)
    os.environ["GOOL_INBOX_DIR"] = str(raw_live)
    os.environ["SIGNAL_JOURNAL"] = str(journal)
    os.environ["SIGNAL_JOURNAL_PATH"] = str(journal)
    os.environ["SIGNAL_ANALYSIS_PATH"] = str(analysis)
    os.environ["SHADOW_MARKET_JOURNAL"] = str(shadow_journal)
    os.environ["SHADOW_MARKET_ANALYSIS"] = str(shadow_analysis)
    os.environ["SHADOW_MARKET_CARDS"] = str(shadow_cards)
    os.environ["PREMATCH_CACHE_DIR"] = str(prematch_cache)
    os.environ["XBET_MARKET_STATE"] = str(xbet_state)
    os.environ["XBET_MARKET_HISTORY"] = str(xbet_history)
    os.environ.setdefault("XBET_MARKET_INTERVAL_SECONDS", "12")
    os.environ.setdefault("XBET_MARKET_REQUIRED", "1")
    os.environ.setdefault("VAR_WIN_CONFIRM_SECONDS", "45")
    os.environ.setdefault("VAR_WIN_CONFIRM_SNAPSHOTS", "2")

    raw_live.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    analysis.parent.mkdir(parents=True, exist_ok=True)
    shadow_journal.parent.mkdir(parents=True, exist_ok=True)
    shadow_analysis.parent.mkdir(parents=True, exist_ok=True)
    shadow_cards.mkdir(parents=True, exist_ok=True)
    prematch_cache.mkdir(parents=True, exist_ok=True)
    xbet_state.parent.mkdir(parents=True, exist_ok=True)

    ensure_deps()

    models = {
        "ARCHIVE_FOUNDATION_MODEL": "archive_foundation.pkl",
        "ARCHIVE_HAZARD_MODEL": "archive_hazard.pkl",
        "FOOTBALL_DATA_GOAL_MODEL": "football_data_goal_models.pkl",
    }
    for env_key, filename in models.items():
        path = find_model(filename)
        os.environ[env_key] = str(path)
        print(f"GOOL_BOOT model={filename} path={path}", flush=True)

    os.environ["ARCHIVE_FOUNDATION_MODEL_PATH"] = os.environ["ARCHIVE_FOUNDATION_MODEL"]
    os.environ["ARCHIVE_HAZARD_MODEL_PATH"] = os.environ["ARCHIVE_HAZARD_MODEL"]
    os.environ["FOOTBALL_DATA_GOAL_MODELS_PATH"] = os.environ["FOOTBALL_DATA_GOAL_MODEL"]

    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or not os.getenv("TELEGRAM_CHAT_ID", "").strip():
        raise RuntimeError("telegram_not_configured")
    print("GOOL_BOOT config=ok models=ok telegram=configured", flush=True)
    print(f"GOOL_BOOT paths raw={raw_live} journal={journal} analysis={analysis}", flush=True)
    print(f"GOOL_BOOT shadow journal={shadow_journal} analysis={shadow_analysis} cards={shadow_cards}", flush=True)
    print(f"GOOL_BOOT storage prematch_cache={prematch_cache}", flush=True)
    print(f"GOOL_BOOT xbet state={xbet_state} interval={os.environ['XBET_MARKET_INTERVAL_SECONDS']} required={os.environ['XBET_MARKET_REQUIRED']}", flush=True)
    print(f"GOOL_BOOT var_guard seconds={os.environ['VAR_WIN_CONFIRM_SECONDS']} snapshots={os.environ['VAR_WIN_CONFIRM_SNAPSHOTS']}", flush=True)

    cleanup_env = os.environ.copy()
    cleanup = subprocess.run(
        [sys.executable, "-m", "gool_bot2.storage_runtime", "--once"],
        env=cleanup_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if cleanup.stdout.strip():
        print(cleanup.stdout.strip(), flush=True)
    if cleanup.returncode != 0:
        print(f"GOOL_BOOT storage_cleanup_failed rc={cleanup.returncode} err={cleanup.stderr.strip()}", flush=True)

    env = os.environ.copy()
    collector = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.storage_live_collector",
        "--data-dir", str(raw_live),
        "--interval", os.getenv("LIVE_INTERVAL_SECONDS", "60"),
    ], env=env)
    xbet = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.xbet_market_worker",
        "--state", str(xbet_state),
        "--history", str(xbet_history),
        "--interval", os.getenv("XBET_MARKET_INTERVAL_SECONDS", "12"),
    ], env=env)
    worker = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.storage_market_signal_worker_var",
        "--raw-dir", str(raw_live),
        "--journal", str(journal),
        "--analysis", str(analysis),
    ], env=env)
    shadow = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.storage_market_shadow_worker_var",
        "--raw-dir", str(raw_live),
        "--journal", str(shadow_journal),
        "--analysis", str(shadow_analysis),
        "--cards", str(shadow_cards),
    ], env=env)
    print(f"GOOL_BOOT running collector_pid={collector.pid} xbet_pid={xbet.pid} worker_pid={worker.pid} shadow_pid={shadow.pid}", flush=True)
    procs = (("collector", collector), ("xbet", xbet), ("worker", worker), ("shadow", shadow))
    try:
        while True:
            for name, proc in procs:
                if proc.poll() is not None:
                    raise RuntimeError(f"{name}_exited={proc.returncode}")
            time.sleep(5)
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    main()
