from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .match_context import card_context, provider_count, xg_or_proxy_pair


@dataclass(frozen=True)
class LiveArgumentDecision:
    market: str
    decision: str
    for_score: float
    against_score: float
    judge_score: float
    confidence: float
    reasons_for: tuple[str, ...]
    reasons_against: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _sum_window(window: dict[str, Any] | None, alias: str) -> float | None:
    if not isinstance(window, dict):
        return None
    h = _num(window.get(f"home_{alias}"))
    a = _num(window.get(f"away_{alias}"))
    if h is None or a is None:
        return None
    return max(0.0, h) + max(0.0, a)


def _clamp100(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def evaluate_argument_judge(record: dict[str, Any]) -> LiveArgumentDecision | None:
    """Independent numeric FOR/AGAINST/JUDGE for live goal markets.

    No LLM is used. The module consumes the same live record and Brain V3 memory
    but scores positive and negative evidence separately before a deterministic
    judge combines them.
    """
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    if bool(match.get("is_halftime")) or bool(match.get("is_finished")):
        return None
    if 8 <= minute <= 43:
        market = "GOAL_BEFORE_HT"
        minutes_left = max(0, 47 - minute)
    elif 46 <= minute <= 85:
        market = "ANOTHER_GOAL"
        minutes_left = max(0, 95 - minute)
    else:
        return None

    memory = dict(record.get("brain_v3_memory") or {})
    windows = dict(memory.get("windows") or {})
    w5 = windows.get("5m")
    w10 = windows.get("10m")
    pressure = dict(memory.get("pressure") or {})
    epoch = dict(memory.get("score_epoch") or {})

    xg5 = _sum_window(w5, "xg")
    shots5 = _sum_window(w5, "shots")
    sot5 = _sum_window(w5, "sot")
    big5 = _sum_window(w5, "big")
    xg10 = _sum_window(w10, "xg")
    sot10 = _sum_window(w10, "sot")

    try:
        xh, xa, xg_source, _ = xg_or_proxy_pair(record)
    except Exception:
        xh = xa = None
        xg_source = "unavailable"
    cumulative_xg = None if xh is None or xa is None else max(0.0, float(xh)) + max(0.0, float(xa))

    state = str(pressure.get("state") or "NO_DATA")
    home_trend = str(((pressure.get("home_trend") or {}).get("state") or "WARMING"))
    away_trend = str(((pressure.get("away_trend") or {}).get("state") or "WARMING"))
    strongest_trend = "RISING" if "RISING" in {home_trend, away_trend} else (
        "FALLING" if "FALLING" in {home_trend, away_trend} else "STEADY"
    )

    for_score = 0.0
    reasons_for: list[str] = []

    if xg5 is not None:
        part = min(28.0, 28.0 * xg5 / 0.38)
        for_score += part
        reasons_for.append(f"xg5={xg5:.2f}(+{part:.1f})")
    if sot5 is not None:
        part = min(16.0, 8.0 * sot5)
        for_score += part
        reasons_for.append(f"sot5={sot5:.0f}(+{part:.1f})")
    if big5 is not None:
        part = min(16.0, 16.0 * big5)
        for_score += part
        if big5 > 0:
            reasons_for.append(f"big5={big5:.0f}(+{part:.1f})")
    if shots5 is not None:
        part = min(10.0, 2.0 * shots5)
        for_score += part
        reasons_for.append(f"shots5={shots5:.0f}(+{part:.1f})")
    if state in {"HOME_SIEGE", "AWAY_SIEGE", "END_TO_END"}:
        for_score += 14.0
        reasons_for.append(f"pressure={state}(+14)")
    elif state in {"HOME_PRESSURE", "AWAY_PRESSURE", "HOME_BUILDING", "AWAY_BUILDING"}:
        for_score += 9.0
        reasons_for.append(f"pressure={state}(+9)")
    if strongest_trend == "RISING":
        for_score += 7.0
        reasons_for.append("trend=RISING(+7)")
    if xg10 is not None and xg10 >= 0.45:
        for_score += 5.0
        reasons_for.append(f"xg10={xg10:.2f}(+5)")
    elif sot10 is not None and sot10 >= 3:
        for_score += 4.0
        reasons_for.append(f"sot10={sot10:.0f}(+4)")
    sources = provider_count(record)
    if sources >= 2:
        for_score += 4.0
        reasons_for.append(f"sources={sources}(+4)")

    against_score = 0.0
    reasons_against: list[str] = []
    if w5 is None:
        against_score += 35.0
        reasons_against.append("no_5m_window(+35)")
    if xg5 is not None and xg5 < 0.12:
        against_score += 18.0
        reasons_against.append(f"low_xg5={xg5:.2f}(+18)")
    if (sot5 is not None and sot5 <= 0) and (big5 is not None and big5 <= 0):
        against_score += 14.0
        reasons_against.append("no_sot_or_big5(+14)")
    if state in {"CALM", "NO_DATA", "WARMING"}:
        against_score += 16.0
        reasons_against.append(f"pressure={state}(+16)")
    if strongest_trend == "FALLING":
        against_score += 12.0
        reasons_against.append("trend=FALLING(+12)")
    epoch_age = int(epoch.get("age_minutes") or 0)
    epoch_samples = int(epoch.get("samples") or 0)
    if epoch_age < 3 and sum(epoch.get("score") or [0, 0]) > 0:
        against_score += 24.0
        reasons_against.append("post_goal_reset(+24)")
    if epoch_samples < 4:
        against_score += 12.0
        reasons_against.append(f"epoch_samples={epoch_samples}(+12)")

    cards = card_context(record)
    reds = int(cards.get("home_red") or 0) + int(cards.get("away_red") or 0)
    if reds:
        against_score += 10.0
        reasons_against.append(f"red_cards={reds}(+10)")

    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    margin = abs(hs - aws)
    if market == "ANOTHER_GOAL" and minute >= 78:
        against_score += min(12.0, max(0.0, (minute - 77) * 1.5))
        reasons_against.append(f"late_clock={minute}(+clock)")
    if margin >= 3 and minute >= 60:
        against_score += 12.0
        reasons_against.append(f"large_margin={margin}(+12)")
    if cumulative_xg is not None and minute >= 25:
        expected_rate = cumulative_xg / max(1.0, float(minute))
        if expected_rate < 0.014:
            against_score += 8.0
            reasons_against.append(f"low_cumulative_rate={expected_rate:.3f}(+8)")

    for_score = _clamp100(for_score)
    against_score = _clamp100(against_score)

    # A separate judge: positive evidence must both be strong and materially
    # outweigh the counter-case. This is intentionally independent of Brain V3's
    # probability formula.
    judge_score = _clamp100(48.0 + 0.62 * for_score - 0.72 * against_score)
    evidence_ready = w5 is not None and epoch_samples >= 4
    if evidence_ready and for_score >= 55.0 and against_score <= 42.0 and judge_score >= 62.0:
        decision = "BET"
    elif evidence_ready and judge_score >= 52.0:
        decision = "WATCH"
    else:
        decision = "NO_BET"

    confidence = _clamp100(
        35.0
        + 6.0 * min(sources, 3)
        + (12.0 if xg_source == "provider_xg" else 5.0)
        + (10.0 if w10 is not None else 0.0)
        + min(18.0, abs(for_score - against_score) * 0.35)
    ) / 100.0

    return LiveArgumentDecision(
        market=market,
        decision=decision,
        for_score=round(for_score, 2),
        against_score=round(against_score, 2),
        judge_score=round(judge_score, 2),
        confidence=round(confidence, 3),
        reasons_for=tuple(reasons_for),
        reasons_against=tuple(reasons_against),
    )
