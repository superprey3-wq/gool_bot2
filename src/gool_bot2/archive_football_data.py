from __future__ import annotations

import io
import urllib.error
import urllib.request
from pathlib import Path

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


def _download_csv(url: str, timeout: int = 60) -> pd.DataFrame | None:
    request = urllib.request.Request(url, headers={"User-Agent": "gool_bot2-football-data/1.0"})
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
    for src, dst in (("HTHG", "halftime_home_score"), ("HTAG", "halftime_away_score"), ("FTHG", "final_home_score"), ("FTAG", "final_away_score")):
        out[dst] = pd.to_numeric(frame[src], errors="coerce")

    # Match statistics are optional across seasons/divisions. Missing columns are
    # kept as NaN so the gradient boosting model can still train on all matches.
    optional = {
        "HS": "home_shots",
        "AS": "away_shots",
        "HST": "home_shots_on_target",
        "AST": "away_shots_on_target",
        "HC": "home_corners",
        "AC": "away_corners",
        "HY": "home_yellow_cards",
        "AY": "away_yellow_cards",
        "HR": "home_red_cards",
        "AR": "away_red_cards",
    }
    for src, dst in optional.items():
        out[dst] = pd.to_numeric(frame[src], errors="coerce") if src in frame.columns else float("nan")

    out = out.dropna(subset=["kickoff_at", "halftime_home_score", "halftime_away_score", "final_home_score", "final_away_score"])
    out["halftime_home_score"] = out["halftime_home_score"].astype(float)
    out["halftime_away_score"] = out["halftime_away_score"].astype(float)
    out["final_home_score"] = out["final_home_score"].astype(float)
    out["final_away_score"] = out["final_away_score"].astype(float)
    out["home_score"] = out["halftime_home_score"]
    out["away_score"] = out["halftime_away_score"]
    out["total_goals"] = out["home_score"] + out["away_score"]
    out["score_diff"] = out["home_score"] - out["away_score"]
    out["over_2_5"] = ((out["final_home_score"] + out["final_away_score"]) >= 3).astype(int)
    out["another_goal"] = ((out["final_home_score"] + out["final_away_score"]) > out["total_goals"]).astype(int)
    out["goal_before_ht"] = float("nan")
    out["match_id"] = (
        "football-data:" + season + ":" + division + ":" + out.index.astype(str) + ":" + out["home"] + ":" + out["away"]
    )
    out["minute"] = 45.0
    out["period"] = 1.0
    out["time_remaining_nominal"] = 45.0
    return out.reset_index(drop=True)


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
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_csv, index=False)
    return {
        "files_downloaded": downloaded,
        "files_skipped": skipped,
        "matches": int(combined["match_id"].nunique()),
        "seasons": len(seasons),
        "divisions": len(divisions),
        "dataset": str(output_csv),
    }
