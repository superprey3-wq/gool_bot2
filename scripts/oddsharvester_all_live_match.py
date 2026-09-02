from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(fc|afc|cf|sc|fk|sv|ac|as|u19|u20|u21|u23)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _sim(a: str, b: str) -> float:
    a = _norm(a)
    b = _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.93
    return SequenceMatcher(None, a, b).ratio()


def _pair_score(home: str, away: str, row: dict[str, Any]) -> float:
    rh = str(row.get("home_team") or "")
    ra = str(row.get("away_team") or "")
    direct = (_sim(home, rh) + _sim(away, ra)) / 2
    reverse = (_sim(home, ra) + _sim(away, rh)) / 2
    return max(direct, reverse)


def _market_keys(row: dict[str, Any]) -> list[str]:
    return [key for key, value in row.items() if key.endswith("_market") and isinstance(value, list) and value]


def _load_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("matches", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Match current OddsHarvester in-play football odds to all current Flashscore matches")
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--odds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.72)
    args = parser.parse_args()

    flash = _load_list(args.matches)
    harvested = _load_list(args.odds)
    rows: list[dict[str, Any]] = []
    matched = 0
    matched_with_odds = 0
    exact_score = 0
    with_live_context = 0

    for match in flash:
        best_score = 0.0
        best: dict[str, Any] | None = None
        for candidate in harvested:
            score = _pair_score(str(match.get("home") or ""), str(match.get("away") or ""), candidate)
            if score > best_score:
                best_score = score
                best = candidate
        item: dict[str, Any] = {"match": match, "similarity": round(best_score, 4)}
        if best is None or best_score < args.threshold:
            item.update({"matched": False, "has_odds": False})
            rows.append(item)
            continue

        matched += 1
        markets = _market_keys(best)
        has_odds = bool(markets)
        if has_odds:
            matched_with_odds += 1
        live_context = best.get("live_period") is not None or best.get("scraped_at_utc") is not None
        if live_context:
            with_live_context += 1
        score_match = None
        if best.get("live_score_home") is not None and best.get("live_score_away") is not None:
            try:
                score_match = (
                    int(best.get("live_score_home")) == int(match.get("home_score") or 0)
                    and int(best.get("live_score_away")) == int(match.get("away_score") or 0)
                )
            except (TypeError, ValueError):
                score_match = False
            if score_match:
                exact_score += 1

        item.update(
            {
                "matched": True,
                "has_odds": has_odds,
                "market_keys": markets,
                "score_match": score_match,
                "oddsharvester": best,
            }
        )
        rows.append(item)

    summary = {
        "flashscore_live_tested": len(flash),
        "oddsharvester_live_rows": len(harvested),
        "matched_matches": matched,
        "matched_with_live_odds": matched_with_odds,
        "matched_with_live_context": with_live_context,
        "exact_live_score_matches": exact_score,
        "coverage": matched_with_odds / len(flash) if flash else 0.0,
    }
    payload = {"summary": summary, "matches": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ODDSHARVESTER_ALL_LIVE_SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
