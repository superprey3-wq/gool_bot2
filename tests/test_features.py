import pandas as pd
from gool_bot2.features import add_state_features


def test_state_features_are_snapshot_only():
    df = pd.DataFrame([
        {"minute": 31, "home_score": 0, "away_score": 0},
    ])
    out = add_state_features(df)
    assert out.loc[0, "score_total"] == 0
    assert out.loc[0, "score_diff"] == 0
    assert out.loc[0, "time_remaining_nominal"] == 59
