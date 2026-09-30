from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from gool_bot2.ai_prematch_reviewer import (
    AICandidate,
    AIReviewerError,
    OllamaPrematchReviewer,
    reviewer_enabled,
)


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _candidate(cid: str) -> AICandidate:
    return AICandidate(
        candidate_id=cid,
        market="away_total",
        selection="over 0.5",
        odds=1.31,
        model_probability=0.81,
        market_probability=0.75,
        edge=0.06,
        expected_value=0.061,
        data_quality=0.8,
    )


def test_valid_supported_candidate_is_accepted():
    body = {
        "decision": "BET",
        "candidate_id": "evt:away_o05",
        "review_score": 0.82,
        "scenario": "Away side is more likely to create the stronger scoring threat.",
        "reason": "Form and H2H support the supplied conservative away scoring line.",
        "avoid_candidate_ids": [],
    }
    with patch(
        "urllib.request.urlopen",
        return_value=_Response({"message": {"content": json.dumps(body)}}),
    ):
        review = OllamaPrematchReviewer(model="dummy").review(
            match_context={"home": "A", "away": "B"},
            deterministic_facts={"away_scored_2plus_in_last4": 4},
            candidates=[_candidate("evt:away_o05")],
        )
    assert review.decision == "BET"
    assert review.candidate_id == "evt:away_o05"


def test_hallucinated_candidate_is_rejected():
    body = {
        "decision": "BET",
        "candidate_id": "invented:market",
        "review_score": 0.9,
        "scenario": "test",
        "reason": "test",
        "avoid_candidate_ids": [],
    }
    with patch(
        "urllib.request.urlopen",
        return_value=_Response({"message": {"content": json.dumps(body)}}),
    ):
        with pytest.raises(AIReviewerError, match="failed contract after repair retry"):
            OllamaPrematchReviewer(model="dummy").review(
                match_context={},
                deterministic_facts={},
                candidates=[_candidate("evt:away_o05")],
            )


def test_skip_cannot_hide_an_invented_candidate():
    body = {
        "decision": "SKIP",
        "candidate_id": "evt:away_o05",
        "review_score": 0.2,
        "scenario": "Mixed signals.",
        "reason": "No selection is strong enough.",
        "avoid_candidate_ids": [],
    }
    with patch(
        "urllib.request.urlopen",
        return_value=_Response({"message": {"content": json.dumps(body)}}),
    ):
        with pytest.raises(AIReviewerError, match="failed contract after repair retry"):
            OllamaPrematchReviewer(model="dummy").review(
                match_context={},
                deterministic_facts={},
                candidates=[_candidate("evt:away_o05")],
            )



def test_invalid_bet_is_repaired_to_allowed_candidate():
    invalid = {
        "decision": "BET",
        "candidate_id": None,
        "review_score": 0.78,
        "scenario": "Away side has the clearer scoring direction.",
        "reason": "Supported by recent scoring and H2H.",
        "avoid_candidate_ids": [],
    }
    repaired = {
        "decision": "BET",
        "candidate_id": "evt:away_o05",
        "review_score": 0.78,
        "scenario": "Away side has the clearer scoring direction.",
        "reason": "The conservative away scoring line is directly supported.",
        "avoid_candidate_ids": [],
    }
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _Response({"message": {"content": json.dumps(invalid)}}),
            _Response({"message": {"content": json.dumps(repaired)}}),
        ],
    ) as mocked:
        review = OllamaPrematchReviewer(model="dummy").review(
            match_context={"home": "A", "away": "B"},
            deterministic_facts={"away_scored_2plus_in_last4": 4},
            candidates=[_candidate("evt:away_o05")],
        )
    assert mocked.call_count == 2
    assert review.decision == "BET"
    assert review.candidate_id == "evt:away_o05"


def test_no_candidates_short_circuits_to_skip_without_network():
    with patch("urllib.request.urlopen") as mocked:
        review = OllamaPrematchReviewer(model="dummy").review(
            match_context={},
            deterministic_facts={},
            candidates=[],
        )
    mocked.assert_not_called()
    assert review.decision == "SKIP"
    assert review.review_score == 0.0


def test_reviewer_enabled_is_opt_in(monkeypatch):
    monkeypatch.delenv("GOOL_AI_PREMATCH_ENABLED", raising=False)
    assert reviewer_enabled() is False
    monkeypatch.setenv("GOOL_AI_PREMATCH_ENABLED", "true")
    assert reviewer_enabled() is True
