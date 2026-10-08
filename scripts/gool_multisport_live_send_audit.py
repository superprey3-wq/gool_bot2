from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("GOOL_FOOTBALL_AUTOINSTALL", "0")

from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker


def _n(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


def main() -> None:
    runtime = Path(os.getenv("AUDIT_RUNTIME_DIR", "artifacts/multisport_live_send_audit/runtime"))
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ["RUNTIME_DATA_DIR"] = str(runtime)
    os.environ["GOOL_MULTISPORT_MODE"] = "shadow"
    os.environ.setdefault("GOOL_MULTISPORT_EXACT_GAME_TIMEOUT", "3.0")
    os.environ.setdefault("GOOL_MULTISPORT_GAME_HTTP_TIMEOUT", "2.5")
    os.environ.setdefault("GOOL_MULTISPORT_GAME_ROOT_ATTEMPTS", "1")
    os.environ.setdefault("GOOL_MULTISPORT_V3_GAME_FALLBACK", "0")
    os.environ["XBET_MULTISPORT_TELEGRAM_ENABLED"] = "0"
    os.environ["GOOL_MULTISPORT_PREMATCH_ENABLED"] = "0"
    os.environ.setdefault("XBET_MULTISPORT_MAX_MAPPED_PER_SPORT", "6")
    os.environ.setdefault("XBET_MULTISPORT_GAME_WORKERS", "4")
    os.environ.setdefault("GOOL_MULTISPORT_LIVE_SUBGAME_CACHE_SECONDS", "5")
    os.environ.setdefault("GOOL_MULTISPORT_HTTP_ATTEMPTS", "1")
    os.environ.setdefault("GOOL_FLASHSCORE_STATS_TIMEOUT", "3")
    os.environ.setdefault("GOOL_FLASHSCORE_STATS_MAX_HOSTS", "1")
    os.environ.setdefault("GOOL_HOCKEY_LIVE_STAT_SUBGAMES_MAX", "3")

    snapshots = max(1, int(os.getenv("AUDIT_SNAPSHOTS", "2")))
    sleep_seconds = max(3.0, float(os.getenv("AUDIT_SLEEP_SECONDS", "10")))
    worker = MultiSportSteamWorker(runtime)
    states = []
    final_picks = []

    for idx in range(snapshots):
        state = worker.collect_once()
        states.append(state)
        print(f"LIVE_SEND_AUDIT_SNAPSHOT {idx + 1}/{snapshots}")
        total = 0
        for sport in ("hockey", "basketball"):
            row = (state.get("sports") or {}).get(sport) or {}
            would_send = _n(row.get("detected"))
            total += would_send
            analyses = [x for x in (row.get("flashscore_analysis_matches") or []) if isinstance(x, dict)]
            states_count = {}
            reasons_count = {}
            for item in analyses:
                state_key = str(item.get("brain_state") or "UNKNOWN")
                states_count[state_key] = states_count.get(state_key, 0) + 1
                reason_key = str(item.get("brain_reason") or "").strip() or "no_reason"
                reasons_count[reason_key] = reasons_count.get(reason_key, 0) + 1
            print(
                f"WOULD_SEND_NOW sport={sport} bets={would_send} "
                f"fs={_n(row.get('flashscore_live'))} brain={_n(row.get('live_brain_candidates'))} "
                f"xb={_n(row.get('xbet_live'))} mapped={_n(row.get('mapped'))} "
                f"decoded={_n(row.get('decoded'))} policy_skip={_n(row.get('policy_blocked'))} "
                f"price_rej={_n(row.get('pricing_rejected'))} steam_block={_n(row.get('steam_blocked'))} "
                f"matchbook_block={_n(row.get('matchbook_blocked'))} dup={_n(row.get('duplicate_filtered'))}"
            )
            print(
                "WOULD_SEND_BRAIN "
                + json.dumps(
                    {
                        "sport": sport,
                        "states": states_count,
                        "top_reasons": sorted(reasons_count.items(), key=lambda x: (-x[1], x[0]))[:8],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            for match in row.get("matches") or []:
                for signal in match.get("signals") or []:
                    pick = {
                        "snapshot": idx + 1,
                        "sport": sport,
                        "home": match.get("home"),
                        "away": match.get("away"),
                        "league": match.get("league"),
                        "period": match.get("period"),
                        "score": match.get("score"),
                        "scope": signal.get("scope"),
                        "selection": signal.get("selection"),
                        "odd": signal.get("odd"),
                        "strength": signal.get("strength"),
                        "projected_total": signal.get("projected_total"),
                        "stat_edge": signal.get("stat_edge"),
                        "market_confirmed": signal.get("market_confirmed"),
                        "hockey_pressure": signal.get("hockey_pressure") or {},
                    }
                    final_picks.append(pick)
                    print(
                        "WOULD_SEND_PICK "
                        + json.dumps(pick, ensure_ascii=False, separators=(",", ":"))
                    )
        print(f"WOULD_SEND_NOW_TOTAL snapshot={idx + 1} live_bets={total}")
        if idx + 1 < snapshots:
            time.sleep(sleep_seconds)

    latest = states[-1] if states else {}
    latest_total = sum(
        _n(((latest.get("sports") or {}).get(sport) or {}).get("detected"))
        for sport in ("hockey", "basketball")
    )
    out_dir = Path("artifacts/multisport_live_send_audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "states.json").write_text(json.dumps(states, ensure_ascii=False, indent=2), "utf-8")
    (out_dir / "would_send_picks.json").write_text(json.dumps(final_picks, ensure_ascii=False, indent=2), "utf-8")

    lines = [
        "# GOOL multisport LIVE — would send now",
        "",
        f"## WOULD SEND NOW: **{latest_total} LIVE bets**",
        "",
        "Telegram delivery was disabled. The same LIVE policy, history, score-sync and Brain filters were used.",
        "",
    ]
    for sport in ("hockey", "basketball"):
        row = ((latest.get("sports") or {}).get(sport) or {})
        analyses = [x for x in (row.get("flashscore_analysis_matches") or []) if isinstance(x, dict)]
        states_count = {}
        for item in analyses:
            key = str(item.get("brain_state") or "UNKNOWN")
            states_count[key] = states_count.get(key, 0) + 1
        lines.append(
            f"- {sport}: would_send={_n(row.get('detected'))} "
            f"FS={_n(row.get('flashscore_live'))} Brain={_n(row.get('live_brain_candidates'))} "
            f"1xBet={_n(row.get('xbet_live'))} mapped={_n(row.get('mapped'))} "
            f"decoded={_n(row.get('decoded'))} price_rej={_n(row.get('pricing_rejected'))} "
            f"policy_skip={_n(row.get('policy_blocked'))} brain_states={states_count}"
        )
    if final_picks:
        lines += ["", "### Picks that would have been sent", ""]
        for pick in final_picks:
            lines.append(
                f"- [{pick['sport']}] {pick['home']} — {pick['away']} | {pick['period']} | "
                f"{pick['selection']} @{float(pick['odd'] or 0):.2f} | "
                f"R{float(pick['strength'] or 0):.0f} | proj={float(pick['projected_total'] or 0):.2f} "
                f"edge={float(pick['stat_edge'] or 0):.2f}"
            )
    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, "utf-8")
    print(report)


if __name__ == "__main__":
    main()
