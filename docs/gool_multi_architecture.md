# GOOL MULTI — one bot, many GOOL experts

`feature/gool-multi-router` converts GOOL Bot 2 from separate signal systems into one match-level decision engine. `main` remains untouched during validation.

## Product rule

One match is one decision problem:

`LIVE match -> MODEL + PREMATCH + LIVE experts -> supported 1xBet markets -> one ranking -> BEST BET or WAIT`

The existing GOOL systems remain the football experts. 1xBet is the market/price layer, not the football model.

Current expert inputs:

- `another_goal`;
- `goal_before_ht`;
- `two_more_goals`;
- home team to score;
- away team to score;
- BTTS Yes.

## Supported markets

Only classic goal markets are selectable:

- match total `current goals + 0.5` for one more goal;
- match total `current goals + 1.5` for two more goals;
- first-half total `current goals + 0.5` while the first half is live;
- home team total `current home goals + 0.5`;
- away team total `current away goals + 0.5`;
- BTTS Yes.

Integer/quarter Asian totals are excluded. No 1X2, handicaps, corners, cards, exact score or unrelated markets are part of Multi.

The first-half market is read from the real 1xBet `1st half` subgame. A full-match total is never presented as a first-half bet.

## Time windows

- warm-up: no Multi entry before 10';
- `goal_before_ht`: first half only and never at halftime;
- `two_more_goals`: hard close after 65'; market/value override may revive a soft GOOL rejection only through 60';
- `another_goal`: active through 85';
- team goal and BTTS paths: through 75'.

## GOOL WAIT vs hard block

A normal GOOL WAIT is soft. The corresponding market may return to the ranking only when the existing verified 1xBet `MARKET_OVERRIDE` or `VALUE_OVERRIDE` rules are satisfied.

Hard blocks cannot be overridden:

- Flashscore/1xBet score desynchronisation;
- stale or missing market timestamp;
- finished match;
- closed strategy time window;
- absent decoded market.

An override only restores eligibility. It does not automatically win the final ranking.

## Ranking

Every eligible candidate is compared using:

- GOOL model probability/confidence;
- prematch and live evidence already used by the GOOL experts;
- fair 1xBet probability;
- expected ROI / value edge;
- 1xBet steam / probability movement;
- live data quality;
- time/risk for the required number of goals;
- verified override evidence.

Correlated markets compete for the same slot. For example, at `1:0`, `ОЗ — Да` and `ИТБ2 0.5` describe the same away-goal event, so only the stronger expression remains visible.

## Runtime integration

Multi runs inside the existing `storage_market_signal_worker_var` process and reuses:

- the restored prematch context;
- the already-calculated trained model result;
- GOOL LIVE +2 state;
- team-goal / BTTS pressure analyses;
- the same 1xBet state produced by `xbet_market_worker`.

There is no second bot process and no second bookmaker collector.

## Analysis vs journal

Two persistent files have different jobs:

### `gool_multi_analysis.jsonl`

Contains every current router observation, including WAIT. Each row stores:

- score/minute;
- expert probabilities and pass/WAIT state;
- prematch availability/history counts;
- live xG/proxy, shots, shots on target and momentum summary;
- data quality;
- winner, alternatives and rejected markets;
- odds, value, steam, override state and router reason.

This is the source for the Telegram `🧠 Анализ` view.

### `gool_multi_journal.json`

Contains only unique selected BEST BET entries. Repeated snapshots do not create duplicate bets and there is at most one open Multi exposure per match at a time.

Each journal entry stores the selected market, entry score/minute, odd, probability, rating, value, steam, source (`GOOL`, `MARKET_OVERRIDE`, `VALUE_OVERRIDE`) and final settlement.

Settlement rules use Flashscore score and goal timeline. First-half bets are settled only from goals at minute `<=45`, so a second-half goal can never turn a lost first-half bet into a win.

The Telegram `📊 Отчёт` calculates win/loss, P/L units and ROI from this journal. `🟢 В игре` shows only currently pending Multi entries. `🧠 Анализ` shows all fresh Multi BET/WAIT decisions.

## Virtual bankroll

The shadow journal also behaves like a paper betting account so the Multi strategy can be judged in rubles, not only units.

Defaults:

- initial bankroll: `100000 RUB`;
- stake: `2%` of the realized bankroll at the moment a BEST BET is opened;
- an unsettled bet does not change realized bankroll until settlement;
- every journal row stores `virtual_bank_before_rub`, `virtual_stake_rub`, `virtual_stake_pct` and, after settlement, `virtual_profit_rub`;
- old journal rows created before the bankroll state was initialized are not retroactively charged to the new simulation.

Example: `100000 -> stake 2000`; a win at `2.00` makes the realized bankroll `102000`, so the next 2% stake is `2040`.

The current bankroll is added to the manual `📊 Отчёт`. The main worker also emits one automatic bankroll report every day at `23:59` in `REPORT_TIMEZONE` (default `Europe/Moscow`). The daily report shows opening and closing bank, bets opened, turnover, wins/losses/voids/pending, daily P/L and ROI. It also carries the current calendar-month totals so the month can be evaluated continuously; the last daily report of the month is labelled as the month final.

The report scheduler persists its last delivered date in `gool_multi_bank_state.json` and will send the previous day's report after a restart if the 23:59 delivery was missed.

## Shadow and cutover

The feature branch is still observation-only for Multi bet emission: old production signals can continue while Multi writes its own analysis and settled journal. Menu/report views on this branch read the Multi files so the shadow sample can be audited as one product.

Final cutover is a separate step: disable independent per-strategy Telegram emissions and send only the selected Multi BEST BET/card. That cutover should happen only after the server shadow sample confirms model/prematch/live/1xBet inputs and settlement are correct.
