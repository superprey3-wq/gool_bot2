from __future__ import annotations

import json
import os
import pickle
from pathlib import Path


def _model_path(env_name: str, default: str) -> Path:
    return Path(os.getenv(env_name, default))


def _load_bundle(path: Path) -> dict:
    with path.open("rb") as handle:
        bundle = pickle.load(handle)
    if not isinstance(bundle, dict):
        raise RuntimeError(f"{path}: bundle is not a dict")
    return bundle


def main() -> None:
    direct_path = _model_path("ARCHIVE_FOUNDATION_MODEL", "models/archive_foundation.pkl")
    hazard_path = _model_path("ARCHIVE_HAZARD_MODEL", "models/archive_hazard.pkl")
    fd_path = _model_path("FOOTBALL_DATA_GOAL_MODEL", "models/football_data_goal_models.pkl")

    required_files = [direct_path, hazard_path, fd_path]
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise SystemExit("missing_models=" + ",".join(missing))

    direct = _load_bundle(direct_path)
    hazard = _load_bundle(hazard_path)
    football_data = _load_bundle(fd_path)

    direct_heads = set((direct.get("heads") or {}).keys())
    hazard_heads = set((hazard.get("heads") or {}).keys())
    fd_heads = set((football_data.get("heads") or {}).keys())

    required_event = {"another_goal", "goal_before_ht", "two_plus_goals_second_half"}
    required_hazard = {"future_goals_count", "future_first_half_goals", "second_half_goals_total"}
    required_fd = {"over_2_5_ht", "both_teams_to_score_ht"}

    errors: list[str] = []
    if not required_event.issubset(direct_heads):
        errors.append(f"direct_missing={sorted(required_event - direct_heads)}")
    if not required_hazard.issubset(hazard_heads):
        errors.append(f"hazard_missing={sorted(required_hazard - hazard_heads)}")
    if not required_fd.issubset(fd_heads):
        errors.append(f"football_data_missing={sorted(required_fd - fd_heads)}")

    telegram_token = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_target = bool(
        os.getenv("TELEGRAM_CHAT_ID", "").strip()
        or os.getenv("TELEGRAM_EXTRA_CHAT_IDS", "").strip()
        or Path(os.getenv("TELEGRAM_SUBSCRIBERS_FILE", "/data/telegram_subscribers.json")).exists()
    )
    if not telegram_token:
        errors.append("telegram_token_missing")
    if not telegram_target:
        errors.append("telegram_recipient_missing")

    runtime_dir = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    writable_probe = runtime_dir / ".gool_bot2_write_probe"
    try:
        writable_probe.write_text("ok", encoding="utf-8")
        writable_probe.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f"runtime_not_writable={type(exc).__name__}")

    status = {
        "status": "ready" if not errors else "blocked",
        "models": {
            "direct": str(direct_path),
            "hazard": str(hazard_path),
            "football_data": str(fd_path),
        },
        "direct_heads": sorted(direct_heads),
        "hazard_heads": sorted(hazard_heads),
        "football_data_heads": sorted(fd_heads),
        "telegram_token_present": telegram_token,
        "telegram_recipient_present": telegram_target,
        "runtime_data_dir": str(runtime_dir),
        "errors": errors,
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
