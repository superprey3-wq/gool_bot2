from __future__ import annotations

import io
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path("/home/container")
ARCHIVE_URL = os.getenv(
    "GOOL_RAVEN_CODE_URL",
    "https://github.com/superprey3-wq/gool_bot2/archive/refs/heads/main.zip",
)
PRESERVE = {".env", "gool.env", "gool_bot2_data", "gool_bot2_deploy", ".cache", ".local", ".pip-tmp"}


def _load_env(path: Path = ROOT / ".env") -> int:
    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
            loaded += 1
    return loaded


def _download(url: str, timeout: int = 60, attempts: int = 6) -> bytes:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GOOL-Raven-Code-Bootstrap/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last = exc
            print(
                f"GOOL_RAVEN_BOOTSTRAP download_retry={attempt}/{attempts} "
                f"error={type(exc).__name__}:{exc}",
                flush=True,
            )
            if attempt < attempts:
                time.sleep(min(15, attempt * 3))
    assert last is not None
    raise last


def _copy_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name in PRESERVE:
            continue
        target = dst / item.name
        if target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _run_local(reason: str) -> None:
    local = ROOT / "raven_start.py"
    if not local.is_file():
        raise RuntimeError(f"raven_local_code_missing reason={reason}")
    loaded = _load_env()
    print(
        f"GOOL_RAVEN_BOOTSTRAP offline_fallback=local reason={reason} env_variables={loaded}",
        flush=True,
    )
    os.execv(sys.executable, [sys.executable, str(local)])


def main() -> None:
    _load_env()
    print(f"GOOL_RAVEN_BOOTSTRAP updating_code url={ARCHIVE_URL}", flush=True)
    try:
        payload = _download(ARCHIVE_URL)
    except Exception as exc:
        _run_local(f"{type(exc).__name__}:{exc}")
        return

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            roots = sorted({name.split("/", 1)[0] for name in archive.namelist() if "/" in name})
            if not roots:
                raise RuntimeError("raven_code_archive_empty")
            root_name = roots[0]
            temp = ROOT / ".raven-code-tmp"
            shutil.rmtree(temp, ignore_errors=True)
            temp.mkdir(parents=True, exist_ok=True)
            archive.extractall(temp)
            source = temp / root_name
            if not (source / "raven_start.py").is_file():
                raise RuntimeError("raven_start_missing_in_downloaded_main")
            _copy_tree(source, ROOT)
            shutil.rmtree(temp, ignore_errors=True)
    except Exception as exc:
        _run_local(f"code_update_failed:{type(exc).__name__}:{exc}")
        return

    print("GOOL_RAVEN_BOOTSTRAP code=ready", flush=True)
    os.execv(sys.executable, [sys.executable, str(ROOT / "raven_start.py")])


if __name__ == "__main__":
    main()
