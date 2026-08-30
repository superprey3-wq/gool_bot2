from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive_events import CanonicalEvent, event_to_dict

GOAL_TAG = 101
OWN_GOAL_TAG = 102
ACCURATE_TAG = 1801
RED_CARD_TAGS = {1701, 1703}
YELLOW_CARD_TAG = 1702


def _tags(row: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for tag in row.get("tags") or []:
        try:
            out.add(int(tag.get("id")))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _period(row: dict[str, Any]) -> int | None:
    value = str(row.get("matchPeriod") or "").upper()
    if value == "1H":
        return 1
    if value == "2H":
        return 2
    return None


def _minute(row: dict[str, Any], period: int) -> float:
    seconds = float(row.get("eventSec") or 0.0)
    base = 0.0 if period == 1 else 45.0
    return base + seconds / 60.0


def _team_sides(match: dict[str, Any]) -> tuple[dict[int, str], dict[int, str]]:
    sides: dict[int, str] = {}
    names: dict[int, str] = {}
    teams_data = match.get("teamsData") or {}
    for raw_id, payload in teams_data.items():
        try:
            team_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        side = str((payload or {}).get("side") or "").lower()
        if side in {"home", "away"}:
            sides[team_id] = side
        name = str((payload or {}).get("teamName") or (payload or {}).get("name") or "")
        if name:
            names[team_id] = name
    return sides, names


def parse_wyscout_events(events: list[dict[str, Any]], team_sides: dict[int, str]) -> tuple[list[CanonicalEvent], list[dict[str, object]]]:
    canonical: list[CanonicalEvent] = []
    goals: list[dict[str, object]] = []

    for row in events:
        period = _period(row)
        if period is None:
            continue
        try:
            team_id = int(row.get("teamId"))
        except (TypeError, ValueError):
            continue
        side = team_sides.get(team_id)
        if side not in {"home", "away"}:
            continue
        minute = _minute(row, period)
        tags = _tags(row)
        event_name = str(row.get("eventName") or "")
        subevent_name = str(row.get("subEventName") or "")

        if event_name == "Shot":
            positions = row.get("positions") or []
            first = positions[0] if positions else {}
            canonical.append(
                CanonicalEvent(
                    minute=minute,
                    period=period,
                    side=side,  # type: ignore[arg-type]
                    event_type="shot",
                    xg=None,
                    on_target=ACCURATE_TAG in tags or GOAL_TAG in tags,
                    location_x=float(first.get("x")) if first.get("x") is not None else None,
                    location_y=float(first.get("y")) if first.get("y") is not None else None,
                )
            )
            if GOAL_TAG in tags:
                goal_side = "away" if OWN_GOAL_TAG in tags and side == "home" else "home" if OWN_GOAL_TAG in tags else side
                goals.append({"minute": minute, "period": period, "side": goal_side})
            continue

        if event_name == "Free Kick" and subevent_name == "Corner":
            canonical.append(CanonicalEvent(minute, period, side, "corner"))  # type: ignore[arg-type]
            continue

        if YELLOW_CARD_TAG in tags:
            canonical.append(CanonicalEvent(minute, period, side, "yellow_card"))  # type: ignore[arg-type]
        if RED_CARD_TAGS.intersection(tags):
            canonical.append(CanonicalEvent(minute, period, side, "red_card"))  # type: ignore[arg-type]

    canonical.sort(key=lambda event: (event.minute, event.event_type))
    goals.sort(key=lambda goal: float(goal["minute"]))
    return canonical, goals


def _match_id(match: dict[str, Any]) -> str:
    return str(match.get("wyId") or match.get("matchId") or match.get("id") or "")


def _home_away(match: dict[str, Any], sides: dict[int, str], names: dict[int, str]) -> tuple[str, str]:
    label = str(match.get("label") or "")
    if " - " in label:
        left, right = label.split(" - ", 1)
        return left.strip(), right.split(",", 1)[0].strip()
    home_id = next((team_id for team_id, side in sides.items() if side == "home"), None)
    away_id = next((team_id for team_id, side in sides.items() if side == "away"), None)
    return names.get(home_id or -1, str(home_id or "")), names.get(away_id or -1, str(away_id or ""))


def record_from_wyscout(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, object]:
    sides, names = _team_sides(match)
    canonical, goals = parse_wyscout_events(events, sides)
    home, away = _home_away(match, sides, names)
    raw_date = str(match.get("dateutc") or match.get("date") or "1970-01-01 12:00:00")
    try:
        kickoff = datetime.fromisoformat(raw_date.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        kickoff = datetime(1970, 1, 1, 12, tzinfo=timezone.utc)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    match_id = _match_id(match)
    return {
        "source": "wyscout",
        "match_id": f"wyscout:{match_id}",
        "source_match_id": match_id,
        "kickoff_at": kickoff.astimezone(timezone.utc).isoformat(),
        "home": home,
        "away": away,
        "league": str(match.get("competitionId") or ""),
        "season": str(match.get("seasonId") or ""),
        "goals": goals,
        "events": [event_to_dict(event) for event in canonical],
    }


def _load_matches(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("matches*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for match in rows:
            match_id = _match_id(match)
            if match_id:
                out[match_id] = match
    return out


def import_wyscout(root: Path, output: Path, limit: int | None = None) -> int:
    matches = _load_matches(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(str(json.loads(line).get("match_id") or ""))
            except Exception:
                continue

    saved = 0
    with output.open("a", encoding="utf-8") as handle:
        for path in sorted(root.rglob("events*.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(rows, list):
                continue
            grouped: dict[str, list[dict[str, Any]]] = {}
            for event in rows:
                match_id = str(event.get("matchId") or "")
                if match_id in matches:
                    grouped.setdefault(match_id, []).append(event)
            for match_id, events in grouped.items():
                key = f"wyscout:{match_id}"
                if key in existing:
                    continue
                record = record_from_wyscout(matches[match_id], events)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                existing.add(key)
                saved += 1
                if limit is not None and saved >= limit:
                    return saved
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Wyscout public event data into GOOL canonical archive JSONL")
    parser.add_argument("--root", required=True, help="Directory containing extracted matches*.json and events*.json")
    parser.add_argument("--output", default="data/raw/open_event_archive.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    saved = import_wyscout(Path(args.root), Path(args.output), limit=args.limit)
    print(f"source=wyscout saved_matches={saved} output={args.output}")


if __name__ == "__main__":
    main()
