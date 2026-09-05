from pathlib import Path
import runpy

from gool_bot2.multi_public_metrics import strategy_bucket


_STARTUP = runpy.run_path(str(Path(__file__).resolve().parents[1] / "monkey_start.py"))
_MULTI_RESET_ID = _STARTUP["MULTI_RESET_ID"]
_reset_multi_tracking_once = _STARTUP["_reset_multi_tracking_once"]


def test_two_system_epoch_has_new_reset_id() -> None:
    assert _MULTI_RESET_ID == "two_system_goal_epoch_v1_2026_09_05"


def test_reset_starts_journal_bank_and_analysis_from_zero(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "live"
    live.mkdir(parents=True)
    journal = live / "gool_multi_journal.json"
    bank = live / "gool_multi_bank_state.json"
    analysis = live / "gool_multi_analysis.jsonl"
    journal.write_text('[{"old": true}]', "utf-8")
    bank.write_text('{"bank": 99961}', "utf-8")
    analysis.write_text('{"old": true}\n', "utf-8")

    monkeypatch.delenv("GOOL_MULTI_JOURNAL_PATH", raising=False)
    monkeypatch.delenv("GOOL_MULTI_BANK_STATE_PATH", raising=False)
    monkeypatch.delenv("GOOL_MULTI_ANALYSIS_PATH", raising=False)
    monkeypatch.delenv("GOOL_MULTI_SHADOW_PATH", raising=False)

    _reset_multi_tracking_once(tmp_path)

    assert not journal.exists()
    assert not bank.exists()
    assert not analysis.exists()
    marker = live / f".gool_multi_reset_{_MULTI_RESET_ID}"
    assert marker.exists()

    # One-time reset: later restarts keep the new epoch.
    journal.write_text("[]", "utf-8")
    _reset_multi_tracking_once(tmp_path)
    assert journal.exists()


def test_public_event_buckets_keep_only_two_systems_and_separate_steam() -> None:
    assert strategy_bucket("goal_before_ht") == "goal_before_ht"
    assert strategy_bucket("another_goal") == "another_goal"

    for strategy in (
        "two_more_goals",
        "home_goal",
        "away_goal",
        "both_teams_to_score",
        "btts",
    ):
        assert strategy_bucket(strategy) == "other"

    for strategy in (
        "steam_another_goal",
        "steam_goal_before_ht",
        "steam_two_more_goals",
        "steam_btts",
        "steam_home_goal",
        "steam_away_goal",
    ):
        assert strategy_bucket(strategy) == "steam"
