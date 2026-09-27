#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export GOOL_HISTORY_DB="${GOOL_HISTORY_DB:-data/gool_history.sqlite}"
mkdir -p data logs
python scripts/refresh_odds.py
python scripts/settle_picks.py
python scripts/performance_report.py
