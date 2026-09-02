from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import final_kept_sources_live_audit as kept  # noqa: E402
import live_bookmaker_variants_audit as legacy  # noqa: E402
import russian_books_live_audit as ru  # noqa: E402
import targeted_new_sources_live_audit as base  # noqa: E402

SOURCES = [
    "kambi_unibet",
    "entain_ladbrokes",
    "pinnacle_arcadia",
    "pointsbet",
    "xbet",
    "melbet",
    "fonbet",
    "bovada",
]
GROUPS = kept.KEPT_GROUPS
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137 Safari/537.36"


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> dict[str, Any]:
    h = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept-Encoding": "gzip, deflate",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    status = 0
    ct = ""
    raw = b""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = int(getattr(r, "status", 200) or 200)
            ct = str(r.headers.get("content-type") or "")
            enc = str(r.headers.get("content-encoding") or "").casefold()
            raw = r.read(12_000_000)
            if "gzip" in enc or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        try:
            raw = exc.read(500_000)
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
        except Exception:
            raw = str(exc).encode()
    except Exception as exc:
        return {"url": url, "status": 0, "state": "error", "error": f"{type(exc).__name__}: {exc}", "payload": None}
    body = raw.decode("utf-8", errors="replace")
    low = body[:100_000].casefold()
    blocked = status in {401, 403, 406, 451} or any(x in low for x in ("cloudflare", "captcha", "access denied", "forbidden", "qrator"))
    try:
        payload = json.loads(body)
    except Exception as exc:
        return {
            "url": url,
            "status": status,
            "state": "blocked" if blocked else "error",
            "error": f"non_json:{type(exc).__name__}; ct={ct}; body={body[:300]}",
            "payload": None,
        }
    return {
        "url": url,
        "status": status,
        "state": "ok" if status == 200 else ("blocked" if blocked else "error"),
        "error": None,
        "payload": payload,
    }


