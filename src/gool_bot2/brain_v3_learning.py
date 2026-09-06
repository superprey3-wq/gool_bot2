from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_INSTALLED = False
_LOCK = threading.RLock()
_STATE_CACHE: dict[str, Any] | None = None
_STATE_CACHE_PATH: str | None = None
_BOOTSTRAPPED_JOURNALS: set[str] = set()
_DYNAMIC_BLOCKS = {
    "brain_v3_probability_below_bet",
    "brain_v3_wait_confirmation",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _state_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = str(os.getenv("GOOL_BRAIN_V3_LEARNING_PATH", "")).strip()
    return Path(raw) if raw else runtime / "live" / "brain_v3_learning.json"


def _events_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = str(os.getenv("GOOL_BRAIN_V3_LEARNING_EVENTS_PATH", "")).strip()
    return Path(raw) if raw else runtime / "live" / "brain_v3_learning_events.jsonl"


def _journal_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = str(os.getenv("GOOL_MULTI_JOURNAL_PATH", "")).strip()
    return Path(raw) if raw else runtime / "live" / "gool_multi_journal.json"


def _empty_stats() -> dict[str, Any]:
    return {
        "n": 0,
        "wins": 0,
        "losses": 0,
        "predicted_sum": 0.0,
        "brier_sum": 0.0,
    }


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "totals": _empty_stats(),
        "buckets": {},
        "processed": {},
        "recent_reviews": [],
    }


def _normalize_state(payload: Any) -> dict[str, Any]:
    state = dict(payload) if isinstance(payload, dict) else _empty_state()
    state.setdefault("version", 1)
    state.setdefault("updated_at", None)
    state.setdefault("totals", _empty_stats())
    state.setdefault("buckets", {})
    state.setdefault("processed", {})
    state.setdefault("recent_reviews", [])
    return state


def _load_state() -> dict[str, Any]:
    global _STATE_CACHE, _STATE_CACHE_PATH
    path = _state_path()
    key = str(path)
    if _STATE_CACHE is not None and _STATE_CACHE_PATH == key:
        return _STATE_CACHE
    try:
        payload = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except Exception:
        payload = {}
    _STATE_CACHE = _normalize_state(payload)
    _STATE_CACHE_PATH = key
    return _STATE_CACHE


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _append_event(payload: dict[str, Any]) -> None:
    try:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        print(f"GOOL_BRAIN_V3_LEARN_EVENT_ERROR {type(exc).__name__}:{exc}", flush=True)


def _brain_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    direct = row.get("brain_v3_entry")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    strategy = str(row.get("strategy") or "")
    expert = ((row.get("experts") or {}).get(strategy) or {}) if strategy else {}
    diagnostics = expert.get("diagnostics") or {}
    brain = diagnostics.get("brain_v3") or {}
    return dict(brain) if isinstance(brain, dict) else {}


def _is_brain_v3_row(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "")
    signal_source = str(row.get("signal_source") or "")
    if source.startswith("1xbet:autonomous_steam") or signal_source == "STEAM_OVERRIDE":
        return False
    brain = _brain_snapshot(row)
    return bool(brain and int(_number(brain.get("version")) or 0) >= 3)


def _band(value: float | None, cuts: tuple[float, ...], labels: tuple[str, ...]) -> str:
    if value is None:
        return "unknown"
    for cut, label in zip(cuts, labels):
        if value < cut:
            return label
    return labels[-1]


def _minute_band(period: str, minute: int) -> str:
    if period == "1H":
        if minute <= 15:
            return "1H_01_15"
        if minute <= 25:
            return "1H_16_25"
        return "1H_26_35"
    if minute <= 55:
        return "2H_46_55"
    if minute <= 65:
        return "2H_56_65"
    return "2H_66_75"


def _score_context(score: Any) -> str:
    try:
        hs, aws = int(score[0]), int(score[1])
    except Exception:
        return "unknown"
    margin = abs(hs - aws)
    if margin == 0:
        return "level"
    if margin == 1:
        return "margin1"
    return "margin2plus"


