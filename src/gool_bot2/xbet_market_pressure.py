from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .providers.common import UA, pair_score
from .providers.flashscore import FlashscoreProvider

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
    "User-Agent": UA,
    "Accept": "application/json,*/*",
    "Origin": "https://1xbet.com",
    "Referer": "https://1xbet.com/live/football/",
    "X-Requested-With": "XMLHttpRequest",
}


def _http_json(url: str, timeout: float = 8.0) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _nodes(obj: Any, out: list[dict[str, Any]] | None = None, path: str = "") -> list[dict[str, Any]]:
    out = [] if out is None else out
    if isinstance(obj, dict):
        if "T" in obj and "C" in obj:
            try:
                odd = float(obj.get("C")); t = int(obj.get("T"))
                line = None if obj.get("P") is None else float(obj.get("P"))
            except (TypeError, ValueError):
                odd = 0.0; t = -1; line = None
            if odd > 1.001 and t > 0:
                out.append({"T": t, "C": odd, "P": line, "G": obj.get("G"), "path": path, "sub": bool(re.search(r"(?:^|/)SG\[\d+\]", path))})
        for key, value in obj.items():
            _nodes(value, out, f"{path}/{key}"[-260:])
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            _nodes(value, out, f"{path}[{idx}]"[-260:])
    return out


def _pairs(nodes: list[dict[str, Any]], over_t: int, under_t: int, preferred_group: int) -> list[dict[str, Any]]:
    grouped: dict[float, dict[str, Any]] = {}
    for node in nodes:
        if node.get("sub") or node.get("P") is None:
            continue
        if int(node.get("G") or -1) != preferred_group:
            continue
        t = int(node.get("T") or -1)
        if t not in {over_t, under_t}:
            continue
        row = grouped.setdefault(float(node["P"]), {"line": float(node["P"])})
        row["over" if t == over_t else "under"] = float(node["C"])
    return [grouped[k] for k in sorted(grouped)]


def _btts(nodes: list[dict[str, Any]]) -> dict[str, float | None]:
    out: dict[str, float | None] = {"yes": None, "no": None}
    for node in nodes:
        if node.get("sub") or int(node.get("G") or -1) != 22:
            continue
        t = int(node.get("T") or -1)
        if t == 182 and out["yes"] is None:
            out["yes"] = float(node["C"])
        elif t == 183 and out["no"] is None:
            out["no"] = float(node["C"])
    return out


