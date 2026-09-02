from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flash_fulltime_overs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for row in rows or []:
        if str(row.get("bettingScope") or "") != "FULL_TIME":
            continue
        for odd in row.get("odds") or []:
            if str(odd.get("selection") or "").upper() != "OVER":
                continue
            line = _float((odd.get("handicap") or {}).get("value"))
            price = _float(odd.get("value"))
            if line is None or price is None or price <= 1.0:
                continue
            offers.append(
                {
                    "line": line,
                    "odd": price,
                    "bookmaker_id": row.get("bookmakerId"),
                    "source": row.get("source"),
                }
            )
    return offers


def _kambi_sanity(rows: list[dict[str, Any]], current_goals: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    full_time = [row for row in (rows or []) if str(row.get("scope") or "") == "FULL_TIME"]
    impossible = [row for row in full_time if (_float(row.get("line")) is not None and float(row["line"]) < current_goals)]
    return full_time, impossible


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe legacy GOOL LIVE odds modules against every current Flashscore match")
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.legacy_dir.resolve()))
    from gool_bot import kambi_live_odds, live_odds

    matches = _load(args.matches)
    result_rows: list[dict[str, Any]] = []

    summary: dict[str, Any] = {
        "tested": len(matches),
        "flash_live_ole2_with_odds": 0,
        "flash_live_ole2_sanity_ok": 0,
        "flash_live_ole2_impossible_line": 0,
        "kambi_with_totals": 0,
        "kambi_with_btts": 0,
        "kambi_any": 0,
        "kambi_impossible_line": 0,
        "any_legacy_source": 0,
    }

    for index, match in enumerate(matches, 1):
        event_id = str(match.get("event_id") or "")
        home = str(match.get("home") or "")
        away = str(match.get("away") or "")
        home_score = int(match.get("home_score") or 0)
        away_score = int(match.get("away_score") or 0)
        current_goals = home_score + away_score
        row: dict[str, Any] = {"match": match}

        try:
            flash_rows = live_odds.fetch_live_odds(event_id)
            flash_overs = _flash_fulltime_overs(flash_rows)
            impossible = [offer for offer in flash_overs if float(offer["line"]) < current_goals]
            row["legacy_flashscore"] = {
                "rows": len(flash_rows),
                "fulltime_over_offers": flash_overs,
                "has_odds": bool(flash_overs),
                "sanity_ok": bool(flash_overs) and not impossible,
                "impossible_already_settled_lines": impossible,
                "endpoint": "Flashscore LSApp lobtm + ole2 LIVE",
            }
            if flash_overs:
                summary["flash_live_ole2_with_odds"] += 1
            if flash_overs and not impossible:
                summary["flash_live_ole2_sanity_ok"] += 1
            if impossible:
                summary["flash_live_ole2_impossible_line"] += 1
        except Exception as exc:
            row["legacy_flashscore"] = {"has_odds": False, "error": f"{type(exc).__name__}: {exc}"}
            traceback.print_exc()

        try:
            kambi_totals = kambi_live_odds.get_live_goal_totals(home, away)
            kambi_btts = kambi_live_odds.get_btts_yes(home, away)
            full_time, impossible = _kambi_sanity(kambi_totals, current_goals)
            row["legacy_kambi"] = {
                "totals": kambi_totals,
                "btts_yes": kambi_btts,
                "has_totals": bool(kambi_totals),
                "has_btts": bool(kambi_btts),
                "fulltime_sanity_ok": bool(full_time) and not impossible,
                "impossible_already_settled_lines": impossible,
            }
            if kambi_totals:
                summary["kambi_with_totals"] += 1
            if kambi_btts:
                summary["kambi_with_btts"] += 1
            if kambi_totals or kambi_btts:
                summary["kambi_any"] += 1
            if impossible:
                summary["kambi_impossible_line"] += 1
        except Exception as exc:
            row["legacy_kambi"] = {"has_totals": False, "has_btts": False, "error": f"{type(exc).__name__}: {exc}"}
            traceback.print_exc()

        if row.get("legacy_flashscore", {}).get("has_odds") or row.get("legacy_kambi", {}).get("has_totals") or row.get("legacy_kambi", {}).get("has_btts"):
            summary["any_legacy_source"] += 1

        result_rows.append(row)
        print(
            f"LEGACY {index}/{len(matches)} {match.get('minute', 0)}' {home} - {away} {home_score}:{away_score} "
            f"flash={row.get('legacy_flashscore', {}).get('has_odds', False)} "
            f"kambi={bool(row.get('legacy_kambi', {}).get('has_totals') or row.get('legacy_kambi', {}).get('has_btts'))}"
        )

    summary["flash_live_ole2_coverage"] = summary["flash_live_ole2_with_odds"] / len(matches) if matches else 0.0
    summary["kambi_coverage"] = summary["kambi_any"] / len(matches) if matches else 0.0
    summary["combined_coverage"] = summary["any_legacy_source"] / len(matches) if matches else 0.0

    payload = {"summary": summary, "matches": result_rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LEGACY_LIVE_ODDS_SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
