from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .train_archive_models import _jsonable_metrics, train_archive_models
from .train_first_half_zero_zero import metrics_only as zero_zero_metrics_only
from .train_first_half_zero_zero import train_zero_zero_first_half
from .train_hazard_models import train_hazard_models


FOCUS_HEADS = ("another_goal", "goal_before_ht")


def _save_pickle(bundle: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)


def _focus_metrics(metrics: dict[str, object]) -> dict[str, object]:
    heads = metrics.get("heads", {})
    return {
        **metrics,
        "heads": {name: heads[name] for name in FOCUS_HEADS if name in heads},
    }


def run(work_dir: Path) -> dict[str, object]:
    processed = work_dir / "processed" / "archive_training.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"
    if not processed.exists():
        raise RuntimeError(f"Prepared dataset not found: {processed}")

    frame = pd.read_csv(processed)
    if frame.empty:
        raise RuntimeError("Prepared dataset is empty")
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)

    direct = train_archive_models(frame)
    hazard = train_hazard_models(frame)
    zero_zero_ht = train_zero_zero_first_half(frame)
    _save_pickle(direct, models / "archive_foundation.pkl")
    _save_pickle(hazard, models / "archive_hazard.pkl")
    _save_pickle(zero_zero_ht, models / "first_half_zero_zero.pkl")

    direct_metrics = _focus_metrics(_jsonable_metrics(direct))
    hazard_metrics = {
        "trained_at": hazard["trained_at"],
        "match_counts": hazard["match_counts"],
        "heads": {
            name: hazard["heads"][name].metrics
            for name in FOCUS_HEADS
            if name in hazard["heads"]
        },
    }
    zero_zero_metrics = zero_zero_metrics_only(zero_zero_ht)

    live_bundle = {
        "format_version": 3,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "event_direct": direct,
        "event_hazard": hazard,
        "first_half_zero_zero": zero_zero_ht,
        "routing": {
            "another_goal_live": {"primary": "event_direct:another_goal"},
            "goal_before_ht_live": {
                "primary": "first_half_zero_zero:best_head",
                "fallback": "event_direct:goal_before_ht",
                "condition": "period == 1 and home_score == 0 and away_score == 0",
            },
        },
    }
    _save_pickle(live_bundle, models / "live_goal_models.pkl")

    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "archive_foundation_metrics.json").write_text(
        json.dumps(direct_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifacts / "archive_hazard_metrics.json").write_text(
        json.dumps(hazard_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifacts / "first_half_zero_zero_metrics.json").write_text(
        json.dumps(zero_zero_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifacts / "live_routing.json").write_text(
        json.dumps(live_bundle["routing"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "phase": "train_live_event_heads",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "matches": int(frame["match_id"].nunique()),
        "training_rows": int(len(frame)),
        "focus_heads": list(FOCUS_HEADS),
        "direct": direct_metrics,
        "hazard": hazard_metrics,
        "first_half_zero_zero": zero_zero_metrics,
        "routing": live_bundle["routing"],
        "paths": {
            "dataset": str(processed),
            "foundation_model": str(models / "archive_foundation.pkl"),
            "hazard_model": str(models / "archive_hazard.pkl"),
            "first_half_zero_zero_model": str(models / "first_half_zero_zero.pkl"),
            "live_model": str(models / "live_goal_models.pkl"),
        },
    }
    (artifacts / "foundation_bootstrap_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train live GOOL event heads from StatsBomb and Wyscout")
    parser.add_argument("--work-dir", default="data/foundation_bootstrap")
    args = parser.parse_args()
    print(json.dumps(run(Path(args.work_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
