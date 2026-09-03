from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gool_bot2.match_context import provider_count, provider_pair
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.providers.fotmob import FotMobProvider
from gool_bot2.providers.scores365 import Scores365Provider


KEYS = ("xg", "shots", "shots_on_target", "big_chances", "touches_box")


def _compact(stats: dict[str, Any]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for key in KEYS:
        value = stats.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                out[key] = [round(float(value[0]), 3), round(float(value[1]), 3)]
            except (TypeError, ValueError):
                pass
    return out


def _provider_payload(match: Any) -> dict[str, Any]:
    return {"id": match.provider_match_id, "stats": dict(match.stats or {}), "meta": dict(match.meta or {})}


def _consensus(record: dict[str, Any]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for key in KEYS:
        home, away = provider_pair(record, key)
        if home is not None and away is not None:
            out[key] = [round(float(home), 3), round(float(away), 3)]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit GOOL cumulative LIVE stats across three providers")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--out", default="artifacts/gool_live_stats_audit/report.json")
    args = parser.parse_args()

    flashscore = FlashscoreProvider()
    fotmob = FotMobProvider()
    scores365 = Scores365Provider()

    rows: list[dict[str, Any]] = []
    live = [m for m in flashscore.live_matches() if 5 <= int(m.minute or 0) <= 85]
    for match in live[: max(1, int(args.limit))]:
        fs_stats = flashscore.fetch_stats(match.provider_match_id)
        providers: dict[str, dict[str, Any]] = {
            "flashscore": {"id": match.provider_match_id, "stats": fs_stats, "meta": dict(match.meta or {})}
        }
        try:
            fm = fotmob.enrich(match.home, match.away)
        except Exception:
            fm = None
        if fm:
            providers[fm.provider] = _provider_payload(fm)
        try:
            sc = scores365.enrich(match.home, match.away)
        except Exception:
            sc = None
        if sc:
            providers[sc.provider] = _provider_payload(sc)

        record = {"providers": providers}
        rows.append(
            {
                "home": match.home,
                "away": match.away,
                "minute": int(match.minute or 0),
                "score": [int(match.home_score or 0), int(match.away_score or 0)],
                "provider_count": provider_count(record),
                "providers": {name: _compact(payload.get("stats") or {}) for name, payload in providers.items()},
                "consensus": _consensus(record),
            }
        )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "flashscore_live_in_window": len(live),
        "matches_checked": len(rows),
        "three_provider_matches": sum(1 for row in rows if int(row.get("provider_count") or 0) >= 3),
        "two_plus_provider_matches": sum(1 for row in rows if int(row.get("provider_count") or 0) >= 2),
        "rows": rows,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "GOOL_STATS_AUDIT "
        f"live={report['flashscore_live_in_window']} checked={report['matches_checked']} "
        f"two_plus={report['two_plus_provider_matches']} three={report['three_provider_matches']}",
        flush=True,
    )
    for row in rows:
        print(
            f"{row['minute']:>2}' {row['home']} - {row['away']} "
            f"score={row['score'][0]}:{row['score'][1]} sources={row['provider_count']} "
            f"consensus={json.dumps(row['consensus'], ensure_ascii=False)}",
            flush=True,
        )


if __name__ == "__main__":
    main()
