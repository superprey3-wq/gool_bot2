from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path("/home/container")
DEPLOY_ROOT = ROOT / "gool_bot2_deploy"
MODEL_DIR = DEPLOY_ROOT / "models"
MODEL_URL = os.getenv(
    "GOOL_RAVEN_MODEL_URL",
    "https://github.com/superprey3-wq/gool_bot2/releases/download/raven-models-latest/gool-raven-models.zip",
)
REQUIRED_MODELS = (
    "archive_foundation.pkl",
    "archive_hazard.pkl",
    "football_data_goal_models.pkl",
)


def _download(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GOOL-Raven-Boot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _models_ready() -> bool:
    return all((MODEL_DIR / name).is_file() and (MODEL_DIR / name).stat().st_size > 0 for name in REQUIRED_MODELS)


def ensure_models() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if _models_ready():
        print("GOOL_RAVEN models=current", flush=True)
        return

    print(f"GOOL_RAVEN downloading_models url={MODEL_URL}", flush=True)
    payload = _download(MODEL_URL)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        missing = [name for name in REQUIRED_MODELS if name not in names]
        if missing:
            raise RuntimeError(f"raven_model_bundle_missing={missing}")
        for name in REQUIRED_MODELS:
            target = MODEL_DIR / name
            with archive.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        if "manifest.json" in names:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            for name in REQUIRED_MODELS:
                expected = str((manifest.get(name) or {}).get("sha256") or "")
                if expected:
                    actual = hashlib.sha256((MODEL_DIR / name).read_bytes()).hexdigest()
                    if actual != expected:
                        raise RuntimeError(f"raven_model_checksum_failed={name}")

    print(
        "GOOL_RAVEN models=ready "
        + ",".join(f"{name}:{(MODEL_DIR / name).stat().st_size}" for name in REQUIRED_MODELS),
        flush=True,
    )


def main() -> None:
    print("GOOL_RAVEN boot=starting root=/home/container", flush=True)
    ensure_models()

    # Raven's free 1.5 GB container needs slightly tighter defaults than the old host.
    os.environ.setdefault("GOOL_BROWSER_MAX_MATCHES_PER_CYCLE", "1")
    os.environ.setdefault("GOOL_BROWSER_INTERVAL_SECONDS", "45")
    os.environ.setdefault("XBET_GAME_WORKERS", "6")
    os.environ.setdefault("GOOL_PREMATCH_FUSION_WORKERS", "4")

    from monkey_start import main as production_main
    production_main()


if __name__ == "__main__":
    main()
