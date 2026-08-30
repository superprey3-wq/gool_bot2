# GOOL Bot 2 production cutover

This is the production checklist for replacing the legacy GOOL/Monkey runtime with GOOL Bot 2.

## Required runtime

- Python 3.11+
- persistent writable runtime directory
- three trained model files:
  - `${RUNTIME_DATA_DIR}/models/archive_foundation.pkl`
  - `${RUNTIME_DATA_DIR}/models/archive_hazard.pkl`
  - `${RUNTIME_DATA_DIR}/models/football_data_goal_models.pkl`
- environment file based on `.env.example`
- Telegram bot token and at least one recipient/subscriber

The core live runtime does not require bookmaker odds or an LLM.

## How legacy GOOL used Monkey

The legacy repository did not run as one self-contained process on the main bot host. It had a remote Monkey worker/relay architecture:

- `best_bet_remote_worker.py` was intended to run on MonkeyBytes.
- The Monkey worker used `/home/container/monkey_live_context.json` as shared live truth and `/home/container/remote_best_bet_state.json` as worker state.
- The main GOOL process polled Monkey over HTTP using `GOOL_REMOTE_BEST_BET_URL` (default `/bestbet`) and `GOOL_STRONG_FEED_URL` (default `/strong`) and then relayed approved signals to Telegram.

GOOL Bot 2 does not need this relay architecture when it is deployed directly on the Monkey/main server. The collector and signal worker should run locally on the same server and share one runtime directory. Do not restore the old `remote_best_bet_relay`, `remote_strong_proguz_patch`, `/bestbet`, `/strong`, or `monkey_live_context.json` bridge unless a separate remote-worker architecture is intentionally reintroduced later.

## Monkey server layout

For a direct Monkey deployment, use the host path already used by the old Monkey worker instead of assuming `/data` exists:

```text
RUNTIME_DATA_DIR=/home/container/gool_bot2_data
GOOL_INBOX_DIR=/home/container/gool_bot2_data/raw/live
SIGNAL_JOURNAL_PATH=/home/container/gool_bot2_data/live/signal_journal.json
SIGNAL_ANALYSIS_PATH=/home/container/gool_bot2_data/live/gool_bot2_analysis.jsonl
ARCHIVE_FOUNDATION_MODEL=/home/container/gool_bot2_data/models/archive_foundation.pkl
ARCHIVE_HAZARD_MODEL=/home/container/gool_bot2_data/models/archive_hazard.pkl
FOOTBALL_DATA_GOAL_MODEL=/home/container/gool_bot2_data/models/football_data_goal_models.pkl
TELEGRAM_SUBSCRIBERS_FILE=/home/container/gool_bot2_data/telegram_subscribers.json
```

If the actual Monkey installation exposes a different persistent volume, use that path consistently for every variable above. Collector and worker must point to the same `GOOL_INBOX_DIR`.

## Secret migration

Copy secret **values** from the existing GOOL server environment into the new Monkey/main-server environment. Do not commit them to Git.

The reusable variable names are:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_BOT_ID` if the token is stored without the bot id prefix
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_EXTRA_CHAT_IDS`
- `TELEGRAM_SUBSCRIBERS_FILE`
- `RUNTIME_DATA_DIR`
- `LIVE_INTERVAL_SECONDS`
- `LIVE_COOLDOWN_MINUTES`
- `CORE_ANALYSIS_PREFILTER`

GOOL Bot 2 additionally uses the model paths and market thresholds listed in `.env.example`.

Legacy Monkey-only relay variables (`GOOL_REMOTE_BEST_BET_URL`, `GOOL_STRONG_FEED_URL`, `GOOL_MONKEY_LIVE_CONTEXT`, and their relay state/poll variables) are not required by GOOL Bot 2 direct deployment.

## Start order

Keep the temporary server online during validation.

1. Install the GOOL Bot 2 repository and dependencies on Monkey/main server.
2. Create the selected runtime directory and its `models`, `raw/live`, and `live` subdirectories.
3. Copy the three known-good model files into the configured model paths.
4. Copy the existing GOOL Telegram/API secret values into the new service environment.
5. Run `python -m gool_bot2.production_check`. It must print `"status": "ready"`.
6. Start `python -m gool_bot2.live_collector --interval 60`.
7. Start `python -m gool_bot2.signal_worker`.
8. Confirm new JSONL snapshots appear under the configured `GOOL_INBOX_DIR`.
9. Confirm the signal worker reads the same files and reports `local_model=loaded` without model errors.
10. Confirm one Telegram delivery/callback path works.
11. Stop the old GOOL/Monkey relay processes so they cannot send duplicate Telegram signals.
12. Only after the new local runtime stays healthy, stop the temporary/additional server.

## Signal behavior at launch

- `another_goal`: any score state, within its allowed time window.
- `goal_before_ht`: only first-half 0:0 states and only in the configured first-half window.
- `over_2_5`: evaluated at halftime; no card if the total is already 3+.
- `both_teams_to_score`: evaluated at halftime; no card if both teams have already scored.

Halftime BTTS/O2.5 evaluation bypasses the generic live prefilter because these are dedicated halftime models.

## Rollback

If the Monkey/main server fails the production check, cannot collect live snapshots, cannot load a model, or cannot deliver Telegram messages, leave the temporary server running and do not cut over. Once the new local runtime is confirmed healthy, stop the legacy relay/runtime before leaving GOOL Bot 2 active.
