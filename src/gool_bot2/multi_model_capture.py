from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


def ensure_model_snapshot_capture(worker: Any, ensure_model: Callable[[Any], bool]) -> bool:
    """Wrap the active GOOL model once and retain its real prediction for Multi.

    Multi runs after the legacy worker has processed the same live record.  The
    legacy worker does not expose the model result as a public return value, so
    we capture the exact prediction at its source rather than recomputing the
    model or reconstructing it from Telegram analysis rows.
    """
    ok = bool(ensure_model(worker))
    model = getattr(worker, "model", None)
    if not ok or model is None:
        return ok
    if bool(getattr(model, "_gool_multi_snapshot_wrapped", False)):
        return ok

    current_predict = model.predict

    def predict_with_multi_snapshot(record: dict[str, Any]):
        result = current_predict(record)
        try:
            snapshot = deepcopy(dict(result or {}))
        except Exception:
            snapshot = dict(result or {})
        worker._diag_model_result = snapshot
        return result

    model.predict = predict_with_multi_snapshot
    model._gool_multi_snapshot_wrapped = True
    return ok
