from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_delivery import was_publicly_sent
from .multi_public_metrics import source_label


_INSTALLED = False


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _score(value: Any) -> list[int]:
    try:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [int(value[0] or 0), int(value[1] or 0)]
    except Exception:
        pass
    return [0, 0]


def _odd(row: dict[str, Any]) -> str:
    try:
        value = float(row.get("odd") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return f"{value:.2f}" if value > 1.0 else "—"


def _display_market(row: dict[str, Any]) -> str:
    """Human-readable market label for LIVE and PREMATCH journal rows."""
    import re

    market = str(row.get("market") or "").strip()
    selection = str(row.get("selection") or "").strip()
    origin = str(row.get("origin") or "").strip().casefold()

    # LIVE entries already store a public label such as "1Т ТБ 0.5".
    if origin not in {"prematch", "prematch_value"}:
        return market or selection or "?"

    family = market.casefold().replace("-", "_").replace(" ", "_")
    low = " ".join(selection.casefold().replace("_", " ").split())

    def line_side() -> tuple[str | None, str | None]:
        m = re.search(r"\b(over|under)\s+([+-]?\d+(?:[.,]\d+)?)", low)
        if not m:
            return None, None
        return m.group(1), m.group(2).replace(",", ".")

    side, line = line_side()
    if family in {"match_total", "ft_over_under"} and side and line:
        return f"{'ТБ' if side == 'over' else 'ТМ'} {line}"
    if family == "home_total" and side and line:
        return f"{'ИТБ1' if side == 'over' else 'ИТМ1'} {line}"
    if family == "away_total" and side and line:
        return f"{'ИТБ2' if side == 'over' else 'ИТМ2'} {line}"
    if family in {"1h_over_under", "first_half_total"} and side and line:
        return f"1-й тайм: {'ТБ' if side == 'over' else 'ТМ'} {line}"
    if family in {"2h_over_under", "second_half_total"} and side and line:
        return f"2-й тайм: {'ТБ' if side == 'over' else 'ТМ'} {line}"

    if family in {"btts", "btts_yes", "btts_no"} or "btts" in low:
        negative = family.endswith("_no") or low in {"no", "нет"} or " no" in f" {low}"
        return "Обе забьют — Нет" if negative else "Обе забьют — Да"

    if family in {"match_1x2", "1x2", "home_draw_away"}:
        if low in {"home", "1", "home win", "п1"}:
            return "Победа хозяев"
        if low in {"away", "2", "away win", "п2"}:
            return "Победа гостей"
        if low in {"draw", "x", "ничья"}:
            return "Ничья"

    if family == "double_chance":
        compact = selection.upper().replace(" ", "").replace("_", "")
        aliases = {"HOMEORDRAW": "1X", "DRAWORAWAY": "X2", "HOMEORAWAY": "12"}
        return f"Двойной шанс {aliases.get(compact, selection.upper().replace('_', ' '))}"

    # Legacy exact-trend identifiers.
    exact = {
        "FT_OVER_2.5": "ТБ 2.5",
        "FT_UNDER_2.5": "ТМ 2.5",
        "BTTS_YES": "Обе забьют — Да",
        "BTTS_NO": "Обе забьют — Нет",
        "1H_OVER_0.5": "1-й тайм: ТБ 0.5",
        "1H_OVER_1.5": "1-й тайм: ТБ 1.5",
        "2H_OVER_0.5": "2-й тайм: ТБ 0.5",
        "2H_OVER_1.5": "2-й тайм: ТБ 1.5",
    }
    if market.upper() in exact:
        return exact[market.upper()]

    # If production selected a readable full-market selection, prefer it to an
    # internal family name. Never expose match_total/home_total to Telegram.
    if selection:
        pretty = selection.replace("_", " ")
        m = re.search(r"\b(over|under)\s+(\d+(?:[.,]\d+)?)", pretty.casefold())
        if m:
            return f"{'ТБ' if m.group(1) == 'over' else 'ТМ'} {m.group(2).replace(',', '.')}"
        return pretty
    return market.replace("_", " ") or "?"


def _latest_analysis(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    try:
        for line in path.open("r", encoding="utf-8"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            mid = str(row.get("match_id") or "")
            if not mid:
                continue
            previous = latest.get(mid)
            stamp = str(row.get("created_at") or row.get("captured_at") or "")
            old_stamp = str((previous or {}).get("created_at") or (previous or {}).get("captured_at") or "")
            if previous is None or stamp >= old_stamp:
                latest[mid] = row
    except Exception:
        return {}
    return latest


def _is_open(row: dict[str, Any]) -> bool:
    if str(row.get("mode") or "active").lower() != "active":
        return False
    if str(row.get("result") or "pending").lower() not in {"pending", "tracking"}:
        return False
    if not was_publicly_sent(row):
        return False
    # Parent parlays are containers, not matches. Their legs appear only once
    # the corresponding child match is genuinely live.
    if str(row.get("origin") or "").casefold() == "prematch_parlay":
        return False
    origin = str(row.get("origin") or "").casefold()
    if origin in {"prematch", "prematch_value"}:
        # PREMATCH products stay out of "В ИГРЕ" until their scheduled kickoff.
        # VALUE used to bypass this block because it has origin=prematch_value.
        try:
            kickoff_ts = float(row.get("kickoff_ts") or 0.0)
        except (TypeError, ValueError):
            kickoff_ts = 0.0
        if kickoff_ts > 0:
            if time.time() < kickoff_ts:
                return False
        else:
            # Legacy prematch rows without a stored kickoff still need an
            # explicit lifecycle transition from the settlement/live tracker.
            lifecycle = str(row.get("lifecycle") or "scheduled").casefold()
            if lifecycle not in {"in_game", "live"} and not bool(row.get("in_game")):
                return False
    return True


def journal_in_game_sections(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    """Render delivered, unsettled journal entries only.

    This pure renderer accepts explicit paths for tests/library callers. Production
    installs a wrapper below that always resolves the canonical GOOL Multi paths,
    because the legacy Telegram responder still passes its old signal_journal path.
    Settlement is performed before rendering by the command pipeline, so this view
    never creates or settles a signal itself.
    """
    rows = [dict(row) for row in load_signal_journal(Path(journal_path)) if _is_open(row)]
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("entry_key") or row.get("brain_signal_key") or "")
        if not key:
            key = f"{row.get('match_id')}:{row.get('strategy')}:{row.get('minute')}:{row.get('market')}"
        previous = latest.get(key)
        if previous is None or str(row.get("created_at") or "") >= str(previous.get("created_at") or ""):
            latest[key] = row
    rows = list(latest.values())
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)

    if not rows:
        return ["🟢 <b>GOOL MULTI · В ИГРЕ</b>\n\nОткрытых сигналов сейчас нет."]

    states = _latest_analysis(analysis_path)
    parts = [f"🟢 <b>GOOL MULTI · В ИГРЕ</b>\nОткрыто: <b>{len(rows)}</b>"]
    for index, row in enumerate(rows, 1):
        state = states.get(str(row.get("match_id") or "")) or {}
        entry_score = _score(row.get("score"))
        current_score = _score(state.get("score")) if state.get("score") is not None else entry_score
        try:
            current_minute = int(state.get("minute") or row.get("minute") or 0)
        except (TypeError, ValueError):
            current_minute = int(row.get("minute") or 0)
        try:
            rating = float(row.get("confidence_score") or row.get("rating") or row.get("event_score") or 0.0)
        except (TypeError, ValueError):
            rating = 0.0
        reason = str(row.get("selection_reason") or row.get("reason") or "")
        source = source_label(row.get("signal_source") or row.get("source"))
        block = (
            f"<b>{index}. {_h(row.get('home'))} — {_h(row.get('away'))}</b>\n"
            f"сейчас <b>{current_minute}' · {current_score[0]}:{current_score[1]}</b>\n"
            f"🎯 <b>{_h(_display_market(row))} @ {_odd(row)}</b>\n"
            f"🧠 <b>{rating:.0f}/100</b> · {_h(source)}\n"
            f"↳ вход {int(row.get('minute') or 0)}' · {entry_score[0]}:{entry_score[1]}"
        )
        if reason:
            block += f"\n{_h(reason)}"
        if len("\n\n".join(parts + [block])) > 3800:
            break
        parts.append(block)
    return ["\n\n".join(parts)]


def production_in_game_sections(_: Path | None = None, __: Path | None = None) -> list[str]:
    """Production adapter: ignore legacy responder paths and use Multi storage."""
    from . import multi_menu

    return journal_in_game_sections(multi_menu.journal_path(), multi_menu.analysis_path())


def install_journal_in_game() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import telegram

    telegram.in_game_sections = production_in_game_sections
    _INSTALLED = True
    print("GOOL_IN_GAME installed source=canonical_multi_journal settlement=external", flush=True)


__all__ = ["install_journal_in_game", "journal_in_game_sections", "production_in_game_sections"]
