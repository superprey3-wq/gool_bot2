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
MULTI_RESET_ID = "two_system_goal_epoch_v1_2026_09_05"


def load_env(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"missing_env={path}")
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _reset_multi_tracking_once(runtime: Path) -> None:
    """Start the two-system public epoch with clean Multi tracking.

    The marker lives in persistent runtime storage, so only the first boot after
    this deployment resets Multi journal, virtual bank and disposable analysis.
    Later restarts keep every newly collected public bet.
    """
    live = runtime / "live"
    live.mkdir(parents=True, exist_ok=True)
    marker = live / f".gool_multi_reset_{MULTI_RESET_ID}"
    if marker.exists():
        return

    journal_raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    multi_journal = Path(journal_raw) if journal_raw else live / "gool_multi_journal.json"
    bank_raw = os.getenv("GOOL_MULTI_BANK_STATE_PATH", "").strip()
    bank_state = Path(bank_raw) if bank_raw else multi_journal.with_name("gool_multi_bank_state.json")
    analysis_raw = os.getenv("GOOL_MULTI_ANALYSIS_PATH", "").strip() or os.getenv("GOOL_MULTI_SHADOW_PATH", "").strip()
    multi_analysis = Path(analysis_raw) if analysis_raw else live / "gool_multi_analysis.jsonl"

    removed: list[str] = []
    for path in (multi_journal, bank_state, multi_analysis):
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            raise RuntimeError(f"multi_reset_failed path={path} err={exc}") from exc

    marker.write_text(
        f"reset_id={MULTI_RESET_ID}\narchitecture=one_brain_all_live_market_hunters\n",
        "utf-8",
    )
    print(
        f"GOOL_BOOT multi_tracking_reset={MULTI_RESET_ID} "
        f"removed={len(removed)} journal={multi_journal} bank={bank_state} analysis={multi_analysis}",
        flush=True,
    )


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
    matchbook_state = Path(os.environ.get("MATCHBOOK_MARKET_STATE", str(runtime / "live" / "matchbook_market_state.json")))

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
    os.environ["MATCHBOOK_MARKET_STATE"] = str(matchbook_state)

    # Market hunters need a fast master-score heartbeat, while expensive
    # football detail remains throttled separately. This prevents a 60s ceiling
    # on STEAM/FLOW reaction without multiplying FotMob/365/detail load.
    os.environ.setdefault("LIVE_INTERVAL_SECONDS", "15")
    os.environ.setdefault("LIVE_DETAIL_INTERVAL_SECONDS", "60")
    os.environ.setdefault("STORAGE_CLEANUP_EVERY_CYCLES", "20")
    os.environ.setdefault("SIGNAL_WORKER_SLEEP", "3")
    os.environ.setdefault("SHADOW_MARKET_SLEEP", "5")
    # Both market collectors stay inside the live router freshness window while
    # remaining light enough for the small VPS.
    os.environ.setdefault("XBET_MARKET_INTERVAL_SECONDS", "15")
    os.environ.setdefault("MATCHBOOK_MARKET_INTERVAL_SECONDS", "15")
    os.environ.setdefault("MATCHBOOK_MIN_MARKET_VOLUME", "50")
    os.environ.setdefault("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")
    os.environ.setdefault("MATCHBOOK_PAGE_TIMEOUT_SECONDS", "8")
    os.environ.setdefault("TELEGRAM_API_TIMEOUT_SECONDS", "8")
    os.environ.setdefault("TELEGRAM_PHOTO_TIMEOUT_SECONDS", "10")
    os.environ.setdefault("TELEGRAM_NETWORK_BACKOFF_SECONDS", "30")
    os.environ.setdefault("GOOL_RESULT_RETRY_SECONDS", "60")
    os.environ.setdefault("XBET_MARKET_REQUIRED", "1")
    os.environ.setdefault("VAR_WIN_CONFIRM_SECONDS", "45")
    os.environ.setdefault("VAR_WIN_CONFIRM_SNAPSHOTS", "2")
    # Production cutover: old server env files do not need a new variable.
    # Explicit GOOL_MULTI_TELEGRAM_MODE=shadow still provides an instant rollback.
    os.environ.setdefault("GOOL_MULTI_TELEGRAM_MODE", "active")

    raw_live.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    analysis.parent.mkdir(parents=True, exist_ok=True)
    shadow_journal.parent.mkdir(parents=True, exist_ok=True)
    shadow_analysis.parent.mkdir(parents=True, exist_ok=True)
    shadow_cards.mkdir(parents=True, exist_ok=True)
    prematch_cache.mkdir(parents=True, exist_ok=True)
    xbet_state.parent.mkdir(parents=True, exist_ok=True)
    matchbook_state.parent.mkdir(parents=True, exist_ok=True)

    _reset_multi_tracking_once(runtime)
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
    print(f"GOOL_BOOT multi_telegram_mode={os.environ['GOOL_MULTI_TELEGRAM_MODE']}", flush=True)
    print(f"GOOL_BOOT paths raw={raw_live} journal={journal} analysis={analysis}", flush=True)
    print(f"GOOL_BOOT shadow journal={shadow_journal} analysis={shadow_analysis} cards={shadow_cards}", flush=True)
    print(f"GOOL_BOOT storage prematch_cache={prematch_cache}", flush=True)
    print(f"GOOL_BOOT xbet state={xbet_state} interval={os.environ['XBET_MARKET_INTERVAL_SECONDS']} required={os.environ['XBET_MARKET_REQUIRED']}", flush=True)
    print(
        f"GOOL_BOOT matchbook state={matchbook_state} interval={os.environ['MATCHBOOK_MARKET_INTERVAL_SECONDS']} "
        f"min_market_volume={os.environ['MATCHBOOK_MIN_MARKET_VOLUME']}",
        flush=True,
    )
    print(
        f"GOOL_BOOT collector heartbeat={os.environ['LIVE_INTERVAL_SECONDS']}s "
        f"detail={os.environ['LIVE_DETAIL_INTERVAL_SECONDS']}s worker_sleep={os.environ['SIGNAL_WORKER_SLEEP']}s",
        flush=True,
    )
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
        "--interval", os.getenv("LIVE_INTERVAL_SECONDS", "15"),
    ], env=env)
    xbet = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.xbet_market_worker",
        "--state", str(xbet_state),
        "--history", str(xbet_history),
        "--interval", os.getenv("XBET_MARKET_INTERVAL_SECONDS", "15"),
    ], env=env)
    matchbook = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.matchbook_market_worker",
        "--state", str(matchbook_state),
        "--interval", os.getenv("MATCHBOOK_MARKET_INTERVAL_SECONDS", "15"),
    ], env=env)
    worker = subprocess.Popen([
        sys.executable, "-m", "gool_bot2.storage_market_signal_worker_var",
        "--raw-dir", str(raw_live),
        "--journal", str(journal),
        "--analysis", str(analysis),
    ], env=env)

    # Multi active already calculates BTTS/home-goal/away-goal inside the main
    # worker. Running the old experimental shadow process at the same time
    # duplicates raw-file scans, prematch JSON hydration and market analysis.
    # Keep it automatically for rollback/shadow mode, or allow explicit opt-in.
    multi_mode = str(os.environ.get("GOOL_MULTI_TELEGRAM_MODE", "active")).strip().lower()
    legacy_shadow_enabled = multi_mode != "active" or _truthy("GOOL_LEGACY_SHADOW_WORKER", False)
    shadow = None
    if legacy_shadow_enabled:
        shadow = subprocess.Popen([
            sys.executable, "-m", "gool_bot2.storage_market_shadow_worker_var",
            "--raw-dir", str(raw_live),
            "--journal", str(shadow_journal),
            "--analysis", str(shadow_analysis),
            "--cards", str(shadow_cards),
        ], env=env)
        print(f"GOOL_BOOT legacy_shadow=enabled pid={shadow.pid}", flush=True)
    else:
        print("GOOL_BOOT legacy_shadow=disabled reason=multi_active", flush=True)

    procs: list[tuple[str, subprocess.Popen]] = [
        ("collector", collector),
        ("xbet", xbet),
        ("matchbook", matchbook),
        ("worker", worker),
    ]
    if shadow is not None:
        procs.append(("shadow", shadow))

    print(
        f"GOOL_BOOT running collector_pid={collector.pid} xbet_pid={xbet.pid} "
        f"matchbook_pid={matchbook.pid} worker_pid={worker.pid} "
        f"shadow_pid={shadow.pid if shadow is not None else '-'}",
        flush=True,
    )
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
