"""GOOL Lite football-only bootstrap.

Bot-Hosting.net entry file stays bot_hosting_lite.py.
Does NOT start hockey, basketball, old ML model workers or Chromium.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

DEFAULTS = {
    "GOOL_FOOTBALL_AUTOINSTALL": "0",
    "GOOL_BROWSER_ENABLE": "0",
    "GOOL_MATCHBOOK_ENABLED": "0",
    "GOOL_HOCKEY_ENABLED": "0",
    "GOOL_BASKETBALL_ENABLED": "0",
    "GOOL_GLOBAL_SUPER10_ENABLED": "0",
    "GOOL_MULTISPORT_MODE": "shadow",
    "GOOL_FOOTBALL_LITE_MODE": "shadow",
    "GOOL_LITE_FOOTBALL_PREMATCH_MAX": "5",
    "GOOL_LITE_FOOTBALL_LIVE_MAX": "4",
    "GOOL_LITE_CYCLE_SECONDS": "120",
}
for k, v in DEFAULTS.items():
    os.environ.setdefault(k, v)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    from gool_bot2.lite_football_service import smoke, run
    if args.smoke:
        smoke()
    else:
        run()


if __name__ == "__main__":
    main()
