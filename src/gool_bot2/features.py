from __future__ import annotations

import pandas as pd


def add_state_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create features from the current snapshot only."""
    out = df.copy()
    out["score_total"] = out["home_score"] + out["away_score"]
    out["score_diff"] = out["home_score"] - out["away_score"]
    out["time_remaining_nominal"] = (90 - out["minute"]).clip(lower=0)
    return out


def add_momentum_features(
    df: pd.DataFrame,
    value_columns: list[str],
    windows: tuple[int, ...] = (3, 5, 10, 15),
) -> pd.DataFrame:
    """Backward-looking deltas within a match.

    Input rows must represent chronological snapshots. A feature at time t uses
    only rows at or before t; no centered/forward windows are permitted.
    """
    out = df.sort_values(["match_id", "captured_at"]).copy()
    grouped = out.groupby("match_id", sort=False)
    for col in value_columns:
        for window in windows:
            out[f"{col}_delta_{window}"] = grouped[col].diff(window)
    return out