def _pattern_keys(brain: dict[str, Any]) -> list[str]:
    period = str(brain.get("period") or "UNK")
    state = str(brain.get("match_state") or "NO_DATA")
    minute = int(_number(brain.get("minute")) or 0)
    recent = brain.get("recent") or {}
    xg5 = _number(recent.get("xg5"))
    xg10 = _number(recent.get("xg10"))
    pressure = _number(brain.get("pressure_index"))
    hp = _number(brain.get("home_pressure")) or 0.0
    ap = _number(brain.get("away_pressure")) or 0.0
    strongest_trend = str(brain.get("home_trend") if hp >= ap else brain.get("away_trend") or "WARMING")
    source = str(recent.get("xg5_source") or brain.get("cumulative_xg_source") or "unknown")
    prematch = str(((brain.get("prematch") or {}).get("label")) or "unknown")
    return [
        "global",
        f"period:{period}",
        f"state:{period}:{state}",
        f"minute:{_minute_band(period, minute)}",
        f"score:{period}:{_score_context(brain.get('score'))}",
        f"pressure:{period}:{_band(pressure, (0.55, 0.72), ('low', 'medium', 'high'))}",
        f"xg5:{period}:{_band(xg5, (0.15, 0.25, 0.40), ('low', 'medium', 'strong', 'very_strong'))}",
        f"xg10:{period}:{_band(xg10, (0.28, 0.45, 0.65), ('low', 'medium', 'strong', 'very_strong'))}",
        f"trend:{period}:{strongest_trend}",
        f"source:{period}:{source}",
        f"prematch:{period}:{prematch}",
    ]


def _update_stats(stats: dict[str, Any], outcome: str, p: float, sign: int) -> None:
    if outcome not in {"won", "lost"}:
        return
    y = 1.0 if outcome == "won" else 0.0
    stats["n"] = max(0, int(stats.get("n") or 0) + sign)
    stats["wins"] = max(0, int(stats.get("wins") or 0) + (sign if outcome == "won" else 0))
    stats["losses"] = max(0, int(stats.get("losses") or 0) + (sign if outcome == "lost" else 0))
    stats["predicted_sum"] = max(0.0, float(stats.get("predicted_sum") or 0.0) + sign * p)
    stats["brier_sum"] = max(0.0, float(stats.get("brier_sum") or 0.0) + sign * ((p - y) ** 2))


def _apply_observation(state: dict[str, Any], outcome: str, p: float, keys: list[str], sign: int) -> None:
    _update_stats(state.setdefault("totals", _empty_stats()), outcome, p, sign)
    buckets = state.setdefault("buckets", {})
    for key in keys:
        stats = buckets.setdefault(key, _empty_stats())
        _update_stats(stats, outcome, p, sign)
        if int(stats.get("n") or 0) <= 0:
            buckets.pop(key, None)


def _bucket_gap(stats: dict[str, Any], minimum: int) -> tuple[float, int] | None:
    n = int(stats.get("n") or 0)
    if n < minimum:
        return None
    predicted = float(stats.get("predicted_sum") or 0.0) / max(1, n)
    wins = int(stats.get("wins") or 0)
    prior_strength = max(4.0, float(os.getenv("GOOL_BRAIN_V3_LEARN_PRIOR_STRENGTH", "12")))
    empirical = (wins + prior_strength * predicted) / (n + prior_strength)
    maturity = min(1.0, 0.25 + max(0, n - minimum) / max(10.0, minimum * 1.5))
    return (empirical - predicted) * maturity, n


def learning_adjustment(brain: dict[str, Any]) -> dict[str, Any]:
    if not _truthy("GOOL_BRAIN_V3_LEARNING", True):
        return {"active": False, "samples": 0, "adjustment_pp": 0.0, "reason": "disabled"}
    with _LOCK:
        state = _load_state()
        totals = state.get("totals") or {}
        total_n = int(totals.get("n") or 0)
        min_global = max(10, int(float(os.getenv("GOOL_BRAIN_V3_LEARN_MIN_GLOBAL", "30"))))
        min_pattern = max(8, int(float(os.getenv("GOOL_BRAIN_V3_LEARN_MIN_PATTERN", "15"))))
        if total_n < min_global:
            return {
                "active": False,
                "samples": total_n,
                "adjustment_pp": 0.0,
                "reason": "collecting_samples",
                "minimum": min_global,
            }

        buckets = state.get("buckets") or {}
        weighted: list[tuple[float, float, str, int]] = []
        for key in _pattern_keys(brain):
            stats = buckets.get(key) or {}
            minimum = min_global if key == "global" else min_pattern
            row = _bucket_gap(stats, minimum)
            if row is None:
                continue
            gap, n = row
            specificity = 0.65 if key == "global" else 1.0
            weight = specificity * min(4.0, math.sqrt(max(1, n) / minimum))
            weighted.append((gap, weight, key, n))
        if not weighted:
            return {
                "active": False,
                "samples": total_n,
                "adjustment_pp": 0.0,
                "reason": "patterns_immature",
            }

        raw = sum(gap * weight for gap, weight, _, _ in weighted) / sum(weight for _, weight, _, _ in weighted)
        max_pp = max(0.5, min(5.0, float(os.getenv("GOOL_BRAIN_V3_LEARN_MAX_ADJUST_PP", "3.0"))))
        adjustment = _clamp(raw, -max_pp / 100.0, max_pp / 100.0)
        contributors = sorted(weighted, key=lambda item: item[1], reverse=True)[:5]
        return {
            "active": True,
            "samples": total_n,
            "adjustment_pp": round(adjustment * 100.0, 2),
            "raw_gap_pp": round(raw * 100.0, 2),
            "contributors": [
                {"key": key, "n": n, "gap_pp": round(gap * 100.0, 2)}
                for gap, _, key, n in contributors
            ],
        }


