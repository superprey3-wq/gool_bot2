from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _load_matches(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def discover_live(output: Path, limit: int) -> int:
    from gool_bot2.providers.flashscore import FlashscoreProvider

    provider = FlashscoreProvider()
    matches = provider.live_matches()
    rows: list[dict[str, Any]] = []
    for match in matches:
        minute = int(getattr(match, "minute", 0) or 0)
        if minute <= 0 or minute > 90:
            continue
        event_id = str(getattr(match, "provider_match_id", "") or "").strip()
        if not event_id:
            continue
        rows.append(
            {
                "event_id": event_id,
                "home": str(getattr(match, "home", "") or ""),
                "away": str(getattr(match, "away", "") or ""),
                "league": str(getattr(match, "league", "") or ""),
                "minute": minute,
                "home_score": int(getattr(match, "home_score", 0) or 0),
                "away_score": int(getattr(match, "away_score", 0) or 0),
                "flashscore_url": f"https://www.flashscore.com/match/football/{event_id}/",
            }
        )
    rows.sort(key=lambda row: (-int(row["minute"]), row["home"], row["away"]))
    rows = rows[: max(1, limit)]
    _write(output, rows)
    print(f"FRESH_ODDS_DISCOVER live={len(matches)} selected={len(rows)} output={output}")
    for row in rows:
        print(
            f"  {row['minute']:>2}' {row['home']} - {row['away']} "
            f"{row['home_score']}:{row['away_score']} id={row['event_id']}"
        )
    return 0 if rows else 2


def run_m3mons(matches_path: Path, source_dir: Path, output: Path, limit: int) -> int:
    sys.path.insert(0, str(source_dir))
    from flashscorescraper import scrape_odds
    from models.odds_filter import OddsFilter

    rows = []
    for match in _load_matches(matches_path)[:limit]:
        item: dict[str, Any] = {"match": match, "source": "M3MONs/FlashscoreScraper"}
        try:
            result = scrape_odds(
                match["flashscore_url"],
                sport="football",
                engine="curl",
                timeout=18,
                odds_filter=OddsFilter(odds=["over-under", "both-teams-to-score"]),
            )
            payload = _jsonable(result)
            successful = [x for x in payload.get("odds", []) if not x.get("error") and x.get("data")]
            item.update(
                {
                    "ok": bool(successful),
                    "successful_markets": len(successful),
                    "payload": payload,
                }
            )
        except Exception as exc:
            item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            traceback.print_exc()
        rows.append(item)
        print(
            f"M3MONS match={match.get('home')} - {match.get('away')} "
            f"ok={item.get('ok')} markets={item.get('successful_markets', 0)} error={item.get('error', '-') }"
        )
    _write(output, rows)
    return 0


async def _run_realine_async(matches: list[dict[str, Any]], source_dir: Path, limit: int) -> list[dict[str, Any]]:
    sys.path.insert(0, str(source_dir / "src"))
    from flashscore_bot.common_scraper import fetch_all_odds_data

    rows: list[dict[str, Any]] = []
    bet_types = {"over-under": True, "both-teams-to-score": True}
    for match in matches[:limit]:
        item: dict[str, Any] = {"match": match, "source": "realine0/flashscore-football-odds-scraper"}
        try:
            payload = await fetch_all_odds_data(str(match["event_id"]), ["bet365"], bet_types)
            flat = _jsonable(payload)
            has_values = bool(flat) and any(bool(v) for v in flat.values()) if isinstance(flat, dict) else bool(flat)
            item.update({"ok": has_values, "payload": flat})
        except Exception as exc:
            item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            traceback.print_exc()
        rows.append(item)
        print(
            f"REALINE match={match.get('home')} - {match.get('away')} "
            f"ok={item.get('ok')} error={item.get('error', '-') }"
        )
    return rows


def run_realine(matches_path: Path, source_dir: Path, output: Path, limit: int) -> int:
    rows = asyncio.run(_run_realine_async(_load_matches(matches_path), source_dir, limit))
    _write(output, rows)
    return 0


def run_pinnacle(matches_path: Path, source_dir: Path, output: Path, limit: int) -> int:
    sys.path.insert(0, str(source_dir))
    import pinnacle_scraper as ps

    matches = _load_matches(matches_path)[:limit]
    targets = [(str(m.get("home") or ""), str(m.get("away") or "")) for m in matches]
    rows: list[dict[str, Any]] = []
    try:
        matchups, markets, related = ps.scrape_matches(targets)
    except Exception as exc:
        traceback.print_exc()
        rows = [
            {
                "match": match,
                "source": "ACHBIDHAN/Pinnacle_Football_Odds_Scraper",
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            for match in matches
        ]
        _write(output, rows)
        return 0

    for match in matches:
        item: dict[str, Any] = {"match": match, "source": "ACHBIDHAN/Pinnacle_Football_Odds_Scraper"}
        found = ps.find_matchup(matchups, str(match.get("home") or ""), str(match.get("away") or ""))
        if not found:
            item.update({"ok": False, "error": "match_not_found_on_pinnacle"})
        else:
            try:
                parsed = ps.parse_match(found, markets, related)
                item.update({"ok": bool(parsed.get("markets")), "payload": parsed})
            except Exception as exc:
                item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        rows.append(item)
        print(
            f"PINNACLE match={match.get('home')} - {match.get('away')} "
            f"ok={item.get('ok')} error={item.get('error', '-') }"
        )
    _write(output, rows)
    return 0


def summarize(inputs: list[Path], output: Path) -> int:
    summary: dict[str, Any] = {"sources": {}, "best_sources": []}
    for path in inputs:
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        source = str(rows[0].get("source") if rows else path.stem)
        ok = sum(1 for row in rows if isinstance(row, dict) and row.get("ok"))
        total = len(rows)
        summary["sources"][source] = {"ok": ok, "total": total, "coverage": (ok / total if total else 0.0)}
    summary["best_sources"] = [
        source
        for source, stats in sorted(
            summary["sources"].items(), key=lambda item: (-float(item[1]["coverage"]), -int(item[1]["ok"]), item[0])
        )
        if stats["ok"] > 0
    ]
    _write(output, summary)
    print("FRESH_ODDS_SUMMARY")
    for source, stats in summary["sources"].items():
        print(f"  {source}: {stats['ok']}/{stats['total']} coverage={stats['coverage']:.0%}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test only fresh 2026 odds projects against current Flashscore live matches")
    parser.add_argument("mode", choices=["discover", "m3mons", "realine", "pinnacle", "summary"])
    parser.add_argument("--matches", type=Path, default=Path("fresh_odds_results/online_matches.json"))
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=int(os.getenv("FRESH_ODDS_MAX_MATCHES", "6")))
    parser.add_argument("--inputs", nargs="*", type=Path, default=[])
    args = parser.parse_args()

    if args.mode == "discover":
        return discover_live(args.output, args.limit)
    if args.mode == "m3mons":
        return run_m3mons(args.matches, args.source_dir or Path("fresh_sources/FlashscoreScraper"), args.output, args.limit)
    if args.mode == "realine":
        return run_realine(args.matches, args.source_dir or Path("fresh_sources/realine"), args.output, args.limit)
    if args.mode == "pinnacle":
        return run_pinnacle(args.matches, args.source_dir or Path("fresh_sources/pinnacle"), args.output, min(args.limit, 3))
    return summarize(args.inputs, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
