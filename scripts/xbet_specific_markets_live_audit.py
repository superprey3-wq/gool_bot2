from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import final_eight_sources_live_audit as eight  # noqa: E402
import final_kept_sources_live_audit as kept  # noqa: E402
import live_bookmaker_variants_audit as legacy  # noqa: E402
import targeted_new_sources_live_audit as base  # noqa: E402

ROOTS = [
    "https://1xbet.com/service-api/LiveFeed",
    "https://1xbet.com/LiveFeed",
    "https://1xbet.fi/service-api/LiveFeed",
    "https://1xbet.fi/LiveFeed",
]
INDEX_QUERIES = [
    "sports=1&count=1000&lng=en&mode=4&country=1&getEmpty=true",
    "sports=1&count=1000&lng=en&mode=4&country=137&gr=285&virtualSports=true&noFilterBlockEvent=true&getEmpty=true",
]
HEADERS = {
    "Origin": "https://1xbet.com",
    "Referer": "https://1xbet.com/live/football/",
    "X-Requested-With": "XMLHttpRequest",
}

# Confirmed from the original GOOL 1xBet decoder and legacy 1xBet parsers:
# T9/T10 = match total Over/Under
# T11/T12 = Team 1 individual total Under/Over
# T13/T14 = Team 2 individual total Under/Over
# T182/T183 = Both Teams To Score Yes/No
MARKET_TYPES = {
    "match_total": {"under": 10, "over": 9},
    "home_team_total": {"under": 11, "over": 12},
    "away_team_total": {"under": 13, "over": 14},
    "btts": {"yes": 182, "no": 183},
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_index() -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for root in ROOTS:
        for qs in INDEX_QUERIES:
            url = f"{root}/Get1x2_VZip?{qs}"
            r = eight.http_json(url, HEADERS, 30)
            attempts.append({k: v for k, v in r.items() if k != "payload"})
            if r.get("state") != "ok":
                continue
            payload = r.get("payload")
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, list) and value:
                candidates: list[dict[str, Any]] = []
                for ev in value:
                    if not isinstance(ev, dict):
                        continue
                    h = ev.get("O1")
                    a = ev.get("O2")
                    if not h or not a:
                        continue
                    candidates.append({
                        "home": str(h),
                        "away": str(a),
                        "event_id": str(ev.get("I") or ""),
                        "quote_tokens": ["index=1"],
                        "raw": ev,
                    })
                if candidates:
                    return {"state": "ok", "root": root, "candidates": candidates, "attempts": attempts}
    if attempts and all(a.get("state") == "blocked" for a in attempts):
        state = "blocked"
    else:
        state = "error"
    return {"state": state, "root": "", "candidates": [], "attempts": attempts}


def fetch_game(root: str, event_id: str) -> dict[str, Any]:
    params = {
        "id": event_id,
        "lng": "en",
        "cfview": 0,
        "isSubGames": "true",
        "GroupEvents": "true",
        "allEventsGroupSubGames": "true",
        "countevents": 250,
        "grMode": 2,
    }
    url = f"{root}/GetGameZip?{urllib.parse.urlencode(params)}"
    r = eight.http_json(url, HEADERS, 30)
    if r.get("state") != "ok":
        return {"state": r.get("state"), "error": r.get("error"), "url": url, "value": None}
    payload = r.get("payload")
    value = payload.get("Value") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {"state": "error", "error": "JSON without Value object", "url": url, "value": None}
    return {"state": "ok", "error": None, "url": url, "value": value}


def collect_nodes(obj: Any, out: list[dict[str, Any]] | None = None, path: str = "") -> list[dict[str, Any]]:
    if out is None:
        out = []
    if isinstance(obj, dict):
        if "C" in obj and "T" in obj:
            try:
                odd = float(obj.get("C"))
                t = int(obj.get("T"))
            except (TypeError, ValueError):
                odd = 0.0
                t = -1
            if odd > 1.001 and t > 0:
                p = obj.get("P")
                try:
                    line = float(p) if p is not None else None
                except (TypeError, ValueError):
                    line = None
                g = obj.get("G")
                out.append({
                    "T": t,
                    "C": odd,
                    "P": line,
                    "G": g,
                    "N": obj.get("N"),
                    "path": path,
                    "in_subgame": bool(re.search(r"(?:^|/)SG\[\d+\]", path)),
                })
        for k, v in obj.items():
            collect_nodes(v, out, f"{path}/{k}"[-300:])
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            collect_nodes(v, out, f"{path}[{i}]"[-300:])
    return out


