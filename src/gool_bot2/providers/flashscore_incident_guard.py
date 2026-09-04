from __future__ import annotations

import re
from typing import Any

from .flashscore import FlashscoreProvider, STAT_MAP


_EVENT_KIND = {
    "1": "yellow_card",
    "2": "red_card",
    "3": "goal",
    "5": "penalty_awarded",
    "6": "substitution_out",
    "7": "substitution_in",
    "10": "penalty",
}


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _minute(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d{1,3})(?:\+(\d{1,2}))?", str(value or ""))
    if not match:
        return None
    base = int(match.group(1))
    added = int(match.group(2) or 0)
    return base + added, base, added


def _event_type(code: str, label: str, home_score: int | None, away_score: int | None) -> str:
    normalized = str(label or "").strip().casefold()
    if code == "3" or normalized == "goal":
        return "goal"
    # Flashscore uses IE=10 for the converted penalty row. The preceding IE=5
    # row is only "Penalty Awarded" and must never be counted as a goal.
    if code == "10" and (home_score is not None or away_score is not None):
        return "goal"
    if "red card" in normalized:
        return "red_card"
    if "yellow" in normalized and "card" in normalized:
        return "yellow_card"
    if "var" in normalized:
        return "var"
    return _EVENT_KIND.get(code, normalized.replace(" ", "_") or "incident")


def parse_summary_incidents(body: str) -> list[dict[str, Any]]:
    """Parse the real Flashscore `df_sui` incident grammar.

    Important: IA is the side (1=home, 2=away), not the event type. IE/IK carry
    the incident kind. One chunk may contain multiple IE blocks, e.g. a penalty
    award followed by the converted penalty with the updated scoreboard.
    """
    rows: list[dict[str, Any]] = []
    for chunk_order, raw_chunk in enumerate((body or "").split("~III")):
        chunk = str(raw_chunk or "")
        if not chunk:
            continue

        side_match = re.search(r"(?:^|¬)IA(?:÷|¬)(\d+)", chunk)
        side_code = side_match.group(1) if side_match else ""
        side = "home" if side_code == "1" else ("away" if side_code == "2" else None)
        minute_match = re.search(r"(?:^|¬)(?:IB|IBX)(?:÷|¬)([^¬~]+)", chunk)
        minute = _minute(minute_match.group(1) if minute_match else "")
        if minute is None:
            continue
        total_minute, base_minute, added_time = minute

        segments: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for token in chunk.split("¬"):
            if "÷" not in token:
                continue
            key, value = token.split("÷", 1)
            key = key.strip()
            if key == "IE":
                if current is not None:
                    segments.append(current)
                current = {"event_code": value.strip()}
                continue
            if current is not None and key in {"IK", "INX", "IOX", "IF", "IM", "IU", "ICT"}:
                current[key] = value
        if current is not None:
            segments.append(current)

        # Old fixtures/tests may not carry IE/IK. Only allow a fallback when the
        # chunk itself is explicitly labelled as a goal; never infer a goal from
        # a generic card/substitution row merely because it carries current score.
        if not segments and "goal" in chunk.casefold():
            segments = [{
                "event_code": "legacy_goal",
                "IK": "Goal",
                "INX": (re.search(r"INX(?:÷|¬)(\d+)", chunk) or [None, None])[1],
                "IOX": (re.search(r"IOX(?:÷|¬)(\d+)", chunk) or [None, None])[1],
            }]

        for segment_order, segment in enumerate(segments):
            home_score = _as_int(segment.get("INX"))
            away_score = _as_int(segment.get("IOX"))
            label = str(segment.get("IK") or "").strip()
            code = str(segment.get("event_code") or "")
            event_type = _event_type(code, label, home_score, away_score)
            rows.append({
                "minute": total_minute,
                "base_minute": base_minute,
                "added_time": added_time,
                "display_minute": f"{base_minute}+{added_time}" if added_time else str(base_minute),
                "period": "1H" if base_minute <= 45 else "2H",
                "event_type": event_type,
                "event_code": code,
                "event_label": label,
                "side": side,
                "home": home_score,
                "away": away_score,
                "player": str(segment.get("IF") or "").strip() or None,
                "player_id": str(segment.get("IM") or "").strip() or None,
                "chunk_order": chunk_order,
                "segment_order": segment_order,
            })

    rows.sort(key=lambda row: (int(row["minute"]), int(row["chunk_order"]), int(row["segment_order"])))
    return rows


def goals_from_incidents(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    goals: list[dict[str, Any]] = []
    last = (0, 0)
    for item in incidents:
        if str(item.get("event_type") or "") != "goal":
            continue
        home = item.get("home")
        away = item.get("away")
        side = str(item.get("side") or "")

        if home is None and away is None:
            if side == "home":
                home, away = last[0] + 1, last[1]
            elif side == "away":
                home, away = last[0], last[1] + 1
            else:
                continue
        else:
            home = last[0] if home is None else int(home)
            away = last[1] if away is None else int(away)

        if home < last[0] or away < last[1]:
            continue
        dh, da = home - last[0], away - last[1]
        if (dh, da) != (1, 0) and (dh, da) != (0, 1):
            continue

        goals.append({
            "minute": int(item["minute"]),
            "display_minute": item.get("display_minute") or str(item["minute"]),
            "period": item.get("period") or ("1H" if int(item["minute"]) <= 45 else "2H"),
            "event_type": "goal",
            "side": "home" if dh else "away",
            "score": [home, away],
            "goal_kind": "penalty" if str(item.get("event_code") or "") == "10" else "open_play_or_unknown",
            "player": item.get("player"),
        })
        last = (home, away)
    return goals


def install() -> None:
    if getattr(FlashscoreProvider, "_incident_guard_installed", False):
        return

    # Confirmed by the real Leones-Atletico feed: SD=22 is Red cards.
    STAT_MAP["22"] = "red_cards"

    def fetch_incident_timeline(self: FlashscoreProvider, event_id: str) -> list[dict[str, Any]]:
        return parse_summary_incidents(self._feed(f"df_sui_1_{event_id}"))

    def fetch_goal_timeline(self: FlashscoreProvider, event_id: str) -> list[dict[str, Any]]:
        return goals_from_incidents(fetch_incident_timeline(self, event_id))

    FlashscoreProvider.fetch_incident_timeline = fetch_incident_timeline
    FlashscoreProvider.fetch_goal_timeline = fetch_goal_timeline
    FlashscoreProvider._incident_guard_installed = True
