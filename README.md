# gool_bot2

Independent live-football probability engine for three GOOL goal scenarios. This repository is built and validated separately from the legacy Monkey runtime.

## Prediction heads

1. `another_goal` — probability of at least one more goal after the current snapshot.
2. `goal_before_ht` — probability of at least one goal before half-time; valid only for first-half snapshots.
3. `two_plus_goals_second_half` — probability of at least two goals in the second half; trained only from snapshots taken before the second half starts.

The football core does **not require bookmaker odds** and does not depend on an LLM/API to keep working.

## Data sources

- **Flashscore** — primary source of truth for match identity, minute/status, score, events and live statistics.
- **FotMob** — candidate-only independent enrichment for shot map, xG/xGoT, momentum/lineup/rating context.
- **365Scores** — candidate-only cross-check for shot map, xG/xGoT, shots, cards and lineup/stat availability.

Provider values remain separate. Missing data is not converted into evidence and provider disagreement is retained.

## Archive foundation

The historical Flashscore archive is replayed at past cutoffs instead of treating one match as one row. A finished match can therefore become many supervised examples:

`10' -> state known at 10' -> what happened afterwards`

`20' -> state known at 20' -> what happened afterwards`

`HT -> state known at HT -> were there 2+ second-half goals`

Only timestamped information known at cutoff `t` may be used as a feature. Final-match xG/shots/corners must **never** be copied into an earlier historical minute.

Implemented archive pipeline:

`season feed -> finished match ids -> goal timeline -> append-only JSONL -> minute replay -> direct classifiers + Poisson hazard experts -> chronological holdout/calibration -> local model artifacts`

### Backfill one season

```bash
python -m gool_bot2.archive_flashscore \
  --results-url 'https://www.flashscore.com/football/.../results/' \
  --league 'League / Season' \
  --output data/raw/flashscore_archive.jsonl
```

Or provide known `--tournament-id` and `--season-id` directly.

### Backfill many seasons, resumably

Copy `config/archive_seeds.example.json` to `config/archive_seeds.json`, add results URLs or known ids, then run:

```bash
python -m gool_bot2.archive_backfill_many --seeds config/archive_seeds.json
```

Existing match ids are skipped, and a broken season does not terminate the whole backfill.

### Build training rows

```bash
python -m gool_bot2.archive_dataset \
  --input data/raw/flashscore_archive.jsonl \
  --output data/processed/archive_training.csv \
  --step 1
```

### Train direct GOOL probability heads

```bash
python -m gool_bot2.train_archive_models
```

The split is chronological **by complete match**, so snapshots from one match can never be split across train/calibration/test. Probabilities receive a separate chronological sigmoid calibration block.

### Train remaining-goals hazard experts

```bash
python -m gool_bot2.train_hazard_models
```

These models predict expected remaining goal counts (`lambda`) and derive `P(>=1)` and `P(>=2)` with Poisson probabilities. This gives GOOL an independent goal-hazard expert alongside the direct classifiers.

## Live collector for tonight / Monkey

```bash
python -m gool_bot2.live_collector --interval 60
```

The collector is designed for a small server:

- one lightweight Flashscore master discovery per cycle;
- detailed Flashscore calls only in useful GOOL time windows;
- cheap football prefilter before secondary-provider work;
- FotMob/365 enrichment only for candidates and no more often than the configured interval;
- append-only raw snapshots with `captured_at`, `source_observed_at`, and `ingested_at`;
- provider/network errors are recorded and do not terminate the daemon.

This live data becomes the second-stage training set for richer xG/xGoT/shots/momentum models and the future GRU sequence expert.

## Model architecture

Current/next model stack:

`Flashscore archive replay -> archive direct heads + remaining-goal hazard`

`live Flashscore/FotMob/365 snapshots -> rich tabular model -> later small GRU sequence model`

`experts -> calibrated ensemble/meta-model -> hard signal policy -> Telegram`

A GRU/LSTM is intentionally **not** required for the first launch. It will be trained only after enough true minute-by-minute live sequences exist and must beat the tabular/hazard baselines under chronological evaluation.

## Research ideas incorporated

- Football Forecasting Lab — chronological/rolling-origin evaluation, untouched future holdout, calibration discipline.
- TheDataAthlete — football totals / gradient boosting framing.
- soccer_xg / socceraction — xG, event and threat feature vocabulary.
- football-lstm-betting / Seq2Event / temporal-point-process work — sequence and event-time modeling for a later GRU/hazard expert.
- TikaML — LightGBM/Poisson and live remaining-goals concepts used as research inspiration/benchmark. Its repository currently exposes trained artifacts, but no root LICENSE file was found during the audit, so third-party model/code files are **not vendored into GOOL 2**.

## Non-negotiable validation rules

- Every feature at prediction time `t` must use only information timestamped `<= t`.
- Never random-split temporal football data.
- Split by complete matches and chronological time.
- Intermediate model outputs used as features must be chronological OOF.
- Final-match statistics must never be joined into an earlier live snapshot.
- Raw provider data is append-only and preserved.
- The old Monkey repository remains untouched until this project is independently validated.

## Status

Implemented: provider adapters, live Flashscore discovery, candidate enrichment, append-only live collector, time-aware momentum, archive replay/targets, direct archive trainer, chronological calibration, Poisson hazard trainer and local foundation inference wrapper.

Still required before claiming production accuracy: real archive backfill, executed tests, measured chronological metrics, richer live-feature training, ensemble calibration and end-to-end signal/Telegram runtime validation.
