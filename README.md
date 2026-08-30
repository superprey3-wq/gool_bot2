# gool_bot2

Independent live-football probability engine for three goal scenarios. This repository is built and validated separately from the legacy Monkey runtime.

## First prediction heads

1. `another_goal` — probability of at least one more goal after the current snapshot.
2. `goal_before_ht` — probability of at least one goal before half-time; valid only for first-half snapshots.
3. `two_plus_goals_second_half` — probability of at least two goals in the second half; trained only from snapshots taken before the second half starts.

## Core idea

The core model does **not require bookmaker odds**. It learns from football context and its recent dynamics:

- prematch team context;
- current minute, score and cards;
- shots / shots on target;
- corners;
- dangerous attacks or equivalent pressure signals;
- possession where available;
- changes over the last 3/5/10/15 minutes;
- multiple statistics providers normalized into one canonical timeline.

Bookmaker markets can be added later as a separate comparison/value layer, not as a mandatory model input.

## Non-negotiable validation rules

- Every feature at prediction time `t` must be derived only from information with timestamp `<= t`.
- No random train/test split for model evaluation.
- Use chronological walk-forward validation.
- Calibration is measured separately from discrimination.
- Raw provider data is preserved; provider-specific parsing is isolated in adapters.
- Final-match statistics must never be joined back into earlier live snapshots.
- The old Monkey repository remains untouched until this project is independently validated.

## Initial pipeline

`raw providers -> canonical snapshots/events -> backward-only features -> targets -> baseline model -> calibration -> walk-forward evaluation -> live inference`

## Build order

1. Canonical snapshot/event schema.
2. Data-source adapters (Flashscore + lower-level/alternative statistics sources where legally and technically available).
3. Snapshot history store.
4. Leakage-safe target generation.
5. Tabular baseline (LightGBM/XGBoost or sklearn baseline).
6. Calibration and walk-forward report.
7. Sequence model only after the baseline is measured.
8. Later: optional market/odds layer and clean Monkey migration.
