from __future__ import annotations

import io
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd


BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{division}.csv"
DEFAULT_DIVISIONS = (
    "E0", "E1", "E2", "E3",  # England
    "SC0",                       # Scotland
    "D1", "D2",                # Germany
    "I1", "I2",                # Italy
    "SP1", "SP2",              # Spain
    "F1", "F2",                # France
    "N1",                        # Netherlands
    "B1",                        # Belgium
    "P1",                        # Portugal
    "T1",                        # Turkey
    "G1",                        # Greece
)
DEFAULT_SEASONS = tuple(f"{year % 100:02d}{(year + 1) % 100:02d}" for year in range(2015, 2026))
PRIOR_WINDOW = 10
PRIOR_COLUMNS = [
    "home_prior_ft_goals_for",
    "home_prior_ft_goals_against",
    "home_prior_ht_goals_for",
    "home_prior_ht_goals_against",
    "home_prior_over25_rate",
    "away_prior_ft_goals_for",
    "away_prior_ft_goals_against",
    "away_prior_ht_goals_for",
    "away_prior_ht_goals_against",
    "away_prior_over25_rate",
    "home_prior_matches",
    "away_prior_matches",
]


def _download_csv(url: str, timeout: int = 60) -> pd.DataFrame | None:
    request = urllib.request.Request(url, headers={"User-Agent": "gool_bot2-football-data/1.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    if not payload:
        return None
    try:
        return pd.read_csv(io.BytesIO(payload))
    except Exception:
        return None


def _normalize(frame: pd.DataFrame, *, season: str, division: str) -> pd.DataFrame:
    required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()

    out = pd.DataFrame()
    out["source"] = "football-data.co.uk"
    out["season"] = season
    out["league"] = division
    out["home"] = frame["HomeTeam"].astype(str)
    out["away"] = frame["AwayTeam"].astype(str)
    out["kickoff_at"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce", utc=True)
    for src, dst in (
        ("HTHG", "halftime_home_score"),
        ("HTAG", "halftime_away_score"),
        ("FTHG", "final_home_score"),
        ("FTAG", "final_away_score"),
    ):
        out[dst] = pd.to_numeric(frame[src], errors="coerce")

    out = out.dropna(
        subset=["kickoff_at", "halftime_home_score", "halftime_away_score", "final_home_score", "final_away_score"]
    ).copy()
    for column in ("halftime_home_score", "halftime_away_score", "final_home_score", "final_away_score"):
        out[column] = out[column].astype(float)

    out["home_score"] = out["halftime_home_score"]
    out["away_score"] = out["halftime_away_score"]
    out["total_goals"] = out["home_score"] + out["away_score"]
    out["score_diff"] = out["home_score"] - out["away_score"]
    out["over_2_5"] = ((out["final_home_score"] + out["final_away_score"]) >= 3).astype(int)
    out["both_teams_to_score"] = ((out["final_home_score"] > 0) & (out["final_away_score"] > 0)).astype(int)
    out["another_goal"] = ((out["final_home_score"] + out["final_away_score"]) > out["total_goals"]).astype(int)
    out["goal_in_first_half"] = (out["total_goals"] > 0).astype(int)
    out["match_id"] = (
        "football-data:" + season + ":" + division + ":" + out.index.astype(str) + ":" + out["home"] + ":" + out["away"]
    )
    out["minute"] = 45.0
    out["period"] = 1.0
    out["time_remaining_nominal"] = 45.0
    return out.reset_index(drop=True)


def _prior_summary(history: deque[tuple[float, float, float, float, float]]) -> tuple[float, float, float, float, float, float]:
    if not history:
        return (np.nan, np.nan, np.nan, np.nan, np.nan, 0.0)
    values = np.asarray(history, dtype=float)
    return (
        float(values[:, 0].mean()),
        float(values[:, 1].mean()),
        float(values[:, 2].mean()),
        float(values[:, 3].mean()),
        float(values[:, 4].mean()),
        float(len(history)),
    )


def _add_rolling_team_priors(frame: pd.DataFrame, window: int = PRIOR_WINDOW) -> pd.DataFrame:
    """Create pre-match team form features using only matches played earlier.

    Football-Data match-stat columns such as shots/corners are full-time values,
    so they are intentionally NOT used at halftime. These rolling priors are
    leakage-safe because each row is featurized before the current result is
    added to either team's history.
    """
    ordered = frame.sort_values(["kickoff_at", "match_id"]).copy()
    histories: dict[str, deque[tuple[float, float, float, float, float]]] = defaultdict(lambda: deque(maxlen=window))
    rows: list[dict[str, float]] = []

    for row in ordered.itertuples(index=False):
        home_hist = histories[str(row.home)]
        away_hist = histories[str(row.away)]
        h = _prior_summary(home_hist)
        a = _prior_summary(away_hist)
        rows.append(
            dict(
                zip(
                    PRIOR_COLUMNS,
                    (
                        h[0], h[1], h[2], h[3], h[4],
                        a[0], a[1], a[2], a[3], a[4],
                        h[5], a[5],
                    ),
                )
            )
        )

        home_hist.append(
            (
                float(row.final_home_score),
                float(row.final_away_score),
                float(row.halftime_home_score),
                float(row.halftime_away_score),
                float(row.over_2_5),
            )
        )
        away_hist.append(
            (
                float(row.final_away_score),
                float(row.final_home_score),
                float(row.halftime_away_score),
                float(row.halftime_home_score),
                float(row.over_2_5),
            )
        )

    priors = pd.DataFrame(rows, index=ordered.index)
    for column in PRIOR_COLUMNS:
        ordered[column] = priors[column]
    return ordered.reset_index(drop=True)


def import_football_data(
    output_csv: Path,
    *,
    seasons: tuple[str, ...] = DEFAULT_SEASONS,
    divisions: tuple[str, ...] = DEFAULT_DIVISIONS,
) -> dict[str, object]:
    frames: list[pd.DataFrame] = []
    downloaded = 0
    skipped = 0
    for season in seasons:
        for division in divisions:
            url = BASE_URL.format(season=season, division=division)
            raw = _download_csv(url)
            if raw is None:
                skipped += 1
                continue
            normalized = _normalize(raw, season=season, division=division)
            if normalized.empty:
                skipped += 1
                continue
            frames.append(normalized)
            downloaded += 1

    if not frames:
        raise RuntimeError("Football-Data archive produced no usable HT/FT matches")

    combined = pd.concat(frames, ignore_index=True).sort_values(["kickoff_at", "match_id"]).reset_index(drop=True)
    combined = _add_rolling_team_priors(combined)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_csv, index=False)
    return {
        "files_downloaded": downloaded,
        "files_skipped": skipped,
        "matches": int(combined["match_id"].nunique()),
        "seasons": len(seasons),
        "divisions": len(divisions),
        "prior_window": PRIOR_WINDOW,
        "dataset": str(output_csv),
    }
