from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gool_bot2.providers.flashscore import FlashscoreProvider, _as_int, _fields


EXCLUDED = ("esports", "e-sports", "cyber", "virtual", "ebasketball", "2x2", "3x3")


def parse_feed(body: str, feed_offset: int) -> list[dict]:
    league = ""
    out = []
    for chunk in (body or "").split("~"):
        if not chunk:
            continue
        if chunk.startswith("ZA÷"):
            league = str(_fields(chunk).get("ZA") or "").strip()
            continue
        if not chunk.startswith("AA÷"):
            continue
        event_id, sep, rest = chunk[3:].partition("¬")
        if not sep or len(event_id) != 8 or not event_id.isalnum():
            continue
        f = _fields(rest)
        home = str(f.get("AE") or f.get("CX") or "").strip()
        away = str(f.get("AF") or "").strip()
        if not home or not away:
            continue
        parts = []
        for hk, ak in (("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ")):
            hv, av = f.get(hk), f.get(ak)
            if hv is None and av is None:
                continue
            parts.append([_as_int(hv), _as_int(av)])
        out.append({
            "event_id": event_id,
            "home": home,
            "away": away,
            "league": league,
            "coarse_status": str(f.get("AB") or ""),
            "status_code": str(f.get("AC") or ""),
            "start_ts": _as_int(f.get("AD") or f.get("AO"), 0),
            "score": [_as_int(f.get("AG"), _as_int(f.get("AT"))), _as_int(f.get("AH"), _as_int(f.get("AU")))],
            "score_parts": parts,
            "feed_offset": feed_offset,
        })
    return out


def status_label(code: str) -> str:
    return {"1": "scheduled", "2": "live", "3": "finished"}.get(str(code), f"status_{code or '?'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--tz", default="Europe/Moscow")
    parser.add_argument("--out-dir", default="artifacts/basketball_daily_quarters")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(tz).date()
    )

    provider = FlashscoreProvider()
    merged: dict[str, dict] = {}
    for offset in (-1, 0, 1):
        body = provider._feed(f"f_3_{offset}_3_en_1", timeout=12)
        for row in parse_feed(body, offset):
            text = " ".join((row["league"], row["home"], row["away"])).casefold()
            if any(marker in text for marker in EXCLUDED):
                continue
            ts = int(row.get("start_ts") or 0)
            if ts:
                local_date = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).date()
                if local_date != target_date:
                    continue
            elif offset != 0:
                continue
            current = merged.get(row["event_id"])
            if current is None or len(row["score_parts"]) >= len(current["score_parts"]):
                merged[row["event_id"]] = row

    rows = sorted(
        merged.values(),
        key=lambda x: (int(x.get("start_ts") or 0), x.get("league") or "", x.get("home") or ""),
    )

    for row in rows:
        parts = list(row.get("score_parts") or [])
        row["status"] = status_label(row.get("coarse_status"))
        row["Q1"] = parts[0] if len(parts) > 0 else None
        row["Q2"] = parts[1] if len(parts) > 1 else None
        row["Q3"] = parts[2] if len(parts) > 2 else None
        row["Q4"] = parts[3] if len(parts) > 3 else None
        row["OT"] = parts[4] if len(parts) > 4 else None
        row["local_start"] = (
            datetime.fromtimestamp(row["start_ts"], timezone.utc).astimezone(tz).isoformat()
            if row.get("start_ts")
            else None
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": target_date.isoformat(),
        "timezone": args.tz,
        "count": len(rows),
        "finished": sum(1 for x in rows if x["status"] == "finished"),
        "live": sum(1 for x in rows if x["status"] == "live"),
        "scheduled": sum(1 for x in rows if x["status"] == "scheduled"),
        "matches": rows,
    }
    (out_dir / "basketball_quarters.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# Basketball quarter scores — {target_date.isoformat()}",
        "",
        f"Timezone: **{args.tz}**",
        f"Matches: **{payload['count']}** · finished: **{payload['finished']}** · live: **{payload['live']}** · scheduled: **{payload['scheduled']}**",
        "",
        "| Time | League | Match | Q1 | Q2 | Q3 | Q4 | OT | Final | Status |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    def p(value):
        return "—" if not value else f"{value[0]}:{value[1]}"
    for row in rows:
        local_time = (row.get("local_start") or "")[11:16] or "—"
        lines.append(
            f"| {local_time} | {row['league']} | {row['home']} — {row['away']} | "
            f"{p(row['Q1'])} | {p(row['Q2'])} | {p(row['Q3'])} | {p(row['Q4'])} | "
            f"{p(row['OT'])} | {p(row['score'])} | {row['status']} |"
        )
    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
