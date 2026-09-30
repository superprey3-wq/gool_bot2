from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class DebateReviewerError(RuntimeError):
    """Raised when the local debate reviewer cannot produce a valid answer."""


def _normalize_score(value):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return value
    if 1.0 < score <= 10.0:
        return score / 10.0
    if 10.0 < score <= 100.0:
        return score / 100.0
    return score


class DebateCandidate(BaseModel):
    candidate_id: str
    event_id: str
    match: str
    market: str
    selection: str
    odds: float = Field(gt=1.0)
    model_probability: float = Field(ge=0.0, le=1.0)
    market_probability: float = Field(ge=0.0, le=1.0)
    edge: float
    expected_value: float
    data_quality: float = Field(ge=0.0, le=1.0)
    math_qualified: bool = True
    evidence: dict = Field(default_factory=dict)


class AdvocateRead(BaseModel):
    candidate_id: str
    stance: Literal["FOR", "AGAINST"]
    strength: float = Field(ge=0.0, le=1.0)
    thesis: str = Field(min_length=1, max_length=280)
    evidence_points: list[str] = Field(default_factory=list, max_length=4)
    self_critique: str = Field(min_length=1, max_length=220)

    @field_validator("strength", mode="before")
    @classmethod
    def normalize_strength(cls, value):
        return _normalize_score(value)


class JudgeRead(BaseModel):
    decision: Literal["BET", "SKIP"]
    candidate_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    contextual_delta: float = Field(ge=-0.12, le=0.12)
    reason: str = Field(min_length=1, max_length=360)
    decisive_risks: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        return _normalize_score(value)


class DebateVerdict(BaseModel):
    decision: Literal["BET", "SKIP"]
    candidate_id: str
    base_probability: float
    adjusted_probability: float
    market_probability: float
    adjusted_edge: float
    adjusted_expected_value: float
    reviewer_weight: float
    raw_contextual_delta: float
    applied_contextual_delta: float
    judge_confidence: float
    reason: str
    for_read: AdvocateRead | None = None
    against_read: AdvocateRead | None = None
    judge_read: JudgeRead | None = None


FOR_PROMPT = """You are the FOR analyst in GOOL's football debate reviewer.

The candidate has already been generated and numerically assessed by GOOL.
Your job is NOT to invent a new bet and NOT to change any probability, odds, edge or EV.

Argue the strongest football case FOR this exact supplied candidate using only supplied evidence.
Rules:
- deterministic evidence is authoritative;
- never invent injuries, lineups, motivation, table position, news or statistics;
- never use the final result;
- candidate_id must be copied exactly;
- identify 1-4 concrete supporting facts;
- you MUST include one honest weakness in self_critique;
- a conservative line is not a weakness by itself;
- be concise.
Return only the structured object.
"""

AGAINST_PROMPT = """You are the AGAINST analyst in GOOL's football debate reviewer.

Stress-test this exact supplied candidate. Your purpose is to find concrete reasons the bet could be wrong.
You are NOT allowed to invent a different bet or alter GOOL's numeric calculations.

Rules:
- deterministic evidence is authoritative;
- never invent injuries, lineups, motivation, table position, news or statistics;
- never use the final result;
- candidate_id must be copied exactly;
- focus on contradictions, small samples, one-sided evidence, market/model disagreement and scenario fragility;
- absence of extra sources is not automatically a fatal flaw when the supplied sample itself is valid;
- you MUST acknowledge the strongest fact that still supports the candidate in self_critique;
- be concise.
Return only the structured object.
"""