def calibrate_brain_v3_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Apply slow outcome calibration without bypassing any football safety gate."""
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision
    probability = _number(decision.get("probability"))
    if probability is None:
        return decision
    learning = learning_adjustment(decision)
    adjustment_pp = float(learning.get("adjustment_pp") or 0.0)
    adjusted = probability + adjustment_pp / 100.0
    cap = _number(decision.get("confidence_cap"))
    if cap is not None:
        adjusted = min(adjusted, cap)
    adjusted = _clamp(adjusted, 0.01, 0.99)

    decision["base_probability_before_learning"] = round(probability, 4)
    decision["probability"] = round(adjusted, 4)
    decision["confidence_score"] = round(adjusted * 100.0, 1)
    decision["learning"] = learning

    if abs(adjustment_pp) >= 0.05:
        thoughts = list(decision.get("thoughts") or [])
        thoughts.append(f"learning={adjustment_pp:+.1f}pp/{int(learning.get('samples') or 0)}")
        decision["thoughts"] = thoughts

    blocks = [str(row) for row in list(decision.get("blocks") or []) if str(row) not in _DYNAMIC_BLOCKS]
    bet_min = float(_number(decision.get("bet_min")) or 0.70)
    ready_min = float(_number(decision.get("ready_min")) or 0.60)
    live_foundation = bool(decision.get("live_foundation"))
    bet_ready = bool(
        live_foundation
        and bool(decision.get("sustained_pressure"))
        and "brain_v3_pressure_falling" not in blocks
    )
    if not live_foundation:
        status = "WATCH"
    elif adjusted >= bet_min and bet_ready:
        status = "BET"
    elif adjusted >= ready_min:
        status = "READY"
    else:
        status = "WATCH"
    if status != "BET":
        if adjusted < bet_min:
            blocks.append("brain_v3_probability_below_bet")
        elif not bet_ready:
            blocks.append("brain_v3_wait_confirmation")
    decision["status"] = status
    decision["blocks"] = list(dict.fromkeys(blocks))
    return decision


def _parse_pair(value: Any) -> tuple[float, float] | None:
    text = str(value or "")
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    if len(numbers) < 2:
        return None
    try:
        return float(numbers[0]), float(numbers[1])
    except ValueError:
        return None


def _stat_delta(row: dict[str, Any], key: str) -> float | None:
    before = _parse_pair((row.get("stats_snapshot") or {}).get(key))
    after = _parse_pair((row.get("settled_stats_snapshot") or {}).get(key))
    if before is None or after is None:
        return None
    return max(0.0, (after[0] + after[1]) - (before[0] + before[1]))


def _goal_after_entry(row: dict[str, Any], record: dict[str, Any] | None) -> int | None:
    if not isinstance(record, dict):
        return None
    meta = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {})
    timeline = list(meta.get("goal_timeline") or [])
    entry_minute = int(row.get("minute") or 0)
    max_minute = 45 if str(row.get("market_family") or "") == "first_half_total" else 200
    for event in sorted(timeline, key=lambda item: int(_number(item.get("minute")) or 0)):
        if str(event.get("event_type") or "goal").strip().lower() not in {"", "goal"}:
            continue
        minute = int(_number(event.get("minute")) or 0)
        if entry_minute < minute <= max_minute:
            return minute
    return None


def _diagnose(row: dict[str, Any], brain: dict[str, Any], record: dict[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    outcome = str(row.get("result") or "").lower()
    recent = brain.get("recent") or {}
    xg5 = _number(recent.get("xg5"))
    xg10 = _number(recent.get("xg10"))
    sot5 = _number(recent.get("sot5"))
    big5 = _number(recent.get("big5"))
    p = _number(brain.get("probability")) or _number(row.get("probability")) or 0.0
    minute = int(row.get("minute") or 0)
    state = str(brain.get("match_state") or "NO_DATA")
    source = str(recent.get("xg5_source") or brain.get("cumulative_xg_source") or "unknown")
    prematch_pp = _number(((brain.get("prematch") or {}).get("adjustment_pp"))) or 0.0

    post = {
        "minutes_observed": max(0, int(row.get("settled_minute") or minute) - minute),
        "goal_after_entry_minute": _goal_after_entry(row, record),
        "shots_delta": _stat_delta(row, "shots"),
        "sot_delta": _stat_delta(row, "sot"),
        "xg_delta": _stat_delta(row, "xg"),
        "big_chances_delta": _stat_delta(row, "big_chances"),
    }
    reasons: list[str] = []
    if outcome == "lost":
        if p >= 0.80:
            reasons.append("high_confidence_miss")
        if xg5 is not None and xg5 >= 0.25 and (xg10 is None or xg10 < max(0.32, xg5 * 1.25)):
            reasons.append("short_burst_overweighted")
        if state.endswith("BUILDING"):
            reasons.append("building_pressure_failed_to_mature")
        if (post["shots_delta"] is not None and post["shots_delta"] <= 2.0) and (post["sot_delta"] is None or post["sot_delta"] <= 0.0):
            reasons.append("pressure_died_after_entry")
        elif (post["shots_delta"] is not None and post["shots_delta"] >= 4.0) and (post["sot_delta"] is not None and post["sot_delta"] <= 1.0):
            reasons.append("volume_without_quality_after_entry")
        elif (post["sot_delta"] is not None and post["sot_delta"] >= 2.0) or (post["xg_delta"] is not None and post["xg_delta"] >= 0.35):
            reasons.append("finishing_variance_despite_pressure")
        if (big5 is None or big5 <= 0.0) and (sot5 is None or sot5 < 2.0):
            reasons.append("entry_chance_quality_thin")
        if source == "attack_proxy":
            reasons.append("proxy_data_uncertainty")
        if prematch_pp >= 1.5:
            reasons.append("prematch_support_did_not_convert")
        if minute >= 70:
            reasons.append("late_window_conversion_risk")
        if not reasons:
            reasons.append("normal_football_variance")
    elif outcome == "won":
        goal_minute = post.get("goal_after_entry_minute")
        if goal_minute is not None and int(goal_minute) - minute <= 10:
            reasons.append("fast_conversion_after_signal")
        if xg5 is not None and xg5 >= 0.25:
            reasons.append("recent_xg_confirmed")
        if sot5 is not None and sot5 >= 2.0:
            reasons.append("recent_sot_confirmed")
        if state in {"HOME_SIEGE", "AWAY_SIEGE", "END_TO_END"}:
            reasons.append("strong_match_state_confirmed")
        if not reasons:
            reasons.append("signal_converted")
    else:
        reasons.append("void_no_learning")
    return list(dict.fromkeys(reasons)), post


def learn_settled_entry(row: dict[str, Any], record: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not _truthy("GOOL_BRAIN_V3_LEARNING", True) or not _is_brain_v3_row(row):
        return None
    outcome = str(row.get("result") or "").lower()
    if outcome not in {"won", "lost", "void"}:
        return None
    entry_key = str(row.get("entry_key") or "")
    if not entry_key:
        return None
    brain = _brain_snapshot(row)
    p = _number(brain.get("probability")) or _number(row.get("probability")) or 0.50
    p = _clamp(p, 0.01, 0.99)
    keys = _pattern_keys(brain)
    reasons, post = _diagnose(row, brain, record)

    with _LOCK:
        state = _load_state()
        processed = state.setdefault("processed", {})
        previous = processed.get(entry_key)
        correction = False
        if isinstance(previous, dict):
            previous_outcome = str(previous.get("outcome") or "")
            previous_p = float(_number(previous.get("probability")) or p)
            previous_keys = [str(key) for key in list(previous.get("pattern_keys") or [])]
            if previous_outcome == outcome and abs(previous_p - p) < 1e-9:
                existing = row.get("brain_v3_learning_review")
                return dict(existing) if isinstance(existing, dict) else None
            _apply_observation(state, previous_outcome, previous_p, previous_keys, -1)
            correction = True

        _apply_observation(state, outcome, p, keys, +1)
        processed[entry_key] = {
            "outcome": outcome,
            "probability": round(p, 6),
            "pattern_keys": keys,
            "processed_at": _now(),
        }
        totals = state.get("totals") or {}
        n = int(totals.get("n") or 0)
        y = 1.0 if outcome == "won" else 0.0
        review = {
            "version": 1,
            "processed_at": _now(),
            "outcome": outcome,
            "predicted_probability": round(p, 4),
            "prediction_error_pp": None if outcome == "void" else round((y - p) * 100.0, 2),
            "brier": None if outcome == "void" else round((p - y) ** 2, 5),
            "primary_diagnosis": reasons[0] if reasons else None,
            "diagnoses": reasons,
            "post_entry": post,
            "pattern_keys": keys,
            "learning_samples": n,
            "calibration_active": n >= max(10, int(float(os.getenv("GOOL_BRAIN_V3_LEARN_MIN_GLOBAL", "30")))),
            "settlement_correction": correction,
        }
        state["recent_reviews"] = ([review, *list(state.get("recent_reviews") or [])])[:200]
        _save_state(state)
        _append_event({
            "entry_key": entry_key,
            "match_id": row.get("match_id"),
            "minute": row.get("minute"),
            "strategy": row.get("strategy"),
            **review,
        })
        print(
            f"GOOL_BRAIN_V3_LEARN match={row.get('match_id') or '-'} result={outcome} "
            f"p={p:.3f} samples={n} active={int(bool(review['calibration_active']))} "
            f"why={review.get('primary_diagnosis') or '-'} correction={int(correction)}",
            flush=True,
        )
        return review


def bootstrap_journal_learning(rows: list[dict[str, Any]], journal_path: Path | None = None) -> bool:
    """On worker start, absorb already-settled V3 bets exactly once, including current six-signal epoch."""
    if not _truthy("GOOL_BRAIN_V3_LEARNING", True):
        return False
    path = str(journal_path or _journal_path())
    with _LOCK:
        if path in _BOOTSTRAPPED_JOURNALS:
            return False
        _BOOTSTRAPPED_JOURNALS.add(path)
    changed = False
    for row in rows:
        if str(row.get("result") or "pending").lower() not in {"won", "lost", "void"}:
            continue
        review = learn_settled_entry(row, None)
        if review is not None and not isinstance(row.get("brain_v3_learning_review"), dict):
            row["brain_v3_learning_review"] = review
            changed = True
    return changed


def learning_summary() -> dict[str, Any]:
    with _LOCK:
        state = _load_state()
        totals = dict(state.get("totals") or {})
        n = int(totals.get("n") or 0)
        wins = int(totals.get("wins") or 0)
        return {
            "samples": n,
            "wins": wins,
            "losses": int(totals.get("losses") or 0),
            "hit_rate": None if n <= 0 else round(wins / n, 4),
            "average_predicted": None if n <= 0 else round(float(totals.get("predicted_sum") or 0.0) / n, 4),
            "brier": None if n <= 0 else round(float(totals.get("brier_sum") or 0.0) / n, 5),
        }


def install_brain_v3_learning() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    min_global = max(10, int(float(os.getenv("GOOL_BRAIN_V3_LEARN_MIN_GLOBAL", "30"))))
    min_pattern = max(8, int(float(os.getenv("GOOL_BRAIN_V3_LEARN_MIN_PATTERN", "15"))))
    max_pp = max(0.5, min(5.0, float(os.getenv("GOOL_BRAIN_V3_LEARN_MAX_ADJUST_PP", "3.0"))))
    summary = learning_summary()
    print(
        f"GOOL_BRAIN_V3_LEARNING installed samples={summary.get('samples', 0)} "
        f"min_global={min_global} min_pattern={min_pattern} max_adjust={max_pp:.1f}pp "
        "mode=analyze_every_settlement+slow_calibration",
        flush=True,
    )


__all__ = [
    "bootstrap_journal_learning",
    "calibrate_brain_v3_decision",
    "install_brain_v3_learning",
    "learn_settled_entry",
    "learning_adjustment",
    "learning_summary",
]
