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
PLAYWRIGHT_ROOT = ROOT / ".cache" / "ms-playwright"
MULTI_RESET_ID = "brain_v3_market_systems_clean_epoch_2026_09_06"


def load_env(path: Path, *, required: bool = False) -> bool:
    """Load KEY=VALUE pairs without overriding variables injected by the host.

    Monkey/Pterodactyl deployments often provide secrets as process environment
    variables. A missing local env file must therefore not prevent the runtime
    from booting when those variables are already present.
    """
    if not path.exists():
        if required:
            raise RuntimeError(f"missing_env={path}")
        print(f"GOOL_BOOT env_file=missing path={path} source=process_environment", flush=True)
        return False
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    print(f"GOOL_BOOT env_file=loaded path={path}", flush=True)
    return True


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _reset_multi_tracking_once(runtime: Path) -> None:
    """Start the Brain V3 public epoch with clean GOOL tracking once."""
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
        f"removed={len(removed)} journal={multi_journal} bank={bank_state}",
        flush=True,
    )


def _pip_env() -> dict[str, str]:
    PIP_TMP.mkdir(parents=True, exist_ok=True)
    PLAYWRIGHT_ROOT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "TMPDIR": str(PIP_TMP),
            "TMP": str(PIP_TMP),
            "TEMP": str(PIP_TMP),
            "PIP_NO_CACHE_DIR": "1",
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_ROOT)),
        }
    )
    return env


def ensure_deps() -> None:
    packages = {
        "numpy": "numpy>=1.26",
        "pandas": "pandas>=2.2",
        "sklearn": "scikit-learn>=1.5",
        "pydantic": "pydantic>=2.8",
        "yaml": "pyyaml>=6.0",
        "PIL": "pillow>=10.0",
        "websockets": "websockets>=15,<16",
    }
    if _truthy("GOOL_BROWSER_ENABLE", True):
        packages["playwright"] = "playwright>=1.55,<2"
    missing = [pkg for module, pkg in packages.items() if importlib.util.find_spec(module) is None]
    if not missing:
        print("GOOL_BOOT dependencies=ok", flush=True)
        return
    print(f"GOOL_BOOT installing_dependencies tmp={PIP_TMP} packages={','.join(missing)}", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *missing], env=_pip_env())


