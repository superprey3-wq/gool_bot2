from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from gool_bot2.ai_debate_reviewer import (
    AdvocateRead,
    DebateCandidate,
    DebateReviewerError,
    JudgeRead,
    OllamaDebateReviewer,
    binary_brier,
    fit_reviewer_weight,
    result_to_binary,
)


class _Response:
    def __init__(self, content: dict):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"message": {"content": json.dumps(self.content)}}).encode()


def candidate(**overrides):
    row = {
        "candidate_id": "evt:over25",
        "event_id": "evt",
        "match": "A - B",
        "market": "match_total",
        "selection": "over 2.5",
        "odds": 1.70,
        "model_probability": 0.72,
        "market_probability": 0.61,
        "edge": 0.11,
        "expected_value": 0.224,
        "data_quality": 0.9,
        "math_qualified": True,
        "evidence": {"expected_total": 3.5},
    }
    row.update(overrides)
    return DebateCandidate.model_validate(row)


def test_math_rejected_candidate_never_calls_llm():
    with patch("urllib.request.urlopen") as mocked:
        verdict = OllamaDebateReviewer().review(candidate(math_qualified=False))
    mocked.assert_not_called()
    assert verdict.decision == "SKIP"
    assert "cannot rescue" in verdict.reason


def test_valid_debate_stays_anchored_to_math():
    for_read = {
        "candidate_id": "evt:over25",
        "stance": "FOR",
        "strength": 0.8,
        "thesis": "Goal profile supports the supplied over.",
        "evidence_points": ["Expected total is elevated."],
        "self_critique": "One team could fail to contribute.",
    }
    against = {
        "candidate_id": "evt:over25",
        "stance": "AGAINST",
        "strength": 0.4,
        "thesis": "The total still depends on conversion.",
        "evidence_points": ["Scoring variance remains."],
        "self_critique": "The expected-total signal is genuinely strong.",
    }
    judge = {
        "decision": "BET",
        "confidence": 0.82,
        "contextual_delta": 0.12,
        "reason": "FOR evidence is stronger and there is no material contradiction.",
        "decisive_risks": ["Finishing variance"],
    }
    with patch(
        "urllib.request.urlopen",
        side_effect=[_Response(for_read), _Response(against), _Response(judge)],
    ):
        verdict = OllamaDebateReviewer(
            reviewer_weight=0.25,
            max_contextual_delta=0.08,
        ).review(candidate())

    assert verdict.decision == "BET"
    assert verdict.raw_contextual_delta == 0.08
    assert verdict.applied_contextual_delta == 0.02
    assert verdict.adjusted_probability == pytest.approx(0.74)
    assert verdict.adjusted_probability - verdict.base_probability <= 0.02 + 1e-9


def test_score_normalization_accepts_small_model_0_to_10():
    a = AdvocateRead.model_validate({
        "candidate_id": "x", "stance": "FOR", "strength": 8,
        "thesis": "x", "evidence_points": [], "self_critique": "y",
    })
    j = JudgeRead.model_validate({
        "decision": "SKIP", "confidence": 9,
        "contextual_delta": -0.02, "reason": "x", "decisive_risks": [],
    })
    assert a.strength == 0.8
    assert j.confidence == 0.9


def test_brier_calibration_finds_helpful_positive_weight():
    records = [
        {"base_probability": 0.55, "raw_contextual_delta": 0.08, "outcome": 1},
        {"base_probability": 0.54, "raw_contextual_delta": 0.08, "outcome": 1},
        {"base_probability": 0.62, "raw_contextual_delta": -0.08, "outcome": 0},
        {"base_probability": 0.60, "raw_contextual_delta": -0.08, "outcome": 0},
    ] * 8
    fit = fit_reviewer_weight(records, prior_weight=0.35, shrink_k=5)
    assert fit is not None
    assert fit.fitted_weight > 0
    assert fit.brier_fitted < fit.brier_base
    assert 0 <= fit.weight <= 1


def test_result_to_binary_ignores_push_and_void():
    assert result_to_binary("won") == 1
    assert result_to_binary("lost") == 0
    assert result_to_binary("push") is None
    assert result_to_binary("void") is None


def test_binary_brier():
    assert binary_brier(0.8, 1) == pytest.approx(0.04)
    assert binary_brier(0.8, 0) == pytest.approx(0.64)
