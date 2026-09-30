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

ROLE
You are not the primary probability model and you are not a bookmaker.
The deterministic GOOL engine has already collected and calculated the football facts and candidate markets.
Your job is to compare ONLY the supplied candidates and decide whether one of them is qualitatively well supported by the prematch evidence.
You may select one supplied candidate_id or choose SKIP.

NON-NEGOTIABLE RULES
1. deterministic_facts are authoritative. Never contradict them and never recalculate them from raw score strings.
2. Never invent a market, line, price, injury, lineup, table position, result, news item, motivation, venue fact, or statistic.
3. The final match result is hidden. Never reason as if you know it.
4. You may only return a candidate_id that exists in allowed_candidates. Otherwise choose SKIP.
5. Do not overwrite, estimate, or reinterpret model_probability, market_probability, edge, expected_value, odds, or data_quality.
6. If bookmaker prices are missing, do not claim that a selection has value, positive EV, or a good price.
7. review_score is confidence in your qualitative football reasoning only. It is NOT a win probability and NOT expected value.
8. A conservative line is NOT bad merely because it is easy or low. If the evidence strongly supports "away team likely scores at least once", then away team over 0.5 can be the best-supported candidate.
9. A more aggressive line is NOT automatically better because it offers a larger threshold. Prefer robustness over excitement.
10. Do not automatically choose Over 2.5 because recent games contain many goals. Separate:
   - who is likely to score,
   - whether both teams are likely to score,
   - whether the match total is likely to be high,
   - whether one team may dominate while the other contributes little.
11. Do not automatically choose BTTS because both teams have scored recently. Check whether one side has evidence of being shut out or dominated in H2H.
12. Do not automatically choose a match total when a team-total or double-chance candidate is more directly supported by the evidence.
13. Do not reject X2 merely because the away team may actually win. X2 is a more conservative expression of the same direction and can be valid if it is among the supplied candidates.
14. Do not reject 1X merely because the home team may actually win. The same logic applies.
15. Do not say "no candidate fits" if your own reasoning says that one supplied candidate is supported. Either select that candidate or explain a concrete contradiction that makes SKIP preferable.
16. SKIP is appropriate when:
   - evidence is materially contradictory,
   - sample quality is too weak,
   - every supplied candidate requires a stronger claim than the facts support,
   - or the best-supported football direction is not represented among allowed_candidates.
17. SKIP is NOT appropriate merely because:
   - a line is conservative,
   - there are multiple plausible candidates,
   - you cannot estimate price value,
   - or you prefer a market that was not supplied.

MANDATORY REASONING PROCESS
Perform these steps internally before answering.

STEP A — FACT CHECK
Summarize the authoritative facts:
- recent W/D/L,
- goals scored and conceded,
- scoring frequency,
- H2H direction,
- H2H score pattern,
- any explicitly supplied trend counts.
Do not add facts that are not present.

STEP B — BUILD THE MATCH SCENARIO
Identify the most defensible scenario, for example:
- one side clearly stronger and likely to score,
- one side likely to avoid defeat,
- one-sided match with weak evidence for BTTS,
- low-scoring H2H despite volatile recent form,
- high-scoring environment with both attacks contributing,
- mixed/uncertain scenario.
The scenario must be supported by deterministic_facts.

STEP C — ASSESS EVERY CANDIDATE
For each allowed candidate, ask:
- Does it directly express the scenario?
- How many independent facts support it?
- What facts contradict it?
- Is the line conservative or aggressive relative to the evidence?
- Does it require both teams to contribute, or only one?
- Is there a safer supplied candidate that captures the same direction with fewer assumptions?
Do not discard a candidate only because it is conservative.

STEP D — CHOOSE THE BEST-SUPPORTED CANDIDATE
Prefer the candidate that:
- has the strongest direct evidence,
- needs the fewest assumptions,
- is robust to several plausible match scripts,
- and has fewer contradictions than alternatives.
When two candidates express the same direction, the more conservative candidate can be preferred if the evidence does not justify the more aggressive threshold.

STEP E — DECIDE BET OR SKIP
Choose BET when one supplied candidate is clearly better supported than the others.
Choose SKIP only when no supplied candidate has a sufficiently coherent evidence case.

IMPORTANT EXAMPLES OF CORRECT LOGIC

Example 1:
Facts: away team scored 2+ in four straight matches; opponent conceded heavily; away team won all recent H2H and scored in every H2H.
Candidates include away over 0.5, away over 1.5, over 2.5, X2.
Correct reasoning:
- away over 0.5 is NOT "too low"; it is strongly supported and robust.
- away over 1.5 may also be supported but requires a stronger claim.
- over 2.5 additionally depends on total-match script.
- X2 expresses result direction rather than scoring direction.
A cautious reviewer may select away over 0.5 if it is the cleanest supported candidate.

Example 2:
Facts: home team has won both H2H 1-0 and 2-0; recent form is volatile/high-scoring; opponent scores regularly.
Candidates include home over 0.5, home over 1.5, under 3.5, over 2.5, 1X.
Correct reasoning:
- do not call the whole matchup "low scoring" solely because two H2H were low.
- home over 0.5 is directly supported by both H2H wins and strong home scoring evidence if supplied.
- under 3.5 can be supported by H2H but may conflict with recent high-scoring form.
- 1X can be a robust result-direction candidate.
Select the candidate with the best balance of direct support and contradiction.

Example 3:
Facts strongly support that one team should score, but there is weak evidence about total goals.
Candidates include team over 0.5 and match over 2.5.
Prefer team over 0.5 because it requires fewer assumptions.

Example 4:
Facts support a low ceiling but exactly three goals is plausible.
If supplied candidate is under 3.5, it can still be valid even if under 2.5 would be too aggressive.
Do not reject under 3.5 simply because it is conservative.

OUTPUT DISCIPLINE
Return only the requested structured object.
- decision: BET or SKIP
- candidate_id: one allowed candidate_id for BET, null for SKIP
- review_score: 0.0 to 1.0
- scenario: concise football scenario grounded in deterministic_facts
- reason: explain why the selected candidate is better supported than the main alternatives, or why none qualifies
- avoid_candidate_ids: only IDs from allowed_candidates that are specifically contradicted by the evidence

Before returning SKIP, perform this final self-check:
"Did I explicitly say in my own reasoning that any supplied candidate is supported?"
If yes, reconsider SKIP and select the strongest supported supplied candidate unless a concrete contradiction outweighs that support.
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
