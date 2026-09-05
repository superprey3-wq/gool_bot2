# GOOL Match Selection Intelligence

This layer strengthens the existing two ordinary GOOL systems without adding new public strategies.

## Production concept preserved

- `goal_before_ht`: ordinary entries only from minute 1 through 30.
- `another_goal`: ordinary entries only from minute 46 through 75.
- Minimum production odd remains 1.50.
- Autonomous 1xBet STEAM remains a separate exceptional system with its existing guards.

## Added context

1. **True pre-kickoff 1xBet market**
   - Background LineFeed collector stores P1/X/P2 and paired match totals before kickoff.
   - 1X2 is converted to fair probabilities after removing overround.
   - Paired O/U provides a de-vigged scoring prior.
   - Early-live data is only a fallback; a verified LineFeed snapshot replaces it when available.

2. **Score Epoch**
   - A new baseline is created when the score changes and at the start of the second half.
   - Cumulative xG/shots/SOT/box shots/big chances/xGOT/danger/corners are differenced from that baseline.
   - The chance that created the previous goal is therefore not reused as evidence for the next goal.

3. **Minute hazard**
   - Ordinary entry windows use conservative time buckets rather than treating every minute equally.
   - The adjustment is deliberately small and cannot manufacture a PASS from weak football.

4. **Chance quality**
   - Uses xG per shot, SOT rate, inside-box rate, big chances and xGOT/xG where available.
   - High shot volume with poor chance quality is no longer treated like a smaller number of dangerous chances.

5. **Lineup uncertainty**
   - FotMob lineup/unavailable context shrinks the historical half-profile contribution when today's squad differs materially.
   - It does not erase current LIVE football evidence.

6. **Match Suitability Score**
   - Combines data quality, score-epoch evidence, market freshness/coverage, historical context, kickoff market and lineup context.
   - Ordinary GOOL entries can be rejected when the match itself is too poorly observed.
   - Autonomous STEAM remains governed by its own safety checks.

7. **Calibration diagnostics**
   - New entries store predicted probability, market fair probability, probability bucket and full Match Intelligence context.
   - Calibration is diagnostic-only for now so it can be measured before it is allowed to change production thresholds.

## Safety principles

- Prematch/H2H/market priors remain subordinate to LIVE football.
- Missing optional context does not automatically become a fake negative signal.
- True prematch odds are never reconstructed from a late LIVE snapshot.
- Existing VAR settlement, two-system routing, 1.50 floor and 75-minute global cutoff are preserved.
