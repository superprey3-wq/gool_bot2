from __future__ import annotations

import io
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path("/home/container")
ARCHIVE_URL = os.getenv(
    "GOOL_RAVEN_CODE_URL",
    "https://github.com/superprey3-wq/gool_bot2/archive/refs/heads/main.zip",
)
PRESERVE = {".env", "gool.env", "gool_bot2_data", "gool_bot2_deploy", ".cache", ".local", ".pip-tmp"}


def _download(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "GOOL-Raven-Code-Bootstrap/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


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


def main() -> None:
    print(f"GOOL_RAVEN_BOOTSTRAP downloading_code url={ARCHIVE_URL}", flush=True)
    payload = _download(ARCHIVE_URL)
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

    print("GOOL_RAVEN_BOOTSTRAP code=ready", flush=True)
    os.execv(sys.executable, [sys.executable, str(ROOT / "raven_start.py")])


if __name__ == "__main__":
    main()
