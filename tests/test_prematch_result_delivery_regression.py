from __future__ import annotations

from pathlib import Path

from gool_bot2 import storage_market_signal_worker_var as worker_var
from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.v4_prematch_delivery import reconcile_and_deliver_prematch_results


def test_direct_menu_path_drains_prematch_result_cards(tmp_path: Path, monkeypatch):
    journal=tmp_path/"gool_multi_journal.json"
    save_signal_journal(journal,[])
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH",str(journal))
    calls=[]

    monkeypatch.setattr(worker_var.telegram_mod,"_force_reconcile_pending",lambda p: 0)
    monkeypatch.setattr(worker_var.telegram_mod,"report_text",lambda p: "REPORT")
    monkeypatch.setattr(worker_var,"_direct_send_message",lambda *a,**k: True)
    monkeypatch.setattr(worker_var,"_drain_prematch_results_safely",lambda p: calls.append(Path(p)) or 1)

    update={"message":{"text":"📊 Отчёт","chat":{"id":123}}}
    changed=worker_var._handle_direct_telegram_update("token",journal,update)

    assert calls
    assert changed>=2


def test_reconcile_and_deliver_marks_prematch_result_sent(tmp_path: Path, monkeypatch):
    journal=tmp_path/"gool_multi_journal.json"
    row={
        "entry_id":"prematch:fs1:FT_OVER_2.5",
        "origin":"prematch",
        "event_id":"fs1","match_id":"fs1",
        "home":"Home","away":"Away",
        "market":"FT_OVER_2.5","selection":"over 2.5","market_family":"match_total",
        "odd":1.70,"probability":0.75,
        "result":"won","settled_at":"2026-09-29T12:00:00+00:00",
        "settled_score":[2,1],
        "telegram_sent":True,
        "result_notification_pending":True,
        "result_notification_created_at":"2026-09-29T12:00:00+00:00",
    }
    save_signal_journal(journal,[row])

    monkeypatch.setattr("gool_bot2.v4_prematch_settlement.reconcile_pending_prematch",lambda p: 0)
    monkeypatch.setattr("gool_bot2.v4_prematch_delivery.emit_prematch_result",lambda r,record=None: 1)

    result=reconcile_and_deliver_prematch_results(journal)
    assert result["delivered"]==1
    stored=load_signal_journal(journal)[0]
    assert stored["result_notification_pending"] is False
    assert stored["result_telegram_sent"] is True

    # Durable no-duplicate behavior.
    result2=reconcile_and_deliver_prematch_results(journal)
    assert result2["delivered"]==0
