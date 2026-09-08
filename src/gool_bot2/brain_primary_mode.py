from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .multi_router import MarketCandidate, RouterDecision


_LOCK = threading.Lock()
_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def _signal_state_path() -> Path:
    raw = os.getenv("GOOL_BRAIN_SIGNAL_STATE_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_brain_signal_state.json"


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _active_strategy(match: dict[str, Any]) -> str | None:
    if bool(match.get("is_finished")) or bool(match.get("is_halftime")):
        return None
    minute = int(match.get("minute") or 0)
    if 1 <= minute <= 35:
        return "goal_before_ht"
    if 46 <= minute <= 75:
        return "another_goal"
    return None


def _expert_state(expert: dict[str, Any]) -> str:
    return str(expert.get("state") or ("PASS" if expert.get("passed") else "NO_DATA")).upper()


def _target(match: dict[str, Any], strategy: str) -> tuple[str, str, float]:
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    line = float(hs + aws) + 0.5
    if strategy == "goal_before_ht":
        return f"first_half_total:{line:g}", f"1Т ТБ {line:g}", line
    return f"match_total:{line:g}", f"ТБ {line:g}", line


def _market_price(
    match: dict[str, Any],
    market_row: dict[str, Any] | None,
    strategy: str,
    line: float,
) -> tuple[float, float, str]:
    """Read 1xBet only as optional display context; never as a GOOL veto."""
    if not isinstance(market_row, dict):
        return 0.0, 0.0, "NO_DATA"
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    try:
        market_score = (int(market_row.get("score_home") or 0), int(market_row.get("score_away") or 0))
    except (TypeError, ValueError):
        return 0.0, 0.0, "NO_DATA"
    if market_score != (hs, aws):
        return 0.0, 0.0, "SCORE_DESYNC"

    market_name = "first_half_total" if strategy == "goal_before_ht" else "match_total"
    item = None
    for row in ((market_row.get("markets") or {}).get(market_name) or []):
        try:
            if abs(float(row.get("line")) - line) < 1e-9:
                item = row
                break
        except (TypeError, ValueError):
            continue
    if not isinstance(item, dict):
        return 0.0, 0.0, "NO_LINE"
    odd = _number(item.get("over"))
    pressure = _number(((market_row.get("pressure") or {}).get(f"{market_name}:{line}") or {}).get("prob_delta_pp"))
    return (odd if odd > 1.0 else 0.0), pressure, "INFO_ONLY"


def analyze_brain_primary_match(
    match: dict[str, Any],
    market_row: dict[str, Any] | None,
    experts: dict[str, Any],
    *,
    data_quality: float = 1.0,
) -> RouterDecision:
    """Create the ordinary GOOL decision from the LIVE Brain alone."""
    minute = int(match.get("minute") or 0)
    score = (int(match.get("home_score") or 0), int(match.get("away_score") or 0))
    strategy = _active_strategy(match)
    if strategy is None:
        return RouterDecision("WAIT", minute, score, None, [], [], "WAIT: вне окна обычного GOOL Brain.")

    expert = dict(experts.get(strategy) or {})
    if not expert:
        return RouterDecision("WAIT", minute, score, None, [], [], "WAIT: главный LIVE Brain не дал активную оценку.")

    probability = _number(expert.get("probability"), _number(expert.get("confidence")))
    if probability > 1.0:
        probability /= 100.0
    probability = max(0.0, min(1.0, probability))
    rating = probability * 100.0
    state = _expert_state(expert)
    minimum = _number(os.getenv("GOOL_MULTI_MIN_RATING", "70"), 70.0)
    min_quality = _number(os.getenv("GOOL_MATCH_SUITABILITY_HARD_DATA_QUALITY", "0.45"), 0.45)

    key, label, line = _target(match, strategy)
    odd, pressure, market_level = _market_price(match, market_row, strategy, line)
    family = "first_half_total" if strategy == "goal_before_ht" else "match_total"
    candidate = MarketCandidate(
        key=key,
        family=family,
        label=label,
        odd=odd,
        model_probability=probability,
        market_probability=None,
        goals_to_win=1,
        correlation_key="any_next_goal",
        strategy=strategy,
        source=f"brain_primary:{strategy}",
        expert_passed=state == "PASS",
        expert_blocks=list(expert.get("blocks") or []),
        market_pressure_pp=pressure,
        market_level=market_level,
        market_override=False,
        value_override=False,
        market_age_seconds=None,
        data_quality=max(0.0, min(1.0, float(data_quality))),
        rating=round(rating, 1),
        expected_roi=0.0,
        value_edge_pp=0.0,
        eligible=True,
        reason_tags=["brain_primary", "live_brain_only", "xbet_info_only"],
    )

    blocks: list[str] = []
    if state != "PASS":
        blocks.append(f"brain_state_{state.lower()}")
    if rating < minimum:
        blocks.append(f"brain_rating_below_{minimum:g}")
    if float(data_quality) < min_quality:
        blocks.append("data_quality_too_low")
    candidate.blocks = blocks
    candidate.eligible = not blocks

    if blocks:
        return RouterDecision(
            "WAIT",
            minute,
            score,
            None,
            [],
            [candidate],
            f"WAIT: главный LIVE Brain {state} {rating:.0f}/100; нужен PASS не ниже {minimum:.0f}/100.",
        )

    price_note = f" · 1xBet {odd:.2f} (справочно)" if odd > 1.0 else " · без обязательного кэфа"
    return RouterDecision(
        "BET",
        minute,
        score,
        candidate,
        [],
        [],
        f"GOOL LIVE Brain дал PASS {rating:.0f}/100{price_note}.",
    )


def _load_signal_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return {"version": 1, "signals": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "signals": {}}
    if not isinstance(payload.get("signals"), dict):
        payload["signals"] = {}
    return payload


def _save_signal_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _brain_entry(record: dict[str, Any], decision: RouterDecision) -> dict[str, Any] | None:
    winner = decision.winner
    if winner is None or not str(winner.source or "").startswith("brain_primary:"):
        return None
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return None
    strategy = str(winner.strategy or "")
    signal_key = f"{match_id}:{strategy}"
    path = _signal_state_path()
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        state = _load_signal_state(path)
        signals = state.get("signals") or {}
        if signal_key in signals:
            return None
        row = {
            "signal_key": signal_key,
            "created_at": now,
            "mode": "signal_only",
            "match_id": match_id,
            "home": match.get("home"),
            "away": match.get("away"),
            "league": match.get("league"),
            "minute": int(match.get("minute") or 0),
            "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
            "strategy": strategy,
            "head": strategy,
            "market": winner.label,
            "odd": float(winner.odd or 0.0),
            "price_available": bool(float(winner.odd or 0.0) > 1.0),
            "probability": float(winner.model_probability or 0.0),
            "event_score": float(winner.rating or 0.0),
            "confidence_score": float(winner.rating or 0.0),
            "source": winner.source,
            "signal_source": "GOOL_BRAIN",
            "result": "signal_only",
        }
        signals[signal_key] = row
        ordered = sorted(
            (value for value in signals.values() if isinstance(value, dict)),
            key=lambda value: str(value.get("created_at") or ""),
            reverse=True,
        )[:1000]
        state = {
            "version": 1,
            "updated_at": now,
            "signals": {str(value.get("signal_key") or ""): value for value in ordered if value.get("signal_key")},
        }
        try:
            _save_signal_state(path, state)
        except Exception as exc:
            print(f"GOOL_BRAIN_SIGNAL_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        return row


def sync_brain_or_market_journal(
    record: dict[str, Any],
    decision: RouterDecision,
    experts: dict[str, Any],
    journal_path: Path,
    *,
    data_quality: float = 1.0,
):
    winner = decision.winner
    if winner is not None and str(winner.source or "").startswith("brain_primary:"):
        return [], _brain_entry(record, decision)
    original = _ORIGINALS.get("sync_multi_journal")
    if original is None:
        return [], None
    return original(record, decision, experts, journal_path, data_quality=data_quality)


def _brain_text(record: dict[str, Any], entry: dict[str, Any]) -> str:
    match = record.get("match") or {}
    score = entry.get("score") or [0, 0]
    odd = float(entry.get("odd") or 0.0)
    price_line = (
        f"💱 1xBet: {odd:.2f} · только справочно, не подтверждение"
        if odd > 1.0
        else "💱 1xBet: кэф не требуется для этого сигнала"
    )
    strategy_line = "Гол до перерыва" if str(entry.get("strategy")) == "goal_before_ht" else "Ещё один гол"
    return (
        "🧠 <b>GOOL BRAIN · СИГНАЛ</b>\n\n"
        f"<b>{match.get('home','?')} — {match.get('away','?')}</b>\n"
        f"{int(entry.get('minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}\n"
        f"🎯 <b>{strategy_line}: {entry.get('market','?')}</b>\n"
        f"🧠 Главный LIVE Brain: <b>{float(entry.get('confidence_score') or 0):.0f}/100 · PASS</b>\n"
        f"{price_line}\n"
        "✅ Решение принято по футболу/LIVE-данным; движение кэфа не является условием сигнала."
    )


def emit_brain_or_market_signal(
    record: dict[str, Any],
    decision: RouterDecision,
    entry: dict[str, Any] | None,
    *,
    market_row: dict[str, Any] | None = None,
) -> int:
    winner = decision.winner
    if entry is not None and winner is not None and str(winner.source or "").startswith("brain_primary:"):
        from . import telegram
        from .multi_telegram import is_multi_telegram_active

        if not is_multi_telegram_active():
            return 0
        sent = telegram.broadcast(_brain_text(record, entry))
        print(
            f"GOOL_BRAIN_SIGNAL_SENT match={entry.get('match_id')} strategy={entry.get('strategy')} "
            f"score={entry.get('confidence_score')} price_available={int(bool(entry.get('price_available')))} sent={sent}",
            flush=True,
        )
        return sent
    original = _ORIGINALS.get("emit_multi_signal")
    if original is None:
        return 0
    return original(record, decision, entry, market_row=market_row)


def _identity_policy(decision: RouterDecision, *_: Any, **__: Any) -> RouterDecision:
    return decision


def _brain_post_goal_only(
    decision: RouterDecision,
    record: dict[str, Any],
    experts: dict[str, Any],
    market_row: dict[str, Any] | None,
) -> RouterDecision:
    winner = decision.winner
    if winner is None or not str(winner.source or "").startswith("brain_primary:"):
        original = _ORIGINALS.get("another_goal_guard")
        return original(decision, record, experts, market_row) if original else decision
    if str(winner.strategy or "") != "another_goal":
        return decision

    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    timeline = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or []
    last_goal: int | None = None
    for row in timeline:
        if not isinstance(row, dict) or str(row.get("event_type") or "goal").lower() != "goal":
            continue
        try:
            value = int(float(row.get("minute") or 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            last_goal = value if last_goal is None else max(last_goal, value)
    wait_minutes = max(0, int(_number(os.getenv("GOOL_ANOTHER_GOAL_POST_GOAL_WAIT_MINUTES", "3"), 3)))
    if last_goal is None or wait_minutes <= 0 or minute - last_goal >= wait_minutes:
        return decision

    winner.blocks.append("another_goal_post_goal_rebuild")
    winner.eligible = False
    decision.rejected.append(winner)
    decision.winner = None
    decision.alternatives = []
    decision.status = "WAIT"
    decision.reason = f"WAIT: после гола на {last_goal}' ждём {wait_minutes} мин., чтобы LIVE давление сформировалось заново."
    return decision


def install_runtime_patches() -> None:
    global _INSTALLED
    if _INSTALLED or not _truthy("GOOL_BRAIN_PRIMARY_SIGNALS", True):
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import multi_runtime as runtime

        _ORIGINALS.setdefault("analyze_multi_match", runtime.analyze_multi_match)
        _ORIGINALS.setdefault("enforce_goal_state_policy", runtime.enforce_goal_state_policy)
        _ORIGINALS.setdefault("apply_matchbook_confirmation", runtime.apply_matchbook_confirmation)
        _ORIGINALS.setdefault("another_goal_guard", runtime._enforce_another_goal_context_for_mode)
        _ORIGINALS.setdefault("enforce_match_suitability", runtime.enforce_match_suitability)
        _ORIGINALS.setdefault("sync_multi_journal", runtime.sync_multi_journal)
        _ORIGINALS.setdefault("emit_multi_signal", runtime.emit_multi_signal)

        runtime.analyze_multi_match = analyze_brain_primary_match
        runtime.enforce_goal_state_policy = _identity_policy
        runtime.apply_matchbook_confirmation = _identity_policy
        runtime._enforce_another_goal_context_for_mode = _brain_post_goal_only
        runtime.enforce_match_suitability = _identity_policy
        runtime.sync_multi_journal = sync_brain_or_market_journal
        runtime.emit_multi_signal = emit_brain_or_market_signal
        _INSTALLED = True
        print("GOOL_BRAIN_PRIMARY installed xbet_confirmation=off steam=unchanged", flush=True)


__all__ = [
    "analyze_brain_primary_match",
    "emit_brain_or_market_signal",
    "install_runtime_patches",
    "sync_brain_or_market_journal",
]
