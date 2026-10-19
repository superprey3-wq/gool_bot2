# GOOL Lite — free Bot-Hosting.net

**Experimental 3-sport, one-process, SHADOW-by-default build.** On Bot-Hosting.net
(256 MB RAM / 25% CPU / 512 MB disk) it is a staged replacement, NOT yet
production-equivalent to the full GOOL MULTI.

## Features

- 🏒 Hockey PREMATCH + LIVE: existing scenarios, real Flashscore/1xBet markets.
- 🏀 Basketball PREMATCH + LIVE: existing formulas, team history and market lines.
- ⚽ Football PREMATCH: Flashscore history + F01–F05; only a subset (F01–F03)
  can produce a **diagnostic** priced candidate from real two-sided odds.
  Only 3 upcoming games per 30min by default, to keep CPU within free quota.
- ⚽ Football LIVE: Flashscore minute, score and attack stats, momentum, V4
  decision diagnostics. **No football LIVE wager is delivered**, because this
  Lite path has not yet verified live 1xBet quote and score synchronization.
- Telegram: /start, /stop, /status, /football, /footballprematch, /footballlive,
  /hockeyjournal, /basketjournal, /hockeyprematch, /basketprematch,
  /parlays, /super10, /dayreport. Football commands show diagnostic candidates.
- SUPER 10 pool/eligibility from all 3 sports, but **no automatic message in
  shadow mode**. Football candidates are uncalibrated and must be validated.
- /dayreport exports full HTML document using current journal data, which may
  be empty or incomplete in the staging period (football auto-delivery is disabled).
- Only Pillow dependency; no pandas, numpy, sklearn, Chromium, trained models.
- No ongoing production processing on GitHub Actions: CI runs offline tests only.

## Deploy

1. Create **Python** service on https://bot-hosting.net.
2. GitHub repo: `superprey3-wq/gool_bot2`.
3. Branch: **bot-hosting-lite** (NOT main).
4. Python 3.11.
5. Startup File: **bot_hosting_lite.py**.
6. The branch's root `requirements.txt` installs **Pillow only**.
7. Configure secrets on the **host**, never GitHub:
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
8. Keep `GOOL_MULTISPORT_MODE=shadow` to prevent automatic bet cards
   during testing. Leave `GOOL_FOOTBALL_AUTOINSTALL=0`.
9. Restart once the exact target commit is known. If 'pull on restart' is enabled,
   it may download recent code; check Actions before restarting.
10. Console: `GOOL_LITE_STAGE1_STARTED` (legacy startup event name),
   `GOOL_LITE_FOOTBALL_PRE`, `GOOL_LITE_FOOTBALL_LIVE`,
   `GOOL_MULTISPORT_FS_BRAIN`, `GOOL_LITE_HEALTH`.
11. Telegram: /start, /status, /football, /super10, /dayreport.

**CAUTION:** Switching to `active` enables LIVE/PREMATCH delivery of hockey
and basketball and may also send SUPER 10 if all delivery conditions pass.
The Lite football analyzer still does *not* automatically send football bets.
Do not switch to active without checking real price mapping and safeguards.

Default settings can be overridden with:
- `GOOL_LITE_FOOTBALL_ENABLED=0`: temporarily disable football scanner.
- `GOOL_LITE_FOOTBALL_PREMATCH_MAX=3`: number of PREMATCH fixtures per cycle
  (every 30min; max=8).
- `GOOL_LITE_FOOTBALL_LIVE_MAX=3`: live fixtures for statistics
  (every 150sec; max=8).
- `GOOL_MULTISPORT_INTERVAL_SECONDS=90`: multi-sport full scan period.
- `GOOL_GLOBAL_SUPER10_ENABLED=1`: monitor global readiness in shadow mode.

**Important measurements**: 33–45 MiB startup RSS on GitHub CI, ~94 MB when
hockey started processing in one observed real bot-hosting.net session.
This is NOT the steady-state peak with all three sports. The free plan's
25% CPU quota is likely a tighter constraint than RAM.

Stop the service or set `GOOL_LITE_FOOTBALL_ENABLED=0` if health checks show
the provider calls are repeatedly timing out or the process is killed.

## Not yet verified

1. 24-hour host endurance under actual football+hockey+basketball load.
2. Correct real-data pairing of football prematch odds; LIVE bookmaker quotes
   and live football automated signal delivery.
3. All journals, SUPER 10 and /dayreport populated with settled entries.
4. No CPU throttling during weekend match surges.

## Security

Never send bot token in chat or screenshots. Do not run the full GOOL bot on
another host with the same token simultaneously (Telegram getUpdates conflict).
