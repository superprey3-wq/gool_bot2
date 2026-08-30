from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .archive_statsbomb import record_from_statsbomb


RAW_BASES = (
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data",
    "https://raw.githubusercontent.com/hudl/open-data/master/data",
    "https://raw.githubusercontent.com/zagilbert/StatsBomb-open-data/master/data",
)
USER_AGENT = "gool_bot2-statsbomb-stream/1.0"


def _get_json(url: str, retries: int = 3, timeout: int = 120) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def _resolve_base() -> tuple[str, list[dict[str, Any]]]:
    errors: list[str] = []
    for base in RAW_BASES:
        try:
            rows = _get_json(f"{base}/competitions.json")
            if isinstance(rows, list) and rows:
                return base, rows
        except Exception as exc:
            errors.append(f"{base}: {type(exc).__name__}: {exc}")
    raise RuntimeError("No StatsBomb raw mirror available: " + " | ".join(errors))


def _collect_matches(base: str, competitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for index, competition in enumerate(competitions, start=1):
        competition_id = competition.get("competition_id")
        season_id = competition.get("season_id")
        if competition_id is None or season_id is None:
            continue
        url = f"{base}/matches/{competition_id}/{season_id}.json"
        try:
            rows = _get_json(url, retries=2)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        except Exception as exc:
            print(f"statsbomb matches skip url={url} error={type(exc).__name__}:{exc}", flush=True)
            continue
        if not isinstance(rows, list):
            continue
        for match in rows:
            match_id = match.get("match_id")
            if match_id is not None:
                matches[str(match_id)] = match
        if index % 20 == 0:
            print(f"statsbomb metadata competitions={index}/{len(competitions)} matches={len(matches)}", flush=True)
    return sorted(matches.values(), key=lambda row: (str(row.get("match_date") or ""), int(row.get("match_id") or 0)))


def _fetch_record(base: str, match: dict[str, Any]) -> dict[str, object] | None:
    match_id = match.get("match_id")
    if match_id is None:
        return None
    try:
        events = _get_json(f"{base}/events/{match_id}.json")
    except Exception as exc:
        print(f"statsbomb event skip match={match_id} error={type(exc).__name__}:{exc}", flush=True)
        return None
    if not isinstance(events, list):
        return None
    return record_from_statsbomb(match, events)


def import_statsbomb_stream(
    output: Path,
    limit: int | None = None,
    workers: int = 6,
) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if output.exists():
        with output.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    existing.add(str(json.loads(line).get("match_id") or ""))
                except Exception:
                    continue

    base, competitions = _resolve_base()
    matches = _collect_matches(base, competitions)
    pending = [match for match in matches if f"statsbomb:{match.get('match_id')}" not in existing]
    if limit is not None:
        pending = pending[: max(0, int(limit))]

    saved = 0
    failed = 0
    with output.open("a", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures = {pool.submit(_fetch_record, base, match): match for match in pending}
            for future in as_completed(futures):
                match = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    failed += 1
                    print(f"statsbomb future failed match={match.get('match_id')} error={type(exc).__name__}:{exc}", flush=True)
                    continue
                if record is None:
                    failed += 1
                    continue
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                saved += 1
                if saved % 100 == 0:
                    handle.flush()
                    print(f"statsbomb streamed saved={saved}/{len(pending)} failed={failed}", flush=True)

    return {
        "base": base,
        "competitions": len(competitions),
        "matches_discovered": len(matches),
        "matches_requested": len(pending),
        "matches_saved": saved,
        "matches_failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream StatsBomb Open Data match-by-match into GOOL canonical JSONL")
    parser.add_argument("--output", default="data/raw/statsbomb_stream.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    result = import_statsbomb_stream(Path(args.output), limit=args.limit, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
