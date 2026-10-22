"""Pure stdlib contracts for launching unmodified football GOOL from main."""
from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "football_main_start",
    Path(__file__).resolve().parents[1] / "football_main_start.py",
)
launcher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(launcher)


def test_plan_only_uses_production_main_modules():
    state = launcher.plan()
    assert state["branch"] == "main"
    assert state["implementation"] == "UNCHANGED_ORIGINAL_MAIN_WORKERS"
    assert state["commands"]["live"] == "gool_bot2.storage_live_collector"
    assert state["commands"]["xbet"] == "gool_bot2.xbet_market_worker"
    assert state["commands"]["brain"] == "gool_bot2.storage_market_signal_worker_var"
    assert "multisport" not in state["commands"]
    assert "hockey" in state["disabled"]
    assert "basketball" in state["disabled"]


def test_hard_disable_unwanted_processes_and_preserve_original_brain(monkeypatch):
    monkeypatch.setenv("GOOL_HOCKEY_ENABLED", "1")
    monkeypatch.setenv("GOOL_BASKETBALL_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_ENABLED", "1")
    monkeypatch.setenv("GOOL_BROWSER_ENABLE", "1")
    monkeypatch.setenv("GOOL_FOOTBALL_AUTOINSTALL", "0")
    env = launcher.make_env()
    for key in ("GOOL_HOCKEY_ENABLED", "GOOL_BASKETBALL_ENABLED",
                "GOOL_MULTISPORT_ENABLED", "GOOL_BROWSER_ENABLE"):
        assert env[key] == "0"
    assert env["GOOL_FOOTBALL_AUTOINSTALL"] == "1"
    assert env["GOOL_FOOTBALL_V5_ACTIVE"] == "1"
    assert env["XBET_MARKET_REQUIRED"] == "1"


def test_no_send_when_user_not_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("GOOL_MAIN_FOOTBALL_ALLOW_SEND", raising=False)
    try:
        launcher.run()
    except SystemExit as exc:
        assert "REFUSED" in str(exc)
    else:
        raise AssertionError("original MAIN launcher must fail closed")


def test_preflight_reports_missing_original_binary_models(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOL_MODEL_DIRECTORY", str(tmp_path))
    for key in launcher.MODEL_FILES:
        monkeypatch.delenv(key, raising=False)
    state = launcher.checks(launcher.make_env())
    assert not any(state["models_found"].values())
    assert state["original_main"]
    assert isinstance(state["packages_found"], dict)


def test_model_bootstrap_reuses_production_raven_downloader(monkeypatch, tmp_path):
    # The full repository suite sets PYTHONPATH=src; top-level launchers are
    # not importable by name unless repository root is added explicitly.
    monkeypatch.syspath_prepend(str(launcher.ROOT))
    import raven_start
    fake_dir = tmp_path / "raven_models"
    monkeypatch.setenv("GOOL_MODEL_DIRECTORY", str(fake_dir))
    called = []
    def fake_download_with_verified_manifest():
        called.append(raven_start.MODEL_DIR)
        raven_start.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for name in launcher.MODEL_FILES.values():
            (raven_start.MODEL_DIR / name).write_bytes(b"test")
    monkeypatch.setattr(raven_start, "ensure_models", fake_download_with_verified_manifest)
    original = raven_start.MODEL_DIR
    try:
        result = launcher.fetch_original_release_models()
    finally:
        raven_start.MODEL_DIR = original
    assert called == [fake_dir]
    assert all(result.values())
