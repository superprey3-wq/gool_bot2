from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import journal as journal_mod
from . import signal_worker_all as base
from . import signal_worker_all_cards as cards
from . import storage_signal_worker as storage
from .market_card_overlay import append_xbet_market_block
from .market_override_policy import can_override_another_goal, decorate_market_info
from .signal_policy import GateResult
from .value_bet_policy import attach_value
from .xbet_market_pressure import evaluate_system, load_market_state

_CURRENT: dict[str, Any] | None = None
_ORIG_STORAGE_PROCESS = storage.StorageCardAllMatchSignalWorker._process
_ORIG_MODEL_GATE = base.model_threshold_gate
_ORIG_TRAINED_CARD = base.render_signal_card
_ORIG_LIVE_CARD = cards.render_gool_live_signal_card
_ORIG_LIVE_EMIT = cards.CardAllMatchSignalWorker._emit_gool_live_signal
_ORIG_SAVE = journal_mod.save_signal_journal
_ORIG_PREMATCH_CONFIRM = cards._prematch_confirmation
_ORIG_LIVE_CONFIRM = cards._another_goal_live_confirmation
_ORIG_TWO_MORE = base.analyze_two_more_goals
_ORIG_ENSURE_MODEL = cards.CardAllMatchSignalWorker._ensure_model


