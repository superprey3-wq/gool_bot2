from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from .v4_prematch_engine import PrematchPick


@dataclass(frozen=True)
class PredictionSnapshot:
    snapshot_id: str
    created_at: str
    event_id: str
    home: str
    away: str
    market: str
    selection: str
    odds: float
    probability: float
    market_probability: float
    edge: float
    expected_value: float
    data_quality: float
    model_version: str = "v4"

    @classmethod
    def from_pick(cls, pick: PrematchPick, *, snapshot_id: str, model_version: str = "v4") -> "PredictionSnapshot":
        return cls(
            snapshot_id=snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            event_id=pick.event_id,
            home=pick.home,
            away=pick.away,
            market=pick.market,
            selection=pick.selection,
            odds=pick.odds,
            probability=pick.model_probability,
            market_probability=pick.market_probability,
            edge=pick.edge,
            expected_value=pick.expected_value,
            data_quality=pick.data_quality,
            model_version=model_version,
        )


def append_snapshots(path: str | Path, snapshots: Iterable[PredictionSnapshot]) -> int:
    """Append-only pre-kickoff ledger. Existing rows are never rewritten."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("a", encoding="utf-8") as fh:
        for row in snapshots:
            fh.write(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def brier_binary(probabilities: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = list(zip(probabilities, outcomes))
    if not pairs:
        return 0.0
    return sum((float(p) - int(y)) ** 2 for p, y in pairs) / len(pairs)


def log_loss_binary(probabilities: Iterable[float], outcomes: Iterable[int], eps: float = 1e-9) -> float:
    import math
    pairs = list(zip(probabilities, outcomes))
    if not pairs:
        return 0.0
    loss = 0.0
    for p, y in pairs:
        p = min(1.0 - eps, max(eps, float(p)))
        loss -= int(y) * math.log(p) + (1 - int(y)) * math.log(1.0 - p)
    return loss / len(pairs)


def expected_calibration_error(
    probabilities: Iterable[float], outcomes: Iterable[int], *, bins: int = 10
) -> float:
    pairs = [(min(1.0, max(0.0, float(p))), int(y)) for p, y in zip(probabilities, outcomes)]
    if not pairs:
        return 0.0
    total = len(pairs)
    ece = 0.0
    for i in range(max(1, bins)):
        lo, hi = i / bins, (i + 1) / bins
        bucket = [(p, y) for p, y in pairs if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if not bucket:
            continue
        confidence = sum(p for p, _ in bucket) / len(bucket)
        accuracy = sum(y for _, y in bucket) / len(bucket)
        ece += len(bucket) / total * abs(confidence - accuracy)
    return ece


def closing_line_value(taken_odds: float, closing_fair_probability: float) -> float:
    """Price-based CLV: positive means the taken price beat the fair close."""
    return float(taken_odds) * float(closing_fair_probability) - 1.0