JUDGE_PROMPT = """You are the JUDGE in GOOL's football debate reviewer.

GOOL's numeric model is the anchor. The FOR and AGAINST analysts provide qualitative context only.
You may confirm the already-qualified candidate or veto it. You may NOT select another market.

Hard rules:
- candidate_id must be the supplied candidate_id for BET; null for SKIP;
- never invent facts or a new market;
- do not rewrite model_probability, market_probability, odds, edge or EV;
- contextual_delta is only a small qualitative nudge in [-0.12,+0.12];
- GOOL will multiply that delta by a calibrated reviewer weight and cap its effect;
- strong FOR evidence with weak concrete opposition supports BET;
- a concrete contradiction or fragile scenario can justify SKIP;
- do not call 0.70+ model probability "low" in an already-qualified pool;
- data_quality=1.0 means maximum data quality;
- source_coverage values are counts, not percentages;
- if both advocates are plausible, prefer the deterministic model unless AGAINST identifies a material contradiction;
- be concise.
Return only the structured object.
"""


@dataclass(frozen=True)
class CalibrationFit:
    weight: float
    fitted_weight: float
    n: int
    brier_base: float
    brier_fitted: float


class OllamaDebateReviewer:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 90.0,
        reviewer_weight: float = 0.35,
        max_contextual_delta: float = 0.08,
    ) -> None:
        self.base_url = (base_url or os.getenv("GOOL_AI_PREMATCH_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or os.getenv("GOOL_AI_PREMATCH_MODEL") or "qwen3:1.7b"
        self.timeout_seconds = float(timeout_seconds)
        self.reviewer_weight = max(0.0, min(1.0, float(reviewer_weight)))
        self.max_contextual_delta = max(0.0, min(0.20, float(max_contextual_delta)))

    def _chat(self, *, system: str, user_payload: dict, schema_model):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
        ]
        last_problem = ""
        last_content = ""
        for attempt in range(2):
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "think": False,
                "format": schema_model.model_json_schema(),
                "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 260},
            }
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                    raw = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise DebateReviewerError(f"Ollama request failed: {exc}") from exc

            content = raw.get("message", {}).get("content", "")
            last_content = content if isinstance(content, str) else repr(content)
            if not isinstance(content, str) or not content.strip():
                last_problem = "empty response"
            else:
                try:
                    return schema_model.model_validate_json(content)
                except ValidationError as exc:
                    last_problem = str(exc)

            if attempt == 0:
                messages.extend([
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": (
                            "Repair the previous answer. It violated the required JSON schema. "
                            f"Problem: {last_problem}. Return only a corrected structured object."
                        ),
                    },
                ])
        raise DebateReviewerError(
            f"Reviewer failed schema after repair retry: {last_problem}; raw={last_content[:500]!r}"
        )

    def review(self, candidate: DebateCandidate | dict) -> DebateVerdict:
        c = candidate if isinstance(candidate, DebateCandidate) else DebateCandidate.model_validate(candidate)

        if not c.math_qualified:
            return DebateVerdict(
                decision="SKIP",
                candidate_id=c.candidate_id,
                base_probability=c.model_probability,
                adjusted_probability=c.model_probability,
                market_probability=c.market_probability,
                adjusted_edge=c.edge,
                adjusted_expected_value=c.expected_value,
                reviewer_weight=self.reviewer_weight,
                raw_contextual_delta=0.0,
                applied_contextual_delta=0.0,
                judge_confidence=1.0,
                reason="Deterministic GOOL gate rejected the candidate; AI cannot rescue it.",
            )

        payload = {"candidate": c.model_dump()}
        for_read = self._chat(system=FOR_PROMPT, user_payload=payload, schema_model=AdvocateRead)
        against_read = self._chat(system=AGAINST_PROMPT, user_payload=payload, schema_model=AdvocateRead)

        if for_read.candidate_id != c.candidate_id or for_read.stance != "FOR":
            raise DebateReviewerError("FOR analyst violated candidate/stance contract")
        if against_read.candidate_id != c.candidate_id or against_read.stance != "AGAINST":
            raise DebateReviewerError("AGAINST analyst violated candidate/stance contract")

        judge_payload = {
            "candidate": c.model_dump(),
            "for_read": for_read.model_dump(),
            "against_read": against_read.model_dump(),
            "reviewer_weight": self.reviewer_weight,
            "max_contextual_delta": self.max_contextual_delta,
        }
        judge = self._chat(system=JUDGE_PROMPT, user_payload=judge_payload, schema_model=JudgeRead)

        if judge.decision == "BET" and judge.candidate_id != c.candidate_id:
            raise DebateReviewerError(
                f"Judge BET must select exact candidate_id={c.candidate_id!r}; got {judge.candidate_id!r}"
            )
        if judge.decision == "SKIP" and judge.candidate_id not in (None, ""):
            raise DebateReviewerError("Judge SKIP must use candidate_id=null")

        raw_delta = max(-self.max_contextual_delta, min(self.max_contextual_delta, judge.contextual_delta))
        applied_delta = self.reviewer_weight * raw_delta
        adjusted_p = max(0.01, min(0.99, c.model_probability + applied_delta))
        adjusted_edge = adjusted_p - c.market_probability
        adjusted_ev = adjusted_p * c.odds - 1.0

        final_decision: Literal["BET", "SKIP"] = judge.decision
        reason = judge.reason
        if final_decision == "BET" and (adjusted_edge <= 0.0 or adjusted_ev <= 0.0):
            final_decision = "SKIP"
            reason = (
                f"Judge supported the candidate, but the calibrated probability no longer has "
                f"positive edge/EV. {judge.reason}"
            )

        return DebateVerdict(
            decision=final_decision,
            candidate_id=c.candidate_id,
            base_probability=round(c.model_probability, 6),
            adjusted_probability=round(adjusted_p, 6),
            market_probability=round(c.market_probability, 6),
            adjusted_edge=round(adjusted_edge, 6),
            adjusted_expected_value=round(adjusted_ev, 6),
            reviewer_weight=round(self.reviewer_weight, 6),
            raw_contextual_delta=round(raw_delta, 6),
            applied_contextual_delta=round(applied_delta, 6),
            judge_confidence=round(judge.confidence, 6),
            reason=reason,
            for_read=for_read,
            against_read=against_read,
            judge_read=judge,
        )


