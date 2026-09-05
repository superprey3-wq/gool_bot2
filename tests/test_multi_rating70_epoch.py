from __future__ import annotations

import importlib.util
from pathlib import Path

import gool_bot2.multi_runtime as multi_runtime


_SPEC = importlib.util.spec_from_file_location(
    "monkey_start",
    Path(__file__).resolve().parents[1] / "monkey_start.py",
)
assert _SPEC is not None and _SPEC.loader is not None
monkey_start = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(monkey_start)


def test_runtime_has_no_second_final_rating_brain():
    assert not hasattr(multi_runtime, "_enforce_min_rating")


def test_rating70_epoch_reset_clears_multi_tracking_only_once(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    live = runtime / "live"
    live.mkdir(parents=True)
    journal = live / "gool_multi_journal.json"
    bank = live / "gool_multi_bank_state.json"
    journal.write_text('[{"result":"lost"}]', "utf-8")
    bank.write_text('{"current_bank_rub":88824}', "utf-8")

    monkeypatch.delenv("GOOL_MULTI_JOURNAL_PATH", raising=False)
    monkeypatch.delenv("GOOL_MULTI_BANK_STATE_PATH", raising=False)
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")

    monkey_start._reset_multi_tracking_once(runtime)

    assert not journal.exists()
    assert not bank.exists()
    marker = live / f".gool_multi_reset_{monkey_start.MULTI_RESET_ID}"
    assert marker.exists()

    # A normal restart after the cutover must keep the new epoch's statistics.
    journal.write_text("[]", "utf-8")
    bank.write_text('{"current_bank_rub":100000}', "utf-8")

    monkey_start._reset_multi_tracking_once(runtime)

    assert journal.exists()
    assert bank.exists()
