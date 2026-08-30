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
    os.environ.setdefault("RUNTIME_DATA_DIR", str(RUNTIME_ROOT))
    os.environ.setdefault("GOOL_INBOX_DIR", str(RUNTIME_ROOT / "raw" / "live"))
    os.environ.setdefault("SIGNAL_JOURNAL_PATH", str(RUNTIME_ROOT / "live" / "signal_journal.json"))
    os.environ.setdefault("SIGNAL_ANALYSIS_PATH", str(RUNTIME_ROOT / "live" / "gool_bot2_analysis.jsonl"))
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

    env = os.environ.copy()
    collector = subprocess.Popen([sys.executable, "-m", "gool_bot2.live_collector", "--interval", os.getenv("LIVE_INTERVAL_SECONDS", "60")], env=env)
    worker = subprocess.Popen([sys.executable, "-m", "gool_bot2.signal_worker"], env=env)
    print(f"GOOL_BOOT running collector_pid={collector.pid} worker_pid={worker.pid}", flush=True)
    try:
        while True:
            if collector.poll() is not None:
                raise RuntimeError(f"collector_exited={collector.returncode}")
            if worker.poll() is not None:
                raise RuntimeError(f"worker_exited={worker.returncode}")
            time.sleep(5)
    finally:
        for proc in (collector, worker):
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    main()
