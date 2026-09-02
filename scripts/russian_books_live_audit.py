from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import random
import re
import string
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bookmaker_variants_audit as legacy  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
SOURCES = ["xbet_family", "winline", "fonbet", "pari", "betcity", "leon", "olimpbet"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> dict[str, Any]:
    h = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    status = 0
    body = ""
    ct = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = int(getattr(r, "status", 200) or 200)
            ct = str(r.headers.get("content-type") or "")
            body = r.read(8_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        try:
            body = exc.read(300_000).decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
    except Exception as exc:
        return {"url": url, "status": 0, "state": "error", "error": f"{type(exc).__name__}: {exc}", "payload": None}
    low = body[:100_000].casefold()
    blocked = status in {401, 403, 406, 451} or any(x in low for x in ("cloudflare", "captcha", "access denied", "forbidden", "qrator"))
    try:
        payload = json.loads(body)
    except Exception as exc:
        return {"url": url, "status": status, "state": "blocked" if blocked else "error", "error": f"non_json:{type(exc).__name__}; ct={ct}; body={body[:300]}", "payload": None}
    return {"url": url, "status": status, "state": "ok" if status == 200 else ("blocked" if blocked else "error"), "error": None, "payload": payload}


def numeric_leaf_tokens(node: Any, prefix: str = "", limit: int = 180) -> list[str]:
    out: list[str] = []
    def walk(v: Any, p: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{p}.{k}" if p else str(k))
        elif isinstance(v, list):
            for i, x in enumerate(v[:160]):
                walk(x, f"{p}[{i}]")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            f = float(v)
            if 1.001 < abs(f) < 100000:
                out.append(f"{p}={v}")
    walk(node, prefix)
    return sorted(set(out))


def candidate(home: str, away: str, event_id: Any, quote_tokens: list[str]) -> dict[str, Any]:
    q = sorted(set(quote_tokens))[:240]
    return {"home": str(home or ""), "away": str(away or ""), "event_id": str(event_id or ""), "quote_tokens": q, "quote_count": len(q)}


def parse_fonbet_like(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    factors: dict[str, list[str]] = {}
    for cf in payload.get("customFactors", []) or []:
        if not isinstance(cf, dict):
            continue
        eid = str(cf.get("e") or "")
        if not eid:
            continue
        toks: list[str] = []
        for f in cf.get("factors", []) or []:
            if not isinstance(f, dict):
                continue
            fid = f.get("f")
            val = f.get("v")
            if isinstance(val, (int, float)) and float(val) > 1:
                toks.append(f"factor[{fid}]={val}")
        factors[eid] = sorted(set(toks))
    rows = []
    for ev in payload.get("events", []) or []:
        if not isinstance(ev, dict) or str(ev.get("place") or "").casefold() != "live":
            continue
        h, a = ev.get("team1"), ev.get("team2")
        eid = str(ev.get("id") or "")
        if h and a:
            rows.append(candidate(h, a, eid, factors.get(eid, [])))
    return rows


def fetch_fonbet() -> dict[str, Any]:
    hosts = ["line02w.bk6bba-resources.com", "line03w.bk6bba-resources.com", "line04w.bk6bba-resources.com", "line05w.bk6bba-resources.com", "line31w.bk6bba-resources.com"]
    attempts = []
    for host in hosts:
        r = http_json(f"https://{host}/events/list?lang=ru&version=0&scopeMarket=1600", {"Origin": "https://www.fon.bet", "Referer": "https://www.fon.bet/"})
        attempts.append({k: v for k, v in r.items() if k != "payload"})
        if r["state"] == "ok":
            return {"state": "ok", "candidates": parse_fonbet_like(r["payload"]), "attempts": attempts}
    state = "blocked" if attempts and all(a["state"] == "blocked" for a in attempts) else "error"
    return {"state": state, "candidates": [], "attempts": attempts}


def fetch_pari() -> dict[str, Any]:
    hosts = ["line01.pb06e2-resources.com", "line02.pb06e2-resources.com", "line03.pb06e2-resources.com", "line31.pb06e2-resources.com"]
    attempts = []
    for host in hosts:
        r = http_json(f"https://{host}/events/list?lang=ru&version=0&scopeMarket=2300", {"Origin": "https://www.pari.ru", "Referer": "https://www.pari.ru/"})
        attempts.append({k: v for k, v in r.items() if k != "payload"})
        if r["state"] == "ok":
            return {"state": "ok", "candidates": parse_fonbet_like(r["payload"]), "attempts": attempts}
    state = "blocked" if attempts and all(a["state"] == "blocked" for a in attempts) else "error"
    return {"state": state, "candidates": [], "attempts": attempts}


def parse_xbet(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            h = node.get("O1") or node.get("team1") or node.get("home")
            a = node.get("O2") or node.get("team2") or node.get("away")
            if h and a:
                odds_node = {k: node.get(k) for k in ("E", "AE", "C1E", "C2E", "coef", "odds") if k in node}
                rows.append(candidate(h, a, node.get("I") or node.get("CI") or node.get("id"), numeric_leaf_tokens(odds_node)))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(payload)
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (legacy.norm(r["home"]), legacy.norm(r["away"]), r["event_id"])
        if key not in dedup or r["quote_count"] > dedup[key]["quote_count"]:
            dedup[key] = r
    return list(dedup.values())


def fetch_xbet_family() -> dict[str, Any]:
    urls = [
        "https://1xbet.com/LiveFeed/Get1x2_VZip?sports=1&count=500&lng=en&mode=4&country=1&partner=51&getEmpty=true",
        "https://1xbet.com/service-api/LiveFeed/Get1x2_VZip?sports=1&count=500&lng=en&mode=4&country=1&partner=51&getEmpty=true",
        "https://linebet.com/LiveFeed/Get1x2_VZip?sports=1&count=500&lng=en&mode=4&country=87&partner=189&getEmpty=true",
        "https://linebet.com/service-api/LiveFeed/Get1x2_VZip?sports=1&count=500&lng=en&mode=4&country=87&partner=189&getEmpty=true",
        "https://1xstavka.ru/LiveFeed/Get1x2_VZip?sports=1&count=500&lng=ru&mode=4&country=1&partner=51&getEmpty=true",
    ]
    attempts = []
    for url in urls:
        base = url.split("/LiveFeed", 1)[0].split("/service-api", 1)[0]
        r = http_json(url, {"Origin": base, "Referer": base + "/live/football/", "X-Requested-With": "XMLHttpRequest"})
        attempts.append({k: v for k, v in r.items() if k != "payload"})
        if r["state"] == "ok":
            cand = parse_xbet(r["payload"])
            if cand:
                return {"state": "ok", "candidates": cand, "attempts": attempts}
    state = "blocked" if attempts and all(a["state"] == "blocked" for a in attempts) else "error"
    return {"state": state, "candidates": [], "attempts": attempts}


def fetch_betcity() -> dict[str, Any]:
    csn = "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
    qs = urllib.parse.urlencode({"rev": 8, "add": "dep_event", "template": 1, "ver": 73, "csn": csn})
    r = http_json(f"https://ad.betcity.ru/d/on_air/bets?{qs}", {"Origin": "https://betcity.ru", "Referer": "https://betcity.ru/live"}, 35)
    if r["state"] != "ok":
        return {"state": r["state"], "candidates": [], "attempts": [{k: v for k, v in r.items() if k != "payload"}]}
    rows = []
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            h = node.get("t1") or node.get("team1")
            a = node.get("t2") or node.get("team2")
            if h and a:
                odds = node.get("bets") or node.get("b") or node.get("o") or {}
                rows.append(candidate(h, a, node.get("id_e") or node.get("id") or "", numeric_leaf_tokens(odds)))
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for x in node: walk(x)
    walk(r["payload"].get("reply", {}) if isinstance(r["payload"], dict) else r["payload"])
    return {"state": "ok", "candidates": rows, "attempts": [{k: v for k, v in r.items() if k != "payload"}]}


def fetch_leon() -> dict[str, Any]:
    headers = {"Origin": "https://leonbets.ru", "Referer": "https://leonbets.ru/"}
    s = http_json("https://leonbets.ru/api-2/betline/sports?ctag=ru-RU&flags=urlv2", headers)
    attempts = [{k: v for k, v in s.items() if k != "payload"}]
    if s["state"] != "ok" or not isinstance(s["payload"], list):
        return {"state": s["state"], "candidates": [], "attempts": attempts}
    football = next((x for x in s["payload"] if isinstance(x, dict) and any(k in str(x.get("name") or "").casefold() for k in ("футбол", "football", "soccer"))), None)
    if not football:
        return {"state": "ok", "candidates": [], "attempts": attempts}
    league_ids = []
    for region in football.get("regions", []) or []:
        for league in region.get("leagues", []) or []:
            if league.get("id") is not None:
                league_ids.append(league["id"])
    rows: list[dict[str, Any]] = []
    for lid in league_ids[:120]:
        url = "https://leonbets.ru/api-2/betline/events/all?" + urllib.parse.urlencode({"ctag": "ru-RU", "league_id": lid, "flags": "reg,balltime,urlv2,setNrh"})
        r = http_json(url, headers, 15)
        if r["state"] != "ok" or not isinstance(r["payload"], dict):
            continue
        for ev in r["payload"].get("events", []) or []:
            if not isinstance(ev, dict): continue
            comps = ev.get("competitors", []) or []
            h = next((c.get("name") for c in comps if isinstance(c, dict) and c.get("homeAway") == "HOME"), None)
            a = next((c.get("name") for c in comps if isinstance(c, dict) and c.get("homeAway") == "AWAY"), None)
            if not h or not a: continue
            toks: list[str] = []
            for mi, market in enumerate(ev.get("markets", []) or []):
                if not isinstance(market, dict): continue
                for ri, runner in enumerate(market.get("runners", []) or []):
                    if isinstance(runner, dict) and isinstance(runner.get("price"), (int, float)) and float(runner["price"]) > 1:
                        toks.append(f"m{mi}.r{ri}.price={runner['price']}")
            rows.append(candidate(h, a, ev.get("id"), toks))
    return {"state": "ok", "candidates": rows, "attempts": attempts, "league_count": len(league_ids)}


def fetch_olimpbet() -> dict[str, Any]:
    headers = {"Origin": "https://www.olimp.bet", "Referer": "https://www.olimp.bet/live"}
    urls = [
        "https://www.olimp.bet/api/v4/0/live/sports-with-competitions-with-events",
        "https://www.olimp.bet/api/v4/0/live/sports-with-competitions-with-events?vids%5B%5D=1%3A",
    ]
    attempts = []
    for url in urls:
        r = http_json(url, headers, 30)
        attempts.append({k: v for k, v in r.items() if k != "payload"})
        if r["state"] != "ok": continue
        rows: list[dict[str, Any]] = []
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                h = node.get("team1Name") or node.get("team1")
                a = node.get("team2Name") or node.get("team2")
                if h and a:
                    odds = node.get("outcomes") or node.get("markets") or node.get("odds") or []
                    rows.append(candidate(h, a, node.get("id") or node.get("eventId") or "", numeric_leaf_tokens(odds)))
                for v in node.values(): walk(v)
            elif isinstance(node, list):
                for x in node: walk(x)
        walk(r["payload"])
        if rows:
            return {"state": "ok", "candidates": rows, "attempts": attempts}
    state = "blocked" if attempts and all(a["state"] == "blocked" for a in attempts) else ("ok" if any(a["state"] == "ok" for a in attempts) else "error")
    return {"state": state, "candidates": [], "attempts": attempts}


def parse_winline_full(data: bytes) -> list[dict[str, Any]]:
    strings: list[tuple[int, str, int, bool]] = []
    i = 0
    while i < len(data) - 2:
        ln = struct.unpack_from("<H", data, i)[0]
        if 2 <= ln <= 200 and i + 2 + ln <= len(data):
            chunk = data[i+2:i+2+ln]
            try:
                raw = chunk.decode("utf-8")
                live = any(not (c.isprintable() or c == " ") for c in raw)
                clean = "".join(c for c in raw if c.isprintable() or c == " ").strip()
                if len(clean) >= 2 and any(c.isalpha() for c in clean):
                    strings.append((i, clean, ln, live)); i += 2 + ln; continue
            except Exception:
                pass
        i += 1
    target = b"\x56\x05\x10\x27\x10\x27"
    rows = []
    for j in range(len(strings)-1):
        p1, s1, l1, live1 = strings[j]
        p2, s2, l2, live2 = strings[j+1]
        if p1 + 2 + l1 != p2 or not (live1 or live2):
            continue
        region = data[p2+2+l2:min(len(data), p2+2+l2+450)]
        pos = region.find(target)
        if pos < 5 or region[pos-5] != 0x04 or pos + len(target) + 4 > len(region):
            continue
        a = struct.unpack_from("<H", region, pos + len(target))[0] / 100.0
        b = struct.unpack_from("<H", region, pos + len(target) + 2)[0] / 100.0
        if a > 1.01 and b > 1.01 and 0.85 <= 1/a + 1/b <= 1.25:
            rows.append(candidate(s1, s2, "", [f"winner.home={a:.2f}", f"winner.away={b:.2f}"]))
    return rows


async def fetch_winline_async() -> dict[str, Any]:
    try:
        import websockets
        async with websockets.connect("wss://wss.winline.ru/data_ng?client=newsite&nb=true", additional_headers={"User-Agent": UA, "Origin": "https://winline.ru"}, open_timeout=20, ping_interval=20) as ws:
            for cmd in ["lang", "ru", "data", "WINLINE", "getdate"]:
                await ws.send(cmd); await asyncio.sleep(0.05)
            deadline = asyncio.get_running_loop().time() + 18
            while asyncio.get_running_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    break
                if not isinstance(raw, bytes): continue
                try: data = gzip.decompress(raw)
                except Exception: data = raw
                if data and data[0] == 0x03:
                    return {"state": "ok", "candidates": parse_winline_full(data), "attempts": [{"url": "wss://wss.winline.ru/data_ng", "status": 101, "state": "ok"}]}
        return {"state": "error", "candidates": [], "attempts": [{"url": "wss://wss.winline.ru/data_ng", "state": "error", "error": "no 0x03 full state"}]}
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        state = "blocked" if any(x in text.casefold() for x in ("403", "forbidden", "geo")) else "error"
        return {"state": state, "candidates": [], "attempts": [{"url": "wss://wss.winline.ru/data_ng", "state": state, "error": text[:500]}]}


def fs_rows() -> list[dict[str, Any]]:
    return legacy.flashscore_rows()


def match_rows(matches: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for m in matches:
        cand, score, rev = legacy.best_candidate(m, source.get("candidates", []))
        if cand is None:
            status = "source_blocked" if source.get("state") == "blocked" else ("source_error" if source.get("state") == "error" else "not_found")
            rows.append({"event_id": m["event_id"], "home": m["home"], "away": m["away"], "status": status, "mapping_score": round(score, 4)})
            continue
        q = list(cand.get("quote_tokens") or [])
        rows.append({"event_id": m["event_id"], "home": m["home"], "away": m["away"], "status": "matched" if q else "matched_no_quotes", "mapping_score": round(score, 4), "reversed_order": rev, "book_event_id": cand.get("event_id"), "book_home": cand.get("home"), "book_away": cand.get("away"), "quote_count": len(q), "quote_tokens": q, "fingerprint": "|".join(q)})
    return rows


async def fetch_all_sources() -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["xbet_family"] = await asyncio.to_thread(fetch_xbet_family)
    out["winline"] = await fetch_winline_async()
    out["fonbet"] = await asyncio.to_thread(fetch_fonbet)
    out["pari"] = await asyncio.to_thread(fetch_pari)
    out["betcity"] = await asyncio.to_thread(fetch_betcity)
    out["leon"] = await asyncio.to_thread(fetch_leon)
    out["olimpbet"] = await asyncio.to_thread(fetch_olimpbet)
    return out


def summarize(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    ids: dict[str, dict[str, Any]] = {}
    by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    source_states = {s: [] for s in SOURCES}
    source_diag = {s: [] for s in SOURCES}
    for snap in snaps:
        for m in snap["flashscore"]:
            eid = m["event_id"]
            ids.setdefault(eid, {"home": m["home"], "away": m["away"], "league": m["league"], "states": []})
            ids[eid]["states"].append({"snapshot": snap["snapshot"], "captured_at": snap["captured_at"], "minute": m["minute"], "home_score": m["home_score"], "away_score": m["away_score"]})
        for src, sp in snap["sources"].items():
            source_states[src].append(sp.get("state"))
            source_diag[src].append(sp.get("attempts"))
            for r in sp["matches"]:
                by.setdefault((r["event_id"], src), []).append({"snapshot": snap["snapshot"], "captured_at": snap["captured_at"], **r})
    source_summary = {s: {} for s in SOURCES}
    coverage = {}
    events = []
    for eid, meta in ids.items():
        progress = len({(x["minute"], x["home_score"], x["away_score"]) for x in meta["states"]}) > 1
        srcs = {}
        for src in SOURCES:
            rs = by.get((eid, src), [])
            quoted = [r for r in rs if r.get("status") == "matched" and r.get("fingerprint")]
            fps = [r["fingerprint"] for r in quoted]
            changed = len(set(fps)) > 1
            if len(quoted) >= 2 and changed and progress: v = "PROVEN"
            elif len(quoted) >= 2 and changed: v = "ODDS_CHANGED_NO_FS_PROGRESS"
            elif quoted: v = "FOUND_NO_CHANGE"
            elif any(r.get("status") == "matched_no_quotes" for r in rs): v = "MATCHED_NO_QUOTES"
            elif rs and all(r.get("status") == "source_blocked" for r in rs): v = "BLOCKED_GEO"
            elif rs and all(r.get("status") == "source_error" for r in rs): v = "SOURCE_ERROR"
            else: v = "NOT_FOUND"
            source_summary[src][v] = source_summary[src].get(v, 0) + 1
            srcs[src] = {"verdict": v, "matched_quote_snapshots": len(quoted), "odds_changed": changed, "flashscore_progress": progress, "snapshots": [{**{k:r.get(k) for k in ("snapshot","captured_at","status","book_event_id","book_home","book_away","mapping_score","quote_count")}, "quote_sample": list(r.get("quote_tokens") or [])[:16]} for r in rs]}
        events.append({"event_id": eid, "home": meta["home"], "away": meta["away"], "league": meta["league"], "flashscore_states": meta["states"], "sources": srcs})
    total = len(ids)
    for src in SOURCES:
        quote_events = sum(1 for e in events if e["sources"][src]["matched_quote_snapshots"] > 0)
        proven = sum(1 for e in events if e["sources"][src]["verdict"] == "PROVEN")
        coverage[src] = {"total_events": total, "quote_events": quote_events, "coverage_pct": round(100*quote_events/total,1) if total else 0.0, "proven": proven, "verdicts": source_summary[src]}
    return {"generated_at": utc_now(), "snapshot_count": len(snaps), "initial_flashscore_live_matches": len(snaps[0]["flashscore"]) if snaps else 0, "strict_rule": "PROVEN requires >=2 quote-bearing snapshots, Flashscore minute/score progress, and changed bookmaker quote fingerprint for same matched event.", "source_states": source_states, "source_diagnostics": source_diag, "source_summary": source_summary, "source_coverage": coverage, "events": events}


async def run(args: argparse.Namespace) -> int:
    snaps = []
    for idx in range(1, args.snapshots + 1):
        captured = utc_now()
        matches = await asyncio.to_thread(fs_rows)
        print(f"RUS_AUDIT snapshot={idx}/{args.snapshots} flashscore_live={len(matches)} at={captured}", flush=True)
        raw = await fetch_all_sources()
        sources = {}
        for src in SOURCES:
            mr = match_rows(matches, raw[src])
            sources[src] = {"state": raw[src].get("state"), "attempts": raw[src].get("attempts"), "matches": mr}
            print(f"  {src}: state={raw[src].get('state')} candidates={len(raw[src].get('candidates') or [])} matched_quotes={sum(1 for r in mr if r.get('status')=='matched')}/{len(matches)}", flush=True)
        snap = {"snapshot": idx, "captured_at": captured, "flashscore": matches, "sources": sources}
        snaps.append(snap)
        write_json(args.output_dir / "snapshots" / f"snapshot_{idx}.json", snap)
        if idx < args.snapshots:
            await asyncio.sleep(args.interval)
    summary = summarize(snaps)
    write_json(args.output_dir / "audit.json", snaps)
    write_json(args.output_dir / "summary.json", summary)
    print("RUS_AUDIT_FINAL " + json.dumps(summary["source_coverage"], ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, default=Path("russian_books_audit"))
    p.add_argument("--snapshots", type=int, default=3)
    p.add_argument("--interval", type=int, default=30)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args))

if __name__ == "__main__":
    raise SystemExit(main())
