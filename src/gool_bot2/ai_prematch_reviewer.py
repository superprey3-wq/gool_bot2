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


SYSTEM_PROMPT = """You are GOOL's cautious football prematch reviewer.

INPUT CONTRACT
- deterministic_facts are authoritative. Do not invent or recalculate facts.
- allowed_candidates are the ONLY selections you may choose.
- The final result is hidden. Never act as if you know it.
- Never invent injuries, lineups, table position, motivation, news, odds, EV, probabilities, or markets.
- Odds/model_probability/market_probability/edge/expected_value come from GOOL. Do not overwrite them.
- review_score measures only how coherent the football evidence is, not win probability.

DECISION METHOD
1. Read recent-form summaries, scoring/conceding frequencies, H2H, source coverage and GOOL goal profile.
2. Form one likely match scenario.
3. Compare every supplied candidate with that scenario.
4. Prefer the candidate with direct support, fewer assumptions and fewer contradictions.
5. A conservative line is NOT bad because it is easy. Team over 0.5, 1X or X2 can be the best expression of the evidence.
6. A more aggressive line is NOT better merely because the threshold is higher.
7. High-scoring recent matches do not automatically justify Over 2.5. Separate team scoring, BTTS, match total and result direction.
8. Two low-scoring H2Hs do not automatically prove a low total if recent evidence strongly conflicts.
9. If your own reasoning says a supplied candidate is clearly supported, do not return SKIP without naming a concrete contradiction.
10. Choose SKIP when evidence is contradictory, samples are too weak, or every supplied candidate requires a stronger claim than the facts justify.
11. If prices are absent, do not claim value/EV. If odds are present, they are context only; GOOL remains authoritative for mathematical qualification.

Useful pattern:
- evidence strongly supports one team scoring but not the whole match being high-scoring -> prefer that team's conservative scoring candidate when supplied.
- evidence supports avoiding defeat more strongly than an outright win -> 1X/X2 can be preferable when supplied.
- H2H is consistently low but recent games are volatile -> a conservative under line may be supportable while a tighter under may not be.

OUTPUT
Return only the structured object.
For BET, candidate_id MUST be exactly one ID from allowed_candidates.
For SKIP, candidate_id MUST be null.
Keep scenario and reason concise and evidence-based.
avoid_candidate_ids may contain only supplied IDs that are specifically contradicted by the evidence.
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
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]

        last_problem = ""
        last_content = ""
        for attempt in range(2):
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "think": self.think,
                "format": AIPrematchReview.model_json_schema(),
                "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 220},
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
            last_content = content if isinstance(content, str) else repr(content)
            if not isinstance(content, str) or not content.strip():
                last_problem = "empty response"
            else:
                try:
                    review = AIPrematchReview.model_validate_json(content)
                except ValidationError as exc:
                    last_problem = f"invalid structured output: {exc}"
                else:
                    problem = ""
                    if review.decision == "BET":
                        if not review.candidate_id or review.candidate_id not in allowed_ids:
                            problem = (
                                "decision=BET requires candidate_id to be exactly one of "
                                f"{sorted(allowed_ids)!r}; got {review.candidate_id!r}"
                            )
                    elif review.candidate_id not in (None, ""):
                        problem = "decision=SKIP requires candidate_id=null"

                    invalid_avoid = [
                        cid for cid in review.avoid_candidate_ids if cid not in allowed_ids
                    ]
                    if invalid_avoid:
                        problem = f"invented avoid_candidate_ids={invalid_avoid!r}"

                    if not problem:
                        return review
                    last_problem = problem

            if attempt == 0:
                messages = [
                    *messages,
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": (
                            "Your previous structured answer violated the output contract. "
                            f"Problem: {last_problem}. "
                            "Correct it now. If decision is BET, candidate_id MUST be exactly one "
                            f"of these IDs: {sorted(allowed_ids)}. "
                            "If none is sufficiently supported, use decision=SKIP and candidate_id=null. "
                            "Return only the corrected structured object."
                        ),
                    },
                ]

        preview = last_content[:600].replace("\n", " ")
        raise AIReviewerError(
            f"Reviewer failed contract after repair retry: {last_problem}; raw={preview!r}"
        )


def reviewer_enabled() -> bool:
    return os.getenv("GOOL_AI_PREMATCH_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
