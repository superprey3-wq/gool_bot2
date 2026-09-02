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


def _required() -> bool:
    return str(os.getenv("XBET_MARKET_REQUIRED", "1")).strip().lower() not in {"0", "false", "no", "off"}


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


def _attach(record: dict[str, Any]) -> None:
    confirmations = record.setdefault("xbet_market", {})
    confirmations["another_goal"] = _eval(record, "another_goal")
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
        return {"available": False, "confirmed": False, "override": False, "level": "NO_DATA", "reason": "1xBet context unavailable"}
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


def _two_more(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(_ORIG_TWO_MORE(record) or {})
    info = ((record.get("xbet_market") or {}).get("two_more_goals") or {})
    if info.get("override"):
        result["passed_without_market"] = bool(result.get("passed"))
        result["passed"] = True
        result["market_override"] = True
        result["market_override_reason"] = info.get("reason")
        result["soft_blocks_overridden"] = list(result.get("blocks") or [])
        result["blocks"] = []
        if result.get("confidence_score") is None:
            result["confidence_score"] = _market_strength(info)
            result["confidence_source"] = "xbet_market_strength_proxy"
        mid = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
        if mid:
            cards._LAST_TWO_MORE[mid] = dict(result)
    return result


def _model_gate(head: str, probability: float, score: float) -> GateResult:
    gate = _ORIG_MODEL_GATE(head, probability, score)
    if head != "another_goal":
        return gate
    info = _confirmation(head)
    if info.get("override") and can_override_another_goal(info, probability):
        return GateResult(True, ())
    if not _required():
        return gate
    if info.get("confirmed"):
        return gate
    return GateResult(False, tuple(gate.reasons) + (f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}",))


def _trained_card(record, head, probability, model_result, card_ctx):
    png = _ORIG_TRAINED_CARD(record, head, probability, model_result, card_ctx)
    info = ((record.get("xbet_market") or {}).get(head) or {})
    return append_xbet_market_block(png, info)


def _live_card(record, head, confidence, pressure, card_ctx):
    png = _ORIG_LIVE_CARD(record, head, confidence, pressure, card_ctx)
    info = ((record.get("xbet_market") or {}).get(head) or {})
    return append_xbet_market_block(png, info)


def _live_emit(self, record, journal, head, confidence, analyzer, model_result, card_ctx):
    if head != "two_more_goals":
        return _ORIG_LIVE_EMIT(self, record, journal, head, confidence, analyzer, model_result, card_ctx)
    info = ((record.get("xbet_market") or {}).get(head) or {})
    if info.get("override"):
        patched = dict(analyzer or {})
        patched["passed_without_market"] = bool(patched.get("passed"))
        patched["passed"] = True
        patched["market_override"] = True
        patched["market_override_reason"] = info.get("reason")
        old = os.environ.get("GOOL_LIVE_MIN_STRENGTH")
        os.environ["GOOL_LIVE_MIN_STRENGTH"] = "0"
        try:
            return _ORIG_LIVE_EMIT(self, record, journal, head, confidence, patched, model_result, card_ctx)
        finally:
            if old is None:
                os.environ.pop("GOOL_LIVE_MIN_STRENGTH", None)
            else:
                os.environ["GOOL_LIVE_MIN_STRENGTH"] = old
    if _required() and not info.get("confirmed"):
        base.append_analysis(self.analysis_path, {
            **self._base_analysis(record, str(((record.get('match') or {}).get('flashscore_event_id') or ''))),
            "head": head,
            "decision": "WAIT",
            "blocks": [f"xbet_market_not_confirmed:{info.get('level','NO_DATA')}"],
            "xbet_market": info,
        })
        return 0
    return _ORIG_LIVE_EMIT(self, record, journal, head, confidence, analyzer, model_result, card_ctx)


def _save(path: Path, rows: list[dict[str, Any]]) -> None:
    if _CURRENT:
        match = _CURRENT.get("match") or {}; mid = str(match.get("flashscore_event_id") or "")
        market = _CURRENT.get("xbet_market") or {}
        for row in reversed(rows):
            if str(row.get("match_id") or "") != mid:
                continue
            head = str(row.get("head") or "")
            if head in {"another_goal", "two_more_goals"} and not row.get("xbet_market"):
                info = dict(market.get(head) or {})
                row["xbet_market"] = info
                row["xbet_market_required"] = _required()
                row["signal_source"] = "xbet_market_override" if info.get("override") else row.get("signal_source", "gool")
                break
    _ORIG_SAVE(path, rows)


storage.StorageCardAllMatchSignalWorker._process = _process
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