def binary_brier(probability: float, outcome: int) -> float:
    p = max(0.0, min(1.0, float(probability)))
    y = 1 if int(outcome) else 0
    return (p - y) ** 2


def fit_reviewer_weight(
    records: list[dict],
    *,
    prior_weight: float = 0.35,
    max_contextual_delta: float = 0.08,
    decay: float = 0.90,
    shrink_k: float = 20.0,
) -> CalibrationFit | None:
    usable = []
    for row in records:
        try:
            base = float(row["base_probability"])
            delta = float(row["raw_contextual_delta"])
            outcome = int(row["outcome"])
        except (KeyError, TypeError, ValueError):
            continue
        if outcome not in (0, 1):
            continue
        usable.append((base, delta, outcome))
    if not usable:
        return None

    max_delta = max(0.0, min(0.20, float(max_contextual_delta)))
    weighted = [(decay ** i, row) for i, row in enumerate(reversed(usable))]

    def score(weight: float) -> float:
        total = 0.0
        wsum = 0.0
        for recency_w, (base, delta, outcome) in weighted:
            d = max(-max_delta, min(max_delta, delta))
            p = max(0.01, min(0.99, base + weight * d))
            total += recency_w * binary_brier(p, outcome)
            wsum += recency_w
        return total / wsum if wsum else math.inf

    candidates = [i / 20.0 for i in range(21)]
    fitted = min(candidates, key=score)
    n = len(usable)
    prior = max(0.0, min(1.0, float(prior_weight)))
    shrunk = (n * fitted + float(shrink_k) * prior) / (n + float(shrink_k))
    return CalibrationFit(
        weight=round(shrunk, 4),
        fitted_weight=round(fitted, 4),
        n=n,
        brier_base=round(score(0.0), 6),
        brier_fitted=round(score(fitted), 6),
    )


def calibration_records_from_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_calibration_record(path: str | Path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def result_to_binary(result: str) -> int | None:
    value = str(result or "").strip().casefold()
    if value == "won":
        return 1
    if value == "lost":
        return 0
    return None
