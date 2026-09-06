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
MULTI_RESET_ID = "brain_v3_market_systems_clean_epoch_2026_09_06"


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
    """Start the Brain V3 production epoch with clean public performance tracking.

    This removes only betting journals/banks and disposable Brain analysis. Market
    histories for 1xBet and Matchbook are deliberately preserved so STEAM/FLOW do
    not lose their live movement context.
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

    flow_journal_raw = os.getenv("GOOL_MONEY_FLOW_JOURNAL_PATH", "").strip()
    flow_journal = Path(flow_journal_raw) if flow_journal_raw else live / "gool_money_flow_journal.json"
    flow_bank_raw = os.getenv("GOOL_MONEY_FLOW_BANK_STATE_PATH", "").strip()
    flow_bank = Path(flow_bank_raw) if flow_bank_raw else flow_journal.with_name("gool_money_flow_bank_state.json")

    removed: list[str] = []
    for path in (multi_journal, bank_state, multi_analysis, flow_journal, flow_bank):
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            raise RuntimeError(f"multi_reset_failed path={path} err={exc}") from exc

    trace_root = Path(os.getenv("GOOL_BRAIN_V3_TRACE_DIR", str(live / "brain_v3")))
    try:
        if trace_root.exists():
            for path in trace_root.glob("*.jsonl"):
                path.unlink()
                removed.append(str(path))
    except OSError as exc:
        raise RuntimeError(f"brain_v3_trace_reset_failed path={trace_root} err={exc}") from exc

    marker.write_text(
        f"reset_id={MULTI_RESET_ID}\nbrain=V3\nmin_rating={os.getenv('GOOL_MULTI_MIN_RATING', '70')}\n",
        "utf-8",
    )
    print(
        f"GOOL_BOOT multi_tracking_reset={MULTI_RESET_ID} "
        f"removed={len(removed)} journal={multi_journal} bank={bank_state} flow_journal={flow_journal}",
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

    os.environ.setdefault("SIGNAL_WORKER_SLEEP", "5")
    os.environ.setdefault("SHADOW_MARKET_SLEEP", "5")
    os.environ.setdefault("XBET_MARKET_INTERVAL_SECONDS", "15")
    # The upgraded host can comfortably sample Matchbook more often. Ten-second
    # snapshots make 30/60/120/300s money-flow trajectories materially cleaner.
    os.environ.setdefault("MATCHBOOK_MARKET_INTERVAL_SECONDS", "10")
    os.environ.setdefault("MATCHBOOK_MIN_MARKET_VOLUME", "50")
    os.environ.setdefault("XBET_MARKET_REQUIRED", "1")
    os.environ.setdefault("VAR_WIN_CONFIRM_SECONDS", "45")
    os.environ.setdefault("VAR_WIN_CONFIRM_SNAPSHOTS", "2")
    os.environ.setdefault("GOOL_MULTI_MIN_RATING", "70")
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
    print("GOOL_BOOT brain=V3 prematch=support_only xbet=odds+separate_steam matchbook=separate_money_flow", flush=True)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.environ["PYTHONPATH"]

    commands = {
        "live": [sys.executable, "-m", "gool_bot2.storage_live_collector", "--interval", os.environ.get("LIVE_INTERVAL_SECONDS", "60")],
        "xbet": [sys.executable, "-m", "gool_bot2.xbet_market_worker", "--interval", os.environ.get("XBET_MARKET_INTERVAL_SECONDS", "15")],
        "matchbook": [sys.executable, "-m", "gool_bot2.matchbook_market_worker", "--interval", os.environ.get("MATCHBOOK_MARKET_INTERVAL_SECONDS", "10")],
        "worker": [sys.executable, "-m", "gool_bot2.storage_market_signal_worker_var"],
    }
    children: dict[str, subprocess.Popen] = {}

    def start_child(name: str) -> None:
        command = commands[name]
        children[name] = subprocess.Popen(command, env=env, cwd=str(ROOT))
        print(f"GOOL_BOOT child={name} pid={children[name].pid} command={' '.join(command)}", flush=True)

    for name in commands:
        start_child(name)

    while True:
        time.sleep(3)
        for name, process in list(children.items()):
            code = process.poll()
            if code is None:
                continue
            print(f"GOOL_BOOT child_exit={name} code={code}; restarting in 2s", flush=True)
            time.sleep(2)
            start_child(name)


if __name__ == "__main__":
    main()
