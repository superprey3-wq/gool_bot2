from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2.journal import save_signal_journal
from gool_bot2.prematch_day_audit import prematch_day_audit_text


def test_prematch_day_audit_breaks_down_losses(tmp_path: Path, monkeypatch):
    p=tmp_path/"journal.json"
    now=datetime.now(timezone.utc).isoformat()
    rows=[
        {"created_at":now,"origin":"prematch","telegram_sent":True,"result":"won","home":"A","away":"B","league":"L1","market":"FT_OVER_2.5","selection":"FT_OVER_2.5","market_family":"match_total","odd":1.65,"probability":0.72,"edge":0.08,"data_quality":0.8},
        {"created_at":now,"origin":"prematch","telegram_sent":True,"result":"lost","home":"C","away":"D","league":"L1","market":"BTTS_YES","selection":"BTTS_YES","market_family":"btts","odd":1.80,"probability":0.68,"edge":0.05,"data_quality":0.7},
        {"created_at":now,"origin":"prematch","telegram_sent":True,"result":"lost","home":"E","away":"F","league":"L2","market":"FT_OVER_2.5","selection":"FT_OVER_2.5","market_family":"match_total","odd":1.55,"probability":0.66,"edge":0.04,"data_quality":0.6},
    ]
    save_signal_journal(p,rows)
    monkeypatch.setenv("REPORT_TIMEZONE","UTC")
    text=prematch_day_audit_text(p)
    assert "PREMATCH · РАЗБОР ДНЯ" in text
    assert "Все: 3" in text
    assert "Плюсы: 1" in text
    assert "Минусы: 2" in text
    assert "По рынкам" in text
    assert "По коэффициентам" in text
    assert "По вероятности модели" in text
    assert "По edge" in text
    assert "C — D" in text
    assert "E — F" in text
