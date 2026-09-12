from __future__ import annotations

import importlib.util
import os
import pickle
from pathlib import Path

from gool_bot2 import production_check


def _load_monkey_start():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("monkey_start_under_test", root / "monkey_start.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monkey_env_file_is_optional(tmp_path, monkeypatch):
    monkey_start = _load_monkey_start()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-host")

    assert monkey_start.load_env(tmp_path / "missing.env") is False

    env_file = tmp_path / "gool.env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=from-file\n", encoding="utf-8")
    assert monkey_start.load_env(env_file) is True
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "from-host"


def test_production_check_accepts_trained_model_head_names(tmp_path, monkeypatch, capsys):
    models = tmp_path / "models"
    models.mkdir()
    payloads = {
        "archive_foundation.pkl": {
            "heads": {
                "another_goal": None,
                "goal_before_ht": None,
                "two_plus_goals_second_half": None,
            }
        },
        "archive_hazard.pkl": {
            "heads": {
                "future_goals_count": None,
                "future_first_half_goals": None,
                "second_half_goals_total": None,
            }
        },
        "football_data_goal_models.pkl": {
            "heads": {
                "over_2_5_ht": None,
                "both_teams_to_score_ht": None,
            }
        },
    }
    for name, payload in payloads.items():
        with (models / name).open("wb") as handle:
            pickle.dump(payload, handle)

    monkeypatch.setenv("ARCHIVE_FOUNDATION_MODEL", str(models / "archive_foundation.pkl"))
    monkeypatch.setenv("ARCHIVE_HAZARD_MODEL", str(models / "archive_hazard.pkl"))
    monkeypatch.setenv("FOOTBALL_DATA_GOAL_MODEL", str(models / "football_data_goal_models.pkl"))
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.delenv("TELEGRAM_EXTRA_CHAT_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_SUBSCRIBERS_FILE", str(tmp_path / "subscribers.json"))

    production_check.main()

    output = capsys.readouterr().out
    assert '"status": "ready"' in output
    assert '"errors": []' in output
