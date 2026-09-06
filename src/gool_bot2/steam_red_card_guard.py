from __future__ import annotations

from typing import Any, Callable

from .match_context import card_context


_INSTALLED = False


def _red_context(record: dict[str, Any]) -> dict[str, Any]:
    cards = dict(card_context(record) or {})
    home = int(cards.get("home_red") or 0)
    away = int(cards.get("away_red") or 0)
    return {
        "has_red_card": bool(cards.get("has_red_card") or home > 0 or away > 0),
        "home_red": home,
        "away_red": away,
    }


def install_steam_red_card_guard() -> None:
    """Block autonomous STEAM when a red card can explain the repricing.

    A dismissal is a football event, not anonymous market information.  The
    bookmaker reprices several correlated goal markets immediately after it, so
    treating that move as independent steam double-counts the same event.  The
    guard is intentionally conservative: any current red card blocks STEAM for
    the rest of that match. Ordinary Brain V3 remains independent and may still
    evaluate the new 10v11 football state from LIVE statistics.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from . import multi_autonomous_steam as steam
    from . import multi_runtime

    original: Callable[..., list[Any]] = steam.build_autonomous_steam_candidates

    def guarded_build(
        record: dict[str, Any],
        market_row: dict[str, Any] | None,
        *,
        data_quality: float,
    ) -> list[Any]:
        match = record.get("match") or {}
        red = _red_context(record)
        record["xbet_steam_red_card_guard"] = {
            "blocked": bool(red["has_red_card"]),
            "reason": "red_card_present" if red["has_red_card"] else "clear",
            **red,
        }
        if red["has_red_card"]:
            print(
                f"GOOL_STEAM_GUARD match={match.get('flashscore_event_id') or '-'} "
                f"minute={int(match.get('minute') or 0)} reason=red_card_present "
                f"red={red['home_red']}:{red['away_red']} action=block_steam",
                flush=True,
            )
            return []
        return original(record, market_row, data_quality=data_quality)

    steam.build_autonomous_steam_candidates = guarded_build
    # multi_runtime imports apply_autonomous_steam by value. The function itself
    # resolves build_autonomous_steam_candidates from the steam module at runtime,
    # but reassigning keeps the production binding explicit after installer layers.
    multi_runtime.apply_autonomous_steam = steam.apply_autonomous_steam
    _INSTALLED = True
    print("GOOL_STEAM_RED_GUARD installed mode=block_any_current_red_card", flush=True)


__all__ = ["install_steam_red_card_guard", "_red_context"]