def _ensure_chromium() -> bool:
    if not _truthy("GOOL_BROWSER_ENABLE", True):
        os.environ["GOOL_BROWSER_ENABLE"] = "0"
        print("GOOL_BOOT browser365=disabled reason=config", flush=True)
        return False
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        os.environ["GOOL_BROWSER_ENABLE"] = "0"
        print(f"GOOL_BOOT browser365=disabled reason=playwright_import error={type(exc).__name__}:{exc}", flush=True)
        return False

    try:
        with sync_playwright() as p:
            executable = Path(p.chromium.executable_path)
        if not executable.exists():
            print(f"GOOL_BOOT chromium=install path={PLAYWRIGHT_ROOT}", flush=True)
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                env=_pip_env(),
                check=True,
                timeout=max(120, int(os.getenv("GOOL_BROWSER_INSTALL_TIMEOUT_SECONDS", "300"))),
            )
        with sync_playwright() as p:
            executable = Path(p.chromium.executable_path)
            if not executable.exists():
                raise RuntimeError(f"chromium_executable_missing={executable}")
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = browser.new_page()
            page.set_content("<html><body>gool-browser-smoke</body></html>")
            if "gool-browser-smoke" not in page.inner_text("body"):
                raise RuntimeError("chromium_smoke_content_failed")
            browser.close()
        os.environ["GOOL_BROWSER_ENABLE"] = "1"
        print(f"GOOL_BOOT chromium=ready executable={executable}", flush=True)
        return True
    except Exception as exc:
        os.environ["GOOL_BROWSER_ENABLE"] = "0"
        print(
            f"GOOL_BOOT browser365=disabled reason=chromium_smoke error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return False


def find_model(filename: str) -> Path:
    matches = list(DEPLOY_ROOT.rglob(filename)) if DEPLOY_ROOT.exists() else []
    if not matches:
        raise RuntimeError(f"missing_model={filename} under={DEPLOY_ROOT}")
    return matches[0]


def _matchbook_runtime_enabled() -> bool:
    if not _truthy("GOOL_MATCHBOOK_ENABLED", True):
        return False
    token = os.getenv("MATCHBOOK_SESSION_TOKEN", "").strip()
    username = os.getenv("MATCHBOOK_USERNAME", "").strip()
    password = os.getenv("MATCHBOOK_PASSWORD", "").strip()
    return bool(token or (username and password))


def _production_commands(browser_enabled: bool) -> dict[str, list[str]]:
    """Return the only processes allowed in the public two-system product."""
    commands = {
        "live": [
            sys.executable,
            "-m",
            "gool_bot2.storage_live_collector",
            "--interval",
            os.environ.get("LIVE_INTERVAL_SECONDS", "60"),
        ],
        "xbet": [
            sys.executable,
            "-m",
            "gool_bot2.xbet_market_worker",
            "--interval",
            os.environ.get("XBET_MARKET_INTERVAL_SECONDS", "15"),
        ],
        "multisport": [
            sys.executable,
            "-m",
            "gool_bot2.xbet_multisport_steam",
            "--interval",
            os.environ.get("GOOL_MULTISPORT_INTERVAL_SECONDS", "20"),
        ],
        # Independent football book-catalog sampler. Its state is separate from
        # the football betting daemon's shortlist state to prevent lost writes.
        "prematch_market_archive": [
            sys.executable,
            "-m",
            "gool_bot2.xbet_prematch_market",
            "--interval",
            os.environ.get("XBET_PREMATCH_ARCHIVE_INTERVAL_SECONDS", "120"),
            "--state",
            str(Path(os.environ.get("RUNTIME_DATA_DIR", "data")) / "live" / "xbet_prematch_archive_state.json"),
        ],
        "daily_market_archive": [
            sys.executable,
            "-m",
            "gool_bot2.daily_market_collector",
            "--interval",
            os.environ.get("GOOL_DAILY_MARKET_INTERVAL_SECONDS", "40"),
        ],
        "worker": [sys.executable, "-m", "gool_bot2.storage_market_signal_worker_var"],
        "prematch": [sys.executable, "-m", "gool_bot2.v4_prematch_daemon"],
    }
    if _matchbook_runtime_enabled():
        commands["matchbook"] = [
            sys.executable,
            "-m",
            "gool_bot2.matchbook_market_worker",
            "--interval",
            os.environ.get("MATCHBOOK_MARKET_INTERVAL_SECONDS", "10"),
        ]
    if browser_enabled:
        commands["browser"] = [
            sys.executable,
            "-m",
            "gool_bot2.browser_context_worker",
            "--interval",
            os.environ.get("GOOL_BROWSER_INTERVAL_SECONDS", "30"),
        ]
    return commands


def main() -> None:
    load_env(ENV_FILE)
    os.environ["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_ROOT))

    runtime = Path(os.environ.get("RUNTIME_DATA_DIR", str(RUNTIME_ROOT)))
    raw_live = Path(os.environ.get("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    journal = Path(
        os.environ.get(
            "SIGNAL_JOURNAL",
            os.environ.get("SIGNAL_JOURNAL_PATH", str(runtime / "live" / "signal_journal.json")),
        )
    )
    analysis = Path(os.environ.get("SIGNAL_ANALYSIS_PATH", str(runtime / "live" / "gool_bot2_analysis.jsonl")))
    shadow_journal = Path(os.environ.get("SHADOW_MARKET_JOURNAL", str(runtime / "live" / "gool_bot2_shadow_markets.json")))
    shadow_analysis = Path(os.environ.get("SHADOW_MARKET_ANALYSIS", str(runtime / "live" / "gool_bot2_shadow_analysis.jsonl")))
    shadow_cards = Path(os.environ.get("SHADOW_MARKET_CARDS", str(runtime / "live" / "shadow_cards")))
    prematch_cache = Path(os.environ.get("PREMATCH_CACHE_DIR", str(runtime / "live" / "prematch_cache")))
    xbet_state = Path(os.environ.get("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    xbet_history = Path(os.environ.get("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))
    multi_journal = Path(os.environ.get("GOOL_MULTI_JOURNAL_PATH", str(runtime / "live" / "gool_multi_journal.json")))

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
    os.environ["GOOL_MULTI_JOURNAL_PATH"] = str(multi_journal)
    os.environ.setdefault("TELEGRAM_SUBSCRIBERS_FILE", str(runtime / "telegram_subscribers.json"))
    os.environ.setdefault("GOOL_BROWSER_CONTEXT_PATH", str(runtime / "live" / "browser_context.json"))

    os.environ.setdefault("SIGNAL_WORKER_SLEEP", "5")
    os.environ.setdefault("SHADOW_MARKET_SLEEP", "5")
    os.environ.setdefault("XBET_MARKET_INTERVAL_SECONDS", "15")
    os.environ.setdefault("GOOL_MULTISPORT_ENABLED", "1")
    # Multisport runs as its own process. Never embed it in the football 1xBet worker.
    os.environ["GOOL_MULTISPORT_EMBEDDED_ENABLED"] = "0"
    # Monkey is the production runtime: multisport signals must be live here.
    os.environ["GOOL_MULTISPORT_MODE"] = "active"
    os.environ.setdefault("GOOL_MULTISPORT_INTERVAL_SECONDS", "20")
    # Keep the expensive all-day bookmaker crawler outside the LIVE process.
    os.environ["GOOL_DAILY_MARKET_EMBEDDED_ENABLED"] = "0"
    os.environ.setdefault("GOOL_DAILY_MARKET_INTERVAL_SECONDS", "40")
    os.environ.setdefault("GOOL_HOCKEY_ENABLED", "1")
    os.environ.setdefault("GOOL_BASKETBALL_ENABLED", "1")
    os.environ.setdefault("GOOL_MULTISPORT_MIN_ODD", "1.50")
    os.environ.setdefault("GOOL_MULTISPORT_MAX_ODD", "3.25")
    os.environ.setdefault("GOOL_MULTISPORT_MIN_FAIR_EDGE_PP", "3.0")
    # Product policy: send the three strongest distinct hockey/basketball
    # parlays per sport/day when enough safe fixtures exist.
    os.environ["GOOL_MULTISPORT_PARLAY_MAX_DAILY_PER_SPORT"] = "3"
    os.environ.setdefault("GOOL_MULTISPORT_PARLAY_MAX_RESULTS", "3")
    os.environ.setdefault("GOOL_MULTISPORT_PARLAY_MIN_ODD", "1.45")
    os.environ.setdefault("GOOL_MULTISPORT_PARLAY_MAX_ODD", "1.50")
    os.environ.setdefault("GOOL_MULTISPORT_PARLAY_MAX_EVENT_REUSE", "1")
    # GLOBAL SUPER 10 stays active in production and may complete strict legs
    # from a separately gated safe reserve pool. Candidate snapshots live long
    # enough for football + hockey + basketball publishers to overlap.
    os.environ["GOOL_GLOBAL_SUPER10_ENABLED"] = "1"
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_LEGS", "10")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_REQUIRE_ALL_SPORTS", "1")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_MIN_ODD", "1.30")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_MAX_ODD", "1.50")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_RESERVE_MIN_ODD", "1.30")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_RESERVE_MAX_ODD", "1.50")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_SOURCE_TTL_SECONDS", "129600")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_RESERVE_MIN_PROBABILITY", "0.68")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_RESERVE_MIN_EDGE", "0.055")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_RESERVE_MIN_QUALITY", "0.55")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_RESERVE_MIN_STRENGTH", "74")
    os.environ["XBET_MULTISPORT_CARDS_ENABLED"] = "1"
    os.environ["GOOL_MULTISPORT_TEXT_FALLBACK_ENABLED"] = "1"
    os.environ.setdefault("XBET_MULTISPORT_STEAM_ENABLED", "1")
    os.environ.setdefault("XBET_PREMATCH_INTERVAL_SECONDS", "60")
    os.environ.setdefault("XBET_PREMATCH_FETCH_EVENTS", "120")
    os.environ.setdefault("XBET_PREMATCH_TRACK_MAX_EVENTS", "2000")
    os.environ.setdefault("XBET_PREMATCH_MAX_DUE_PER_CYCLE", "32")
    # SUPER 10 is once a day: wait for the broad archived bookmaker field.
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_REQUIRE_DAY_MARKET_COVERAGE", "1")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_MIN_ODDS_COVERAGE", "0.85")
    os.environ.setdefault("GOOL_GLOBAL_SUPER10_SAME_MOSCOW_DAY", "1")
    os.environ.setdefault("XBET_MARKET_MEMORY_EPHEMERAL", "1")
    os.environ.setdefault("XBET_MARKET_MEMORY_RETENTION_DAYS", "2")
    os.environ.setdefault("XBET_MARKET_MEMORY_MAX_BYTES", str(160 * 1024 * 1024))
    # Keep enough multisport state snapshots for a full next-morning LIVE funnel audit.
    os.environ.setdefault("XBET_MULTISPORT_HISTORY_KEEP_BYTES", str(64 * 1024 * 1024))
    os.environ.setdefault("XBET_MARKET_REQUIRED", "1")
    os.environ["GOOL_FOOTBALL_V5_ACTIVE"] = "1"
    os.environ.setdefault("GOOL_FOOTBALL_V5_MIN_ODD", "1.40")
    os.environ.setdefault("GOOL_FOOTBALL_V5_SHORTLIST_CAP", "240")
    os.environ["GOOL_PREMATCH_DELIVER"] = "1"
    os.environ.setdefault("GOOL_PREMATCH_INTERVAL_SECONDS", "10800")
    os.environ["GOOL_PREMATCH_FULL_MARKET_ACTIVE"] = "1"
    os.environ.setdefault("GOOL_PREMATCH_FULL_MARKET_MIN_CONFIDENCE", "70")
    os.environ.setdefault("GOOL_PREMATCH_MAX_DOUBLES", "3")
    os.environ["GOOL_LIVE_CONSENSUS_ACTIVE"] = "1"
    os.environ.setdefault("VAR_WIN_CONFIRM_SECONDS", "45")
    os.environ.setdefault("VAR_WIN_CONFIRM_SNAPSHOTS", "2")
    os.environ.setdefault("GOOL_MULTI_MIN_RATING", "70")
    os.environ.setdefault("GOOL_MULTI_TELEGRAM_MODE", "active")
    # MonkeyBytes/Pterodactyl is the production launcher here; make V4 authoritative here too.
    os.environ["GOOL_LIVE_V4_MODE"] = "active"
    os.environ.setdefault("GOOL_LIVE_MULTI_ALL_MARKETS_SHADOW", "1")

    # Keep legacy exchange emitters/push systems disabled. Matchbook context is
    # a separate read-only collector below and starts only with real auth.
    os.environ["GOOL_MONEY_FLOW_ENABLED"] = "0"
    os.environ["BETDAQ_SELECTION_PUSH_ENABLED"] = "0"
    os.environ["GOOL_MULTI_DAILY_BANK_REPORT_ENABLED"] = "0"
    os.environ["GOOL_EXCHANGE_MONEY_SYSTEMS_ENABLED"] = "0"

    os.environ.setdefault("GOOL_MATCHBOOK_ENABLED", "1")
    os.environ.setdefault("MATCHBOOK_MARKET_INTERVAL_SECONDS", "10")
    os.environ.setdefault("GOOL_BROWSER_ENABLE", "0")
    os.environ.setdefault("GOOL_BROWSER_INTERVAL_SECONDS", "30")
    os.environ.setdefault("GOOL_BROWSER_MAX_MATCHES_PER_CYCLE", "2")
    os.environ.setdefault("GOOL_BROWSER_MATCH_CACHE_SECONDS", "90")
    os.environ.setdefault("GOOL_BROWSER_CONTEXT_TTL_SECONDS", "180")
    os.environ.setdefault("GOOL_BROWSER_MAX_MINUTE_LAG", "3")
    os.environ.setdefault("GOOL_BROWSER_MAX_RSS_MB", "550")

    raw_live.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    analysis.parent.mkdir(parents=True, exist_ok=True)
    shadow_journal.parent.mkdir(parents=True, exist_ok=True)
    shadow_analysis.parent.mkdir(parents=True, exist_ok=True)
    shadow_cards.mkdir(parents=True, exist_ok=True)
    prematch_cache.mkdir(parents=True, exist_ok=True)
    xbet_state.parent.mkdir(parents=True, exist_ok=True)
    multi_journal.parent.mkdir(parents=True, exist_ok=True)

    _reset_multi_tracking_once(runtime)
    ensure_deps()
    browser_enabled = _ensure_chromium()

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
        raise RuntimeError(
            "telegram_not_configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            f"in {ENV_FILE} or in the server environment"
        )
    print("GOOL_BOOT config=ok models=ok telegram=configured brain=V4 mode=active", flush=True)
    print(f"GOOL_BOOT multi_telegram_mode={os.environ['GOOL_MULTI_TELEGRAM_MODE']}", flush=True)
    print(
        f"GOOL_BOOT systems=GOOL_BRAIN+1XBET_STEAM+MULTISPORT multisport_mode={os.environ['GOOL_MULTISPORT_MODE']} "
        f"matchbook_worker={'on' if _matchbook_runtime_enabled() else 'off_auth_missing'} "
        "prematch_full_market=active live_consensus=2of3 legacy_exchange_emitters=off "
        "betdaq_worker=off sx_board=off",
        flush=True,
    )
    if browser_enabled:
        print(
            "GOOL_BOOT browser365=enabled engine=chromium "
            f"max_matches={os.environ['GOOL_BROWSER_MAX_MATCHES_PER_CYCLE']} "
            f"rss_guard={os.environ['GOOL_BROWSER_MAX_RSS_MB']}MB trends=capped_support stats=fallback_only",
            flush=True,
        )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.environ["PYTHONPATH"]

    commands = _production_commands(browser_enabled)
    children: dict[str, subprocess.Popen] = {}

    def start_child(name: str) -> None:
        command = commands[name]
        child_env = env.copy()
        if name == "multisport":
            # Hard isolation: hockey/basketball must never install football Brain V3
            # runtime patches (1H/2H/minute/field-scan/goal-hazard).
            child_env["GOOL_FOOTBALL_AUTOINSTALL"] = "0"
            child_env["GOOL_MULTISPORT_MODE"] = "active"
        children[name] = subprocess.Popen(command, env=child_env, cwd=str(ROOT))
        print(
            f"GOOL_BOOT child={name} pid={children[name].pid} "
            f"football_autoinstall={child_env.get('GOOL_FOOTBALL_AUTOINSTALL', '1')} "
            f"command={' '.join(command)}",
            flush=True,
        )

    for name in commands:
        start_child(name)

    while True:
        time.sleep(3)
        for name, process in list(children.items()):
            code = process.poll()
            if code is None:
                continue
            delay = 15 if name == "browser" else 2
            print(f"GOOL_BOOT child_exit={name} code={code}; restarting in {delay}s", flush=True)
            time.sleep(delay)
            start_child(name)


if __name__ == "__main__":
    main()
