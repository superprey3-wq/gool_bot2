from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import journal as journal_mod
from . import signal_worker_all as base
from . import signal_worker_all_cards as cards
from . import storage_signal_worker as storage
from .market_card_overlay import append_xbet_market_block
from .signal_policy import GateResult
from .xbet_market_pressure import evaluate_system, load_market_state

_CURRENT: dict[str, Any] | None = None
_ORIG_STORAGE_PROCESS = storage.StorageCardAllMatchSignalWorker._process
_ORIG_MODEL_GATE = base.model_threshold_gate
_ORIG_TRAINED_CARD = base.render_signal_card
_ORIG_LIVE_CARD = cards.render_gool_live_signal_card
_ORIG_LIVE_EMIT = cards.CardAllMatchSignalWorker._emit_gool_live_signal
_ORIG_SAVE = journal_mod.save_signal_journal


def _required() -> bool:
    return str(os.getenv("XBET_MARKET_REQUIRED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _market_row(record: dict[str, Any]) -> dict[str, Any] | None:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    state = load_market_state()
    return ((state.get("matches") or {}).get(mid) or None) if mid else None


def _attach(record: dict[str, Any]) -> None:
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0); aws = int(match.get("away_score") or 0)
    row = _market_row(record)
    confirmations = record.setdefault("xbet_market", {})
    confirmations["another_goal"] = evaluate_system(row, "another_goal", hs, aws)
    confirmations["two_more_goals"] = evaluate_system(row, "two_more_goals", hs, aws)
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
        return {"available": False, "confirmed": False, "level": "NO_DATA", "reason": "1xBet context unavailable"}
    return dict(((_CURRENT.get("xbet_market") or {}).get(head) or {}))


def _model_gate(head: str, probability: float, score: float) -> GateResult:
    gate = _ORIG_MODEL_GATE(head, probability, score)
    if head != "another_goal" or not _required():
        return gate
    info = _confirmation(head)
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
    if head == "two_more_goals" and _required():
        info = ((record.get("xbet_market") or {}).get(head) or {})
        if not info.get("confirmed"):
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
                row["xbet_market"] = dict(market.get(head) or {})
                row["xbet_market_required"] = _required()
                break
    _ORIG_SAVE(path, rows)


storage.StorageCardAllMatchSignalWorker._process = _process
base.model_threshold_gate = _model_gate
base.render_signal_card = _trained_card
cards.render_gool_live_signal_card = _live_card
cards.CardAllMatchSignalWorker._emit_gool_live_signal = _live_emit
base.save_signal_journal = _save
cards.save_signal_journal = _save


def main() -> None:
    storage.main()


if __name__ == "__main__":
    main()
