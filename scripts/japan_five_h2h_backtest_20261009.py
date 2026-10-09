#!/usr/bin/env python3
"""Read-only 2026-10-09 Japan B.League five-game H2H backtest.

No bookmaker bets are placed. All history is strictly prior to the evaluated game.
"""
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_multisport_steam import parse_flashscore_events

MATCHES = [
    ("Ibaraki Robots", "Utsunomiya Brex", ("ibaraki",), ("utsunomiya", "brex")),
    ("Sendai 89ers", "SeaHorses Mikawa", ("sendai",), ("mikawa", "seahorses")),
    ("Altiri Chiba", "Osaka Evessa", ("altiri",), ("osaka",)),
    ("Tokyo Sunrockers", "Toyama Grouses", ("sunrockers", "shibuya"), ("toyama",)),
    ("Kyoto Hannaryz", "Yokohama B-Corsairs", ("kyoto", "hannaryz"), ("yokohama",)),
]
TARGET_DAY = "2026-10-09"
JST = ZoneInfo("Asia/Tokyo")
fs = FlashscoreProvider()

def norm(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())

def matches_name(label, aliases):
    return any(norm(a) in norm(label) for a in aliases)

def today_row(rows, fixture):
    _, _, left, right = fixture
    possible = []
    for row in rows:
        ts = int(row.get("start_ts") or 0)
        if not ts or datetime.fromtimestamp(ts, JST).date().isoformat() != TARGET_DAY:
            continue
        home, away = row.get("home", ""), row.get("away", "")
        if matches_name(home, left) and matches_name(away, right):
            possible.append(row)
    return max(possible, key=lambda x: len(x.get("score_parts") or []), default=None)

def quarter_totals(segments):
    values = []
    for i in range(1, 5):
        pair = segments.get(f"QUARTER_{i}")
        if not pair or len(pair) < 2:
            return None
        values.append(int(pair[0]) + int(pair[1]))
    return values

def process(fixture, row):
    home, away, _, _ = fixture
    report = {"match": home + " — " + away, "target": TARGET_DAY}
    if row is None:
        report["error"] = "today_fixture_not_found_in_flashscore_basketball_master"
        return report
    eid = str(row["flashscore_event_id"])
    now_ts = int(row["start_ts"])
    report["flashscore_event_id"] = eid
    report["today_score"] = row["score"]
    report["today_name"] = [row["home"], row["away"]]
    today_q = quarter_totals(fs.fetch_segment_scores(eid, "basketball"))
    if today_q is None:
        parts = list(row.get("score_parts") or [])
        if len(parts) >= 4:
            today_q = [sum(map(int, pair)) for pair in parts[:4]]
    report["today_quarters"] = today_q
    history = fs.fetch_match_history(eid, row["home"], row["away"], limit=20)
    report["h2h_feed_present"] = bool(history.get("feed_present"))
    report["h2h_candidates"] = len(history.get("h2h") or [])
    def is_pair(m):
        h, a = m.get("home", ""), m.get("away", "")
        return (matches_name(h, fixture[2]) and matches_name(a, fixture[3])) or (matches_name(a, fixture[2]) and matches_name(h, fixture[3]))
    past = [
        x for x in (history.get("h2h") or [])
        if is_pair(x) and int(x.get("timestamp") or 0) < now_ts
        and str(x.get("event_id") or "") != eid
    ]
    past.sort(key=lambda x: int(x.get("timestamp") or 0), reverse=True)
    seen = set()
    five = []
    for m in past:
        mid = m.get("event_id")
        if mid in seen: continue
        seen.add(mid)
        five.append(dict(m))
        if len(five) >= 5: break
    report["h2h_found"] = len(five)
    for match in five:
        q = quarter_totals(fs.fetch_segment_scores(match["event_id"], "basketball"))
        match["quarter_totals"] = q
        match["date"] = datetime.fromtimestamp(int(match["timestamp"]), JST).date().isoformat()
    report["historical_matches"] = five
    eligible = [m["quarter_totals"] for m in five if m.get("quarter_totals") is not None]
    report["h2h_with_four_quarters"] = len(eligible)
    if len(eligible) < 5:
        report["error"] = "fewer_than_five_prior_h2h_with_complete_quarter_data"
    if not eligible:
        return report
    expected_q = [round(statistics.mean(x[q] for x in eligible), 2) for q in range(4)]
    report["predicted_quarters"] = expected_q
    report["predicted_total"] = round(sum(expected_q), 2)
    if today_q:
        report["actual_regulation_total"] = sum(today_q)
        report["quarter_errors"] = [round(actual - predicted, 2) for actual, predicted in zip(today_q, expected_q)]
        report["total_error"] = round(sum(today_q) - sum(expected_q), 2)
        report["quarter_mae"] = round(statistics.mean(abs(x) for x in report["quarter_errors"]), 2)
        # Frozen policy: use four of five past observations to determine candidate thresholds.
        if len(eligible) == 5:
            bets = []
            for q in range(4):
                sample = sorted(x[q] for x in eligible)
                # Lowest OVER line covering 4/5, highest UNDER line covering 4/5.
                over = sample[1] - 0.5
                under = sample[3] + 0.5
                # Pre-defined choice: favor the closer line to historical mean.
                mean = expected_q[q]
                direction, line = ("OVER", over) if mean - over <= under - mean else ("UNDER", under)
                won = today_q[q] > line if direction == "OVER" else today_q[q] < line
                bets.append({"quarter":q+1, "direction":direction, "line":line, "actual":today_q[q], "won":won, "past_hits":4, "note":"hypothetical threshold, odds unknown"})
            report["hypothetical_bets"] = bets
            report["hypothetical_wins"] = sum(b["won"] for b in bets)
    return report

def main():
    rows = {}
    for path in ("f_3_0_3_en_1", "f_3_0_0_en_1", "f_3_-1_3_en_1"):
        body = fs._feed(path, timeout=16)
        parsed = parse_flashscore_events(body)
        print(f"FEED {path}: {len(parsed)} rows, {len(body)} bytes", flush=True)
        for row in parsed: rows[row["flashscore_event_id"]] = row
    output = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        tasks = [executor.submit(process, fixture, today_row(rows.values(), fixture)) for fixture in MATCHES]
        for task in as_completed(tasks):
            try: output.append(task.result())
            except Exception as error: output.append({"error":type(error).__name__ + ": " + str(error)})
    order = {f[0] : i for i,f in enumerate(MATCHES)}
    output.sort(key=lambda x: next((v for k,v in order.items() if x.get("match","").startswith(k)), 999))
    Path("artifacts/h2h_20261009").mkdir(parents=True,exist_ok=True)
    Path("artifacts/h2h_20261009/result.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print("RESULT_JSON_START")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("RESULT_JSON_END")
    valid = [x for x in output if x.get("h2h_with_four_quarters") == 5 and x.get("today_quarters")]
    print(f"SUMMARY valid_strict_five_h2h={len(valid)}/5 quarter_bets={sum(len(x.get('hypothetical_bets', [])) for x in valid)} won={sum(x.get('hypothetical_wins', 0) for x in valid)}")
    for row in output: print(f"SUMMARY_MATCH {row.get('match')} h2h={row.get('h2h_found')} valid={row.get('h2h_with_four_quarters')} forecast={row.get('predicted_quarters')} actual={row.get('today_quarters')} error={row.get('error','none')}")
if __name__ == "__main__":
    main()
