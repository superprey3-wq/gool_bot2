from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    train_index: pd.Index
    test_index: pd.Index


def expanding_walk_forward_splits(
    df: pd.DataFrame,
    date_column: str,
    min_train_dates: int = 30,
    test_dates: int = 7,
) -> list[TimeSplit]:
    """Chronological expanding-window splits by match date."""
    dates = pd.Index(sorted(pd.to_datetime(df[date_column]).dt.date.unique()))
    splits: list[TimeSplit] = []

    cutoff = min_train_dates
    while cutoff < len(dates):
        test_end = min(cutoff + test_dates, len(dates))
        train_dates = set(dates[:cutoff])
        test_set = set(dates[cutoff:test_end])

        normalized = pd.to_datetime(df[date_column]).dt.date
        splits.append(
            TimeSplit(
                train_index=df.index[normalized.isin(train_dates)],
                test_index=df.index[normalized.isin(test_set)],
            )
        )
        cutoff = test_end

    return splits
