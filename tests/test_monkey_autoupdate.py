from __future__ import annotations

import importlib.util
import io
import zipfile
from pathlib import Path


def _load():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("monkey_autoupdate_tested", root / "monkey_autoupdate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(tmp_path: Path) -> Path:
    snap = tmp_path / "repo-main"
    package = snap / "src" / "gool_bot2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION='new'\n", "utf-8")
    (package / "fresh.py").write_text("VALUE=2\n", "utf-8")
    (snap / "monkey_start.py").write_text("# new launcher\n", "utf-8")
    (snap / "monkey_autoupdate.py").write_text("# new updater\n", "utf-8")
    (snap / "pyproject.toml").write_text("[project]\nname='gool-bot2'\nversion='0.1.0'\n", "utf-8")
    return snap


def test_install_preserves_env_data_and_models(tmp_path: Path) -> None:
    updater = _load()
    root = tmp_path / "server"
    (root / "src" / "gool_bot2").mkdir(parents=True)
    (root / "src" / "gool_bot2" / "old.py").write_text("OLD=1\n", "utf-8")
    env = root / "gool.env"
    env.write_text("TELEGRAM_BOT_TOKEN=secret\n", "utf-8")
    data = root / "gool_bot2_data" / "live" / "history.jsonl"
    data.parent.mkdir(parents=True)
    data.write_text("keep\n", "utf-8")
    model = root / "gool_bot2_deploy" / "models" / "archive_foundation.pkl"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"MODEL")

    updater._install_snapshot(_snapshot(tmp_path), root)

    assert (root / "src" / "gool_bot2" / "fresh.py").exists()
    assert not (root / "src" / "gool_bot2" / "old.py").exists()
    assert env.read_text("utf-8") == "TELEGRAM_BOT_TOKEN=secret\n"
    assert data.read_text("utf-8") == "keep\n"
    assert model.read_bytes() == b"MODEL"


def test_safe_extract_rejects_traversal(tmp_path: Path) -> None:
    updater = _load()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.txt", "bad")
    try:
        updater._safe_extract(payload.getvalue(), tmp_path / "out")
    except RuntimeError as exc:
        assert "unsafe_archive_member" in str(exc)
    else:
        raise AssertionError("unsafe archive member accepted")


def test_update_falls_back_when_github_is_offline(tmp_path: Path, monkeypatch) -> None:
    updater = _load()
    root = tmp_path / "server"
    (root / "src" / "gool_bot2").mkdir(parents=True)
    (root / "monkey_start.py").write_text("# local launcher\n", "utf-8")
    monkeypatch.setattr(updater, "ROOT", root)
    monkeypatch.setattr(updater, "RUNTIME_ROOT", root / "gool_bot2_data")
    monkeypatch.setattr(updater, "STATE_FILE", root / "gool_bot2_data" / ".code_version")
    monkeypatch.setattr(updater, "_latest_sha", lambda repo, ref: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setenv("GOOL_AUTO_UPDATE", "1")

    assert updater.update_from_github() is False


def test_invalid_snapshot_keeps_working_code(tmp_path):
    import pytest
    updater = _load()
    snapshot = _snapshot(tmp_path)
    (snapshot/'src'/'gool_bot2'/'broken.py').write_text('invalid python !!!')
    root = tmp_path/'server'
    package = root/'src'/'gool_bot2'
    package.mkdir(parents=True)
    (package/'working.py').write_text('VALUE=1\n')
    with pytest.raises(SyntaxError):
        updater._install_snapshot(snapshot, root)
    assert (package/'working.py').read_text() == 'VALUE=1\n'
    assert not (package/'broken.py').exists()


def test_disk_recovery_removes_only_rebuildable_files(tmp_path: Path):
    import os
    import time

    updater = _load()
    root = tmp_path / "server"
    (root / ".pip-tmp").mkdir(parents=True)
    (root / ".pip-tmp" / "pip-download.whl").write_bytes(b"x" * 100)
    (root / ".cache" / "pip").mkdir(parents=True)
    (root / ".cache" / "pip" / "old").write_bytes(b"x" * 50)
    (root / ".cache" / "ms-playwright").mkdir(parents=True)
    (root / ".cache" / "ms-playwright" / "browser").write_bytes(b"x" * 50)
    (root / "gool.env").write_text("GOOL_BROWSER_ENABLE=0\nSECRET=preserve\n")
    data = root / "gool_bot2_data"
    (data / "live" / "shadow_cards").mkdir(parents=True)
    (data / "live" / "shadow_cards" / "old.png").write_bytes(b"x" * 80)
    (data / "raw" / "archive" / "20261001T120000Z").mkdir(parents=True)
    (data / "raw" / "archive" / "20261001T120000Z" / "snap.jsonl").write_bytes(b"x" * 60)
    raw = data / "raw" / "live"
    raw.mkdir(parents=True)
    old = raw / "old.jsonl"
    old.write_bytes(b"x" * 100)
    os.utime(old, (time.time() - 8 * 3600, time.time() - 8 * 3600))
    recent = raw / "current.jsonl"
    recent.write_bytes(b"x" * 100)
    sqlite = data / "live" / "daily_odds.sqlite"
    sqlite.parent.mkdir(exist_ok=True)
    sqlite.write_bytes(b"not for deletion")
    journal = data / "live" / "gool_multi_journal.json"
    journal.write_text("keep")
    models = root / "gool_bot2_deploy" / "models"
    models.mkdir(parents=True)
    (models / "weights.pkl").write_bytes(b"model")

    result = updater._startup_disk_recovery(root)
    assert result["removed"] >= 5
    assert result["freed_bytes"] >= 340
    assert not (root / ".pip-tmp").exists()
    assert not (root / ".cache" / "pip").exists()
    assert not (root / ".cache" / "ms-playwright").exists()
    assert not (data / "raw" / "archive" / "20261001T120000Z").exists()
    assert not old.exists()
    assert recent.exists()
    assert sqlite.read_bytes() == b"not for deletion"
    assert journal.read_text() == "keep"
    assert (models / "weights.pkl").read_bytes() == b"model"
    assert "SECRET=preserve" in (root / "gool.env").read_text()


def test_disk_recovery_keeps_browser_if_enabled(tmp_path: Path, monkeypatch):
    updater = _load()
    monkeypatch.setenv("GOOL_BROWSER_ENABLE", "1")
    root = tmp_path / "server"
    browser = root / ".cache" / "ms-playwright"
    browser.mkdir(parents=True)
    (browser / "chromium").write_bytes(b"x")
    updater._startup_disk_recovery(root)
    assert (browser / "chromium").exists()
