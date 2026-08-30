from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .archive_events import CanonicalEvent, event_to_dict


ON_TARGET_OUTCOMES = {"Goal", "Saved", "Saved to Post"}


def _side(team_id: object, home_id: object, away_id: object) -> str | None:
    if team_id == home_id:
        return "home"
    if team_id == away_id:
        return "away"
    return None


def _minute(event: dict[str, Any]) -> float:
    minute = float(event.get("minute") or 0.0)
    second = float(event.get("second") or 0.0)
    return minute + second / 60.0


def parse_statsbomb_events(
    events: list[dict[str, Any]],
    home_team_id: object,
    away_team_id: object,
) -> tuple[list[CanonicalEvent], list[dict[str, object]]]:
    canonical: list[CanonicalEvent] = []
    goals: list[dict[str, object]] = []

    for row in events:
        period = int(row.get("period") or 0)
        if period not in {1, 2}:
            continue
        team = row.get("team") or {}
        side = _side(team.get("id"), home_team_id, away_team_id)
        if side is None:
            continue
        minute = _minute(row)
        event_name = str((row.get("type") or {}).get("name") or "")

        if event_name == "Shot":
            shot = row.get("shot") or {}
            outcome = str((shot.get("outcome") or {}).get("name") or "")
            try:
                shot_xg = float(shot.get("statsbomb_xg")) if shot.get("statsbomb_xg") is not None else None
            except (TypeError, ValueError):
                shot_xg = None
            location = row.get("location") or []
            canonical.append(
                CanonicalEvent(
                    minute=minute,
                    period=period,
                    side=side,  # type: ignore[arg-type]
                    event_type="shot",
                    xg=shot_xg,
                    on_target=outcome in ON_TARGET_OUTCOMES,
                    location_x=float(location[0]) if len(location) > 0 else None,
                    location_y=float(location[1]) if len(location) > 1 else None,
                )
            )
            if outcome == "Goal":
                goals.append({"minute": minute, "period": period, "side": side})
            continue

        if event_name == "Own Goal Against":
            other = "away" if side == "home" else "home"
            goals.append({"minute": minute, "period": period, "side": other})
            continue

        if event_name == "Pass" and str(((row.get("pass") or {}).get("type") or {}).get("name") or "") == "Corner":
            canonical.append(CanonicalEvent(minute, period, side, "corner"))  # type: ignore[arg-type]
            continue

        card_name = str((((row.get("foul_committed") or {}).get("card") or {}).get("name")) or "")
        if card_name in {"Red Card", "Second Yellow"}:
            canonical.append(CanonicalEvent(minute, period, side, "red_card"))  # type: ignore[arg-type]

    canonical.sort(key=lambda event: (event.minute, event.event_type))
    goals.sort(key=lambda goal: float(goal["minute"]))
    return canonical, goals


def _kickoff(match: dict[str, Any]) -> str:
    date = str(match.get("match_date") or "1970-01-01")
    kick = str(match.get("kick_off") or "12:00:00")
    try:
        dt = datetime.fromisoformat(f"{date}T{kick}")
    except ValueError:
        dt = datetime.fromisoformat(f"{date}T12:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def record_from_statsbomb(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, object]:
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    canonical, goals = parse_statsbomb_events(events, home.get("home_team_id"), away.get("away_team_id"))
    competition = match.get("competition") or {}
    season = match.get("season") or {}
    return {
        "source": "statsbomb",
        "match_id": f"statsbomb:{match.get('match_id')}",
        "source_match_id": str(match.get("match_id")),
        "kickoff_at": _kickoff(match),
        "home": str(home.get("home_team_name") or ""),
        "away": str(away.get("away_team_name") or ""),
        "league": str(competition.get("competition_name") or ""),
        "season": str(season.get("season_name") or ""),
        "goals": goals,
        "events": [event_to_dict(event) for event in canonical],
    }


def iter_match_files(root: Path) -> Iterable[tuple[dict[str, Any], Path]]:
    matches_root = root / "data" / "matches"
    events_root = root / "data" / "events"
    for match_file in sorted(matches_root.rglob("*.json")):
        try:
            matches = json.loads(match_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(matches, list):
            continue
        for match in matches:
            match_id = match.get("match_id")
            if match_id is None:
                continue
            event_file = events_root / f"{match_id}.json"
            if event_file.exists():
                yield match, event_file


def import_statsbomb(root: Path, output: Path, limit: int | None = None) -> int:
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
        for match, event_file in iter_match_files(root):
            key = f"statsbomb:{match.get('match_id')}"
            if key in existing:
                continue
            events = json.loads(event_file.read_text(encoding="utf-8"))
            record = record_from_statsbomb(match, events)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.add(key)
            saved += 1
            if limit is not None and saved >= limit:
                break
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Import StatsBomb Open Data into GOOL canonical archive JSONL")
    parser.add_argument("--root", required=True, help="Path to a clone/extract of StatsBomb open-data")
    parser.add_argument("--output", default="data/raw/open_event_archive.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    saved = import_statsbomb(Path(args.root), Path(args.output), limit=args.limit)
    print(f"source=statsbomb saved_matches={saved} output={args.output}")


if __name__ == "__main__":
    main()
