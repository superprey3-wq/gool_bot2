# GOOL Lite Stage 1 — Bot-Hosting.net Free

This is an experimental, **not production-equivalent**, one-process runtime for the
`bot-hosting-lite` Git branch. `main` is not changed.

## Capabilities

- Real hockey and basketball Flashscore/1xBet scans, PREMATCH and LIVE.
- Original multi-sport formulas and scenario engines; SHADOW signals by default.
- Telegram /start /stop /status /hockeyjournal /basketjournal
  /hockeyprematch /basketprematch (requires TELEGRAM_BOT_TOKEN).
- Small SQLite state and bounded multisport history (approximately 1 MiB).
- No football production process, no pretrained football models, no Chromium,
  no full bookmaker archive, no full /dayreport or cross-sport SUPER 10.
- Single Python process. No live production worker on GitHub Actions.

## Deploy using the hosting panel

1. Create a free **Python** Application on https://bot-hosting.net/ .
2. Select **GitHub repository** as the source:
   `superprey3-wq/gool_bot2`, branch **bot-hosting-lite**.
3. Python version **3.11+**.
4. Entry File / STARTUP_FILE: **bot_hosting_lite.py**.
5. Leave the Python runtime's default start command, which installs root
   `requirements.txt` (Lite branch installs **Pillow only**).
   If custom command is required:
   `python -m pip install --no-cache-dir -r requirements.txt && exec python bot_hosting_lite.py`
6. Under Environment (never commit secrets): `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
7. Leave `GOOL_MULTISPORT_MODE=shadow` until at least a few real feed cycles
   have correct matches, periods, prices and journal results.
8. Start. Console should show `GOOL_LITE_STAGE1_STARTED` and later
   `GOOL_MULTISPORT_FS_BRAIN` and `GOOL_LITE_HEALTH`.
9. In Telegram use `/start` then `/status`.
10. To explicitly enable sending signals after validation, set
    `GOOL_MULTISPORT_MODE=active` and restart.

**Free quota**: 256 MB RAM / 25% vCPU / 512 MB disk, manual renewal every 4 days.
These are very tight constraints. Imports and no-network constructor were tested
in CI at ~45 MiB RSS; a full feed cycle with hundreds of fixtures has NOT yet
been tested against the free quota. Active delivery is not approved by CI alone.

## Troubleshooting

- `ModuleNotFoundError: PIL`: ensure `requirements.txt` from
  **bot-hosting-lite**, not `main`, and dependency installation is configured.
- `ModuleNotFoundError: gool_bot2`: confirm the entire repo was cloned,
  not only `bot_hosting_lite.py`; the launcher adds `src` to Python path.
- `GOOL_LITE_COLLECT_ERROR`: check the printed exception and upstream
  Flashscore/1xBet reachability; the worker will retry.
- Frequent RAM/CPU kills: increase GOOL_MULTISPORT_INTERVAL_SECONDS or set
  GOOL_HOCKEY_ENABLED=0 temporarily to isolate memory/CPU load. This limits
  sports coverage, so it is diagnostic only, not a final GOOL configuration.
- Empty status after startup: the first scan may take time or fail if providers
  block traffic. Check logs rather than treating zero cards as an engine pass.
- If `/status` gets no reply, check Telegram token and whether another GOOL
  deployment is polling the **same bot token**.
- Never expose Telegram tokens in screenshots, logs or public repositories.

GitHub Actions tests build and validate the program; they do not act as
a permanent host and do not store runtime Telegram journals.
