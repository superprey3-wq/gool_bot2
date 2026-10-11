# GOOL ACTIVE decision upgrade — 11 October 2026

**This release changes published selections.** It does not merely collect
shadow forecasts, and it is NOT a validated increase in ROI.

| Sport/phase | Active decision | Old model behavior |
|---|---|---|
| Football PREMATCH | V6 opponent-adjusted and independent Poisson consensus on real same-bookmaker odds, min quality 0.65, heuristic EV >= 0.07 and <=10 pp model disagreement | V5 calculates reference only. V5 is **not** a fallback if V6 says WAIT |
| Football LIVE | V3 picks remain candidate; V6 vetoes only extremely low, observed goal hazard | V3 remains authoritative when V6 has missing data; V6 cannot create a bet |
| Hockey PREMATCH singles and express | Push-aware EV checker **must agree** with V2's original direction and exceed 0.10 uncalibrated EV, quality >=0.65 | Otherwise WAIT, not V2 auto-send |
| Hockey LIVE | Current period V2 unchanged | No untested replacement |
| Basketball LIVE | V4 quarter-clock/possession model chooses the live direction; historical V3 can veto a conflicting side | No V3 fallback if V4 declines; real verified quarter scoreboard/1xBet clock mandatory |
| Basketball PREMATCH | Historical V3 remains active | V4 has no prematch model, so no V4 prematch promotion |

## Enable or roll back a lane

Each toggle defaults to ON if absent in the environment, so updating server
code on startup will activate it:

```env
GOOL_FOOTBALL_V6_ACTIVE=1
GOOL_FOOTBALL_LIVE_V6_ACTIVE=1
GOOL_HOCKEY_PREMATCH_EV_ACTIVE=1
GOOL_BASKETBALL_V4_ACTIVE=1
```

To immediately return to the earlier active brain for one sport, set that
specific environment key to 0 and **restart the bot**. Do not delete old
journals, statistics or .env. See `.env.example` for optional thresholds.

V6/V4/push-aware EV are uncalibrated. These decisions are an experimental
production rollout at the user's request. Logging predicted P, quote, market,
status WAIT/BET, and after-settlement ROI is essential. Unavailable/out-of-
date odds or current-quarter verification must result in WAIT instead of
inventing a market.

## Rollout verification

1. GitHub tests and active challenger workflow must be green on **main**.
2. Back up the persistent journal and `gool.env` on the server.
3. Restart using the standard `monkey_autoupdate.py` start command.
4. Verify console `GOOL_UPDATE updated repo=superprey3-wq/gool_bot2 ref=main sha=...`.
5. Verify `FOOTBALL_BRAIN_ACTIVE V6`, `FOOTBALL_V6_ACTIVE`,
   `GOOL_FOOTBALL_LIVE_V6_SHADOW` and multisport journal
   `hockey_prematch_ev_active` / `basketball_live_v4_active` when
   supported data is available; absence of a qualified bet is a valid WAIT.
6. Run `/dayreport` after a suitable match sample and compare settled outcomes,
   including pushes, quarters and false-positive rates, to the previous model.
7. Never interpret the V6 consensus probability as fitted/calibrated.
