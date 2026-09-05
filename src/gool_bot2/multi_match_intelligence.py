from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from .match_context import provider_pair, xg_or_proxy_pair


_EPOCH_BASELINES: dict[str, dict[str, Any]] = {}
_KICKOFF_CACHE: dict[str, dict[str, Any]] | None = None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pair(record: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    try:
        if key == "xg":
            home, away, _, _ = xg_or_proxy_pair(record)
        else:
            home, away = provider_pair(record, key)
    except Exception:
        return None, None
    return (
        None if home is None else float(home),
        None if away is None else float(away),
    )


def _last_goal_minute(record: dict[str, Any]) -> int | None:
    timeline = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or []
    latest: int | None = None
    for row in timeline:
        if not isinstance(row, dict):
            continue
        try:
            minute = int(float(row.get("minute") or 0))
        except (TypeError, ValueError):
            continue
        if minute <= 0:
            continue
        latest = minute if latest is None else max(latest, minute)
    return latest


def minute_hazard(minute: int) -> dict[str, Any]:
    """Small live-time prior, deliberately weaker than football evidence.

    GOOL only enters ordinary bets in 1-30 and 46-75. The curve therefore only
    covers those windows and remains conservative; it is a prior modifier, not a
    reason to manufacture PASS.
    """
    minute = int(minute or 0)
    if 1 <= minute <= 10:
        return {"period": "1H", "bucket": "1-10", "factor": 0.88}
    if 11 <= minute <= 20:
        return {"period": "1H", "bucket": "11-20", "factor": 0.98}
    if 21 <= minute <= 30:
        return {"period": "1H", "bucket": "21-30", "factor": 1.08}
    if 31 <= minute <= 35:
        return {"period": "1H", "bucket": "31-35", "factor": 1.12}
    if 46 <= minute <= 55:
        return {"period": "2H", "bucket": "46-55", "factor": 0.92}
    if 56 <= minute <= 65:
        return {"period": "2H", "bucket": "56-65", "factor": 1.03}
    if 66 <= minute <= 75:
        return {"period": "2H", "bucket": "66-75", "factor": 1.10}
    return {"period": None, "bucket": "outside", "factor": 1.0}


def _epoch_key(record: dict[str, Any]) -> tuple[str, str]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    half = "2H" if minute >= 46 else "1H"
    score = f"{int(match.get('home_score') or 0)}:{int(match.get('away_score') or 0)}"
    return half, score


def score_epoch_context(record: dict[str, Any]) -> dict[str, Any]:
    """Measure football created *after* the current score/half was established.

    The first snapshot of a new score epoch becomes the baseline, so the shot/xG
    that produced the just-scored goal is excluded rather than reused as evidence
    for the next goal. A new 2H epoch also prevents 41-45' pressure leaking into
    the second half.
    """
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    minute = int(match.get("minute") or 0)
    if not match_id or minute <= 0:
        return {"available": False}

    half, score = _epoch_key(record)
    keys = (
        "xg",
        "xgot",
        "shots",
        "shots_on_target",
        "shots_inside_box",
        "big_chances",
        "high_xg_shots",
        "dangerous_attacks",
        "corners",
    )
    current = {key: _pair(record, key) for key in keys}
    state = _EPOCH_BASELINES.get(match_id)
    identity = f"{half}:{score}"
    if state is None or str(state.get("identity")) != identity:
        state = {
            "identity": identity,
            "start_minute": minute,
            "baseline": current,
            "last_goal_minute": _last_goal_minute(record),
        }
        _EPOCH_BASELINES[match_id] = state

    baseline = state.get("baseline") or {}
    home: dict[str, float | None] = {}
    away: dict[str, float | None] = {}
    for key in keys:
        cur_h, cur_a = current.get(key) or (None, None)
        base_h, base_a = baseline.get(key) or (None, None)
        home[key] = None if cur_h is None or base_h is None else max(0.0, float(cur_h) - float(base_h))
        away[key] = None if cur_a is None or base_a is None else max(0.0, float(cur_a) - float(base_a))

    totals: dict[str, float | None] = {}
    for key in keys:
        h = home.get(key)
        a = away.get(key)
        totals[key] = None if h is None or a is None else float(h + a)

    momentum = record.get("live_momentum") or {}
    start_minute = int(state.get("start_minute") or minute)
    momentum_start = int(float(momentum.get("epoch_start_minute") or start_minute))
    effective_start = max(start_minute, momentum_start)
    return {
        "available": True,
        "identity": identity,
        "period": half,
        "score": score,
        "start_minute": effective_start,
        "minutes": max(0, minute - effective_start),
        "last_goal_minute": state.get("last_goal_minute"),
        "home": home,
        "away": away,
        "total": totals,
    }


def _quality_side(row: dict[str, Any]) -> dict[str, Any]:
    shots = _number(row.get("shots"))
    xg = _number(row.get("xg"))
    xgot = _number(row.get("xgot"))
    sot = _number(row.get("shots_on_target"))
    inside = _number(row.get("shots_inside_box"))
    big = _number(row.get("big_chances"))

    parts: list[tuple[float, float]] = []
    metrics: dict[str, float | None] = {}
    if shots is not None and shots > 0:
        if xg is not None:
            metrics["xg_per_shot"] = xg / shots
            parts.append((_clamp((xg / shots) / 0.18), 0.34))
        if sot is not None:
            metrics["sot_rate"] = sot / shots
            parts.append((_clamp((sot / shots) / 0.45), 0.22))
        if inside is not None:
            metrics["inside_rate"] = inside / shots
            parts.append((_clamp((inside / shots) / 0.65), 0.16))
    else:
        metrics["xg_per_shot"] = None
        metrics["sot_rate"] = None
        metrics["inside_rate"] = None
    if big is not None:
        parts.append((_clamp(big / 1.0), 0.18))
    if xgot is not None and xg is not None and xg > 0.03:
        metrics["xgot_xg_ratio"] = xgot / xg
        parts.append((_clamp((xgot / xg) / 1.25), 0.10))
    else:
        metrics["xgot_xg_ratio"] = None

    if not parts:
        score = 0.50
    else:
        weight = sum(w for _, w in parts)
        score = sum(v * w for v, w in parts) / weight
    return {
        "score": round(_clamp(score), 4),
        "evidence": len(parts),
        "shots": shots,
        "xg": xg,
        "sot": sot,
        "inside": inside,
        "big": big,
        **metrics,
    }


def chance_quality_context(epoch: dict[str, Any]) -> dict[str, Any]:
    if not epoch.get("available"):
        return {"available": False, "score": 0.5, "evidence": 0}
    home = _quality_side(dict(epoch.get("home") or {}))
    away = _quality_side(dict(epoch.get("away") or {}))
    stronger = max(float(home["score"]), float(away["score"]))
    weaker = min(float(home["score"]), float(away["score"]))
    combined = 0.68 * stronger + 0.32 * weaker
    evidence = int(home.get("evidence") or 0) + int(away.get("evidence") or 0)
    return {
        "available": evidence > 0,
        "score": round(_clamp(combined), 4),
        "evidence": evidence,
        "home": home,
        "away": away,
    }


def _kickoff_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = os.getenv("GOOL_KICKOFF_MARKET_PATH", "").strip()
    return Path(raw) if raw else runtime / "live" / "gool_kickoff_market.json"


def _load_kickoff_cache() -> dict[str, dict[str, Any]]:
    global _KICKOFF_CACHE
    if _KICKOFF_CACHE is not None:
        return _KICKOFF_CACHE
    try:
        payload = json.loads(_kickoff_path().read_text(encoding="utf-8"))
        _KICKOFF_CACHE = payload if isinstance(payload, dict) else {}
    except Exception:
        _KICKOFF_CACHE = {}
    return _KICKOFF_CACHE


def _save_kickoff_cache(cache: dict[str, dict[str, Any]]) -> None:
    path = _kickoff_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _fair_over(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    over = _number(row.get("over"))
    under = _number(row.get("under"))
    if over is None or over <= 1.0:
        return None
    a = 1.0 / over
    if under is None or under <= 1.0:
        return a
    b = 1.0 / under
    return a / (a + b) if a + b > 0 else None


def _main_total(markets: dict[str, Any]) -> dict[str, Any] | None:
    rows = [dict(row) for row in (markets.get("match_total") or []) if isinstance(row, dict)]
    if not rows:
        return None
    def distance(row: dict[str, Any]) -> tuple[float, float]:
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            line = 99.0
        fair = _fair_over(row)
        margin_penalty = 1.0 if fair is None else 0.0
        return abs(line - 2.5), margin_penalty
    row = min(rows, key=distance)
    return {
        "line": _number(row.get("line")),
        "over": _number(row.get("over")),
        "under": _number(row.get("under")),
        "fair_over": _fair_over(row),
    }


def kickoff_market_context(record: dict[str, Any], market_row: dict[str, Any] | None) -> dict[str, Any]:
    """Persist the earliest 1xBet baseline; only <=5' counts as kickoff-grade.

    A late first observation is retained for diagnostics but is never labelled as
    prematch/kickoff quality. This prevents a 35' market from being mistaken for
    the starting expectation after a process restart.
    """
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    minute = int(match.get("minute") or 0)
    if not match_id:
        return {"available": False}
    cache = _load_kickoff_cache()
    existing = cache.get(match_id)
    if isinstance(existing, dict):
        return dict(existing)
    if not market_row:
        return {"available": False}
    markets = market_row.get("markets") or {}
    one_x_two = dict(markets.get("match_1x2") or {})
    total = _main_total(markets)
    if not one_x_two and total is None:
        return {"available": False}
    quality = "near_kickoff" if 0 < minute <= 5 else "late_first_observation"
    snapshot = {
        "available": True,
        "quality": quality,
        "usable_as_kickoff_prior": quality == "near_kickoff",
        "minute": minute,
        "captured_at": market_row.get("captured_at"),
        "1x2": one_x_two,
        "main_total": total,
    }
    cache[match_id] = snapshot
    _save_kickoff_cache(cache)
    return dict(snapshot)


def lineup_context(record: dict[str, Any]) -> dict[str, Any]:
    providers = record.get("providers") or {}
    available_from: list[str] = []
    for name, payload in providers.items():
        meta = (payload or {}).get("meta") or {}
        if bool(meta.get("has_lineup") or meta.get("has_lineups")):
            available_from.append(str(name))
    incidents = (((providers.get("flashscore") or {}).get("meta") or {}).get("incident_timeline") or [])
    substitutions = 0
    for row in incidents:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("event_type") or row.get("type") or "").lower()
        if "sub" in kind:
            substitutions += 1
    return {
        "available": bool(available_from),
        "providers": available_from,
        "substitutions_seen": substitutions,
        # Player-strength adjustment stays observation-only until stable player IDs
        # and starter history are available. Missing names must not be invented.
        "player_strength_adjustment": None,
    }


def red_card_context(record: dict[str, Any]) -> dict[str, Any]:
    cards = record.get("cards") or {}
    try:
        home = int(cards.get("home_red") or 0)
        away = int(cards.get("away_red") or 0)
    except (TypeError, ValueError):
        home = away = 0
    if home == away == 0:
        pair = _pair(record, "red_cards")
        home = int(pair[0] or 0)
        away = int(pair[1] or 0)
    return {
        "home": max(0, home),
        "away": max(0, away),
        "asymmetry": max(-3, min(3, away - home)),
        "side_specific_already_in_goal_state": True,
    }


def _market_age_seconds(market_row: dict[str, Any] | None) -> float | None:
    if not market_row:
        return None
    raw = market_row.get("captured_at")
    if not raw:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _market_quality(market_row: dict[str, Any] | None) -> float:
    if not market_row:
        return 0.0
    markets = market_row.get("markets") or {}
    one_x_two = markets.get("match_1x2") or {}
    has_1x2 = bool((one_x_two.get("fair") or {}) and all(one_x_two.get(k) for k in ("home", "draw", "away")))
    totals = list(markets.get("match_total") or [])
    has_paired_total = any(row.get("over") and row.get("under") for row in totals if isinstance(row, dict))
    if has_1x2 and has_paired_total:
        return 1.0
    if has_1x2 or has_paired_total:
        return 0.72
    return 0.35


def _history_quality(record: dict[str, Any]) -> float:
    profile = record.get("prematch_goal_profile") or {}
    active = profile.get("active") or {}
    try:
        sample = int(active.get("pair_sample") or 0)
    except (TypeError, ValueError):
        sample = 0
    if sample <= 0:
        return 0.35
    return _clamp(0.35 + 0.13 * sample, 0.35, 1.0)


def _suitability(
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    data_quality: float,
    epoch: dict[str, Any],
    chance: dict[str, Any],
    kickoff: dict[str, Any],
    lineup: dict[str, Any],
) -> dict[str, Any]:
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    hard_blocks: list[str] = []
    age = _market_age_seconds(market_row)
    integrity = 1.0
    if not market_row:
        integrity = 0.15
        hard_blocks.append("market_state_missing")
    else:
        market_score = (int(market_row.get("score_home") or 0), int(market_row.get("score_away") or 0))
        if market_score != (hs, aws) or bool(market_row.get("score_desync")):
            integrity = 0.0
            hard_blocks.append("score_desync")
        if bool(market_row.get("timeline_score_desync")):
            integrity = min(integrity, 0.15)
            hard_blocks.append("timeline_score_desync")
        if age is not None and age > float(os.getenv("GOOL_MATCH_SUITABILITY_MAX_MARKET_AGE", "60")):
            integrity = min(integrity, 0.20)
            hard_blocks.append("market_state_stale")
    if data_quality < float(os.getenv("GOOL_MATCH_SUITABILITY_HARD_DATA_QUALITY", "0.45")):
        hard_blocks.append("data_quality_too_low")

    epoch_evidence = 0.55
    if epoch.get("available"):
        minutes = int(epoch.get("minutes") or 0)
        evidence = int(chance.get("evidence") or 0)
        epoch_evidence = _clamp(0.45 + min(0.30, minutes * 0.04) + min(0.25, evidence * 0.035))

    components = {
        "integrity": integrity,
        "provider_data": _clamp(data_quality),
        "history": _history_quality(record),
        "market": _market_quality(market_row),
        "epoch_evidence": epoch_evidence,
        "kickoff": 0.92 if kickoff.get("usable_as_kickoff_prior") else 0.55,
        "lineup": 0.82 if lineup.get("available") else 0.55,
    }
    weights = {
        "integrity": 0.22,
        "provider_data": 0.18,
        "history": 0.18,
        "market": 0.18,
        "epoch_evidence": 0.16,
        "kickoff": 0.05,
        "lineup": 0.03,
    }
    score = sum(components[key] * weights[key] for key in weights)
    return {
        "score": round(_clamp(score), 4),
        "minimum": float(os.getenv("GOOL_MATCH_SUITABILITY_MIN", "0.58")),
        "components": {key: round(value, 4) for key, value in components.items()},
        "hard_blocks": list(dict.fromkeys(hard_blocks)),
        "market_age_seconds": None if age is None else round(age, 2),
    }


def apply_match_intelligence(
    record: dict[str, Any],
    experts: dict[str, dict[str, Any]],
    market_row: dict[str, Any] | None,
    *,
    data_quality: float,
) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    epoch = score_epoch_context(record)
    chance = chance_quality_context(epoch)
    hazard = minute_hazard(minute)
    kickoff = kickoff_market_context(record, market_row)
    lineup = lineup_context(record)
    red_cards = red_card_context(record)

    active_strategy = "goal_before_ht" if 1 <= minute <= 35 else ("another_goal" if 46 <= minute <= 75 else None)
    adjustment = {
        "strategy": active_strategy,
        "before": None,
        "after": None,
        "hazard_pp": 0.0,
        "chance_quality_pp": 0.0,
        "kickoff_total_pp": 0.0,
        "total_pp": 0.0,
    }
    if active_strategy and isinstance(experts.get(active_strategy), dict):
        expert = experts[active_strategy]
        p = _number(expert.get("probability"))
        if p is not None:
            p = _clamp(p)
            hazard_delta = max(-0.025, min(0.025, (float(hazard.get("factor") or 1.0) - 1.0) * 0.12))
            quality_delta = 0.0
            if int(epoch.get("minutes") or 0) >= 2 and int(chance.get("evidence") or 0) >= 2:
                quality_delta = max(-0.025, min(0.025, (float(chance.get("score") or 0.5) - 0.5) * 0.05))
            kickoff_delta = 0.0
            total = kickoff.get("main_total") or {}
            fair_over = _number(total.get("fair_over")) if kickoff.get("usable_as_kickoff_prior") else None
            if fair_over is not None:
                kickoff_delta = max(-0.015, min(0.015, (fair_over - 0.5) * 0.08))
            delta = max(-0.04, min(0.04, hazard_delta + quality_delta + kickoff_delta))
            adjusted = _clamp(p + delta, 0.01, 0.99)
            expert["probability"] = round(adjusted, 4)
            diagnostics = dict(expert.get("diagnostics") or {})
            diagnostics["match_intelligence"] = {
                "probability_before": round(p, 4),
                "probability_after": round(adjusted, 4),
                "hazard": hazard,
                "chance_quality": {"score": chance.get("score"), "evidence": chance.get("evidence")},
                "kickoff_prior_used": bool(kickoff.get("usable_as_kickoff_prior")),
                "delta_pp": round(delta * 100.0, 2),
            }
            expert["diagnostics"] = diagnostics
            adjustment.update({
                "before": round(p, 4),
                "after": round(adjusted, 4),
                "hazard_pp": round(hazard_delta * 100.0, 2),
                "chance_quality_pp": round(quality_delta * 100.0, 2),
                "kickoff_total_pp": round(kickoff_delta * 100.0, 2),
                "total_pp": round(delta * 100.0, 2),
            })

    suitability = _suitability(
        record,
        market_row,
        data_quality=data_quality,
        epoch=epoch,
        chance=chance,
        kickoff=kickoff,
        lineup=lineup,
    )
    payload = {
        "version": 1,
        "score_epoch": epoch,
        "minute_hazard": hazard,
        "chance_quality": chance,
        "kickoff_market": kickoff,
        "lineup": lineup,
        "red_cards": red_cards,
        "probability_adjustment": adjustment,
        "suitability": suitability,
    }
    record["match_intelligence"] = payload
    return payload


def _block(decision: Any, tag: str, reason: str) -> Any:
    winner = getattr(decision, "winner", None)
    if winner is None:
        return decision
    if tag not in winner.blocks:
        winner.blocks.append(tag)
    if tag not in winner.reason_tags:
        winner.reason_tags.append(tag)
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)
    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = reason
    return decision


