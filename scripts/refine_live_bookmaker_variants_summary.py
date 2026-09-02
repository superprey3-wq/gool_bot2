from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCES = ("kambi_unibet", "bet365", "betano")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def summarize(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    event_meta: dict[str, dict[str, Any]] = {}
    by_event_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
    source_states: dict[str, list[str]] = {source: [] for source in SOURCES}
    source_diagnostics: dict[str, list[dict[str, Any]]] = {source: [] for source in SOURCES}

    for snap in snapshots:
        snap_no = snap.get("snapshot")
        captured_at = snap.get("captured_at")
        for match in snap.get("flashscore", []):
            event_id = str(match.get("event_id") or "")
            if not event_id:
                continue
            meta = event_meta.setdefault(
                event_id,
                {
                    "home": match.get("home"),
                    "away": match.get("away"),
                    "league": match.get("league"),
                    "states": [],
                },
            )
            meta["states"].append(
                {
                    "snapshot": snap_no,
                    "captured_at": captured_at,
                    "minute": match.get("minute"),
                    "home_score": match.get("home_score"),
                    "away_score": match.get("away_score"),
                }
            )

        for source in SOURCES:
            payload = (snap.get("sources") or {}).get(source) or {}
            state = str(payload.get("state") or "")
            source_states[source].append(state)
            source_diagnostics[source].append(
                {
                    "snapshot": snap_no,
                    "captured_at": captured_at,
                    "state": state,
                    "chosen_url": payload.get("chosen_url"),
                    "attempts": payload.get("attempts") or [],
                }
            )
            for row in payload.get("matches", []) or []:
                event_id = str(row.get("event_id") or "")
                if event_id:
                    by_event_source.setdefault((event_id, source), []).append(
                        {"snapshot": snap_no, "captured_at": captured_at, **row}
                    )

    verdict_counts: dict[str, int] = {}
    source_summary: dict[str, dict[str, int]] = {source: {} for source in SOURCES}
    events: list[dict[str, Any]] = []

    for event_id, meta in sorted(
        event_meta.items(), key=lambda item: (str(item[1].get("league") or ""), str(item[1].get("home") or ""))
    ):
        states = list(meta.get("states") or [])
        fs_state_keys = {(s.get("minute"), s.get("home_score"), s.get("away_score")) for s in states}
        progress = len(fs_state_keys) > 1
        source_rows: dict[str, Any] = {}

        for source in SOURCES:
            rows = by_event_source.get((event_id, source), [])
            matched_quotes = [r for r in rows if r.get("status") == "matched"]
            matched_no_quotes = [r for r in rows if r.get("status") == "matched_no_quotes"]
            fingerprints = [str(r.get("fingerprint") or "") for r in matched_quotes if str(r.get("fingerprint") or "")]
            odds_changed = len(set(fingerprints)) > 1

            if odds_changed and progress:
                verdict = "PROVEN"
            elif odds_changed:
                verdict = "ODDS_CHANGED_NO_FS_PROGRESS"
            elif matched_quotes:
                verdict = "FOUND_NO_CHANGE"
            elif matched_no_quotes:
                verdict = "MATCHED_NO_QUOTES"
            elif rows and all(r.get("status") == "source_blocked" for r in rows):
                verdict = "BLOCKED_GEO"
            elif rows and all(r.get("status") == "source_error" for r in rows):
                verdict = "SOURCE_ERROR"
            else:
                verdict = "NOT_FOUND"

            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            source_summary[source][verdict] = source_summary[source].get(verdict, 0) + 1
            source_rows[source] = {
                "verdict": verdict,
                "flashscore_progress": progress,
                "odds_changed": odds_changed,
                "matched_quote_snapshots": len(matched_quotes),
                "matched_no_quote_snapshots": len(matched_no_quotes),
                "snapshots": rows,
            }

        events.append(
            {
                "event_id": event_id,
                "home": meta.get("home"),
                "away": meta.get("away"),
                "league": meta.get("league"),
                "flashscore_states": states,
                "sources": source_rows,
            }
        )

    return {
        "generated_at": utc_now(),
        "strict_quote_evidence": True,
        "snapshot_count": len(snapshots),
        "unique_flashscore_live_matches": len(event_meta),
        "source_states": source_states,
        "source_diagnostics": source_diagnostics,
        "source_summary": source_summary,
        "verdict_counts": verdict_counts,
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild bookmaker audit summary with strict quote-evidence verdicts")
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    snapshots = load(args.audit)
    if not isinstance(snapshots, list):
        raise SystemExit("audit payload must be a list of snapshots")
    summary = summarize(snapshots)
    write(args.output, summary)
    print(
        "STRICT_FINAL_SUMMARY "
        + json.dumps(
            {
                "snapshot_count": summary["snapshot_count"],
                "unique_flashscore_live_matches": summary["unique_flashscore_live_matches"],
                "source_summary": summary["source_summary"],
                "verdict_counts": summary["verdict_counts"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
