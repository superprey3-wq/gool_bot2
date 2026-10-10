# GOOL release checklist — 2026-10-11 (Football V6 / Hockey EV / Basketball V4)

## Scope

This release builds evaluation and safer data integrity around the existing
**active** GOOL models; the new challengers are **shadow-only**. It does not
claim a measured gain in hit rate or ROI and does not switch public Telegram
bet selection over to uncalibrated models.

- Basketball V4 shadow: compare historical V3 vs possession/current-quarter V2.
- Fix WNBA 10-minute quarter clock in the historical basketball in-play filter.
- Hockey PREMATCH: compare Poisson V2 total-market decisions against push-aware
  monetary EV, while hockey LIVE V2 remains unchanged.
- Football PREMATCH V6: compare current V5 opponent-adjusted Poisson to a
  separate historical profile; Dixon–Coles is a sensitivity check, NOT a third
  independent input. Capture bookmaker quotes and first predictions before kickoff.
- Football LIVE V6: observe Brain V3 vs LIVE V4 vs goal hazard; do not treat
  correlation as independent evidence or alter the public Brain V3 choice.
- Reject clear cross-day 1xBet/Flashscore fixture-identity mismatches; protect
  basketball points markets against implausible decoded lines.
- Explain new comparisons in journal and /dayreport where supported.

## Defaults and rollback

All new diagnostics default to ON but **none** deliver bets or veto existing
models. If a diagnostic is expensive or misbehaves, change a flag to 0 in
the server environment and restart (the other sport remains unaffected).

```env
GOOL_BASKETBALL_V4_SHADOW_ENABLED=1
GOOL_HOCKEY_PREMATCH_EV_SHADOW_ENABLED=1
GOOL_FOOTBALL_V6_SHADOW_ENABLED=1
GOOL_FOOTBALL_LIVE_V6_SHADOW_ENABLED=1
```

**Do not** promote V4/V6 to active or treat uncalibrated model estimates as
true probabilities. The production risk gates and ordinary signal policies
remain the active ones.

## Deployment (Monkey/Pterodactyl)

1. Confirm all required checks on the final merged **main** SHA are green.
2. Retain a backup of server environment and persistent runtime journal.
3. Confirm `GOOL_AUTO_UPDATE=1` and `GOOL_AUTO_UPDATE_REF=main` if using the
   standard `python monkey_autoupdate.py` startup.
4. Restart the existing bot using its hosting control panel **once**.
   `monkey_autoupdate.py` downloads `main` at startup: a green GitHub
   build alone does **not** mean the running server updated.
5. In console inspect `GOOL_UPDATE updated repo=... ref=main sha=...`,
   `GOOL_BOOT ...`, and absence of repeated process crashes.
6. Inspect one active /dayreport and diagnostic logs, then monitor whether
   pricing, signals, period clock and journals continue working.
7. If needed, disable only the implicated new shadow flag and restart. Avoid
   deleting journal, runtime-data and credential directories.

## Evaluation before any V4/V6 model promotion

- Chronological train/test split, no settled outcomes used before kickoff.
- Same event, line, bookmaker quote, scope, clock and settlement rules for
  both contender and active model.
- Track hit rate, sample count, Brier, log loss, real P/L and ROI; track
  rejected/WAIT candidates as well as Telegram alerts.
- Grade integer totals with push probability, distinguish regulation vs OT,
  and require valid kickoff mapping. No extrapolating a winner from 5 or
  20 successful bets.
- No arbitrary promise of a profitable strategy before enough settled events.
