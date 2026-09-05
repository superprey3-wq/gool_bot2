from pathlib import Path

from gool_bot2.public_epoch_reset import PUBLIC_RESET_ID, reset_public_tracking_once


def test_public_epoch_reset_clears_all_public_tracking_once(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    live = runtime / "live"
    live.mkdir(parents=True)

    multi_journal = live / "gool_multi_journal.json"
    multi_bank = live / "gool_multi_bank_state.json"
    multi_analysis = live / "gool_multi_analysis.jsonl"
    flow_journal = live / "gool_money_flow_journal.json"
    flow_bank = live / "gool_money_flow_bank_state.json"

    for path in (multi_journal, multi_bank, multi_analysis, flow_journal, flow_bank):
        path.write_text("old", "utf-8")

    monkeypatch.setenv("RUNTIME_DATA_DIR", str(runtime))
    for name in (
        "GOOL_MULTI_JOURNAL_PATH",
        "GOOL_MULTI_BANK_STATE_PATH",
        "GOOL_MULTI_ANALYSIS_PATH",
        "GOOL_MULTI_SHADOW_PATH",
        "GOOL_MONEY_FLOW_JOURNAL_PATH",
        "GOOL_MONEY_FLOW_BANK_STATE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    first = reset_public_tracking_once()
    assert first["reset"] is True
    assert len(first["removed"]) == 5
    assert not multi_journal.exists()
    assert not multi_bank.exists()
    assert not multi_analysis.exists()
    assert not flow_journal.exists()
    assert not flow_bank.exists()

    marker = live / f".gool_public_reset_{PUBLIC_RESET_ID}"
    assert marker.exists()

    # New epoch data must survive normal process restarts.
    multi_journal.write_text('[{"new":true}]', "utf-8")
    second = reset_public_tracking_once()
    assert second["reset"] is False
    assert multi_journal.exists()
    assert "new" in multi_journal.read_text("utf-8")


def test_public_epoch_reset_honors_custom_paths(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    custom = tmp_path / "custom"
    custom.mkdir()

    targets = {
        "GOOL_MULTI_JOURNAL_PATH": custom / "multi.json",
        "GOOL_MULTI_BANK_STATE_PATH": custom / "multi_bank.json",
        "GOOL_MULTI_ANALYSIS_PATH": custom / "multi_analysis.jsonl",
        "GOOL_MONEY_FLOW_JOURNAL_PATH": custom / "flow.json",
        "GOOL_MONEY_FLOW_BANK_STATE_PATH": custom / "flow_bank.json",
    }
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(runtime))
    monkeypatch.delenv("GOOL_MULTI_SHADOW_PATH", raising=False)
    for name, path in targets.items():
        monkeypatch.setenv(name, str(path))
        path.write_text("old", "utf-8")

    result = reset_public_tracking_once()
    assert result["reset"] is True
    assert set(result["removed"]) == {str(path) for path in targets.values()}
    assert all(not path.exists() for path in targets.values())
