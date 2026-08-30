from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd


def _score(value: float | int) -> int:
    return int(round(float(value)))


def evaluate(dataset_path: Path) -> dict[str, object]:
    frame = pd.read_csv(dataset_path, parse_dates=["kickoff_at"])
    required = {
        "match_id",
        "period",
        "minute",
        "home_score",
        "away_score",
        "second_half_goals_total",
        "final_home_score",
        "final_away_score",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise RuntimeError(f"Prepared archive is missing required columns: {missing}")

    # The foundation dataset is built with a 2-minute step starting at minute 1,
    # so minute 45 is present. Keep one HT snapshot per finished match.
    halftime = frame[
        frame["period"].eq(1)
        & frame["minute"].between(44.5, 45.0)
        & frame["second_half_goals_total"].notna()
    ].copy()
    halftime = halftime.sort_values(["match_id", "minute"]).groupby("match_id", as_index=False).tail(1)

    if halftime.empty:
        raise RuntimeError("No halftime rows found in prepared archive dataset")

    groups: dict[str, object] = {}
    ht_counter: Counter[str] = Counter()
    ft_counter: Counter[str] = Counter()

    for (ht_home, ht_away), part in halftime.groupby(["home_score", "away_score"], sort=True):
        ht = f"{_score(ht_home)}-{_score(ht_away)}"
        finals = Counter(
            f"{_score(row.final_home_score)}-{_score(row.final_away_score)}"
            for row in part.itertuples()
        )
        matches = int(part["match_id"].nunique())
        two_plus = int((part["second_half_goals_total"] >= 2).sum())
        zero = int((part["second_half_goals_total"] == 0).sum())
        one = int((part["second_half_goals_total"] == 1).sum())
        three_plus = int((part["second_half_goals_total"] >= 3).sum())

        groups[ht] = {
            "matches": matches,
            "second_half_goals": {
                "0": zero,
                "1": one,
                "2_plus": two_plus,
                "3_plus": three_plus,
            },
            "probability_2_plus_second_half_goals": two_plus / matches,
            "final_scores": [
                {
                    "score": score,
                    "matches": count,
                    "share": count / matches,
                }
                for score, count in finals.most_common()
            ],
        }
        ht_counter[ht] += matches
        ft_counter.update(finals)

    focus_scores = ["0-0", "1-0", "0-1", "1-1", "2-0", "0-2"]
    focus = {score: groups[score] for score in focus_scores if score in groups}

    return {
        "definition": "Finished archive matches grouped by score at halftime, then by final score",
        "uses_bookmaker_odds": False,
        "matches": int(halftime["match_id"].nunique()),
        "halftime_score_groups": groups,
        "focus": focus,
        "most_common_halftime_scores": [
            {"score": score, "matches": count}
            for score, count in ht_counter.most_common(20)
        ],
        "most_common_final_scores": [
            {"score": score, "matches": count}
            for score, count in ft_counter.most_common(20)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split the prepared football archive by halftime score and final outcome"
    )
    parser.add_argument("--dataset", default="data/foundation_bootstrap/processed/archive_training.csv")
    parser.add_argument(
        "--output",
        default="data/foundation_bootstrap/artifacts/halftime_final_score_report.json",
    )
    args = parser.parse_args()

    report = evaluate(Path(args.dataset))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