def enforce_match_suitability(decision: Any, record: dict[str, Any]) -> Any:
    """Final ordinary-GOOL match-quality gate; STEAM remains a separate system."""
    if str(getattr(decision, "status", "")) != "BET" or getattr(decision, "winner", None) is None:
        return decision
    winner = decision.winner
    if str(getattr(winner, "source", "") or "").startswith("1xbet:autonomous_steam"):
        return decision
    intel = record.get("match_intelligence") or {}
    suitability = intel.get("suitability") or {}
    hard = list(suitability.get("hard_blocks") or [])
    score = float(suitability.get("score") or 0.0)
    minimum = float(suitability.get("minimum") or 0.58)
    if hard:
        return _block(
            decision,
            "match_suitability_hard_block",
            "WAIT: матч не проходит контроль качества данных/рынка: " + ", ".join(hard),
        )
    if score < minimum:
        return _block(
            decision,
            "match_suitability_below_floor",
            f"WAIT: качество матча {score*100:.0f}/100 ниже порога {minimum*100:.0f}/100.",
        )
    return decision


def calibration_snapshot(entry: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    p = _number(entry.get("model_probability"))
    market_p = _number(entry.get("market_probability"))
    odd = _number(entry.get("odd"))
    if p is None:
        p = _number(entry.get("confidence_probability"))
    if p is not None and p > 1.0:
        p /= 100.0
    if market_p is not None and market_p > 1.0:
        market_p /= 100.0
    bucket = None
    if p is not None:
        low = math.floor(_clamp(p) * 20.0) / 20.0
        high = min(1.0, low + 0.05)
        bucket = f"{int(round(low*100))}-{int(round(high*100))}%"
    return {
        "version": 1,
        "strategy": entry.get("strategy"),
        "predicted_probability": None if p is None else round(_clamp(p), 4),
        "market_fair_probability": None if market_p is None else round(_clamp(market_p), 4),
        "market_raw_implied_probability": None if odd is None or odd <= 1.0 else round(1.0 / odd, 4),
        "edge_pp": None if p is None or market_p is None else round((p - market_p) * 100.0, 2),
        "probability_bucket": bucket,
        "kickoff_market": dict(((record.get("match_intelligence") or {}).get("kickoff_market") or {})),
    }


__all__ = [
    "apply_match_intelligence",
    "calibration_snapshot",
    "chance_quality_context",
    "enforce_match_suitability",
    "kickoff_market_context",
    "minute_hazard",
    "score_epoch_context",
]
