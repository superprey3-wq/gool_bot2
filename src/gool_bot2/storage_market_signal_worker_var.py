from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import signal_worker_all as base
from . import storage_market_signal_worker as app
from .first_half_goal_policy import first_half_goal_decision
from .var_settlement_guard import clear_provisional, confirmed_win

_ORIG_SETTLE = base._settle_pending
_ORIG_TWO = base._settle_two_more
_ORIG_PROCESS = app.storage.StorageCardAllMatchSignalWorker._process


def _find_row(journal: list[dict[str, Any]], returned: dict[str, Any]) -> dict[str, Any] | None:
    for row in journal:
        if str(row.get("match_id") or "") != str(returned.get("match_id") or ""):
            continue
        if str(row.get("head") or "") != str(returned.get("head") or ""):
            continue
        if str(row.get("created_at") or "") == str(returned.get("created_at") or ""):
            return row
        if int(row.get("minute") or 0) == int(returned.get("minute") or 0):
            return row
    return None


def _revert(row: dict[str, Any]) -> None:
    row["result"] = "pending"
    for key in ("settled_at", "settled_minute", "settled_score", "settlement_source"):
        row.pop(key, None)


def _guard_main(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    hs, aws = base._reconciled_score(record)
    returned = _ORIG_SETTLE(record, journal)
    kept: list[dict[str, Any]] = []

    for item in returned:
        row = _find_row(journal, item) or item
        if str(item.get("result") or "") != "won" or finished:
            clear_provisional(row)
            kept.append(item)
            continue
        raw = base._is_won(str(row.get("head") or ""), row.get("score") or [0, 0], minute, hs, aws)
        if confirmed_win(row, raw_won=raw, minute=minute, home_score=hs, away_score=aws):
            kept.append(dict(row))
        else:
            _revert(row)
            print(f"VAR_PROVISIONAL_WIN match={row.get('match_id')} head={row.get('head')} score={hs}:{aws} minute={minute}", flush=True)

    for row in journal:
        if str(row.get("match_id") or "") != str(match.get("flashscore_event_id") or ""):
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        raw = base._is_won(str(row.get("head") or ""), row.get("score") or [0, 0], minute, hs, aws)
        if not raw:
            clear_provisional(row)
    return kept


def _guard_two(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    hs, aws = base._reconciled_score(record)
    total = hs + aws
    returned = _ORIG_TWO(record, journal)
    kept: list[dict[str, Any]] = []
    for item in returned:
        row = _find_row(journal, item) or item
        if str(item.get("result") or "") != "won" or finished:
            clear_provisional(row)
            kept.append(item)
            continue
        entry = row.get("score") or [0, 0]
        raw = total >= int(entry[0] or 0) + int(entry[1] or 0) + 2
        if confirmed_win(row, raw_won=raw, minute=minute, home_score=hs, away_score=aws):
            kept.append(dict(row))
        else:
            _revert(row)
            print(f"VAR_PROVISIONAL_WIN match={row.get('match_id')} head=two_more_goals score={hs}:{aws} minute={minute}", flush=True)
    for row in journal:
        if str(row.get("match_id") or "") != str(match.get("flashscore_event_id") or "") or str(row.get("head") or "") != "two_more_goals":
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        entry = row.get("score") or [0, 0]
        raw = total >= int(entry[0] or 0) + int(entry[1] or 0) + 2
        if not raw:
            clear_provisional(row)
    return kept


def _settle_first_half(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    if not mid:
        return []
    minute = int(match.get("minute") or 0)
    is_halftime = bool(match.get("is_halftime"))
    finished = bool(match.get("is_finished"))
    hs, aws = base._reconciled_score(record)
    total = hs + aws
    settled: list[dict[str, Any]] = []
    for row in journal:
        if str(row.get("match_id") or "") != mid or str(row.get("head") or "") != "first_half_goal":
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        entry = row.get("score") or [0, 0]
        entry_total = int(entry[0] or 0) + int(entry[1] or 0)
        raw_won = total > entry_total
        if raw_won:
            if not confirmed_win(row, raw_won=True, minute=minute, home_score=hs, away_score=aws):
                continue
            result = "won"
        elif is_halftime or minute >= 46 or finished:
            clear_provisional(row)
            result = "lost"
        else:
            clear_provisional(row)
            continue
        row.update({
            "result": result,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "settled_minute": minute,
            "settled_score": [hs, aws],
            "settlement_source": "first_half_goal_var_guard",
        })
        settled.append(dict(row))
    return settled


def _notify_first_half_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("result_notified"):
            continue
        won = str(row.get("result") or "lost") == "won"
        score = row.get("settled_score") or row.get("score") or [0, 0]
        icon = "✅" if won else "❌"
        base.broadcast(
            f"{icon} <b>ГОЛ ДО ПЕРЕРЫВА</b> · {'ЗАШЁЛ' if won else 'НЕ ЗАШЁЛ'}\n"
            f"{row.get('home','?')} — {row.get('away','?')} · "
            f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
        )
        row["result_notified"] = True


def _emit_first_half(self, record: dict[str, Any]) -> int:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    if not mid:
        return 0

    journal = base.load_signal_journal(self.journal_path)
    settled = _settle_first_half(record, journal)
    if settled:
        _notify_first_half_results(settled)
        base.save_signal_journal(self.journal_path, journal)

    minute = int(match.get("minute") or 0)
    if minute <= 0 or bool(match.get("is_halftime")) or bool(match.get("is_finished")) or minute >= 46:
        return 0
    model = getattr(self, "model", None)
    if model is None:
        return 0

    try:
        model_result = model.predict(record)
    except Exception as exc:
        print(f"FIRST_HALF_MODEL_ERROR {type(exc).__name__}:{exc}", flush=True)
        return 0

    probability = (model_result.get("blended") or {}).get("another_goal")
    if probability is None:
        probability = (model_result.get("trained_probability") or {}).get("another_goal")
    analyzer = (model_result.get("gool_analyzer") or {}).get("another_goal") or {}
    market_info = ((record.get("xbet_market") or {}).get("another_goal") or {})
    already = any(str(row.get("match_id") or "") == mid and str(row.get("head") or "") == "first_half_goal" for row in journal)
    open_signals = sum(
        1 for row in journal
        if str(row.get("match_id") or "") == mid and str(row.get("result") or "pending").lower() == "pending"
    )
    last_goal = base._last_goal_minute(record)
    decision = first_half_goal_decision(
        minute=minute,
        is_halftime=bool(match.get("is_halftime")),
        is_finished=bool(match.get("is_finished")),
        probability=probability,
        analyzer_passed=bool(analyzer.get("passed")),
        market_info=market_info,
        last_goal_minute=last_goal,
        already_recorded=already,
        open_signals=open_signals,
    )

    base.append_analysis(self.analysis_path, {
        **self._base_analysis(record, mid),
        "head": "first_half_goal",
        "probability": decision.get("probability"),
        "model_disagreement": (model_result.get("disagreement") or {}).get("another_goal"),
        "decision": "SIGNAL" if decision.get("passed") else "WAIT",
        "blocks": list(decision.get("reasons") or []),
        "gool_analyzer": analyzer,
        "xbet_market": market_info,
        "first_half_policy": decision,
    })
    if not decision.get("passed"):
        return 0

    hs, aws = base._reconciled_score(record)
    odd = decision.get("market_odd")
    pressure = float(analyzer.get("pressure_score") or 0.0)
    p = float(decision.get("probability") or 0.0)
    sent = base.broadcast(
        f"🟡 <b>ГОЛ ДО ПЕРЕРЫВА</b>\n"
        f"{match.get('home','?')} — {match.get('away','?')}\n"
        f"{minute}' · {hs}:{aws}\n"
        f"Нужен ещё 1 гол до конца 1-го тайма\n"
        f"GOOL: <b>{p*100:.1f}%</b> · LIVE pressure {pressure:.2f} · 1xBet {float(odd):.2f}",
        reply_markup=base.signal_keyboard(mid, "first_half_goal"),
    )
    journal.append({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "match_id": mid,
        "head": "first_half_goal",
        "minute": minute,
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "score": [hs, aws],
        "probability": p,
        "signal_source": "gool_first_half_market_confirmed",
        "gool_pressure": pressure,
        "xbet_market": dict(market_info),
        "market_odd": odd,
        "first_half_policy": dict(decision),
        "provider_count": len(record.get("providers") or {}),
        "result": "pending",
        "in_game": False,
        "telegram_sent": bool(sent),
    })
    base.save_signal_journal(self.journal_path, journal)
    print(f"FIRST_HALF_GOAL_SIGNAL match={mid} minute={minute} score={hs}:{aws} p={p:.3f} odd={odd}", flush=True)
    return int(sent > 0)


def _process_with_first_half(self, record: dict[str, Any]):
    result = _ORIG_PROCESS(self, record)
    try:
        extra = _emit_first_half(self, record)
    except Exception as exc:
        print(f"FIRST_HALF_LAYER_ERROR {type(exc).__name__}:{exc}", flush=True)
        extra = 0
    try:
        return int(result or 0) + int(extra or 0)
    except (TypeError, ValueError):
        return result


base._settle_pending = _guard_main
base._settle_two_more = _guard_two
app.storage.StorageCardAllMatchSignalWorker._process = _process_with_first_half


def main() -> None:
    app.main()


if __name__ == "__main__":
    main()
