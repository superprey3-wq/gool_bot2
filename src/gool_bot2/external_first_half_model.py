from __future__ import annotations

"""Bridge to Lang et al.'s pretrained short-term soccer goal models.

The upstream project ships three trained logistic-regression classifiers and a
MinMaxScaler. We intentionally download those assets from the upstream project
at runtime instead of vendoring/redistributing the binary files in GOOL.

These models predict short-term goal-related events, not the exact GOOL label
"0-0 now -> a goal before halftime". GOOL therefore treats their output as an
external momentum prior that can be combined with the native first-half model,
not as a semantically identical replacement target.
"""

import hashlib
import json
import pickle
import sys
import types
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

UPSTREAM_REPO = "SteffenLa/Shortterm_Soccer_Event_Prediction"
UPSTREAM_BASE = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main/output"

MODEL_SPECS = {
    "rank1": {
        "filename": "models/ApplicationScenario_rank1_LR-OutpOpp_diff-TackWon",
        "features": ("OutpOpp_diff", "TackWon"),
        "lookback": 60,
        "outlook": 36,
    },
    "rank2": {
        "filename": "models/ApplicationScenario_rank2_LR-Danger_diff-TackWon",
        "features": ("TackWon", "Danger_diff"),
        "lookback": 60,
        "outlook": 36,
    },
    "rank3": {
        "filename": "models/ApplicationScenario_rank3_LR-Danger-Entr3rd_diff",
        "features": ("Entr3rd_diff", "Danger"),
        "lookback": 180,
        "outlook": 36,
    },
}

SCALER_FILENAME = "MinMaxScaler.pkl"
PI_LIST = (
    "Shot", "BP", "BP3rd", "BPBox", "Goal", "Cross", "PassBox", "Pass3rd",
    "Corner", "TackWon", "OutpOpp", "EntrBox", "Entr3rd", "Danger",
    "Shot_diff", "BP_diff", "BP3rd_diff", "BPBox_diff", "Goal_diff", "Cross_diff",
    "PassBox_diff", "Pass3rd_diff", "Corner_diff", "TackWon_diff", "OutpOpp_diff",
    "EntrBox_diff", "Entr3rd_diff", "Danger_diff",
)


def _download(url: str, target: Path, timeout: int = 60) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "gool-bot2/0.1 pretrained-model-bridge"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError(f"Downloaded empty pretrained asset: {url}")
    target.write_bytes(payload)


def ensure_assets(cache_dir: Path, ranks: Iterable[str] = ("rank2", "rank3")) -> dict[str, Path]:
    """Download the upstream scaler/models once and return their local paths."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    scaler = cache_dir / SCALER_FILENAME
    if not scaler.exists() or scaler.stat().st_size == 0:
        _download(f"{UPSTREAM_BASE}/{SCALER_FILENAME}", scaler)
    paths["scaler"] = scaler

    for rank in ranks:
        if rank not in MODEL_SPECS:
            raise ValueError(f"Unknown pretrained rank: {rank}")
        filename = str(MODEL_SPECS[rank]["filename"])
        local = cache_dir / Path(filename).name
        if not local.exists() or local.stat().st_size == 0:
            _download(f"{UPSTREAM_BASE}/{filename}", local)
        paths[rank] = local
    return paths


class _CompatLogisticRegressionClassifier:
    """Minimal pickle compatibility shell for the upstream wrapper class."""

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        values = self.model.predict_proba(x)
        if values.ndim == 2 and values.shape[1] >= 2:
            return values[:, 1]
        return np.asarray(values, dtype=float).reshape(-1)


def _install_pickle_compat() -> None:
    """Expose models.linear.LogisticRegressionClassifier for upstream pickles."""
    models_module = sys.modules.setdefault("models", types.ModuleType("models"))
    linear_module = types.ModuleType("models.linear")
    cls = type(
        "LogisticRegressionClassifier",
        (_CompatLogisticRegressionClassifier,),
        {"__module__": "models.linear"},
    )
    linear_module.LogisticRegressionClassifier = cls
    models_module.linear = linear_module
    sys.modules["models.linear"] = linear_module


def _load_pickle(path: Path):
    _install_pickle_compat()
    try:
        import joblib

        return joblib.load(path)
    except Exception:
        with path.open("rb") as handle:
            return pickle.load(handle)


@dataclass
class PretrainedShortTermGoalModel:
    rank: str
    model: object
    scaler: object

    @property
    def spec(self) -> dict[str, object]:
        return MODEL_SPECS[self.rank]

    def predict_sequence(self, selected_sequence: np.ndarray) -> np.ndarray:
        """Predict from an upstream-compatible PI sequence.

        selected_sequence must have shape [n_samples, lookback, 2] and contain
        the two raw PIs declared by MODEL_SPECS[rank]. The bridge restores the
        original 28-PI scaler layout before selecting the two normalized PIs.
        """
        x = np.asarray(selected_sequence, dtype=float)
        lookback = int(self.spec["lookback"])
        features = tuple(self.spec["features"])
        if x.ndim != 3 or x.shape[1:] != (lookback, len(features)):
            raise ValueError(
                f"Expected shape [n, {lookback}, {len(features)}] for {self.rank}; got {x.shape}"
            )

        indices = [PI_LIST.index(name) for name in features]
        extended = np.zeros((x.shape[0], x.shape[1], len(PI_LIST)), dtype=float)
        extended[:, :, indices] = x
        scaled = self.scaler.transform(extended.reshape(-1, len(PI_LIST)))
        scaled = scaled.reshape(extended.shape)[:, :, indices]
        flat = scaled.reshape(len(scaled), -1)
        return np.asarray(self.model.predict_proba(flat), dtype=float).reshape(-1)


def load_pretrained(cache_dir: Path, rank: str = "rank3") -> PretrainedShortTermGoalModel:
    paths = ensure_assets(cache_dir, ranks=(rank,))
    model = _load_pickle(paths[rank])
    scaler = _load_pickle(paths["scaler"])
    return PretrainedShortTermGoalModel(rank=rank, model=model, scaler=scaler)


def asset_manifest(cache_dir: Path, ranks: Iterable[str] = ("rank2", "rank3")) -> dict[str, object]:
    paths = ensure_assets(cache_dir, ranks=ranks)
    files = {}
    for name, path in paths.items():
        payload = path.read_bytes()
        files[name] = {
            "path": str(path),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    return {
        "source": UPSTREAM_REPO,
        "role": "external_short_term_goal_momentum_prior",
        "files": files,
        "model_specs": {rank: MODEL_SPECS[rank] for rank in ranks},
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch and validate pretrained short-term soccer goal models")
    parser.add_argument("--cache-dir", default="data/pretrained/shortterm_soccer")
    parser.add_argument("--ranks", nargs="+", default=["rank2", "rank3"])
    args = parser.parse_args()

    cache = Path(args.cache_dir)
    manifest = asset_manifest(cache, ranks=args.ranks)
    for rank in args.ranks:
        loaded = load_pretrained(cache, rank=rank)
        manifest.setdefault("validated", {})[rank] = {
            "model_class": type(loaded.model).__name__,
            "scaler_class": type(loaded.scaler).__name__,
        }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
