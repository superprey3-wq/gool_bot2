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
    runtime = Path(os.getenv("AUDIT_RUNTIME_DIR", "artifacts/multisport_real_audit/runtime"))
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ["RUNTIME_DATA_DIR"] = str(runtime)
    os.environ["GOOL_MULTISPORT_MODE"] = "shadow"
    os.environ.setdefault("GOOL_MULTISPORT_EXACT_GAME_TIMEOUT", "3.0")
    os.environ.setdefault("GOOL_MULTISPORT_GAME_HTTP_TIMEOUT", "2.5")
    os.environ.setdefault("GOOL_MULTISPORT_GAME_ROOT_ATTEMPTS", "1")
    os.environ.setdefault("GOOL_MULTISPORT_V3_GAME_FALLBACK", "0")
    os.environ.setdefault("GOOL_MULTISPORT_CURRENT_LINEFEED_FALLBACK", "0")
    os.environ["XBET_MULTISPORT_TELEGRAM_ENABLED"] = "0"
    os.environ["GOOL_MULTISPORT_PREMATCH_ENABLED"] = "1"
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_HORIZON_SECONDS", str(30 * 3600))
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_WINDOW_SECONDS", str(2 * 3600))
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_MIN_AGE_SECONDS", "45")
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_SUBGAME_CACHE_SECONDS", "5")
    os.environ.setdefault("GOOL_MULTISPORT_LIVE_SUBGAME_CACHE_SECONDS", "5")
    os.environ.setdefault("XBET_MULTISPORT_INDEX_COUNT", "300")
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_MAX_MAPPED_PER_SPORT", "20")
    os.environ.setdefault("XBET_MULTISPORT_MAX_MAPPED_PER_SPORT", "20")
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_BATCH_SIZE", "12")
    os.environ.setdefault("GOOL_MULTISPORT_PREMATCH_GAME_WORKERS", "4")
    os.environ.setdefault("XBET_MULTISPORT_GAME_WORKERS", "4")

    snapshots = max(1, int(os.getenv("AUDIT_SNAPSHOTS", "3")))
    sleep_seconds = max(0.0, float(os.getenv("AUDIT_SLEEP_SECONDS", "30")))
    worker = MultiSportSteamWorker(runtime)

    states = []
    for idx in range(snapshots):
        state = worker.collect_once()
        states.append(state)
        print(f"AUDIT_SNAPSHOT {idx + 1}/{snapshots}")
        for sport in ("hockey", "basketball"):
            row = (state.get("sports") or {}).get(sport) or {}
            would_live = _n(row.get("detected"))
            print(
                f"{sport.upper()} "
                f"PRE fs={_n(row.get('flashscore_prematch'))} xb={_n(row.get('xbet_prematch'))} "
                f"mapped={_n(row.get('prematch_mapped'))} scanned={_n(row.get('prematch_scanned'))} decoded={_n(row.get('prematch_decoded'))} "
                f"signals={_n(row.get('prematch_detected'))} decode_fail={_n(row.get('prematch_market_decode_failed'))} | "
                f"LIVE fs={_n(row.get('flashscore_live'))} xb={_n(row.get('xbet_live'))} "
                f"mapped={_n(row.get('mapped'))} decoded={_n(row.get('decoded'))} "
                f"signals={would_live} mismatch={_n(row.get('score_mismatch'))} "
                f"decode_fail={_n(row.get('market_decode_failed'))} policy_skip={_n(row.get('policy_blocked'))}"
            )
            print(f"WOULD_SEND_NOW sport={sport} phase=LIVE bets={would_live}")
            if would_live:
                for match in row.get("matches") or []:
                    for sig in match.get("signals") or []:
                        print(
                            "WOULD_SEND_PICK "
                            f"sport={sport} match={match.get('home')}--{match.get('away')} "
                            f"scope={sig.get('scope')} selection={sig.get('selection')} "
                            f"odd={float(sig.get('odd') or 0):.2f} strength={float(sig.get('strength') or 0):.0f} "
                            f"projection={float(sig.get('projected_total') or 0):.2f} "
                            f"stat_edge={float(sig.get('stat_edge') or 0):.2f}"
                        )
        if idx + 1 < snapshots:
            time.sleep(sleep_seconds)

    out_dir = Path("artifacts/multisport_real_audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "states.json").write_text(json.dumps(states, ensure_ascii=False, indent=2), "utf-8")

    latest = states[-1] if states else {}

    def coverage_summary(matches):
        scopes = {}
        for match in matches:
            for scope, value in (match.get("market_coverage") or {}).items():
                if not isinstance(value, dict):
                    continue
                row = scopes.setdefault(str(scope), {"matches": 0, "total": 0, "it1": 0, "it2": 0, "handicap": 0, "moneyline": 0})
                row["matches"] += 1
                row["total"] += int(int(value.get("match_total_lines") or 0) > 0)
                row["it1"] += int(int(value.get("home_total_lines") or 0) > 0)
                row["it2"] += int(int(value.get("away_total_lines") or 0) > 0)
                row["handicap"] += int(int(value.get("handicap_lines") or 0) > 0)
                row["moneyline"] += int(bool(value.get("moneyline")))
        return scopes

    lines = ["# GOOL multisport real PREMATCH + LIVE audit", ""]
    for sport in ("hockey", "basketball"):
        row = (latest.get("sports") or {}).get(sport) or {}
        lines += [
            f"## {sport.title()}",
            "",
            "### PREMATCH",
            f"- Flashscore upcoming: **{_n(row.get('flashscore_prematch'))}**",
            f"- 1xBet prematch index: **{_n(row.get('xbet_prematch'))}**",
            f"- Mapped total: **{_n(row.get('prematch_mapped'))}**",
            f"- Scanned this cycle: **{_n(row.get('prematch_scanned'))}**",
            f"- Decoded market trees this cycle: **{_n(row.get('prematch_decoded'))}**",
            f"- Policy-skipped lanes: **{_n(row.get('prematch_policy_blocked'))}**",
            f"- Signals this snapshot: **{_n(row.get('prematch_detected'))}**",
            f"- Market decode failures: **{_n(row.get('prematch_market_decode_failed'))}**",
            "",
            "#### PREMATCH scope coverage",
        ]
        for scope, value in sorted(coverage_summary(row.get("prematch_matches") or []).items()):
            lines.append(
                f"- {scope}: matches={value['matches']} total={value['total']} IT1={value['it1']} "
                f"IT2={value['it2']} handicap={value['handicap']} moneyline={value['moneyline']}"
            )
        lines += [
            "",
            "### LIVE",
            f"- Flashscore live: **{_n(row.get('flashscore_live'))}**",
            f"- 1xBet live index: **{_n(row.get('xbet_live'))}**",
            f"- Mapped: **{_n(row.get('mapped'))}**",
            f"- Decoded main totals: **{_n(row.get('decoded'))}**",
            f"- Signals this snapshot: **{_n(row.get('detected'))}**",
            f"- **WOULD SEND NOW (LIVE): {_n(row.get('detected'))} bets**",
            f"- Score mismatches: **{_n(row.get('score_mismatch'))}**",
            f"- Market decode failures: **{_n(row.get('market_decode_failed'))}**",
            f"- Policy-skipped LIVE lanes: **{_n(row.get('policy_blocked'))}**",
            "",
            "#### LIVE scope coverage",
        ]
        for scope, value in sorted(coverage_summary(row.get("matches") or []).items()):
            lines.append(
                f"- {scope}: matches={value['matches']} total={value['total']} IT1={value['it1']} "
                f"IT2={value['it2']} handicap={value['handicap']} moneyline={value['moneyline']}"
            )
        lines += [""] 
        prematches = [x for x in (row.get("prematch_matches") or []) if isinstance(x, dict)]
        lives = [x for x in (row.get("matches") or []) if isinstance(x, dict)]
        if prematches:
            lines += ["#### PREMATCH sample", ""]
            for x in prematches[:10]:
                sig = x.get("signal") or {}
                signal = ""
                if sig:
                    side = "OVER" if str(sig.get("direction") or "over") == "over" else "UNDER"
                    signal = (
                        f" | WOULD SEND {side} {float(sig.get('line') or 0):g} @{float(sig.get('odd') or 0):.2f} "
                        f"R{float(sig.get('strength') or 0):.0f} "
                        f"proj={float(sig.get('projected_total') or 0):.2f} edge={float(sig.get('stat_edge') or 0):.2f}"
                    )
                lines.append(
                    f"- {x.get('home')} — {x.get('away')} | total {float(x.get('line') or 0):g} "
                    f"O {float(x.get('over') or 0):.2f} / U {float(x.get('under') or 0):.2f}{signal}"
                )
            lines.append("")
        if lives:
            lines += ["#### LIVE sample", ""]
            for x in lives[:10]:
                score = x.get("score") or [0, 0]
                sig = x.get("signal") or x.get("steam") or {}
                signal = ""
                if sig:
                    side = "OVER" if str(sig.get("direction") or "over") == "over" else "UNDER"
                    signal = f" | SIGNAL {side} {float(sig.get('line') or 0):g} @{float(sig.get('odd') or 0):.2f} R{float(sig.get('strength') or 0):.0f}"
                lines.append(
                    f"- {x.get('home')} — {x.get('away')} | {score[0]}:{score[1]} {x.get('period')} | "
                    f"total {float(x.get('line') or 0):g} O {float(x.get('over') or 0):.2f} / U {float(x.get('under') or 0):.2f}{signal}"
                )
            lines.append("")

    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, "utf-8")
    print(report)


if __name__ == "__main__":
    main()