def _required() -> bool:
    return str(os.getenv("XBET_MARKET_REQUIRED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _two_more_max_minute() -> int:
    try:
        return max(10, min(75, int(os.getenv("GOOL_TWO_MORE_MAX_MINUTE", "65"))))
    except (TypeError, ValueError):
        return 65


def _two_more_override_max_minute() -> int:
    hard_max = _two_more_max_minute()
    try:
        return max(10, min(hard_max, int(os.getenv("GOOL_TWO_MORE_OVERRIDE_MAX_MINUTE", "60"))))
    except (TypeError, ValueError):
        return min(60, hard_max)


def _market_row(record: dict[str, Any]) -> dict[str, Any] | None:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    state = load_market_state()
    return ((state.get("matches") or {}).get(mid) or None) if mid else None


def _eval(record: dict[str, Any], head: str, selected_side: str | None = None) -> dict[str, Any]:
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    row = _market_row(record)
    return decorate_market_info(evaluate_system(row, head, hs, aws, selected_side), row)


def _market_strength(info: dict[str, Any]) -> float:
    delta = max(0.0, float(info.get("strongest_delta_pp") or info.get("score_pp") or 0.0))
    moves = max(0, int(info.get("strongest_one_way_moves") or 0))
    return min(0.94, 0.70 + max(0.0, delta - 6.0) * 0.025 + max(0, moves - 2) * 0.02)


def _set_value(record: dict[str, Any], head: str, probability: Any, source: str) -> dict[str, Any]:
    market = record.setdefault("xbet_market", {})
    info = attach_value(dict(market.get(head) or {}), probability, probability_source=source)
    market[head] = info
    return info


def _attach(record: dict[str, Any]) -> None:
    confirmations = record.setdefault("xbet_market", {})
    another = _eval(record, "another_goal")
    confirmations["another_goal"] = another

    # goal_before_ht already has its own trained model + GOOL 1T analysis. We do
    # not pretend the full-match dynamic total is an exact first-half market; it
    # is only a fresh bookmaker-pressure confirmation for the next goal.
    first_half = dict(another)
    first_half.update({
        "head": "goal_before_ht",
        "market_reference": "next_goal_full_match_total",
        "market_reference_only": True,
        "override": False,
        "value_bet": False,
        "value_override": False,
    })
    confirmations["goal_before_ht"] = first_half
    confirmations["two_more_goals"] = _eval(record, "two_more_goals")
    row = _market_row(record)
    confirmations["source"] = None if row is None else {"xbet_event_id": row.get("xbet_event_id"), "captured_at": row.get("captured_at")}


def _process(self, record: dict[str, Any]):
    global _CURRENT
    _attach(record)
    _CURRENT = record
    try:
        return _ORIG_STORAGE_PROCESS(self, record)
    finally:
        _CURRENT = None


def _confirmation(head: str) -> dict[str, Any]:
    if not _CURRENT:
        return {"available": False, "confirmed": False, "override": False, "value_bet": False, "value_override": False, "level": "NO_DATA", "reason": "1xBet context unavailable"}
    return dict(((_CURRENT.get("xbet_market") or {}).get(head) or {}))


def _prematch_confirmation(record: dict[str, Any]) -> dict[str, Any]:
    result = _ORIG_PREMATCH_CONFIRM(record)
    info = ((record.get("xbet_market") or {}).get("another_goal") or {})
    if info.get("override"):
        result = dict(result)
        result["passed_without_market"] = bool(result.get("passed"))
        result["passed"] = True
        result["market_override"] = True
        result["market_override_reason"] = info.get("reason")
    return result


def _live_confirmation(record: dict[str, Any]) -> dict[str, Any]:
    result = _ORIG_LIVE_CONFIRM(record)
    info = ((record.get("xbet_market") or {}).get("another_goal") or {})
    if info.get("override"):
        result = dict(result)
        result["passed_without_market"] = bool(result.get("passed"))
        result["passed"] = True
        result["market_override"] = True
        result["market_override_reason"] = info.get("reason")
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["blocks"] = []
    return result


def _ensure_model(self):
    ok = _ORIG_ENSURE_MODEL(self)
    model = getattr(self, "model", None)
    if not ok or model is None or getattr(model, "_xbet_value_predict_wrapped", False):
        return ok
    current_predict = model.predict

    def predict_with_value(record):
        result = current_predict(record)
        trained = result.get("trained_probability") or {}
        p = trained.get("another_goal")
        if p is None:
            p = (result.get("direct") or {}).get("another_goal")
        info = _set_value(record, "another_goal", p, "trained_probability")
        if info.get("value_override") and p is not None:
            result.setdefault("blended", {})["another_goal"] = float(p)
            prematch = dict(result.get("prematch_analysis") or {})
            live = dict(result.get("another_goal_live") or {})
            prematch["passed_without_value"] = bool(prematch.get("passed"))
            live["passed_without_value"] = bool(live.get("passed"))
            prematch["soft_blocks_overridden_by_value"] = list(prematch.get("blocks") or [])
            live["soft_blocks_overridden_by_value"] = list(live.get("blocks") or [])
            prematch["passed"] = True; live["passed"] = True
            prematch["value_override"] = True; live["value_override"] = True
            prematch["blocks"] = []; live["blocks"] = []
            result["prematch_analysis"] = prematch
            result["another_goal_live"] = live
            analyzer = result.setdefault("gool_analyzer", {})
            row = dict(analyzer.get("another_goal") or {})
            row["passed_without_value"] = bool(row.get("passed"))
            row["passed"] = True
            row["value_override"] = True
            row["value_override_reason"] = info.get("value_reason")
            analyzer["another_goal"] = row
        return result

    model.predict = predict_with_value
    model._xbet_value_predict_wrapped = True
    return ok


def _two_more(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(_ORIG_TWO_MORE(record) or {})
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hard_max = _two_more_max_minute()
    override_max = _two_more_override_max_minute()
    confidence = result.get("confidence_score")
    info = _set_value(record, "two_more_goals", confidence, "gool_live_confidence")

    # Time is a hard football constraint, not a soft model block. A strong price
    # must never resurrect a two-more-goals signal when there is too little match
    # time left. After 60' market/value may confirm an organic GOOL pass, but may
    # no longer wake a rejected +2 signal. After 65' +2 is completely closed.
    if minute > hard_max:
        block = f"two_more_window_closed:{minute}>{hard_max}"
        result["passed_without_market"] = bool(result.get("passed"))
        result["passed"] = False
        result["confidence_score"] = None
        result["hard_time_block"] = block
        blocks = list(result.get("blocks") or [])
        if block not in blocks:
            blocks.append(block)
        result["blocks"] = blocks
        result["market_override_suppressed"] = bool(info.get("override"))
        result["value_override_suppressed"] = bool(info.get("value_override"))
        result["xbet_market"] = info
        result["value_bet"] = bool(info.get("value_bet"))
        return result

    special_override = minute <= override_max and bool(info.get("override") or info.get("value_override"))
    if special_override:
        result["passed_without_market"] = bool(result.get("passed"))
        result["passed"] = True
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["blocks"] = []
        if info.get("override"):
            result["market_override"] = True
            result["market_override_reason"] = info.get("reason")
        if info.get("value_override"):
            result["value_override"] = True
            result["value_override_reason"] = info.get("value_reason")
        if result.get("confidence_score") is None and info.get("override"):
            result["confidence_score"] = _market_strength(info)
            result["confidence_source"] = "xbet_market_strength_proxy"
        mid = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
        if mid:
            cards._LAST_TWO_MORE[mid] = dict(result)
    elif minute > override_max and (info.get("override") or info.get("value_override")):
        result["market_override_suppressed_late"] = bool(info.get("override"))
        result["value_override_suppressed_late"] = bool(info.get("value_override"))
        result["override_cutoff_minute"] = override_max

    result["xbet_market"] = info
    result["value_bet"] = bool(info.get("value_bet"))
    return result


def _model_gate(head: str, probability: float, score: float) -> GateResult:
    gate = _ORIG_MODEL_GATE(head, probability, score)
    if head not in {"another_goal", "goal_before_ht"}:
        return gate

    if head == "goal_before_ht":
        info = _confirmation(head)
        if not _required():
            return gate
        reasons = tuple(gate.reasons)
        if not info.get("override_fresh"):
            return GateResult(False, reasons + ("xbet_market_not_fresh",))
        if not info.get("override_price_ok"):
            odd = info.get("override_primary_odd")
            odd_text = "NA" if odd is None else f"{float(odd):.2f}"
            return GateResult(False, reasons + (f"xbet_market_low_odd:{odd_text}<{float(info.get('override_min_odd') or 1.40):.2f}",))
        if info.get("confirmed"):
            return gate
        return GateResult(False, reasons + (f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}",))

    if _CURRENT:
        info = _set_value(_CURRENT, head, probability, "blended_probability")
    else:
        info = _confirmation(head)
    if info.get("override") and can_override_another_goal(info, probability):
        return GateResult(True, ())
    if info.get("value_override"):
        return GateResult(True, ())
    if not _required():
        return gate
    if info.get("confirmed") or info.get("value_bet"):
        return gate
    return GateResult(False, tuple(gate.reasons) + (f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}",))


def _trained_card(record, head, probability, model_result, card_ctx):
    if head == "another_goal":
        _set_value(record, head, probability, "blended_probability")
    png = _ORIG_TRAINED_CARD(record, head, probability, model_result, card_ctx)
    info = ((record.get("xbet_market") or {}).get(head) or {})
    return append_xbet_market_block(png, info)


def _live_card(record, head, confidence, pressure, card_ctx):
    if head == "two_more_goals":
        _set_value(record, head, confidence, "gool_live_confidence")
    png = _ORIG_LIVE_CARD(record, head, confidence, pressure, card_ctx)
    info = ((record.get("xbet_market") or {}).get(head) or {})
    return append_xbet_market_block(png, info)


def _live_emit(self, record, journal, head, confidence, analyzer, model_result, card_ctx):
    if head != "two_more_goals":
        return _ORIG_LIVE_EMIT(self, record, journal, head, confidence, analyzer, model_result, card_ctx)

    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hard_max = _two_more_max_minute()
    override_max = _two_more_override_max_minute()
    info = _set_value(record, head, confidence, "gool_live_confidence")

    if minute > hard_max:
        base.append_analysis(self.analysis_path, {
            **self._base_analysis(record, str(match.get("flashscore_event_id") or "")),
            "head": head,
            "decision": "WAIT",
            "blocks": [f"two_more_window_closed:{minute}>{hard_max}"],
            "xbet_market": info,
        })
        return 0

    if minute <= override_max and (info.get("override") or info.get("value_override")):
        patched = dict(analyzer or {})
        patched["passed_without_market"] = bool(patched.get("passed"))
        patched["passed"] = True
        patched["soft_blocks_overridden"] = list(patched.get("blocks") or [])
        patched["blocks"] = []
        if info.get("override"):
            patched["market_override"] = True
            patched["market_override_reason"] = info.get("reason")
        if info.get("value_override"):
            patched["value_override"] = True
            patched["value_override_reason"] = info.get("value_reason")
        old = os.environ.get("GOOL_LIVE_MIN_STRENGTH")
        os.environ["GOOL_LIVE_MIN_STRENGTH"] = "0"
        try:
            return _ORIG_LIVE_EMIT(self, record, journal, head, confidence, patched, model_result, card_ctx)
        finally:
            if old is None:
                os.environ.pop("GOOL_LIVE_MIN_STRENGTH", None)
            else:
                os.environ["GOOL_LIVE_MIN_STRENGTH"] = old
    if _required() and not info.get("confirmed") and not info.get("value_bet"):
        base.append_analysis(self.analysis_path, {
            **self._base_analysis(record, str(((record.get('match') or {}).get('flashscore_event_id') or ''))),
            "head": head,
            "decision": "WAIT",
            "blocks": [f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}"],
            "xbet_market": info,
        })
        return 0
    return _ORIG_LIVE_EMIT(self, record, journal, head, confidence, analyzer, model_result, card_ctx)


def _source(info: dict[str, Any], fallback: str) -> str:
    if info.get("override") and info.get("value_bet"):
        return "xbet_market_value_combo"
    if info.get("override"):
        return "xbet_market_override"
    if info.get("value_override"):
        return "xbet_value_override"
    if info.get("value_bet"):
        return "xbet_value_bet"
    if info.get("market_reference_only") and info.get("confirmed"):
        return "gool_xbet_confirmed"
    return fallback


def _save(path: Path, rows: list[dict[str, Any]]) -> None:
    if _CURRENT:
        match = _CURRENT.get("match") or {}; mid = str(match.get("flashscore_event_id") or "")
        market = _CURRENT.get("xbet_market") or {}
        for row in reversed(rows):
            if str(row.get("match_id") or "") != mid:
                continue
            head = str(row.get("head") or "")
            if head in {"another_goal", "goal_before_ht", "two_more_goals"}:
                info = dict(market.get(head) or row.get("xbet_market") or {})
                row["xbet_market"] = info
                row["xbet_market_required"] = _required()
                row["market_override"] = bool(info.get("override"))
                row["value_bet"] = bool(info.get("value_bet"))
                row["value_override"] = bool(info.get("value_override"))
                row["value_edge_pp"] = info.get("value_edge_pp")
                row["value_level"] = info.get("value_level")
                row["signal_source"] = _source(info, str(row.get("signal_source") or "gool"))
                break
    _ORIG_SAVE(path, rows)


storage.StorageCardAllMatchSignalWorker._process = _process
cards.CardAllMatchSignalWorker._ensure_model = _ensure_model
base.model_threshold_gate = _model_gate
base.render_signal_card = _trained_card
base.analyze_two_more_goals = _two_more
cards.render_gool_live_signal_card = _live_card
cards.CardAllMatchSignalWorker._emit_gool_live_signal = _live_emit
cards._prematch_confirmation = _prematch_confirmation
cards._another_goal_live_confirmation = _live_confirmation
base.save_signal_journal = _save
cards.save_signal_journal = _save


def main() -> None:
    storage.main()


if __name__ == "__main__":
    main()