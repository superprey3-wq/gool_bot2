from __future__ import annotations

from types import SimpleNamespace

from gool_bot2.multi_late_refresh import refresh_late_another_goal_model


class _Model:
    def __init__(self, calls):
        self.calls = calls

    def predict(self, record):
        self.calls.append(("predict", int((record.get("match") or {}).get("minute") or 0)))
        return {
            "trained_probability": {"another_goal": 0.81},
            "blended": {"another_goal": 0.79},
            "gool_analyzer": {"another_goal": {"passed": True}},
        }


class _Worker:
    def __init__(self):
        self.calls = []
        self.model = _Model(self.calls)
        self._diag_model_result = {"trained_probability": {"another_goal": 0.70}}

    def _attach_momentum(self, record, match_id):
        self.calls.append(("momentum", match_id, int(record["match"]["minute"])))
        record["live_momentum"] = {"shots_total_last_5m": 2.0}

    def _ensure_model(self):
        self.calls.append(("ensure",))
        return True


def _record(minute: int, finished: bool = False):
    return {
        "match": {
            "flashscore_event_id": "late-fixture",
            "minute": minute,
            "home_score": 1,
            "away_score": 1,
            "is_finished": finished,
        }
    }


def test_late_multi_refresh_recalculates_model_and_momentum_at_80():
    worker = _Worker()
    record = _record(80)

    assert refresh_late_another_goal_model(worker, record) is True
    assert ("momentum", "late-fixture", 80) in worker.calls
    assert ("predict", 80) in worker.calls
    assert worker._diag_model_result["trained_probability"]["another_goal"] == 0.81


def test_late_multi_refresh_is_only_for_76_through_85():
    for minute in (75, 86, 90):
        worker = _Worker()
        assert refresh_late_another_goal_model(worker, _record(minute)) is False
        assert not any(call[0] == "predict" for call in worker.calls)

    worker = _Worker()
    assert refresh_late_another_goal_model(worker, _record(80, finished=True)) is False
    assert not any(call[0] == "predict" for call in worker.calls)