def _match_1x2(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Decode the main live P1/X/P2 group verified against real GetGameZip rows.

    Live probe confirmed group G=1 with T=1/2/3 as home/draw/away. The fair
    probabilities are normalized across all three outcomes so bookmaker margin
    is not mistaken for football probability.
    """
    odds: dict[str, float] = {}
    names = {1: "home", 2: "draw", 3: "away"}
    for node in nodes:
        if node.get("sub") or node.get("P") is not None:
            continue
        if int(node.get("G") or -1) != 1:
            continue
        name = names.get(int(node.get("T") or -1))
        if name and name not in odds:
            odds[name] = float(node["C"])
    if not all(name in odds for name in ("home", "draw", "away")):
        return {"home": None, "draw": None, "away": None, "fair": {}, "overround": None}
    raw = {name: 1.0 / odds[name] for name in ("home", "draw", "away")}
    total = sum(raw.values())
    fair = {name: raw[name] / total for name in raw} if total > 0 else {}
    return {
        "home": odds["home"],
        "draw": odds["draw"],
        "away": odds["away"],
        "fair": {name: round(prob, 6) for name, prob in fair.items()},
        "overround": round(total - 1.0, 6),
    }


def decode_markets(game: dict[str, Any]) -> dict[str, Any]:
    nodes = _nodes(game)
    return {
        "match_total": _pairs(nodes, 9, 10, 4),
        "home_total": _pairs(nodes, 11, 12, 5),
        "away_total": _pairs(nodes, 13, 14, 6),
        "btts": _btts(nodes),
        "match_1x2": _match_1x2(nodes),
    }


def _norm_probability(primary: float | None, opposite: float | None) -> float | None:
    if not primary or primary <= 1.0:
        return None
    a = 1.0 / float(primary)
    if not opposite or opposite <= 1.0:
        return a
    b = 1.0 / float(opposite)
    return a / (a + b) if a + b > 0 else None


def _line_row(rows: list[dict[str, Any]], line: float) -> dict[str, Any] | None:
    return next((dict(row) for row in rows if abs(float(row.get("line") or -999) - float(line)) < 1e-9), None)


def _selection(markets: dict[str, Any], market: str, line: float | None = None) -> dict[str, Any] | None:
    if market == "btts_yes":
        b = markets.get("btts") or {}; odd = b.get("yes"); opp = b.get("no")
        if not odd:
            return None
        return {"market": market, "line": None, "odd": float(odd), "opposite": opp, "prob": _norm_probability(float(odd), None if opp is None else float(opp))}
    rows = markets.get(market) or []
    if line is None:
        return None
    row = _line_row(rows, line)
    if not row or not row.get("over"):
        return None
    over = float(row["over"]); under = row.get("under")
    return {"market": market, "line": float(line), "odd": over, "opposite": under, "prob": _norm_probability(over, None if under is None else float(under))}


def targets_for_system(score_home: int, score_away: int, head: str, selected_side: str | None = None) -> list[dict[str, Any]]:
    total = int(score_home) + int(score_away)
    if head == "another_goal":
        return [{"market": "match_total", "line": total + 0.5, "weight": 1.0, "label": f"ТБ {total + 0.5:g}"}]
    if head == "two_more_goals":
        return [{"market": "match_total", "line": total + 1.5, "weight": 1.0, "label": f"ТБ {total + 1.5:g}"}]
    if head == "both_teams_to_score":
        out = [{"market": "btts_yes", "line": None, "weight": 0.58, "label": "ОЗ — Да"}]
        if score_home == 0 and score_away > 0:
            out.append({"market": "home_total", "line": 0.5, "weight": 0.42, "label": "ИТБ1 0.5"})
        elif score_away == 0 and score_home > 0:
            out.append({"market": "away_total", "line": 0.5, "weight": 0.42, "label": "ИТБ2 0.5"})
        elif score_home == 0 and score_away == 0:
            out.extend([
                {"market": "home_total", "line": 0.5, "weight": 0.21, "label": "ИТБ1 0.5"},
                {"market": "away_total", "line": 0.5, "weight": 0.21, "label": "ИТБ2 0.5"},
            ])
        return out
    if head == "team_to_score":
        side = "home" if selected_side == "home" else "away"
        goals = score_home if side == "home" else score_away
        return [{"market": f"{side}_total", "line": goals + 0.5, "weight": 1.0, "label": f"ИТБ{'1' if side == 'home' else '2'} {goals + 0.5:g}"}]
    return []


def evaluate_system(market_row: dict[str, Any] | None, head: str, score_home: int, score_away: int, selected_side: str | None = None) -> dict[str, Any]:
    if not market_row:
        return {"available": False, "confirmed": False, "level": "NO_DATA", "reason": "1xBet data unavailable", "head": head}
    if int(market_row.get("score_home") or 0) != int(score_home) or int(market_row.get("score_away") or 0) != int(score_away):
        return {"available": True, "confirmed": False, "level": "SCORE_DESYNC", "reason": "Flashscore/1xBet score epoch mismatch", "head": head}
    current = market_row.get("markets") or {}
    history = market_row.get("pressure") or {}
    targets = targets_for_system(score_home, score_away, head, selected_side)
    parts: list[dict[str, Any]] = []
    weighted = 0.0; weights = 0.0; strong = 0; moved = 0
    for target in targets:
        sel = _selection(current, target["market"], target.get("line"))
        key = f"{target['market']}:{target.get('line')}"
        hist = history.get(key) or {}
        delta = float(hist.get("prob_delta_pp") or 0.0)
        direction = int(hist.get("one_way_moves") or 0)
        item = {**target, "selection": sel, "prob_delta_pp": delta, "one_way_moves": direction, "old_odd": hist.get("old_odd"), "new_odd": None if sel is None else sel.get("odd")}
        parts.append(item)
        if sel is None:
            continue
        w = float(target.get("weight") or 0.0); weights += w; weighted += max(0.0, delta) * w
        if delta >= 3.0: moved += 1
        if delta >= 6.0 and direction >= 2: strong += 1
    score = weighted / weights if weights > 0 else 0.0
    line_move = bool(market_row.get("line_move"))
    if strong >= 1 and (moved >= 2 or line_move):
        level = "MULTI_MARKET_STEAM"; confirmed = True
    elif strong >= 1 or score >= 6.0:
        level = "STRONG_STEAM"; confirmed = True
    elif score >= 3.0 or moved >= 1:
        level = "PRESSURE"; confirmed = True
    else:
        level = "NEUTRAL"; confirmed = False
    return {"available": True, "confirmed": confirmed, "level": level, "score_pp": round(score, 2), "line_move": line_move, "targets": parts, "head": head, "reason": f"1xBet {level} · Δp={score:.1f} п.п."}


def live_1x2_context(market_row: dict[str, Any] | None) -> dict[str, Any]:
    """Return current live 1X2 odds, de-vigged probabilities and score-epoch movement."""
    if not market_row:
        return {"available": False}
    market = ((market_row.get("markets") or {}).get("match_1x2") or {})
    fair = dict(market.get("fair") or {})
    if not all(market.get(name) for name in ("home", "draw", "away")) or not fair:
        return {"available": False}
    pressure = market_row.get("pressure") or {}
    deltas = {
        name: float((pressure.get(f"match_1x2:{name}") or {}).get("prob_delta_pp") or 0.0)
        for name in ("home", "draw", "away")
    }
    return {
        "available": True,
        "odds": {name: float(market[name]) for name in ("home", "draw", "away")},
        "fair": {name: float(fair.get(name) or 0.0) for name in ("home", "draw", "away")},
        "overround": market.get("overround"),
        "delta_pp": deltas,
        "captured_at": market_row.get("captured_at"),
    }


class XBetMarketCollector:
    def __init__(self, state_path: Path, history_path: Path) -> None:
        self.state_path = state_path; self.history_path = history_path
        self.flashscore = FlashscoreProvider(); self.active_root = ROOTS[0]
        self.mapping: dict[str, str] = {}; self.snapshots: dict[str, list[dict[str, Any]]] = {}
        self._stop = threading.Event()

    def stop(self, *_: object) -> None:
        self._stop.set()

    def _fetch_index(self) -> tuple[str | None, list[dict[str, Any]]]:
        roots = [self.active_root] + [r for r in ROOTS if r != self.active_root]
        for root in roots:
            for qs in INDEX_QUERIES:
                payload = _http_json(f"{root}/Get1x2_VZip?{qs}")
                value = payload.get("Value") if isinstance(payload, dict) else None
                if not isinstance(value, list) or not value:
                    continue
                rows = [{"event_id": str(ev.get("I") or ""), "home": str(ev.get("O1") or ""), "away": str(ev.get("O2") or "")} for ev in value if isinstance(ev, dict) and ev.get("I") and ev.get("O1") and ev.get("O2")]
                if rows:
                    self.active_root = root
                    return root, rows
        return None, []

    def _game(self, event_id: str) -> dict[str, Any] | None:
        params = {"id": event_id, "lng": "en", "cfview": 0, "isSubGames": "true", "GroupEvents": "true", "allEventsGroupSubGames": "true", "countevents": 250, "grMode": 2}
        roots = [self.active_root] + [r for r in ROOTS if r != self.active_root]
        for root in roots[:2]:
            payload = _http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}")
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self.active_root = root
                return value
        return None

    def _map(self, fs: Any, candidates: list[dict[str, Any]]) -> str | None:
        mid = str(fs.provider_match_id)
        cached = self.mapping.get(mid)
        if cached and any(c["event_id"] == cached for c in candidates):
            return cached
        best = None; score = 0.0
        for cand in candidates:
            s = pair_score(str(fs.home), str(fs.away), cand["home"], cand["away"])
            if s > score:
                best = cand; score = s
        if best and score >= float(os.getenv("XBET_MATCH_MIN_SCORE", "0.68")):
            self.mapping[mid] = best["event_id"]
            return best["event_id"]
        return None

    @staticmethod
    def _flat(markets: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in ("match_total", "home_total", "away_total"):
            for row in markets.get(name) or []:
                if not row.get("over"):
                    continue
                sel = {"odd": float(row["over"]), "prob": _norm_probability(float(row["over"]), None if row.get("under") is None else float(row["under"]))}
                out[f"{name}:{row.get('line')}"] = sel
        b = markets.get("btts") or {}
        if b.get("yes"):
            out["btts_yes:None"] = {"odd": float(b["yes"]), "prob": _norm_probability(float(b["yes"]), None if b.get("no") is None else float(b["no"]))}
        one_x_two = markets.get("match_1x2") or {}
        fair = one_x_two.get("fair") or {}
        for name in ("home", "draw", "away"):
            if one_x_two.get(name) and fair.get(name) is not None:
                out[f"match_1x2:{name}"] = {"odd": float(one_x_two[name]), "prob": float(fair[name])}
        return out

    def _pressure(self, fsid: str, score: tuple[int, int], now: float, markets: dict[str, Any]) -> dict[str, Any]:
        flat = self._flat(markets); hist = self.snapshots.setdefault(fsid, [])
        hist[:] = [h for h in hist if now - float(h["ts"]) <= 120 and tuple(h["score"]) == score]
        old = hist[0] if hist else None
        result: dict[str, Any] = {}
        for key, cur in flat.items():
            prior = None if old is None else (old.get("flat") or {}).get(key)
            delta = 0.0
            if prior and prior.get("prob") is not None and cur.get("prob") is not None:
                delta = (float(cur["prob"]) - float(prior["prob"])) * 100.0
            one_way = 0
            vals = []
            for h in hist:
                x = (h.get("flat") or {}).get(key)
                if x and x.get("prob") is not None:
                    vals.append(float(x["prob"]))
            if cur.get("prob") is not None:
                vals.append(float(cur["prob"]))
            for a, b in zip(vals, vals[1:]):
                if b > a + 0.002:
                    one_way += 1
            result[key] = {"prob_delta_pp": round(delta, 3), "one_way_moves": one_way, "old_odd": None if prior is None else prior.get("odd")}
        hist.append({"ts": now, "score": list(score), "flat": flat})
        return result

    def collect_once(self) -> dict[str, Any]:
        started = time.time(); matches = self.flashscore.live_matches(); root, candidates = self._fetch_index(); output: dict[str, Any] = {}
        jobs = []
        with ThreadPoolExecutor(max_workers=max(2, int(os.getenv("XBET_GAME_WORKERS", "8")))) as pool:
            for fs in matches:
                event_id = self._map(fs, candidates)
                if event_id:
                    jobs.append((fs, event_id, pool.submit(self._game, event_id)))
            for fs, event_id, future in jobs:
                try: game = future.result(timeout=12)
                except Exception: game = None
                if not game:
                    continue
                markets = decode_markets(game); now = time.time(); score = (int(fs.home_score or 0), int(fs.away_score or 0))
                pressure = self._pressure(str(fs.provider_match_id), score, now, markets)
                output[str(fs.provider_match_id)] = {
                    "flashscore_event_id": str(fs.provider_match_id), "xbet_event_id": event_id,
                    "home": fs.home, "away": fs.away, "minute": int(fs.minute or 0),
                    "score_home": score[0], "score_away": score[1], "captured_at": datetime.now(timezone.utc).isoformat(),
                    "markets": markets, "pressure": pressure, "line_move": False,
                }
        state = {"captured_at": datetime.now(timezone.utc).isoformat(), "root": root, "latency_ms": int((time.time()-started)*1000), "matches": output}
        self.state_path.parent.mkdir(parents=True, exist_ok=True); tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"); tmp.replace(self.state_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        return state

    def run(self, interval: float = 12.0) -> None:
        interval = max(8.0, float(interval))
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = self.collect_once(); print(f"XBET_MARKET live={len((state.get('matches') or {}))} latency_ms={state.get('latency_ms')} root={state.get('root')}", flush=True)
            except Exception as exc:
                print(f"XBET_MARKET_ERROR {type(exc).__name__}:{exc}", flush=True)
            self._stop.wait(max(1.0, interval - (time.monotonic() - started)))


def load_market_state(path: Path | None = None) -> dict[str, Any]:
    p = path or Path(os.getenv("XBET_MARKET_STATE", str(Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "xbet_market_state.json")))
    try:
        payload = json.loads(p.read_text(encoding="utf-8")); return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
