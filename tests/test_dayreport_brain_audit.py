from __future__ import annotations

import json
from datetime import datetime, timezone

from gool_bot2.dayreport_brain_audit import (
    _line_result, _model_evidence, basketball_detail_html, raw_evidence_html,
)
from gool_bot2.full_report_export import _diagnostic_text, build_full_report


def _quarter_handicap() -> dict:
    # Screenshot-like synthetic journal fixture. Scope matters: 22:22 Q4 is
    # perfectly consistent with a Q4 +0.5 handicap WIN, not full-game proof.
    return {
        "entry_id": "test-ktp",
        "sport": "basketball",
        "telegram_sent": True,
        "phase": "PREMATCH",
        "home": "KTP Kotka Basket",
        "away": "Pyrinto Tampere",
        "league": "FINLAND: Korisliiga",
        "flashscore_event_id": "test-ktp-fs",
        "event_id": "test-ktp-xb",
        "market_family": "handicap",
        "scope": "QUARTER_4",
        "selection": "Ф1 +0.5",
        "selection_side": "home",
        "direction": "home",
        "line": 0.5,
        "odd": 1.57,
        "result": "won",
        "profit_units": 0.57,
        "settled_score": [22, 22],
        "settled_match_score": [85, 79],
        "brain_mode": "basketball_prematch_v2",
        "signal_type": "basketball_prematch_v2",
        "created_at": "2026-10-09T17:20:00+00:00",
    }


def _third_quarter_total() -> dict:
    return {
        "entry_id": "test-pelplin",
        "sport": "basketball",
        "telegram_sent": True,
        "phase": "LIVE",
        "home": "Pelplin",
        "away": "Krosno",
        "league": "POLAND: 1. Liga",
        "flashscore_event_id": "test-pelplin-fs",
        "event_id": "test-pelplin-xb",
        "market_family": "match_total",
        "scope": "QUARTER_3",
        "selection": "3-я четверть: ТБ 35.5",
        "direction": "over",
        "line": 35.5,
        "odd": 1.44,
        "result": "won",
        "profit_units": 0.44,
        "settled_score": [15,27],
        "settled_match_score": [80,90],
        "brain_mode": "basketball_historical_v3",
        "signal_type": "basketball_historical_v3",
        "historical_home_hits": 9,
        "historical_away_hits": 8,
        "historical_tier": "PASS_8",
        "historical_coefficient_available": True,
        "historical_coefficient_home": [1.02],
        "historical_coefficient_away": [0.98],
        "historical_correction_points": -1.5,
        "historical_probability_uncalibrated": True,
        "model_probability": 0.67,
        "created_at": "2026-10-09T17:38:00+00:00",
    }


def test_scope_correct_handicap_quarter_22_22_is_win():
    row=_quarter_handicap()
    check=_line_result(row)
    assert check["state"]=="MATCH"
    assert check["estimated"]=="won"
    assert "0.5" in check["detail"]
    assert _line_result({**row,"scope":"FULL_MATCH","settled_score":[70,80]})["state"]=="MISMATCH"


def test_3rd_quarter_15_27_over_35_5():
    row=_third_quarter_total()
    check=_line_result(row)
    assert check["state"]=="MATCH"
    assert check["estimated"]=="won"
    assert "42" in check["detail"]


def test_missing_history_is_not_auto_credited_to_v3():
    row=_quarter_handicap()
    evidence=_model_evidence(row)
    assert any("basketball_prematch_v2" in item for item in evidence)
    assert any("нет подтверждения" in item.lower() for item in evidence)
    assert not any("8/10" in item and "записаны" not in item for item in evidence)


def test_v3_historical_data_preserved_but_p_not_calibrated():
    row=_third_quarter_total()
    html_out=basketball_detail_html([row])
    assert "9/10 и 8/10" in html_out
    assert "K_A=[1.02]" in html_out
    assert "НЕ КАЛИБРОВАНА" in html_out
    assert "MATCH" in html_out
    assert "QUARTER_3" in html_out
    assert "settled_match_score" in html_out
    assert "15" in html_out
    diag=_diagnostic_text(row)
    assert "Brain=basketball_historical_v3" in diag
    assert "H2H=поправка применена" in diag
    assert "НЕ КАЛИБРОВАНА" in diag


def test_raw_journal_html_escapes_untrusted_content():
    content=raw_evidence_html({"selection":"<script>alert('oops')</script>", "odd":1.44})
    assert "<script>" not in content
    assert "&lt;script&gt;" in content
    assert '"odd"' in content or "&quot;odd&quot;" in content


def test_full_dayreport_carries_both_cases_exactly_as_scoped(tmp_path):
    football=tmp_path/"football.json"
    multi=tmp_path/"multisport.json"
    super10=tmp_path/"super10.json"
    football.write_text("[]",encoding="utf-8")
    multi.write_text(json.dumps([_quarter_handicap(),_third_quarter_total()],ensure_ascii=False),encoding="utf-8")
    super10.write_text("[]",encoding="utf-8")
    filename,payload,caption=build_full_report(
        football_path=football,multisport_path=multi,
        super10_history_path=super10,
        multisport_history_path=tmp_path/"absent_history.jsonl",
        now=datetime(2026,10,9,20,50,tzinfo=timezone.utc),
        report_date=datetime(2026,10,9).date(),
    )
    text=payload.decode("utf-8")
    assert filename=="GOOL_DAY_REPORT_2026-10-09.html"
    assert "КТП" not in text  # Only actual journal team name is printed
    assert "KTP Kotka Basket" in text
    assert "Pelplin" in text
    assert "счёт всего матча" in text
    assert "22:22 (QUARTER_4); матч 85:79" in text
    assert "15:27 (QUARTER_3); матч 80:90" in text
    assert "прошлых игр" in text
    assert "счёт всего матча" in text
    assert "Все исходные поля журнала (JSON)" in text
    assert "Версия Brain — отдельно V3 и V2" in text
    assert "basketball_prematch_v2" in text
    assert "basketball_historical_v3" in text
    assert "MATCH" in text
    assert "P/L" in text
    assert "Ставок: <b>2</b>" in caption


def test_report_flags_wrong_bookkeeping_without_changing_stored_result():
    row=_third_quarter_total()
    altered={**row,"result":"lost","profit_units":-1.0}
    s=basketball_detail_html([altered])
    assert "MISMATCH" in s
    assert "журнал=lost" in s
    assert altered["result"]=="lost"


def test_not_claiming_v3_without_signal_type():
    no_version={**_quarter_handicap(),"brain_mode":"","signal_type":""}
    report=basketball_detail_html([no_version])
    assert "не записан" in report
    assert "не приписывать эту ставку V3" in report
