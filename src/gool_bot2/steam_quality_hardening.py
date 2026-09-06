from __future__ import annotations

import os
from typing import Any, Callable


_INSTALLED = False
_STEAM_FORMULA = "100% прогруз 1xBet · LIVE-футбол только справочно"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _floor_env(name: str, floor: float) -> float:
    current = _number(os.getenv(name), floor)
    effective = max(float(floor), current)
    os.environ[name] = f"{effective:g}"
    return effective


def _floor_int_env(name: str, floor: int) -> int:
    current = _integer(os.getenv(name), floor)
    effective = max(int(floor), current)
    os.environ[name] = str(effective)
    return effective


def _breadth_count(candidate: Any) -> int:
    for tag in list(getattr(candidate, "reason_tags", []) or []):
        raw = str(tag or "")
        if not raw.startswith("market_breadth:"):
            continue
        try:
            return max(0, int(raw.split(":", 1)[1]))
        except (TypeError, ValueError):
            return 0
    return 0


def _pressure_key(candidate: Any) -> str:
    strategy = str(getattr(candidate, "strategy", "") or "")
    if strategy == "steam_btts":
        return "btts_yes:None"
    return str(getattr(candidate, "key", "") or "")


def _pressure_row(candidate: Any, market_row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(market_row, dict):
        return {}
    pressure = market_row.get("pressure") or {}
    if not isinstance(pressure, dict):
        return {}
    row = pressure.get(_pressure_key(candidate)) or {}
    return dict(row) if isinstance(row, dict) else {}


def _one_way_moves(candidate: Any, market_row: dict[str, Any] | None) -> int:
    return max(0, _integer(_pressure_row(candidate, market_row).get("one_way_moves"), 0))


def _trajectory_ok(candidate: Any, market_row: dict[str, Any] | None) -> bool:
    """Require the price move to still be moving in the same direction.

    Older/synthetic rows do not contain trajectory fields and remain compatible.
    Production rows receive them from xbet_trajectory_hardening.
    """
    row = _pressure_row(candidate, market_row)
    if "directional_consistency" not in row:
        return True

    consistency = _number(row.get("directional_consistency"), 1.0)
    down_moves = max(0, _integer(row.get("down_moves"), 0))
    retrace_pp = max(0.0, _number(row.get("retrace_pp"), 0.0))
    latest_step_pp = _number(row.get("latest_step_pp"), 0.0)
    consecutive_up = max(0, _integer(row.get("consecutive_up_moves"), 0))
    moves = _one_way_moves(candidate, market_row)

    max_retrace = max(0.25, _number(os.getenv("XBET_STEAM_MAX_RETRACE_PP"), 1.50))
    min_consistency = max(0.50, min(1.0, _number(os.getenv("XBET_STEAM_MIN_DIRECTIONAL_CONSISTENCY"), 0.67)))
    max_latest_reverse = max(0.10, _number(os.getenv("XBET_STEAM_MAX_LATEST_REVERSE_PP"), 0.50))

    if retrace_pp > max_retrace:
        return False
    if latest_step_pp < -max_latest_reverse:
        return False
    if down_moves >= 2 and consistency < min_consistency:
        return False
    if moves <= 2 and down_moves > 0 and consecutive_up < 2:
        return False
    return True


def _passes_quality_shape(candidate: Any, market_row: dict[str, Any] | None) -> bool:
    """Accept only strong, persistent and non-reversing autonomous STEAM."""
    delta = max(0.0, _number(getattr(candidate, "market_pressure_pp", 0.0)))
    moves = _one_way_moves(candidate, market_row)
    breadth = _breadth_count(candidate)

    base_delta = _floor_env("XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP", 6.0)
    base_moves = _floor_int_env("XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES", 2)
    if delta < base_delta or moves < base_moves:
        return False

    two_move_delta = _floor_env("XBET_AUTONOMOUS_STEAM_TWO_MOVE_MIN_DELTA_PP", 7.0)
    persistent_delta = _floor_env("XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_DELTA_PP", 6.0)
    persistent_moves = _floor_int_env("XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_MOVES", 3)
    persistent_breadth = _floor_int_env("XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_RELATED_MARKETS", 2)
    extreme_delta = _floor_env("XBET_AUTONOMOUS_STEAM_EXTREME_DELTA_PP", 8.0)
    extreme_moves = _floor_int_env("XBET_AUTONOMOUS_STEAM_EXTREME_ONE_WAY_MOVES", 3)

    high_delta_confirmed = delta >= two_move_delta and moves >= 2 and breadth >= 1
    persistent_broad = (
        delta >= persistent_delta
        and moves >= persistent_moves
        and breadth >= persistent_breadth
    )
    extreme = delta >= extreme_delta and moves >= extreme_moves
    shape_ok = bool(high_delta_confirmed or persistent_broad or extreme)
    return bool(shape_ok and _trajectory_ok(candidate, market_row))


def _strict_confidence_wrapper(original: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def wrapped(
        record: dict[str, Any],
        winner: Any,
        experts: dict[str, Any],
        *,
        data_quality: float,
    ) -> dict[str, Any]:
        out = dict(original(record, winner, experts, data_quality=data_quality) or {})
        source = str(getattr(winner, "source", "") or "")
        if source.startswith("1xbet:autonomous_steam"):
            strength = max(0.0, min(99.0, _number(getattr(winner, "rating", 0.0))))
            out["confidence_score"] = round(strength, 1)
            out["steam_score"] = round(strength, 1)
            out["formula"] = _STEAM_FORMULA
            out["layer"] = "STEAM"
        return out

    return wrapped


def install_steam_quality_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    _floor_env("XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP", 6.0)
    _floor_int_env("XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES", 2)
    _floor_env("XBET_STEAM_BREADTH_MIN_DELTA_PP", 2.0)
    _floor_int_env("XBET_STEAM_BREADTH_MIN_ONE_WAY_MOVES", 1)
    _floor_int_env("XBET_AUTONOMOUS_STEAM_MIN_RELATED_MARKETS", 1)
    _floor_env("XBET_AUTONOMOUS_STEAM_TWO_MOVE_MIN_DELTA_PP", 7.0)
    _floor_env("XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_DELTA_PP", 6.0)
    _floor_int_env("XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_MOVES", 3)
    _floor_int_env("XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_RELATED_MARKETS", 2)
    _floor_env("XBET_AUTONOMOUS_STEAM_EXTREME_DELTA_PP", 8.0)
    _floor_int_env("XBET_AUTONOMOUS_STEAM_EXTREME_ONE_WAY_MOVES", 3)
    os.environ.setdefault("XBET_STEAM_MAX_RETRACE_PP", "1.5")
    os.environ.setdefault("XBET_STEAM_MIN_DIRECTIONAL_CONSISTENCY", "0.67")
    os.environ.setdefault("XBET_STEAM_MAX_LATEST_REVERSE_PP", "0.5")

    from . import multi_autonomous_steam as steam
    from . import multi_card
    from . import multi_entry_enrichment
    from . import multi_public_metrics as metrics
    from . import multi_runtime

    original_build = steam.build_autonomous_steam_candidates

    def strict_build(
        record: dict[str, Any],
        market_row: dict[str, Any] | None,
        *,
        data_quality: float,
    ) -> list[Any]:
        rows = original_build(record, market_row, data_quality=data_quality)
        kept: list[Any] = []
        for candidate in rows:
            if not _passes_quality_shape(candidate, market_row):
                continue
            tags = list(getattr(candidate, "reason_tags", []) or [])
            if "steam_quality_gate" not in tags:
                tags.append("steam_quality_gate")
            if "steam_trajectory_gate" not in tags:
                tags.append("steam_trajectory_gate")
            candidate.reason_tags = tags
            kept.append(candidate)
        return kept

    steam.build_autonomous_steam_candidates = strict_build
    multi_runtime.apply_autonomous_steam = steam.apply_autonomous_steam

    original_confidence = metrics.confidence_snapshot
    strict_confidence = _strict_confidence_wrapper(original_confidence)
    metrics.confidence_snapshot = strict_confidence
    multi_entry_enrichment.confidence_snapshot = strict_confidence
    metrics.STEAM_FORMULA = _STEAM_FORMULA
    metrics._STRONG_STEAM_LEVELS = set(metrics._STRONG_STEAM_LEVELS) | {"AUTONOMOUS_STEAM"}
    multi_card.STEAM_FORMULA = _STEAM_FORMULA

    _INSTALLED = True
    print(
        "GOOL_STEAM_QUALITY installed "
        f"base={os.getenv('XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP')}pp/"
        f"{os.getenv('XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES')}x "
        f"two_move={os.getenv('XBET_AUTONOMOUS_STEAM_TWO_MOVE_MIN_DELTA_PP')}pp "
        f"persistent={os.getenv('XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_DELTA_PP')}pp/"
        f"{os.getenv('XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_MOVES')}x/"
        f"{os.getenv('XBET_AUTONOMOUS_STEAM_PERSISTENT_MIN_RELATED_MARKETS')}markets "
        f"extreme={os.getenv('XBET_AUTONOMOUS_STEAM_EXTREME_DELTA_PP')}pp/"
        f"{os.getenv('XBET_AUTONOMOUS_STEAM_EXTREME_ONE_WAY_MOVES')}x "
        f"retrace<={os.getenv('XBET_STEAM_MAX_RETRACE_PP')}pp",
        flush=True,
    )


__all__ = ["install_steam_quality_hardening", "_passes_quality_shape", "_trajectory_ok"]