def source_from_candidates(raw: dict[str, Any], selected: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    matches: dict[str, Any] = {}
    candidates = list(raw.get("candidates") or [])
    state = str(raw.get("state") or "error")
    for target in base.TARGETS:
        key = target["key"]
        fs = selected.get(key)
        if fs is None:
            matches[key] = {"status": "flashscore_not_live"}
            continue
        cand, score, rev = legacy.best_candidate(fs, candidates)
        if cand is None:
            if state == "blocked":
                status = "source_blocked"
            elif state == "error":
                status = "source_error"
            else:
                status = "not_found"
            matches[key] = {"status": status, "mapping_score": round(float(score or 0.0), 4)}
            continue
        q = sorted(set(cand.get("quote_tokens") or []))
        matches[key] = {
            "status": "matched" if q else "matched_no_quotes",
            "book_event_id": cand.get("event_id"),
            "book_home": cand.get("home"),
            "book_away": cand.get("away"),
            "mapping_score": round(float(score or 0.0), 4),
            "reversed": rev,
            "quote_count": len(q),
            "quote_tokens": q,
            "fingerprint": "|".join(q),
        }
    return {
        "state": state,
        "matches": matches,
        "attempts": raw.get("attempts") or [],
        "candidate_count": len(candidates),
    }


def fetch_xbet_brand(brand: str) -> dict[str, Any]:
    if brand == "xbet":
        roots = [
            "https://1xbet.com/service-api/LiveFeed",
            "https://1xbet.com/LiveFeed",
            "https://1xbet.fi/service-api/LiveFeed",
            "https://1xbet.fi/LiveFeed",
        ]
        origin = "https://1xbet.com"
    else:
        roots = [
            "https://melbet.com/service-api/LiveFeed",
            "https://melbet.com/LiveFeed",
        ]
        origin = "https://melbet.com"
    queries = [
        "sports=1&count=1000&lng=en&mode=4&country=1&getEmpty=true",
        "sports=1&count=1000&lng=en&mode=4&country=137&gr=285&virtualSports=true&noFilterBlockEvent=true&getEmpty=true",
    ]
    attempts = []
    any_ok = False
    for root in roots:
        for qs in queries:
            url = f"{root}/Get1x2_VZip?{qs}"
            r = http_json(url, {"Origin": origin, "Referer": origin + "/live/football/", "X-Requested-With": "XMLHttpRequest"})
            attempts.append({k: v for k, v in r.items() if k != "payload"})
            if r["state"] == "ok":
                any_ok = True
                cand = ru.parse_xbet(r["payload"])
                if cand:
                    return {"state": "ok", "candidates": cand, "attempts": attempts}
    if attempts and all(a.get("state") == "blocked" for a in attempts):
        state = "blocked"
    elif any_ok:
        state = "ok"
    else:
        state = "error"
    return {"state": state, "candidates": [], "attempts": attempts}


def fetch_fonbet() -> dict[str, Any]:
    attempts = []
    urls = []
    for host in (
        "line02w.bk6bba-resources.com",
        "line03w.bk6bba-resources.com",
        "line04w.bk6bba-resources.com",
        "line05w.bk6bba-resources.com",
        "line31w.bk6bba-resources.com",
    ):
        urls.append(f"https://{host}/events/list?lang=ru&version=0&scopeMarket=1600")
    urls.append("https://line-lb51.bk6bba-resources.com/events/listBase?scopeMarket=1600&lang=ru")
    any_ok = False
    for url in urls:
        r = http_json(url, {"Origin": "https://www.fon.bet", "Referer": "https://www.fon.bet/"})
        attempts.append({k: v for k, v in r.items() if k != "payload"})
        if r["state"] == "ok":
            any_ok = True
            cand = ru.parse_fonbet_like(r["payload"])
            if cand:
                return {"state": "ok", "candidates": cand, "attempts": attempts}
    if attempts and all(a.get("state") == "blocked" for a in attempts):
        state = "blocked"
    elif any_ok:
        state = "ok"
    else:
        state = "error"
    return {"state": state, "candidates": [], "attempts": attempts}


def bovada_quote_tokens(event: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for gi, group in enumerate(event.get("displayGroups") or []):
        if not isinstance(group, dict):
            continue
        for mi, market in enumerate(group.get("markets") or []):
            if not isinstance(market, dict):
                continue
            for oi, outcome in enumerate(market.get("outcomes") or []):
                if not isinstance(outcome, dict):
                    continue
                price = outcome.get("price") or {}
                dec = price.get("decimal")
                try:
                    fdec = float(dec)
                except (TypeError, ValueError):
                    fdec = 0.0
                if fdec > 1.001:
                    out.append(f"g{gi}.m{mi}.o{oi}.decimal={fdec:g}")
                    handicap = price.get("handicap")
                    try:
                        fh = float(handicap)
                    except (TypeError, ValueError):
                        fh = None
                    if fh is not None:
                        out.append(f"g{gi}.m{mi}.o{oi}.handicap={fh:g}")
    return sorted(set(out))[:240]


def parse_bovada(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def teams(desc: str) -> tuple[str, str]:
        for sep in (" vs ", " v ", " - ", " @ "):
            if sep in desc:
                h, a = desc.split(sep, 1)
                return h.strip(), a.strip()
        return "", ""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("displayGroups"), list) and node.get("description"):
                eid = str(node.get("id") or "")
                desc = str(node.get("description") or "")
                h, a = teams(desc)
                if h and a and (eid or desc) not in seen:
                    q = bovada_quote_tokens(node)
                    rows.append({"home": h, "away": a, "event_id": eid, "quote_tokens": q, "quote_count": len(q)})
                    seen.add(eid or desc)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(payload)
    return rows


def fetch_bovada() -> dict[str, Any]:
    url = "https://www.bovada.lv/services/sports/event/v2/events/A/description/soccer?lang=en&liveOnly=true"
    r = http_json(url, {"Origin": "https://www.bovada.lv", "Referer": "https://www.bovada.lv/sports/soccer"})
    attempt = {k: v for k, v in r.items() if k != "payload"}
    if r["state"] != "ok":
        return {"state": r["state"], "candidates": [], "attempts": [attempt]}
    return {"state": "ok", "candidates": parse_bovada(r["payload"]), "attempts": [attempt]}


async def capture_sources(mcp: Any, selected: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["kambi_unibet"] = await base.source_kambi(selected)
    result["entain_ladbrokes"] = await base.source_entain(mcp, selected)
    result["pinnacle_arcadia"] = await base.source_pinnacle(mcp, selected)
    result["pointsbet"] = await base.source_pointsbet(mcp, selected)

    # Keep the direct bookmaker calls sequential to reduce anti-bot/rate-limit noise.
    result["xbet"] = source_from_candidates(await asyncio.to_thread(fetch_xbet_brand, "xbet"), selected)
    result["melbet"] = source_from_candidates(await asyncio.to_thread(fetch_xbet_brand, "melbet"), selected)
    result["fonbet"] = source_from_candidates(await asyncio.to_thread(fetch_fonbet), selected)
    result["bovada"] = source_from_candidates(await asyncio.to_thread(fetch_bovada), selected)
    return result


async def run(args: argparse.Namespace) -> int:
    from sportsdata_mcp.config import Config
    from sportsdata_mcp.server import build_server

    args.output_dir.mkdir(parents=True, exist_ok=True)
    initial_live = await asyncio.to_thread(base.fs_rows)
    targets = [kept.target_from_fs(row) for row in initial_live]
    base.TARGETS = targets
    base.SOURCES = SOURCES
    base.GROUPS = GROUPS

    base.write_json(args.output_dir / "initial_flashscore_live.json", initial_live)
    base.write_json(args.output_dir / "targets.json", targets)
    print(f"FINAL_EIGHT_AUDIT initial_flashscore_live={len(initial_live)}", flush=True)
    for row in initial_live:
        print(
            f"  FS {row.get('minute')}m {row.get('home')} - {row.get('away')} "
            f"{row.get('home_score')}:{row.get('away_score')} id={row.get('event_id')}",
            flush=True,
        )

    mcp, registry = build_server(Config(enabled_groups=GROUPS))
    snapshots: list[dict[str, Any]] = []
    try:
        tools = sorted(t.name for t in await mcp.list_tools())
        base.write_json(args.output_dir / "registered_tools.json", tools)
        for idx in range(1, max(1, args.snapshots) + 1):
            captured_at = base.utc_now()
            all_live = await asyncio.to_thread(base.fs_rows)
            selected = kept.select_same_events(all_live, targets)
            flashscore = {key: base.fs_state(row) for key, row in selected.items()}
            print(f"SNAPSHOT {idx}/{args.snapshots} at={captured_at} current_flashscore_live={len(all_live)}", flush=True)
            sources = await capture_sources(mcp, selected)
            for source in SOURCES:
                sp = sources[source]
                matched = sum(1 for row in (sp.get("matches") or {}).values() if row.get("status") == "matched")
                print(f"  {source}: state={sp.get('state')} matched_with_quotes={matched}/{len(targets)}", flush=True)
            snap = {"snapshot": idx, "captured_at": captured_at, "flashscore": flashscore, "sources": sources}
            snapshots.append(snap)
            base.write_json(args.output_dir / "snapshots" / f"snapshot_{idx}.json", snap)
            if idx < args.snapshots:
                await asyncio.sleep(max(1, args.interval))
    finally:
        await registry.aclose()

    summary = base.summarize(snapshots)
    summary["initial_flashscore_live_matches"] = len(initial_live)
    summary["tested_sources"] = SOURCES
    summary["source_coverage"] = {}
    for source in SOURCES:
        counts = summary.get("source_summary", {}).get(source, {})
        quote_events = sum(counts.get(v, 0) for v in ("PROVEN", "FOUND_NO_CHANGE", "ODDS_CHANGED_NO_FS_PROGRESS"))
        summary["source_coverage"][source] = {
            "quote_events": quote_events,
            "total_events": len(targets),
            "coverage_pct": round(100.0 * quote_events / len(targets), 1) if targets else 0.0,
            "proven": counts.get("PROVEN", 0),
            "verdicts": counts,
        }

    base.write_json(args.output_dir / "audit.json", snapshots)
    base.write_json(args.output_dir / "summary.json", summary)
    print(
        "FINAL_EIGHT_AUDIT_RESULT "
        + json.dumps(
            {
                "initial_flashscore_live_matches": summary["initial_flashscore_live_matches"],
                "source_coverage": summary["source_coverage"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("final_eight_sources_audit"))
    parser.add_argument("--snapshots", type=int, default=3)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
