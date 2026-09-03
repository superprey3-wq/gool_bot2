# GOOL MULTI — one bot, many GOOL experts

`feature/gool-multi-router` is the conversion path from separate GOOL strategies to one match-level Multi Bot. The production `main` branch is not changed while this is validated.

## Product rule

One match is one decision problem:

`LIVE match -> all GOOL systems -> supported 1xBet markets -> one ranking -> BEST BET or WAIT`

The existing systems remain the football experts. They no longer compete by independently sending several signals from the same match.

Current expert inputs:

- `another_goal`;
- `goal_before_ht`;
- `two_more_goals`;
- home team to score;
- away team to score;
- BTTS Yes.

The router receives every usable expert probability/confidence, including a normal GOOL `WAIT`. A `WAIT` is not automatically a bet: it may enter the final competition only when the existing verified 1xBet override rules say that the market move is exceptional.

## 1xBet is the runtime bookmaker source

GOOL MULTI reuses the current `xbet_market_worker` state. It does not start another bookmaker collector and it does not add Bet365/Betano/Kambi to the runtime.

Supported initial 1xBet goal markets:

- dynamic match total `current goals + 0.5` (one more match goal);
- Asian middle total `current goals + 1.0`;
- dynamic match total `current goals + 1.5` (two more goals);
- home team total `current home goals + 0.5`;
- away team total `current away goals + 0.5`;
- BTTS Yes.

No 1X2, handicaps, corners, cards, exact score or unrelated markets are part of this router.

`goal_before_ht` remains an expert in the unified snapshot, but it is not mapped to a fake full-match market. It becomes a selectable Multi Bot market only when the exact 1xBet first-half line is decoded and matched. Until then, the existing first-half production logic remains separate.

## GOOL WAIT vs hard block

This distinction is mandatory.

### Soft GOOL WAIT

Examples: ordinary model threshold miss, prematch/live pressure not strong enough, team-pressure filter miss.

A soft WAIT may be revived when the corresponding 1xBet market produces a verified `MARKET_OVERRIDE` or `VALUE_OVERRIDE` under the existing Bot 2 rules. This preserves real behaviour such as a team-goal market moving from roughly `7.09 -> 4.76` with strong one-way steam even when the normal GOOL system did not pass.

### Hard block

1xBet can never override:

- Flashscore/1xBet score desynchronisation;
- stale or missing market timestamp;
- finished match;
- closed strategy time window;
- `+2` hard window after 65';
- any market that is not actually present in the decoded 1xBet state.

For `two_more_goals`, the existing production rule is preserved: a rejected +2 scenario may be awakened by market/value override only through 60'; after that it can pass only organically, and after 65' it is closed completely.

## Ranking

Every eligible market is scored using the same comparison layer:

- GOOL probability/confidence;
- fair 1xBet probability (margin removed when opposite price exists);
- model VALUE / expected ROI;
- 1xBet steam / probability movement;
- LIVE data quality;
- time/risk of the required number of goals;
- override evidence when GOOL originally said WAIT.

A strong override only returns a soft-rejected market to the competition. It does not automatically make it the winner. It must still beat the other available GOOL markets in the common ranking and retain positive value.

## Correlation / one visible bet

Correlated bets share one slot. The router compares them and keeps the better price instead of sending duplicates.

Example at `1:0`:

- `ОЗ — Да`;
- `ИТБ2 0.5`.

Both settle on the same next away goal, so only the stronger option can remain visible.

The same principle applies to neighboring totals that represent the same two-goal path.

## Asian middle total

For `current goals + 1.0` the existing GOOL outputs are sufficient without inventing a model:

- `P(>=2 more goals)` = win probability;
- `P(>=1 more goal) - P(>=2 more goals)` = push probability;
- `1 - P(>=1 more goal)` = loss probability.

This lets Multi Bot compare, for example, `ТБ 3.5`, `ТБ 4.0` and `ТБ 4.5` with the push correctly accounted for.

## Runtime integration

Shadow validation now runs inside the existing `storage_market_signal_worker_var` process. It reuses:

- the same incoming live record;
- the already-calculated GOOL model result;
- the already-calculated GOOL LIVE +2 analysis;
- team/BTTS GOOL pressure analyses;
- the same 1xBet state collected by `xbet_market_worker`.

There is no second bot process and no second 1xBet process.

Each observation is appended to `gool_multi_shadow.jsonl` and contains the expert states, all candidate markets, blocks, overrides, alternatives and the one selected BEST BET/WAIT decision. The shadow layer does not send Telegram signals yet.

## Cutover rule

Production Telegram remains unchanged until the shadow sample demonstrates that the unified router behaves correctly. After validation, the intended cutover is to replace separate per-strategy emissions with the single Multi Bot decision/card, not to run both products forever.
