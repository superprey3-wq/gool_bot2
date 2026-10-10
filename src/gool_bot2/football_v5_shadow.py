"""GOOL FOOTBALL V5: independent, PREMATCH-only opponent-adjusted shadow model.

No Telegram deliveries, no modification to V4 signals. Uses only completed
matches dated STRICTLY before target kickoff. All coefficients are heuristic
until chronological validation; probability outputs are NOT calibrated.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


def _name(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", str(value or "").casefold()).strip()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _observations(context: dict[str, Any], kickoff: float) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for key in ("home_recent", "away_recent", "home_at_home", "away_away", "h2h"):
        for row in context.get(key) or []:
            if not isinstance(row, dict):
                continue
            eid = str(row.get("event_id") or "")
            try:
                ts = float(row.get("timestamp") or 0)
                hg, ag = int(row["home_score"]), int(row["away_score"])
            except (TypeError, ValueError, KeyError):
                continue
            h, a = _name(row.get("home")), _name(row.get("away"))
            if not eid or not h or not a or h == a or hg < 0 or ag < 0:
                continue
            # Never borrow played-in-future results; unknown dates also fail closed.
            if not (0 < ts < kickoff):
                continue
            if eid not in unique or ts > float(unique[eid]["timestamp"]):
                unique[eid] = {
                    "event_id": eid, "timestamp": ts,
                    "home": h, "away": a, "hg": hg, "ag": ag,
                }
    return sorted(unique.values(), key=lambda r: r["timestamp"], reverse=True)


def _team_view(row: dict[str, Any], team: str) -> tuple[float, float, str, bool] | None:
    if row["home"] == team:
        return float(row["hg"]), float(row["ag"]), row["away"], True
    if row["away"] == team:
        return float(row["ag"]), float(row["hg"]), row["home"], False
    return None


def _weighted_mean(values: list[tuple[float, float]]) -> float:
    n = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / max(n, 1e-9)


def _poisson_probs(lam: float, max_goals: int = 15) -> list[float]:
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    probs[-1] += max(0.0, 1.0 - sum(probs))  # preserve tail mass
    return probs


def score_grid(home_lambda: float, away_lambda: float) -> dict[str, float]:
    hp, ap = _poisson_probs(home_lambda), _poisson_probs(away_lambda)
    hw = dr = aw = over15 = over25 = over35 = btts = 0.0
    for i, ph in enumerate(hp):
        for j, pa in enumerate(ap):
            p = ph * pa
            hw += p if i > j else 0.0
            dr += p if i == j else 0.0
            aw += p if i < j else 0.0
            btts += p if i and j else 0.0
            over15 += p if i + j >= 2 else 0.0
            over25 += p if i + j >= 3 else 0.0
            over35 += p if i + j >= 4 else 0.0
    return {
        "home_win": round(hw, 6), "draw": round(dr, 6),
        "away_win": round(aw, 6), "btts_yes": round(btts, 6),
        "over_1.5": round(over15, 6),
        "over_2.5": round(over25, 6),
        "over_3.5": round(over35, 6),
        "at_least_one": round(1.0 - math.exp(-home_lambda - away_lambda), 6),
    }


def forecast_from_history(
    *, home: str, away: str, kickoff: float, context: dict[str, Any],
    min_games: int = 6,
) -> dict[str, Any]:
    """Opponent difficulty estimated only from *other* available historic games.

    The collected 10+10 game network often has no repeated opponents: such
    fixtures receive a neutral difficulty factor, not an invented rating.
    """
    h, a = _name(home), _name(away)
    if not h or not a or kickoff <= 0:
        return {"status": "WAIT_IDENTITY_OR_KICKOFF", "model": "football_v5_shadow"}
    rows = _observations(context, kickoff)
    hg = [row for row in rows if _team_view(row, h) and row["away"] != a and row["home"] != a][:10]
    ag = [row for row in rows if _team_view(row, a) and row["away"] != h and row["home"] != h][:10]
    if len(hg) < min_games or len(ag) < min_games:
        return {
            "status": "WAIT_HISTORY", "model": "football_v5_shadow",
            "home_games": len(hg), "away_games": len(ag),
        }

    # Defaults are explicitly generic regularization priors, not measured
    # league-specific scoring averages. Local samples can update them modestly.
    rate_h = _clamp((14 * 1.42 + sum(r["hg"] for r in rows)) / (14 + len(rows)), 0.8, 2.5)
    rate_a = _clamp((14 * 1.15 + sum(r["ag"] for r in rows)) / (14 + len(rows)), 0.7, 2.2)
    average = (rate_h + rate_a) / 2.0
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_team[row["home"]].append(row)
        by_team[row["away"]].append(row)

    def other_side_strength(opponent: str, excluded: str, stat: str) -> tuple[float, int]:
        # Derive an opposing club's attack/defence only from its other games,
        # using separate-game counts and regularizing strongly to 1.0.
        other = [r for r in by_team[opponent] if r["event_id"] != excluded]
        if len(other) < 2:
            return 1.0, 0
        values = []
        for r in other:
            side = _team_view(r, opponent)
            if side:
                values.append(side[0] if stat == "attack" else side[1])
        n = len(values)
        if n < 2:
            return 1.0, 0
        ratio = (sum(values) + 4 * average) / ((n + 4) * average)
        return _clamp(ratio, 0.75, 1.25), n

    def team_rates(games: list[dict[str, Any]], team: str, at_home: bool) -> dict[str, float | int]:
        scored: list[tuple[float, float]] = []
        allowed: list[tuple[float, float]] = []
        venue_scores: list[tuple[float, float]] = []
        supported = 0
        for idx, game in enumerate(games):
            own = _team_view(game, team)
            if own is None:
                continue
            gf, ga, opponent, was_home = own
            opp_def, nd = other_side_strength(opponent, game["event_id"], "defence")
            opp_att, na = other_side_strength(opponent, game["event_id"], "attack")
            supported += int(nd >= 2 and na >= 2)
            w = 0.86 ** idx
            scored.append((gf / opp_def, w))
            allowed.append((ga / opp_att, w))
            if was_home == at_home:
                venue_scores.append((gf, w))
        attack = _weighted_mean(scored)
        defence = _weighted_mean(allowed)
        # Cap venue adjustment; a few matches must not dominate.
        venue_boost = 1.0
        if len(venue_scores) >= 3:
            raw_attack = _weighted_mean([(float(_team_view(row, team)[0]), 0.86 ** idx) for idx, row in enumerate(games)])
            venue_ratio = _weighted_mean(venue_scores) / max(0.3, raw_attack)
            venue_boost = 1.0 + 0.15 * (_clamp(venue_ratio, 0.6, 1.4) - 1.0)
        return {
            "attack": attack, "defence": defence,
            "opponent_games_supported": supported, "venue_factor": round(venue_boost, 4),
        }

    hr, ar = team_rates(hg, h, True), team_rates(ag, a, False)
    reliability = _clamp(min(len(hg), len(ag)) / 10.0, 0.0, 1.0)
    strength_coverage = (int(hr["opponent_games_supported"]) + int(ar["opponent_games_supported"])) / (len(hg) + len(ag))
    # Fractional multiplicative offence-vs-defence model with history shrinkage.
    home_attack = _clamp(float(hr["attack"]) / average, 0.45, 2.40)
    away_attack = _clamp(float(ar["attack"]) / average, 0.45, 2.40)
    away_weakness = _clamp(float(ar["defence"]) / average, 0.45, 2.40)
    home_weakness = _clamp(float(hr["defence"]) / average, 0.45, 2.40)
    lam_h_raw = rate_h * home_attack**0.55 * away_weakness**0.45 * float(hr["venue_factor"])
    lam_a_raw = rate_a * away_attack**0.55 * home_weakness**0.45 * float(ar["venue_factor"])
    # Low-support observations cannot create extreme opponent-adjusted odds.
    power = 0.65 + 0.25 * reliability
    lam_h = _clamp(rate_h + (lam_h_raw - rate_h) * power, 0.25, 3.80)
    lam_a = _clamp(rate_a + (lam_a_raw - rate_a) * power, 0.25, 3.80)
    return {
        "status": "READY", "model": "football_v5_shadow",
        "home": home, "away": away, "kickoff": kickoff,
        "home_games": len(hg), "away_games": len(ag),
        "opponent_strength_coverage": round(strength_coverage, 4),
        "opponent_strength_confirmed": strength_coverage >= 0.30,
        "home_lambda": round(lam_h, 5), "away_lambda": round(lam_a, 5),
        "baseline_home": round(rate_h, 5), "baseline_away": round(rate_a, 5),
        "home_attack_index": round(home_attack, 4),
        "away_attack_index": round(away_attack, 4),
        "home_defence_weakness": round(home_weakness, 4),
        "away_defence_weakness": round(away_weakness, 4),
        "home_venue_factor": hr["venue_factor"],
        "away_venue_factor": ar["venue_factor"],
        "quality": round(reliability * (0.78 + 0.22 * strength_coverage), 4),
        "probabilities": score_grid(lam_h, lam_a),
        "prediction_created_utc": datetime.now(timezone.utc).isoformat(),
        "warning": "shadow_only_uncalibrated_no_betting",
    }


def price_shadow(forecast: dict[str, Any], market: dict[str, Any], min_odd: float = 1.4) -> list[dict[str, Any]]:
    """Rank test-only opportunities against the *actual* two-way bookmaker line.

    This is not a publishing/Telegram function. Half-goal total lines only,
    because integer lines include push probability.
    """
    if forecast.get("status") != "READY":
        return []
    lam = float(forecast["home_lambda"]) + float(forecast["away_lambda"])
    rows: list[dict[str, Any]] = []
    for quote in market.get("match_totals") or []:
        try:
            line, o, u = float(quote["line"]), float(quote["over"]), float(quote["under"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (line, o, u)) or min(o, u) <= 1.0 or abs(line % 1.0 - 0.5) > 1e-8:
            continue
        threshold = int(math.floor(line)) + 1
        p_over = 1 - sum(math.exp(-lam) * lam**k / math.factorial(k) for k in range(threshold))
        fair_o = (1 / o) / (1 / o + 1 / u)
        for direction, odd, p, market_p in (
            ("over", o, p_over, fair_o),
            ("under", u, 1 - p_over, 1 - fair_o),
        ):
            edge, ev = p - market_p, p * odd - 1
            if odd < min_odd or edge < 0.05 or ev < 0.035 or float(forecast["quality"]) < 0.55:
                continue
            rows.append({
                "market": "match_total", "direction": direction, "line": line,
                "odd": round(odd, 4), "probability_uncalibrated": round(p, 5),
                "market_probability_novig": round(market_p, 5),
                "edge_uncalibrated": round(edge, 5), "ev_uncalibrated": round(ev, 5),
            })
    rows.sort(key=lambda r: (r["edge_uncalibrated"], r["ev_uncalibrated"]), reverse=True)
    return rows


def append_first_snapshots(path: Any, items: list[dict[str, Any]]) -> int:
    """Keep the FIRST pre-kickoff prediction immutable (no hindsight re-pricing)."""
    import json
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if target.exists():
        for line in target.read_text("utf-8").splitlines():
            try:
                seen.add(str(json.loads(line)["event_id"]))
            except (ValueError, KeyError):
                continue
    created = 0
    with target.open("a", encoding="utf-8") as file:
        for row in items:
            eid = str(row.get("event_id") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            created += 1
    return created
