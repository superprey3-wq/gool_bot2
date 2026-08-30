from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from .archive_events import event_features as open_event_features
from .archive_training import ArchiveGoalEvent, ArchiveMatch, build_archive_examples

BASE_FEATURE_COLUMNS = [
    "minute",
    "period",
    "home_score",
    "away_score",
    "total_goals",
    "score_diff",
    "time_remaining_nominal",
    "goals_last_5m",
    "goals_last_10m",
    "minutes_since_last_goal",
    "home_shots",
    "away_shots",
    "home_shots_on_target",
    "away_shots_on_target",
    "home_xg",
    "away_xg",
    "home_corners",
    "away_corners",
    "home_red_cards",
    "away_red_cards",
    "home_shots_last_5m",
    "away_shots_last_5m",
    "home_sot_last_5m",
    "away_sot_last_5m",
    "home_xg_last_5m",
    "away_xg_last_5m",
    "home_shots_last_10m",
    "away_shots_last_10m",
    "home_xg_last_10m",
    "away_xg_last_10m",
]
TARGET_COLUMNS = ["another_goal", "goal_before_ht", "two_plus_goals_second_half"]
COUNT_TARGET_COLUMNS = ["future_goals_count", "future_first_half_goals", "second_half_goals_total"]


def _archive_match(record: dict[str, object]) -> ArchiveMatch:
    goals = tuple(
        ArchiveGoalEvent(
            minute=float(goal["minute"]),
            period=int(goal["period"]),
            side=str(goal["side"]),
        )
        for goal in record.get("goals", [])
    )
    return ArchiveMatch(
        match_id=str(record["match_id"]),
        kickoff_at=datetime.fromisoformat(str(record["kickoff_at"])),
        goals=goals,
    )


def _goal_features(match: ArchiveMatch, minute: float) -> dict[str, float]:
    seen = [goal for goal in match.goals if goal.minute <= minute]
    last_goal = max((goal.minute for goal in seen), default=None)
    return {
        "goals_last_5m": float(sum(1 for goal in seen if goal.minute > minute - 5)),
        "goals_last_10m": float(sum(1 for goal in seen if goal.minute > minute - 10)),
        "minutes_since_last_goal": float(minute - last_goal) if last_goal is not None else float(minute),
    }


def _rich_event_features(record: dict[str, object], minute: float) -> dict[str, float]:
    events = record.get("events")
    if isinstance(events, list):
        return open_event_features(events, minute)
    # Flashscore goal-only archive rows remain valid. Unknown historical live
    # statistics are represented as NaN, never fake zeroes.
    return {column: float("nan") for column in BASE_FEATURE_COLUMNS if column.startswith(("home_", "away_")) and column not in {"home_score", "away_score"}}


def _count_labels(match: ArchiveMatch, minute: float, period: int) -> dict[str, float | None]:
    future = [goal for goal in match.goals if goal.minute > minute]
    return {
        "future_goals_count": float(len(future)),
        "future_first_half_goals": float(sum(1 for goal in future if goal.period == 1)) if period == 1 else None,
        "second_half_goals_total": float(sum(1 for goal in match.goals if goal.period == 2)) if period == 1 else None,
    }


def record_to_rows(record: dict[str, object], cutoffs: range | None = None) -> list[dict[str, object]]:
    """Expand one finished match into chronological supervised examples.

    Timestamped StatsBomb/Wyscout events are aggregated only up to cutoff t.
    Flashscore rows without timestamped shot/xG history keep those features
    missing instead of backfilling final-match statistics and leaking future data.
    """
    match = _archive_match(record)
    rows: list[dict[str, object]] = []
    for example in build_archive_examples(match, cutoffs=cutoffs):
        row = asdict(example)
        row.update(
            {
                "source": str(record.get("source", "flashscore")),
                "league": str(record.get("league", "")),
                "home": str(record.get("home", "")),
                "away": str(record.get("away", "")),
                "total_goals": example.home_score + example.away_score,
                "score_diff": example.home_score - example.away_score,
                "time_remaining_nominal": max(0.0, 90.0 - example.minute),
            }
        )
        row.update(_goal_features(match, example.minute))
        row.update(_rich_event_features(record, example.minute))
        row.update(_count_labels(match, example.minute, example.period))
        rows.append(row)
    return rows


def build_dataframe(input_path: Path, cutoffs: range | None = None) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            rows.extend(record_to_rows(record, cutoffs=cutoffs))
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
    for column in BASE_FEATURE_COLUMNS:
        if column not in frame:
            frame[column] = float("nan")
    return frame.sort_values(["kickoff_at", "match_id", "minute"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand GOOL canonical archive JSONL into minute-level training rows")
    parser.add_argument("--input", default="data/raw/flashscore_archive.jsonl")
    parser.add_argument("--output", default="data/processed/archive_training.csv")
    parser.add_argument("--step", type=int, default=1, help="Minute step; use 1 for maximum data")
    args = parser.parse_args()

    step = max(1, int(args.step))
    frame = build_dataframe(Path(args.input), cutoffs=range(1, 91, step))
    if frame.empty:
        raise SystemExit("No archive rows found")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    print(f"rows={len(frame)} matches={frame['match_id'].nunique()} output={output}")


if __name__ == "__main__":
    main()
