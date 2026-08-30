from __future__ import annotations

import numpy as np
import pandas as pd


def add_state_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create features from the current snapshot only."""
    out = df.copy()
    out["score_total"] = out["home_score"] + out["away_score"]
    out["score_diff"] = out["home_score"] - out["away_score"]
    out["time_remaining_nominal"] = (90 - out["minute"]).clip(lower=0)
    return out


def _elapsed_delta(group: pd.DataFrame, col: str, window_minutes: int) -> pd.Series:
    """Current value minus latest observation at or before t-window.

    This is elapsed-time based, not row-count based. Missing snapshots therefore
    cannot accidentally turn a 10-row delta into a 25-minute delta.
    """
    times = pd.to_datetime(group["captured_at"], utc=True).astype("int64").to_numpy()
    values = pd.to_numeric(group[col], errors="coerce").to_numpy(dtype=float)
    cutoff_ns = times - int(pd.Timedelta(minutes=window_minutes).value)
    prior_idx = np.searchsorted(times, cutoff_ns, side="right") - 1

    result = np.full(len(group), np.nan, dtype=float)
    valid = prior_idx >= 0
    if valid.any():
        previous = values[prior_idx[valid]]
        current = values[valid]
        result[valid] = current - previous
    return pd.Series(result, index=group.index)


def add_momentum_features(
    df: pd.DataFrame,
    value_columns: list[str],
    windows: tuple[int, ...] = (3, 5, 10, 15),
) -> pd.DataFrame:
    """Add strictly backward-looking elapsed-time momentum deltas.

    For a feature at time t and a W-minute window, the reference value is the
    latest snapshot whose timestamp is <= t-W. No future/centered observations
    are ever used. When there is no old-enough observation, the delta is NaN.
    """
    out = df.copy()
    out["captured_at"] = pd.to_datetime(out["captured_at"], utc=True)
    out = out.sort_values(["match_id", "captured_at"]).copy()

    for col in value_columns:
        for window in windows:
            feature = pd.Series(index=out.index, dtype=float)
            for _, group in out.groupby("match_id", sort=False):
                feature.loc[group.index] = _elapsed_delta(group, col, int(window))
            out[f"{col}_delta_{window}"] = feature
    return out
