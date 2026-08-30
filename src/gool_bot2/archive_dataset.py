from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

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
]
TARGET_COLUMNS = ["another_goal", "goal_before_ht", "two_plus_goals_second_half"]


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


def _event_features(match: ArchiveMatch, minute: float) -> dict[str, float]:
    seen = [goal for goal in match.goals if goal.minute <= minute]
    last_goal = max((goal.minute for goal in seen), default=None)
    return {
        "goals_last_5m": float(sum(1 for goal in seen if goal.minute > minute - 5)),
        "goals_last_10m": float(sum(1 for goal in seen if goal.minute > minute - 10)),
        "minutes_since_last_goal": float(minute - last_goal) if last_goal is not None else float(minute),
    }


def record_to_rows(record: dict[str, object], cutoffs: range | None = None) -> list[dict[str, object]]:
    """Expand one finished match into chronological supervised examples.

    Only timestamped events at or before the cutoff are used as features. Final
    match statistics are deliberately excluded because they would leak future
    information into historical minute-level rows.
    """
    match = _archive_match(record)
    rows: list[dict[str, object]] = []
    for example in build_archive_examples(match, cutoffs=cutoffs):
        row = asdict(example)
        row.update(
            {
                "league": str(record.get("league", "")),
                "home": str(record.get("home", "")),
                "away": str(record.get("away", "")),
                "total_goals": example.home_score + example.away_score,
                "score_diff": example.home_score - example.away_score,
                "time_remaining_nominal": max(0.0, 90.0 - example.minute),
            }
        )
        row.update(_event_features(match, example.minute))
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
    return frame.sort_values(["kickoff_at", "match_id", "minute"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand Flashscore archive JSONL into GOOL minute-level training rows")
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
