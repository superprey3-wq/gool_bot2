from __future__ import annotations

from typing import Any


def refresh_late_another_goal_model(worker: Any, record: dict[str, Any]) -> bool:
    """Refresh MODEL + LIVE snapshot for Multi between 76' and 85'.

    The legacy all-strategy worker intentionally stops its normal signal loop after
    75'. Multi keeps only the another-goal strategy alive through 85', so it needs
    a fresh model prediction and fresh 5m/10m momentum instead of reusing the last
    75' snapshot.
    """
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    if bool(match.get("is_finished")) or not (76 <= minute <= 85):
        return False

    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return False

    attach_momentum = getattr(worker, "_attach_momentum", None)
    if callable(attach_momentum):
        attach_momentum(record, match_id)

    ensure_model = getattr(worker, "_ensure_model", None)
    if not callable(ensure_model) or not ensure_model():
        return False

    model = getattr(worker, "model", None)
    predict = getattr(model, "predict", None)
    if not callable(predict):
        return False

    result = predict(record)
    # The production predict wrapper normally captures this already. Keep an
    # explicit copy as a safety net so Multi never falls back to the 75' result.
    worker._diag_model_result = dict(result or {})
    print(
        f"GOOL_MULTI_LATE_MODEL_REFRESH match={match_id} minute={minute} "
        f"heads={','.join(sorted((worker._diag_model_result.get('trained_probability') or {}).keys())) or '-'}",
        flush=True,
    )
    return True
