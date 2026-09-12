from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path("/home/container")
RUNTIME_ROOT = ROOT / "gool_bot2_data"
STATE_FILE = RUNTIME_ROOT / ".code_version"
REPO = "superprey3-wq/gool_bot2"
REF = "main"
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _download(url: str, *, timeout: float, max_bytes: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GOOL-Bot2-Monkey-Updater/1.0",
            "Accept": "application/vnd.github+json, application/octet-stream;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"download_too_large>{max_bytes}")
            chunks.append(chunk)
    return b"".join(chunks)


def _latest_sha(repo: str, ref: str) -> str:
    safe_ref = urllib.parse.quote(ref, safe="")
    payload = _download(
        f"https://api.github.com/repos/{repo}/commits/{safe_ref}",
        timeout=max(5.0, float(os.getenv("GOOL_AUTO_UPDATE_TIMEOUT_SECONDS", "20"))),
        max_bytes=2 * 1024 * 1024,
    )
    sha = str(json.loads(payload.decode("utf-8")).get("sha") or "").strip()
    if len(sha) < 12:
        raise RuntimeError("github_sha_missing")
    return sha


def _safe_extract(payload: bytes, destination: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if not members:
            raise RuntimeError("archive_empty")
        for member in members:
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe_archive_member={member.filename}")
        archive.extractall(destination)
    roots = [p for p in destination.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"archive_root_count={len(roots)}")
    snapshot = roots[0]
    if not (snapshot / "src" / "gool_bot2").is_dir():
        raise RuntimeError("archive_package_missing")
    if not (snapshot / "monkey_start.py").is_file():
        raise RuntimeError("archive_launcher_missing")
    return snapshot


def _replace_tree(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / f".{target.name}.next"
    backup = target.parent / f".{target.name}.prev"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(source, stage)
    if target.exists():
        target.replace(backup)
    try:
        stage.replace(target)
    except Exception:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _replace_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(f".{target.name}.next")
    shutil.copy2(source, stage)
    os.replace(stage, target)


def _install_snapshot(snapshot: Path, root: Path) -> None:
    """Update code only; never touch local secrets, runtime data or model files."""
    _replace_tree(snapshot / "src" / "gool_bot2", root / "src" / "gool_bot2")
    for name in (
        "monkey_start.py",
        "monkey_autoupdate.py",
        "pyproject.toml",
        "requirements.txt",
        ".env.example",
        "README.md",
    ):
        source = snapshot / name
        if source.is_file():
            _replace_file(source, root / name)


def update_from_github() -> bool:
    if not _truthy("GOOL_AUTO_UPDATE", True):
        print("GOOL_UPDATE disabled", flush=True)
        return False

    repo = str(os.getenv("GOOL_AUTO_UPDATE_REPO", REPO)).strip() or REPO
    ref = str(os.getenv("GOOL_AUTO_UPDATE_REF", REF)).strip() or REF
    local_package = ROOT / "src" / "gool_bot2"
    local_launcher = ROOT / "monkey_start.py"
    try:
        sha = _latest_sha(repo, ref)
        installed = STATE_FILE.read_text("utf-8").strip() if STATE_FILE.exists() else ""
        if installed == sha and local_package.is_dir() and local_launcher.is_file():
            print(f"GOOL_UPDATE current ref={ref} sha={sha[:12]}", flush=True)
            return False

        payload = _download(
            f"https://codeload.github.com/{repo}/zip/{sha}",
            timeout=max(10.0, float(os.getenv("GOOL_AUTO_UPDATE_ARCHIVE_TIMEOUT_SECONDS", "45"))),
            max_bytes=max(5 * 1024 * 1024, int(os.getenv("GOOL_AUTO_UPDATE_MAX_BYTES", str(MAX_ARCHIVE_BYTES)))),
        )
        ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gool-update-", dir=str(ROOT)) as tmp:
            snapshot = _safe_extract(payload, Path(tmp))
            _install_snapshot(snapshot, ROOT)

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(sha + "\n", "utf-8")
        print(
            f"GOOL_UPDATE updated repo={repo} ref={ref} sha={sha[:12]} "
            "preserved=gool.env,gool_bot2_data,gool_bot2_deploy",
            flush=True,
        )
        return True
    except Exception as exc:
        if not local_package.is_dir() or not local_launcher.is_file():
            raise RuntimeError(f"update_failed_no_local_fallback:{type(exc).__name__}:{exc}") from exc
        print(
            f"GOOL_UPDATE warning fallback=local_code error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return False


def main() -> None:
    update_from_github()
    launcher = ROOT / "monkey_start.py"
    if not launcher.is_file():
        raise RuntimeError(f"launcher_missing={launcher}")
    print(f"GOOL_UPDATE launch={launcher}", flush=True)
    os.execv(sys.executable, [sys.executable, str(launcher)])


if __name__ == "__main__":
    main()
