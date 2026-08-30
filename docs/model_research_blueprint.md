# GOOL2 model research blueprint

This document records the architecture ideas we want to combine without copying any one project wholesale.

## Prediction heads

GOOL2 is optimized for three live targets:

1. `another_goal` — at least one more goal after snapshot time `t`.
2. `goal_before_ht` — at least one more first-half goal after `t`; applicable only in the first half.
3. `two_plus_goals_second_half` — at least two goals in the complete second half; trained only from pre-2H snapshots in v1.

## Core research directions

- Football Forecasting Lab: chronological/rolling-origin validation, calibration, untouched holdout, leakage discipline.
- TheDataAthlete: football totals framing and gradient-boosting baselines.
- soccer_xg / socceraction: xG, event context, xT/VAEP feature vocabulary.
- football-lstm-betting: fixed-cadence sequence construction for recurrent/Transformer models.
- TikaML: LightGBM Poisson scoring priors, Dixon-Coles correction and lightweight live remaining-goal update.
- Soccer-SEQ2Event: sequence-to-next-event modeling.
- Football-Match-Event-Forecast / NMSTPP: temporal point-process ideas for event/goal hazard.
- Semi-Markov xT 360: temporal expected-threat and survival-analysis ideas.

## Archive training strategy

A finished Flashscore match should become many supervised examples, not one row.

For each historical match we choose historical cutoff times `t` (for example every minute or a sparser grid). At every cutoff:

1. Reconstruct only information that was genuinely known at or before `t`.
2. Build the score and event state as-of `t`.
3. Label the row from what happened strictly after `t`:
   - `another_goal = 1` if any later goal exists;
   - `goal_before_ht = 1` if a later first-half goal exists;
   - `two_plus_goals_second_half = 1` if the complete second half later contains at least two goals.
4. Keep chronological match dates so train/calibration/test can be split by time.

This means one archived match can contribute tens of leakage-safe learning rows.

## Critical historical-data limitation

Do not put final-match xG, shots, SOT, corners, possession or other completed-match totals into an earlier historical cutoff. That leaks the future.

If Flashscore exposes only final or half-level statistics for old matches, the archive foundation model may safely use:

- minute/period;
- score as-of cutoff from goal events;
- red cards and other timestamped events observed by cutoff;
- pre-match team/league rolling features computed only from matches before kickoff;
- any provider field that can be proven to be an as-of snapshot.

Minute-by-minute xG/SOT/pressure models must be trained from genuinely timestamped live snapshots. GOOL2 therefore has two data phases:

### Phase A — archive foundation

Train direct target models and goal-hazard/remaining-goals baselines on the largest safe historical archive possible.

### Phase B — GOOL2 live snapshots

Continuously collect Flashscore + FotMob + 365Scores provider-separated snapshots. Add xG/xGoT, shots, SOT, big chances, touches in box, pressure/momentum sequences and provider disagreement. Train/fine-tune live tabular and GRU models from these snapshots.

## Proposed model stack

1. Archive/direct LightGBM or CatBoost models for the three heads.
2. Poisson/Dixon-Coles remaining-goal expert inspired by TikaML.
3. Small GRU sequence model over recent live snapshots.
4. Later: event-hazard / temporal point-process expert.
5. Calibrated meta-model combining expert outputs.
6. Deterministic signal policy remains outside ML and enforces time windows, cooldowns and exposure.

## TikaML integration note

TikaML includes trained LightGBM goal artifacts and a live updater. Its goal model expects a large pre-match feature vector, including rolling xG/xGoT, PPDA, progressive actions, league/table/lineup features and odds probabilities. GOOL2 should therefore treat the pretrained model as a research baseline/teacher unless we can reproduce the required features consistently. The live remaining-goal math is useful independently once compatible pre-match goal rates are available.

The repository README describes the project as research/educational and its processed feature table as derived from proprietary match event data for academic use. Do not import that proprietary processed dataset into GOOL2 production training without clear permission.
