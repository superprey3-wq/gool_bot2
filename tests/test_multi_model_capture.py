from __future__ import annotations

from gool_bot2.multi_model_capture import ensure_model_snapshot_capture


class DummyModel:
    def __init__(self):
        self.calls = 0

    def predict(self, record):
        self.calls += 1
        return {
            "blended": {"another_goal": 0.895},
            "trained_probability": {"goal_before_ht": 0.71},
            "gool_analyzer": {
                "another_goal": {"required": True, "passed": True, "blocks": []},
                "goal_before_ht": {"required": True, "passed": False, "blocks": ["live_pressure_low"]},
            },
        }


class DummyWorker:
    def __init__(self):
        self.model = DummyModel()


def test_real_model_prediction_is_captured_for_multi_without_recomputing():
    worker = DummyWorker()
    ensure_calls = {"count": 0}

    def ensure(instance):
        assert instance is worker
        ensure_calls["count"] += 1
        return True

    assert ensure_model_snapshot_capture(worker, ensure)
    result = worker.model.predict({"match": {"minute": 26}})

    assert worker.model.calls == 1
    assert ensure_calls["count"] == 1
    assert worker._diag_model_result["blended"]["another_goal"] == 0.895
    assert worker._diag_model_result["trained_probability"]["goal_before_ht"] == 0.71
    assert worker._diag_model_result["gool_analyzer"]["another_goal"]["passed"] is True

    # The snapshot is independent from later mutation of the result used by the
    # legacy signal path.
    result["blended"]["another_goal"] = 0.10
    assert worker._diag_model_result["blended"]["another_goal"] == 0.895


def test_capture_wrapper_is_installed_only_once_per_model():
    worker = DummyWorker()

    def ensure(_):
        return True

    assert ensure_model_snapshot_capture(worker, ensure)
    wrapped = worker.model.predict
    assert ensure_model_snapshot_capture(worker, ensure)
    assert worker.model.predict is wrapped

    worker.model.predict({})
    assert worker.model.calls == 1
