from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class AIReviewerError(RuntimeError):
    """Raised when the local reviewer is unavailable or returns an invalid answer."""


class AICandidate(BaseModel):
    candidate_id: str
    market: str
    selection: str
    odds: float | None = None
    model_probability: float | None = None
    market_probability: float | None = None
    edge: float | None = None
    expected_value: float | None = None
    data_quality: float | None = None


class AIPrematchReview(BaseModel):
    decision: Literal["BET", "SKIP"]
    candidate_id: str | None = None
    review_score: float = Field(ge=0.0, le=1.0)
    scenario: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    avoid_candidate_ids: list[str] = Field(default_factory=list)

    def selected_candidate(self, candidates: list[AICandidate]) -> AICandidate | None:
        if self.decision == "SKIP":
            return None
        by_id = {item.candidate_id: item for item in candidates}
        return by_id.get(str(self.candidate_id or ""))


SYSTEM_PROMPT = """You are GOOL's cautious football prematch second-opinion reviewer.

The deterministic engine has already collected and calculated the football facts.
Treat deterministic_facts as authoritative. Do not recalculate them from raw score strings.
You may only select a candidate_id present in allowed_candidates, or choose SKIP.
Never invent a market, line, bookmaker price, injury, table position, result, or news item.
The final match result is hidden and must never be guessed as known information.

Process:
1. Understand the likely football scenario from deterministic_facts.
2. Compare the supplied candidate markets against that scenario.
3. Prefer the candidate whose direction is best supported by the evidence.
4. If evidence is contradictory, too thin, or no supplied candidate fits well, choose SKIP.
5. Do not automatically choose Over 2.5 just because one team has high-scoring recent matches.
6. review_score is only the strength of your qualitative review, NOT a calibrated probability.
7. Do not overwrite or reinterpret model_probability, market_probability, edge, EV or odds.
8. Return only the requested structured object.
"""


class OllamaPrematchReviewer:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        think: bool = False,
    ) -> None:
        self.base_url = (base_url or os.getenv("GOOL_AI_PREMATCH_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or os.getenv("GOOL_AI_PREMATCH_MODEL") or "qwen3:4b"
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("GOOL_AI_PREMATCH_TIMEOUT_SECONDS", "120")
        )
        self.think = bool(think)

    def review(
        self,
        *,
        match_context: dict,
        deterministic_facts: dict,
        candidates: list[AICandidate | dict],
    ) -> AIPrematchReview:
        parsed_candidates = [
            item if isinstance(item, AICandidate) else AICandidate.model_validate(item)
            for item in candidates
        ]
        if not parsed_candidates:
            return AIPrematchReview(
                decision="SKIP",
                candidate_id=None,
                review_score=0.0,
                scenario="No supported priced candidates were supplied.",
                reason="There is nothing valid for the reviewer to choose.",
                avoid_candidate_ids=[],
            )

        allowed_ids = {item.candidate_id for item in parsed_candidates}
        user_payload = {
            "match_context": match_context,
            "deterministic_facts": deterministic_facts,
            "allowed_candidates": [item.model_dump(exclude_none=True) for item in parsed_candidates],
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "stream": False,
            "think": self.think,
            "format": AIPrematchReview.model_json_schema(),
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AIReviewerError(f"Ollama reviewer request failed: {exc}") from exc

        content = raw_response.get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise AIReviewerError("Ollama reviewer returned an empty response")

        try:
            review = AIPrematchReview.model_validate_json(content)
        except ValidationError as exc:
            raise AIReviewerError(f"Ollama reviewer returned invalid structured output: {exc}") from exc

        if review.decision == "BET":
            if not review.candidate_id or review.candidate_id not in allowed_ids:
                raise AIReviewerError(
                    f"Reviewer selected unsupported candidate_id={review.candidate_id!r}"
                )
        elif review.candidate_id not in (None, ""):
            raise AIReviewerError("SKIP response must not select candidate_id")

        invalid_avoid = [cid for cid in review.avoid_candidate_ids if cid not in allowed_ids]
        if invalid_avoid:
            raise AIReviewerError(
                f"Reviewer invented avoid_candidate_ids={invalid_avoid!r}"
            )
        return review


def reviewer_enabled() -> bool:
    return os.getenv("GOOL_AI_PREMATCH_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
