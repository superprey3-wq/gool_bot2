# GOOL Bot 2 production cutover

This is the production checklist for replacing the legacy GOOL/Monkey runtime with GOOL Bot 2.

## Required runtime

- Python 3.11+
- persistent writable data directory, recommended `/data`
- three trained model files:
  - `/data/models/archive_foundation.pkl`
  - `/data/models/archive_hazard.pkl`
  - `/data/models/football_data_goal_models.pkl`
- environment file based on `.env.example`
- Telegram bot token and at least one recipient/subscriber

The core live runtime does not require bookmaker odds or an LLM.

## Secret migration

Copy secret **values** from the existing GOOL server environment into the new server environment. Do not commit them to Git.

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

## Start order

Keep the old/temporary server online during validation.

1. Install the repository and dependencies on the main server.
2. Copy the three known-good model files to `/data/models/`.
3. Install the production environment values.
4. Run `python -m gool_bot2.production_check`. It must print `"status": "ready"`.
5. Start `python -m gool_bot2.live_collector --interval 60`.
6. Start `python -m gool_bot2.signal_worker`.
7. Confirm new JSONL snapshots appear under `/data/raw/live/`.
8. Confirm the signal worker reads the same directory without errors.
9. Confirm one Telegram delivery/callback path works.
10. Only after those checks pass, stop the old/temporary server to avoid duplicate Telegram signals.

## Signal behavior at launch

- `another_goal`: any score state, within its allowed time window.
- `goal_before_ht`: only first-half 0:0 states and only in the configured first-half window.
- `over_2_5`: evaluated at halftime; no card if the total is already 3+.
- `both_teams_to_score`: evaluated at halftime; no card if both teams have already scored.

Halftime BTTS/O2.5 evaluation bypasses the generic live prefilter because these are dedicated halftime models.

## Rollback

If the main server fails the production check, cannot collect live snapshots, cannot load a model, or cannot deliver Telegram messages, leave the temporary server running and do not cut over. Once the main server is confirmed healthy, stop the temporary server before leaving the new runtime active.
