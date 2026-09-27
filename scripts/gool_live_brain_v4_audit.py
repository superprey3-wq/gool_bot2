from __future__ import annotations

import json
from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
from gool_bot2.providers.fusion import FootballDataFusion
from gool_bot2.v4_live_policy import LiveV4Input, decide_live_v4


def main() -> None:
    fusion = FootballDataFusion()
    records = [r for r in fusion.live_records() if 8 <= int((r.get("match") or {}).get("minute") or 0) <= 82]
    print(f"LIVE_BRAIN_V4 matches={len(records)}", flush=True)
    bets = 0
    candidates = []
    brain_only = 0
    brain_veto = 0
    agree = 0
    for record in records[:20]:
        match = record.get("match") or {}
        decisions = evaluate_live_goals(record)
        for d in decisions:
            quality = min(1.0, max(0.0, d.confidence))
            policy = decide_live_v4(LiveV4Input(
                market=d.market.lower(), minute=int(match.get("minute") or 0),
                probability=d.probability, data_quality=quality,
                pressure=min(1.0, d.score), trend=min(1.0, d.confidence),
                expected_remaining=max(0.0, d.probability),
            ))
            if d.decision == "BET":
                bets += 1
            if policy.allowed and d.decision != "BET":
                brain_veto += 1
                reason_map = {}
                for reason in d.reasons:
                    if "=" in reason:
                        key, value = reason.split("=", 1)
                        reason_map[key] = value
                blockers = []
                if reason_map.get("momentum_ready") != "1": blockers.append("momentum_not_ready")
                try:
                    if int(reason_map.get("recent_signals", "0")) < 2: blockers.append("recent_signals_lt_2")
                except ValueError: blockers.append("recent_signals_invalid")
                try:
                    threshold = float(reason_map.get("threshold", "1"))
                    if d.probability < threshold: blockers.append(f"probability_below_threshold({d.probability:.3f}<{threshold:.3f})")
                except ValueError: blockers.append("threshold_invalid")
                if d.confidence < .78: blockers.append(f"confidence_below_0.78({d.confidence:.3f})")
                if not blockers: blockers.append("brain_gate_unclassified")
                print(
                    f"LIVE_CONFLICT {match.get('minute')}' {match.get('home')} - {match.get('away')} "
                    f"{match.get('home_score')}:{match.get('away_score')} | {d.market} "
                    f"POLICY={policy.tier}/{policy.score:.1f} BRAIN=NO_BET "
                    f"BLOCKERS={','.join(blockers)} DETAILS={';'.join(d.reasons)}",
                    flush=True,
                )
            if d.decision == "BET" and not policy.allowed:
                brain_only += 1
                print(f"LIVE_ROLE BRAIN_ONLY {match.get('home')} - {match.get('away')} | {d.market} p={d.probability:.3f}", flush=True)
            if policy.allowed:
                if d.decision == "BET": agree += 1
                candidates.append({
                    "home": match.get("home"), "away": match.get("away"),
                    "minute": int(match.get("minute") or 0), "score": f"{match.get('home_score')}:{match.get('away_score')}",
                    "market": d.market, "p": d.probability, "conf": d.confidence,
                    "brain_score": d.score, "policy_score": policy.score, "tier": policy.tier,
                })
            print(
                f"{match.get('minute')}' {match.get('home')} - {match.get('away')} "
                f"{match.get('home_score')}:{match.get('away_score')} | {d.market} {d.decision} "
                f"p={d.probability:.3f} conf={d.confidence:.3f} score={d.score:.3f} source={d.data_source} policy={policy.allowed}/{policy.tier}/{policy.score:.1f}",
                flush=True,
            )
    print(f"LIVE_BRAIN_V4_BETS {bets}", flush=True)
    print(f"LIVE_ROLE_SUMMARY policy_primary={len(candidates)} agree={agree} brain_veto={brain_veto} brain_only={brain_only}", flush=True)
    candidates.sort(key=lambda x:(x["policy_score"],x["p"],x["conf"]), reverse=True)
    print("=== LIVE POLICY PRIMARY / BRAIN SHADOW ===", flush=True)
    if not candidates:
        print("LIVE_POLICY_PRIMARY NO_BET", flush=True)
    else:
        for i,row in enumerate(candidates[:8],1):
            print(f"L{i:02d}. {row['minute']}' {row['home']} - {row['away']} {row['score']} | {row['market']} | {row['tier']} | p={row['p']:.3f} conf={row['conf']:.3f} policy={row['policy_score']:.1f}", flush=True)
        # LIVE accumulator is shadow-only: no odds fabrication and no Telegram delivery.
        strong=[r for r in candidates if r["tier"]=="STRONG" and r["p"]>=.70 and r["conf"]>=.78]
        if len(strong)>=2:
            legs=strong[:min(3,len(strong))]
            joint=1.0
            for r in legs: joint*=r["p"]
            print(f"LIVE_POLICY_PRIMARY_ACCA legs={len(legs)} joint_model_p={joint:.3f}", flush=True)
            for i,r in enumerate(legs,1):
                print(f"LA{i}. {r['home']} - {r['away']} | {r['market']} | p={r['p']:.3f}", flush=True)
        else:
            top=candidates[0]
            print(f"LIVE_POLICY_PRIMARY_SINGLE {top['home']} - {top['away']} | {top['market']} | p={top['p']:.3f}", flush=True)


if __name__ == "__main__":
    main()
