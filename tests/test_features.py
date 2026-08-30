import math

import pandas as pd

from gool_bot2.features import add_momentum_features, add_state_features


def test_state_features_are_snapshot_only():
    df = pd.DataFrame([
        {"minute": 31, "home_score": 0, "away_score": 0},
    ])
    out = add_state_features(df)
    assert out.loc[0, "score_total"] == 0
    assert out.loc[0, "score_diff"] == 0
    assert out.loc[0, "time_remaining_nominal"] == 59


def test_momentum_uses_elapsed_time_not_row_count():
    df = pd.DataFrame(
        [
            {"match_id": "m1", "captured_at": "2026-08-30T12:00:00Z", "shots": 1},
            {"match_id": "m1", "captured_at": "2026-08-30T12:02:00Z", "shots": 2},
            {"match_id": "m1", "captured_at": "2026-08-30T12:11:00Z", "shots": 7},
        ]
    )
    out = add_momentum_features(df, ["shots"], windows=(5,))

    assert math.isnan(out.iloc[0]["shots_delta_5"])
    assert math.isnan(out.iloc[1]["shots_delta_5"])
    # At 12:11 the latest snapshot at or before 12:06 is 12:02, so delta=7-2.
    assert out.iloc[2]["shots_delta_5"] == 5


def test_momentum_never_reads_future_snapshot():
    df = pd.DataFrame(
        [
            {"match_id": "m1", "captured_at": "2026-08-30T12:00:00Z", "xg": 0.1},
            {"match_id": "m1", "captured_at": "2026-08-30T12:10:00Z", "xg": 0.5},
            {"match_id": "m1", "captured_at": "2026-08-30T12:12:00Z", "xg": 1.5},
        ]
    )
    out = add_momentum_features(df, ["xg"], windows=(5,))

    # 12:10 may only use the 12:00 snapshot for a 5-minute lookback.
    assert abs(out.iloc[1]["xg_delta_5"] - 0.4) < 1e-9
