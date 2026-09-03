# GOOL MULTI — shadow architecture

`feature/gool-multi-router` is an isolated redesign layer. It does not replace the production signal workers and it does not send Telegram messages.

## Product idea

GOOL MULTI treats a football match as one decision problem instead of five independent signal strategies.

Pipeline:

1. Existing GOOL experts analyse the match.
2. Live 1xBet lines are decoded into a small set of goal-market candidates.
3. The line optimizer compares neighboring lines that describe the same goal path.
4. The router removes correlated duplicates and chooses one best market.
5. The full decision (winner, alternatives and rejects) is written to the shadow journal.
6. A unified card can be rendered from that decision, but the shadow layer has no Telegram dependency.

## Initial market scope

The first version is deliberately narrow and goal-only:

- next match goal / dynamic match total +0.5;
- Asian middle match total +1.0;
- two more goals / dynamic match total +1.5;
- home team to score / home total +0.5 from current score;
- away team to score / away total +0.5 from current score;
- BTTS Yes when exactly one team is still scoreless.

No 1X2, handicaps, corners, cards, exact score or unrelated markets are included.

## Important probability rule

GOOL MULTI does not invent a probability for a market that has no matching expert model.

For an Asian total `current goals + 1.0`, the current GOOL outputs are sufficient:

- `P(>=2 more goals)` = win probability;
- `P(>=1 more goal) - P(>=2 more goals)` = push probability;
- `1 - P(>=1 more goal)` = loss probability.

This lets the router compare, for example, `TB 3.5 @1.30`, `TB 4.0 @1.60`, and `TB 4.5 @1.90` without pretending that the push line is the same bet as `TB 4.5`.

## Router principles

- One visible bet per match decision.
- Correlated bets compete with each other instead of being sent together.
- Positive model value is required.
- Data quality participates in the rating.
- 1xBet pressure/steam participates in the rating but cannot invent an unsupported football probability.
- Two-goal win paths are hard-blocked after minute 65.
- After minute 65 the router can still choose a one-goal market when it qualifies.

## Unified card

The new card contains:

- match, score, minute and Flashscore team badges;
- one large `BEST MARKET` selection and price;
- GOOL rating;
- one short explanation of why it won;
- up to three alternatives from the same match;
- model VALUE, expected ROI, data quality and market steam.

The renderer currently marks the card as `SHADOW` and never broadcasts it.

## Shadow journal

Each observation stores:

- match identity and score epoch;
- expert snapshot used by the router;
- data quality;
- winning market;
- visible alternatives;
- rejected markets and exact block reasons;
- rating, model probability, push probability, VALUE and market pressure.

This data will later allow retrospective checks such as:

- Did the router choose the best market among its alternatives?
- Did an Asian push-protected line outperform the cheap +0.5 line?
- Did late one-goal routing outperform late +2 decisions?
- Which expert or market-pressure combinations are actually profitable?

## Next integration stage

The next stage should connect the shadow layer to the real runtime data without changing production Telegram output:

1. adapter from the trained `another_goal` / `goal_before_ht` model result;
2. adapter from GOOL LIVE `two_more_goals`;
3. adapters from shadow `team_to_score` / BTTS analyses;
4. current xBet market state by Flashscore event id;
5. computed data-quality score;
6. continuous `gool_multi_shadow.jsonl` collection.

Only after a meaningful shadow sample should Telegram routing be considered.
