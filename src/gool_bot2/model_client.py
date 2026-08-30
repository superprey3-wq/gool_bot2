from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from .analyst_prompt import SYSTEM_PROMPT

API_URL = "https://api.openai.com/v1/responses"

ANALYST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "match_id": {"type": "string"},
        "minute": {"type": "integer"},
        "score": {"type": "string"},
        "data_quality": {
            "type": "object",
            "properties": {
                "provider_count": {"type": "integer"},
                "agreement": {"type": "string", "enum": ["high", "medium", "low"]},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["provider_count", "agreement", "warnings"],
            "additionalProperties": False,
        },
        "predictions": {
            "type": "object",
            "properties": {},
            "required": ["another_goal", "goal_before_ht", "two_plus_goals_second_half"],
            "additionalProperties": False,
        },
    },
    "required": ["match_id", "minute", "score", "data_quality", "predictions"],
    "additionalProperties": False,
}

HEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "probability": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "decision": {"type": "string", "enum": ["SIGNAL", "WAIT", "BLOCKED", "NOT_APPLICABLE"]},
        "score_0_100": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "blocks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["probability", "decision", "score_0_100", "reasons", "blocks"],
    "additionalProperties": False,
}
ANALYST_SCHEMA["properties"]["predictions"]["properties"] = {
    "another_goal": HEAD_SCHEMA,
    "goal_before_ht": HEAD_SCHEMA,
    "two_plus_goals_second_half": HEAD_SCHEMA,
}


def _extract_output_text(response: dict[str, Any]) -> str:
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                return str(content.get("text") or "")
    raise RuntimeError("OpenAI response contained no output_text")


def analyse_match(payload: dict[str, Any]) -> dict[str, Any]:
    """Call the configured OpenAI analyst using strict structured output."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    model = os.getenv("OPENAI_MODEL", "gpt-5.6-terra").strip() or "gpt-5.6-terra"
    reasoning = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip() or "medium"
    timeout = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "35") or 35)

    body = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "reasoning": {"effort": reasoning},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "gool_live_analysis",
                "strict": True,
                "schema": ANALYST_SCHEMA,
            },
            "verbosity": "low",
        },
        "store": False,
    }
    req = Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return json.loads(_extract_output_text(data))