def pairs(nodes: list[dict[str, Any]], over_t: int, under_t: int) -> list[dict[str, Any]]:
    by_line: dict[float, dict[str, Any]] = defaultdict(dict)
    for n in nodes:
        if n.get("in_subgame"):
            continue
        line = n.get("P")
        if line is None:
            continue
        if int(n.get("T") or -1) == over_t:
            by_line[float(line)]["over"] = n
        elif int(n.get("T") or -1) == under_t:
            by_line[float(line)]["under"] = n
    rows: list[dict[str, Any]] = []
    for line in sorted(by_line):
        p = by_line[line]
        if not p.get("over") and not p.get("under"):
            continue
        rows.append({
            "line": line,
            "over": p.get("over", {}).get("C"),
            "under": p.get("under", {}).get("C"),
            "over_g": p.get("over", {}).get("G"),
            "under_g": p.get("under", {}).get("G"),
        })
    return rows


def btts(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    yes: list[dict[str, Any]] = []
    no: list[dict[str, Any]] = []
    for n in nodes:
        if n.get("in_subgame"):
            continue
        if int(n.get("T") or -1) == 182:
            yes.append(n)
        elif int(n.get("T") or -1) == 183:
            no.append(n)
    def pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        # Prefer the historical confirmed root group G=22 when present.
        rows = sorted(rows, key=lambda x: 0 if str(x.get("G")) == "22" else 1)
        n = rows[0]
        return {"odd": n.get("C"), "G": n.get("G"), "path": n.get("path")}
    return {"yes": pick(yes), "no": pick(no)}


def nearest_actionable(rows: list[dict[str, Any]], current: int) -> dict[str, Any] | None:
    if not rows:
        return None
    target = float(current) + 0.5
    exact = next((r for r in rows if abs(float(r["line"]) - target) < 1e-9 and r.get("over")), None)
    if exact:
        return exact
    above = [r for r in rows if float(r["line"]) > float(current) and r.get("over")]
    if above:
        return min(above, key=lambda r: float(r["line"]))
    with_over = [r for r in rows if r.get("over")]
    return min(with_over, key=lambda r: abs(float(r["line"]) - target)) if with_over else rows[0]


def decode_markets(game: dict[str, Any], fs: dict[str, Any]) -> dict[str, Any]:
    nodes = collect_nodes(game)
    match_rows = pairs(nodes, 9, 10)
    home_rows = pairs(nodes, 12, 11)
    away_rows = pairs(nodes, 14, 13)
    hs = int(fs.get("home_score") or 0)
    aas = int(fs.get("away_score") or 0)
    total_goals = hs + aas
    return {
        "node_count": len(nodes),
        "match_total_over": match_rows,
        "match_total_focus": nearest_actionable(match_rows, total_goals),
        "btts": btts(nodes),
        "home_team_total_over": home_rows,
        "home_team_total_focus": nearest_actionable(home_rows, hs),
        "away_team_total_over": away_rows,
        "away_team_total_focus": nearest_actionable(away_rows, aas),
        "type_counts": {
            str(t): sum(1 for n in nodes if not n.get("in_subgame") and int(n.get("T") or -1) == t)
            for t in (9, 10, 11, 12, 13, 14, 182, 183)
        },
    }


def market_fingerprint(decoded: dict[str, Any]) -> str:
    payload = {
        "match": decoded.get("match_total_over"),
        "btts": decoded.get("btts"),
        "home": decoded.get("home_team_total_over"),
        "away": decoded.get("away_team_total_over"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def market_presence(decoded: dict[str, Any]) -> dict[str, bool]:
    b = decoded.get("btts") or {}
    return {
        "match_total_over": bool(decoded.get("match_total_over")),
        "btts": bool((b.get("yes") or {}).get("odd") or (b.get("no") or {}).get("odd")),
        "home_team_total_over": bool(decoded.get("home_team_total_over")),
        "away_team_total_over": bool(decoded.get("away_team_total_over")),
    }


async def capture(selected: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    index = await asyncio.to_thread(fetch_index)
    rows: dict[str, Any] = {}
    candidates = index.get("candidates") or []
    for target in base.TARGETS:
        key = target["key"]
        fs = selected.get(key)
        if fs is None:
            rows[key] = {"status": "flashscore_not_live"}
            continue
        cand, score, rev = legacy.best_candidate(fs, candidates)
        if cand is None:
            rows[key] = {
                "status": "not_found" if index.get("state") == "ok" else "source_error",
                "mapping_score": round(float(score or 0.0), 4),
            }
            continue
        event_id = str(cand.get("event_id") or "")
        game = await asyncio.to_thread(fetch_game, str(index.get("root") or ROOTS[0]), event_id)
        if game.get("state") != "ok" or not isinstance(game.get("value"), dict):
            rows[key] = {
                "status": "game_error",
                "book_event_id": event_id,
                "book_home": cand.get("home"),
                "book_away": cand.get("away"),
                "mapping_score": round(float(score or 0.0), 4),
                "error": game.get("error"),
            }
            continue
        decoded = decode_markets(game["value"], fs)
        rows[key] = {
            "status": "matched",
            "book_event_id": event_id,
            "book_home": cand.get("home"),
            "book_away": cand.get("away"),
            "mapping_score": round(float(score or 0.0), 4),
            "reversed": rev,
            "markets": decoded,
            "presence": market_presence(decoded),
            "fingerprint": market_fingerprint(decoded),
        }
    return {
        "state": index.get("state"),
        "root": index.get("root"),
        "attempts": index.get("attempts"),
        "candidate_count": len(candidates),
        "matches": rows,
    }


async def run(args: argparse.Namespace) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    initial_live = await asyncio.to_thread(base.fs_rows)
    targets = [kept.target_from_fs(row) for row in initial_live]
    base.TARGETS = targets
    write_json(args.output_dir / "initial_flashscore_live.json", initial_live)
    print(f"XBET_MARKETS initial_flashscore_live={len(initial_live)}", flush=True)

    snapshots: list[dict[str, Any]] = []
    for idx in range(1, max(1, args.snapshots) + 1):
        captured_at = base.utc_now()
        all_live = await asyncio.to_thread(base.fs_rows)
        selected = kept.select_same_events(all_live, targets)
        source = await capture(selected)
        fs_states = {k: base.fs_state(v) for k, v in selected.items()}
        snap = {"snapshot": idx, "captured_at": captured_at, "flashscore": fs_states, "source": source}
        snapshots.append(snap)
        write_json(args.output_dir / "snapshots" / f"snapshot_{idx}.json", snap)
        matched = sum(1 for r in source["matches"].values() if r.get("status") == "matched")
        print(f"SNAPSHOT {idx}/{args.snapshots} xbet_state={source.get('state')} matched={matched}/{len(targets)}", flush=True)
        if idx < args.snapshots:
            await asyncio.sleep(max(1, args.interval))

    events: list[dict[str, Any]] = []
    coverage = {k: 0 for k in ("match_total_over", "btts", "home_team_total_over", "away_team_total_over")}
    matched_events = 0
    for target in targets:
        key = target["key"]
        rows = [(s["source"].get("matches") or {}).get(key, {}) for s in snapshots]
        quoted = [r for r in rows if r.get("status") == "matched"]
        fsstates = [s["flashscore"].get(key) for s in snapshots if s["flashscore"].get(key)]
        if quoted:
            matched_events += 1
        latest = quoted[-1] if quoted else (rows[-1] if rows else {})
        presence_any = {m: any((r.get("presence") or {}).get(m) for r in quoted) for m in coverage}
        for m, present in presence_any.items():
            if present:
                coverage[m] += 1
        fs_progress = len({(x.get("minute"), x.get("home_score"), x.get("away_score")) for x in fsstates}) > 1
        odds_changed = len({r.get("fingerprint") for r in quoted if r.get("fingerprint")}) > 1
        events.append({
            "event_id": target.get("event_id"),
            "home": target.get("home"),
            "away": target.get("away"),
            "flashscore_states": fsstates,
            "matched_snapshots": len(quoted),
            "flashscore_progress": fs_progress,
            "market_prices_changed": odds_changed,
            "latest": latest,
        })

    total = len(targets)
    summary = {
        "generated_at": base.utc_now(),
        "snapshot_count": len(snapshots),
        "initial_flashscore_live_matches": total,
        "xbet_matched_events": matched_events,
        "xbet_coverage_pct": round(100.0 * matched_events / total, 1) if total else 0.0,
        "market_coverage": {
            k: {"events": v, "total": total, "coverage_pct": round(100.0 * v / total, 1) if total else 0.0}
            for k, v in coverage.items()
        },
        "market_type_map": MARKET_TYPES,
        "events": events,
    }
    write_json(args.output_dir / "audit.json", snapshots)
    write_json(args.output_dir / "summary.json", summary)
    print("XBET_MARKETS_RESULT " + json.dumps({
        "live": total,
        "matched": matched_events,
        "market_coverage": summary["market_coverage"],
    }, ensure_ascii=False), flush=True)
    for e in events:
        latest = e.get("latest") or {}
        mk = latest.get("markets") or {}
        print("MARKET_ROW " + json.dumps({
            "match": f"{e['home']} - {e['away']}",
            "fs": e.get("flashscore_states", [])[-1] if e.get("flashscore_states") else None,
            "status": latest.get("status"),
            "xbet_event_id": latest.get("book_event_id"),
            "total": mk.get("match_total_focus"),
            "btts": mk.get("btts"),
            "it1": mk.get("home_team_total_focus"),
            "it2": mk.get("away_team_total_focus"),
            "types": mk.get("type_counts"),
        }, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, default=Path("xbet_specific_markets_audit"))
    p.add_argument("--snapshots", type=int, default=2)
    p.add_argument("--interval", type=int, default=20)
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
