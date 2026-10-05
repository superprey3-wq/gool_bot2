from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FINAL_RESULTS = {"won", "lost", "void"}
PHASES = ("PREMATCH", "LIVE")


def normalize_entry(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    phase = str(out.get("phase") or ("PREMATCH" if out.get("origin") == "multisport_prematch" else "LIVE")).upper()
    if phase not in PHASES:
        phase = "LIVE"
    out["journal_version"] = 2
    out["phase"] = phase
    out.setdefault("signal_type", "prematch_total_movement" if phase == "PREMATCH" else "live_total_movement")
    out.setdefault("market_family", "match_total")
    out.setdefault("scope", "FULL_MATCH")
    sport = str(out.get("sport") or "")
    out.setdefault("card_profile", f"{sport}_{phase.lower()}" if sport else phase.lower())
    out.setdefault("phase_policy", "prematch_all_scheduled_scopes" if phase == "PREMATCH" else "live_phase_routed")
    direction = str(out.get("direction") or "over").lower()
    if str(out.get("market_family") or "") in {"handicap", "moneyline"}:
        out["direction"] = direction if direction in {"home", "away", "draw"} else str(out.get("selection_side") or direction)
    else:
        out["direction"] = "under" if direction == "under" else "over"
    if not out.get("selection"):
        prefix = "ТМ" if out["direction"] == "under" else "ТБ"
        try:
            out["selection"] = f"{prefix} {float(out.get('line') or 0):g}"
        except (TypeError, ValueError):
            out["selection"] = prefix
    if phase == "PREMATCH":
        out.setdefault("scheduled_start_ts", float(out.get("start_ts") or 0.0))
    out.setdefault("result", "pending")
    out.setdefault("profit_units", 0.0)
    out.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    return out


def load_journal(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except Exception:
        return []
    if not isinstance(value, list):
        return []
    normalized = [normalize_entry(row) for row in value if isinstance(row, dict)]
    # Collapse legacy duplicates. 1xBet may rotate its internal event id while
    # Flashscore keeps a stable match id, so journal identity must prefer FS.
    deduped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in normalized:
        key = entry_key(row)
        previous = deduped.get(key)
        if previous is None:
            deduped[key] = row
            continue
        # Prefer a settled copy over a pending duplicate, otherwise keep latest.
        prev_final = str(previous.get("result") or "pending") in FINAL_RESULTS
        row_final = str(row.get("result") or "pending") in FINAL_RESULTS
        if row_final and not prev_final:
            deduped[key] = row
        elif row_final == prev_final and str(row.get("created_at") or "") >= str(previous.get("created_at") or ""):
            deduped[key] = row
    return list(deduped.values())


def save_journal(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [normalize_entry(row) for row in rows]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def entry_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    item = normalize_entry(row)
    stable_event_id = str(item.get("flashscore_event_id") or item.get("event_id") or "")
    return (
        str(item.get("sport") or ""),
        str(item.get("phase") or ""),
        stable_event_id,
        str(item.get("scope") or "FULL_MATCH"),
        str(item.get("market_family") or ""),
    )


def append_unique(path: Path, entry: dict[str, Any]) -> bool:
    item = normalize_entry(entry)
    rows = load_journal(path)
    key = entry_key(item)
    if any(entry_key(row) == key for row in rows):
        return False
    rows.append(item)
    save_journal(path, rows)
    return True


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if str(row.get("result") or "") in FINAL_RESULTS]
    won = sum(1 for row in settled if row.get("result") == "won")
    lost = sum(1 for row in settled if row.get("result") == "lost")
    void = sum(1 for row in settled if row.get("result") == "void")
    pending = sum(1 for row in rows if str(row.get("result") or "pending") == "pending")
    profit = sum(float(row.get("profit_units") or 0.0) for row in settled)
    odds: list[float] = []
    for row in rows:
        try:
            odd = float(row.get("odd") or 0.0)
        except (TypeError, ValueError):
            odd = 0.0
        if odd > 1.0:
            odds.append(odd)
    graded = won + lost
    return {
        "total": len(rows),
        "settled": len(settled),
        "won": won,
        "lost": lost,
        "void": void,
        "pending": pending,
        "hit_rate": (won / graded * 100.0) if graded else 0.0,
        "avg_odd": (sum(odds) / len(odds)) if odds else 0.0,
        "profit_units": profit,
        "roi": (profit / len(settled) * 100.0) if settled else 0.0,
    }


def grouped_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_entry(row) for row in rows]
    return {
        "all": stats(normalized),
        "by_phase": {phase: stats([row for row in normalized if row["phase"] == phase]) for phase in PHASES},
        "by_sport": {
            sport: {
                "all": stats([row for row in normalized if row.get("sport") == sport]),
                "by_phase": {
                    phase: stats([row for row in normalized if row.get("sport") == sport and row["phase"] == phase])
                    for phase in PHASES
                },
            }
            for sport in ("hockey", "basketball")
        },
        "markets": dict(Counter(str(row.get("market_family") or "unknown") for row in normalized)),
        "scopes": dict(Counter(str(row.get("scope") or "FULL_MATCH") for row in normalized)),
    }


def stat_line(value: dict[str, Any]) -> str:
    return (
        f"✅ {int(value.get('won') or 0)} · ❌ {int(value.get('lost') or 0)} · "
        f"↩️ {int(value.get('void') or 0)} · ⏳ {int(value.get('pending') or 0)} · "
        f"проход {float(value.get('hit_rate') or 0):.1f}% · "
        f"ср. кэф {float(value.get('avg_odd') or 0):.2f} · "
        f"P/L {float(value.get('profit_units') or 0):+.2f}u · ROI {float(value.get('roi') or 0):+.1f}%"
    )
