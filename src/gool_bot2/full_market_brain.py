from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .full_market_decode import _find_participant_ids, _selection_observations
from .prematch_probability_calibration import calibrate_probability, load_prematch_calibration_rows
from .full_market_math import (
    _asian_handicap_net,
    _asian_total_net,
    _half_full_prob,
    _half_full_prob_from_distributions,
    _period_lambdas,
    _result_probs,
    _settlement_summary,
    _simple_prob,
    score_distribution,
)

MODEL_WEIGHT = 0.65


@dataclass(frozen=True)
class FullMarketCandidate:
    scope: str
    market_type: str
    selection: str
    odds: float
    bookmaker: str
    raw_model_probability: float | None
    model_probability: float | None
    honest_probability: float | None
    probability_range_low: float | None
    probability_range_high: float | None
    calibration_sample: int
    calibration_confidence: str
    calibration_source: str
    profile_sample: int
    market_probability: float | None
    edge: float | None
    raw_expected_value: float | None
    expected_value: float | None
    quality: float
    status: str
    settlement: dict[str, float]
    scope_source: str | None = None
    observations: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _poisson_over_probability(lam: float, line: float) -> float:
    import math
    threshold = int(math.floor(line)) + 1
    cdf = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(threshold))
    return max(0.0, min(1.0, 1.0 - cdf))


def _lambda_from_over_probability(line: float, probability: float) -> float | None:
    # Half-goal binary lines have no push, so their de-vig market probability
    # can safely identify a Poisson intensity. This is used only to learn the
    # market's FIRST_HALF/SECOND_HALF split, never as the final model p.
    if abs(line * 2 - round(line * 2)) > 1e-9 or int(round(line * 2)) % 2 == 0:
        return None
    target = max(0.005, min(0.995, float(probability)))
    lo, hi = 0.01, 7.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _poisson_over_probability(mid, line) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _market_scope_lambda(observations: dict[tuple, list[dict[str, Any]]], scope: str) -> float | None:
    values = []
    for key, rows in observations.items():
        k_scope, typ, sel, line = key
        if k_scope != scope or typ != "OVER_UNDER" or sel != "OVER" or line is None:
            continue
        ps = [
            float(row["market_probability"])
            for row in rows
            if row.get("market_probability") is not None
        ]
        if not ps:
            continue
        market_p = statistics.median(ps)
        lam = _lambda_from_over_probability(float(line), market_p)
        if lam is not None and 0.05 <= lam <= 6.0:
            values.append(lam)
    return statistics.median(values) if values else None


def _scope_distributions(
    profile: dict[str, Any],
    observations: dict[tuple, list[dict[str, Any]]],
) -> tuple[dict[str, dict[tuple[int, int], float]], dict[str, Any]]:
    out = {}
    derived = {}
    full = _period_lambdas(profile, "FULL_TIME")
    for scope in ("FULL_TIME", "FIRST_HALF", "SECOND_HALF"):
        pair = _period_lambdas(profile, scope)
        if pair is not None:
            out[scope] = score_distribution(*pair)
            derived[scope] = {"source": "historical_period_profile", "home_lambda": pair[0], "away_lambda": pair[1]}

    # When half-specific historical scores are unavailable, do not use a fixed
    # 45/55 assumption. Infer only the relative period split from the de-vig
    # bookmaker totals, then apply that split to GOOL's independent FT lambda.
    if full is not None and ("FIRST_HALF" not in out or "SECOND_HALF" not in out):
        market_first = _market_scope_lambda(observations, "FIRST_HALF")
        market_second = _market_scope_lambda(observations, "SECOND_HALF")
        if market_first is not None and market_second is not None and market_first + market_second > 0:
            total_model = full[0] + full[1]
            home_share = full[0] / total_model if total_model > 0 else .5
            first_total = total_model * market_first / (market_first + market_second)
            second_total = total_model - first_total
            for scope, total in (("FIRST_HALF", first_total), ("SECOND_HALF", second_total)):
                if scope in out:
                    continue
                pair = (max(.01, total * home_share), max(.01, total * (1.0 - home_share)))
                out[scope] = score_distribution(*pair)
                derived[scope] = {
                    "source": "ft_model_with_market_period_split",
                    "home_lambda": pair[0],
                    "away_lambda": pair[1],
                    "market_split_lambda_first": market_first,
                    "market_split_lambda_second": market_second,
                }
    return out, derived


def _model_for_key(
    profile: dict[str, Any],
    dists: dict[str, dict[tuple[int, int], float]],
    key: tuple,
    odds: float,
) -> tuple[float | None, float | None, dict[str, float]]:
    scope, typ, sel, line = key
    dist = dists.get(scope)

    if typ == "HALF_FULL_TIME":
        p = _half_full_prob(profile, str(sel))
        if p is None and "FIRST_HALF" in dists and "SECOND_HALF" in dists:
            p = _half_full_prob_from_distributions(
                dists["FIRST_HALF"], dists["SECOND_HALF"], str(sel)
            )
        return p, None if p is None else p * odds - 1.0, {}

    if dist is None:
        return None, None, {}

    if typ in {
        "HOME_DRAW_AWAY",
        "DOUBLE_CHANCE",
        "BOTH_TEAMS_TO_SCORE",
        "ODD_OR_EVEN",
        "CORRECT_SCORE",
    }:
        p = _simple_prob(dist, typ, str(sel), None if line is None else float(line))
        return p, None if p is None else p * odds - 1.0, {}

    if typ == "OVER_UNDER" and line is not None:
        side_prefix = None
        side_sel = str(sel)
        if side_sel.startswith("HOME_"):
            side_prefix = "HOME"
            side_sel = side_sel[5:]
        elif side_sel.startswith("AWAY_"):
            side_prefix = "AWAY"
            side_sel = side_sel[5:]
        over = side_sel == "OVER"

        def net(score):
            h, a = score
            total = h if side_prefix == "HOME" else a if side_prefix == "AWAY" else h + a
            return _asian_total_net(total, float(line), over, odds)

        st = _settlement_summary(dist, net)
        p = _simple_prob(dist, typ, side_sel, float(line)) if side_prefix is None else None
        if (
            p is None
            and abs(float(line) * 2 - round(float(line) * 2)) < 1e-9
            and int(round(float(line) * 2)) % 2 == 1
        ):
            if side_prefix == "HOME":
                base = sum(v for (h, _a), v in dist.items() if h > float(line))
            elif side_prefix == "AWAY":
                base = sum(v for (_h, a), v in dist.items() if a > float(line))
            else:
                base = None
            if base is not None:
                p = base if over else 1 - base
        return p, st["raw_ev"], st

    if typ == "DRAW_NO_BET":
        h, d, a = _result_probs(dist)
        pwin = h if sel == "HOME" else a if sel == "AWAY" else None
        plose = a if sel == "HOME" else h if sel == "AWAY" else None
        if pwin is None or plose is None:
            return None, None, {}
        ev = pwin * (odds - 1.0) - plose
        cond = pwin / (pwin + plose) if pwin + plose > 0 else .5
        return cond, ev, {
            "full_win": pwin,
            "push": d,
            "loss": plose,
            "raw_ev": ev,
            "non_loss": pwin + d,
        }

    if typ == "ASIAN_HANDICAP" and line is not None:
        side = str(sel)
        handicap = float(line)

        def net(score):
            h, a = score
            diff = h - a if side == "HOME" else a - h
            return _asian_handicap_net(diff, handicap, odds)

        st = _settlement_summary(dist, net)
        denom = st["full_win"] + st["half_win"] + st["half_loss"] + st["loss"]
        equiv = (st["full_win"] + .5 * st["half_win"]) / denom if denom > 0 else None
        return equiv, st["raw_ev"], st

    if typ == "EUROPEAN_HANDICAP" and line is not None:
        orientation = float(line)
        probs = {"HOME": 0.0, "DRAW": 0.0, "AWAY": 0.0}
        for (h, a), p in dist.items():
            adj = h + orientation - a
            state = "HOME" if adj > 0 else "DRAW" if abs(adj) < 1e-9 else "AWAY"
            probs[state] += p
        p = probs.get(str(sel))
        return p, None if p is None else p * odds - 1.0, {}

    return None, None, {}


def analyze_full_market(
    data: dict[str, Any],
    profile: dict[str, Any],
    *,
    quality: float = 1.0,
    model_weight: float = MODEL_WEIGHT,
    calibration_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [r for r in (data.get("odds") or []) if isinstance(r, dict)]
    home_pid, away_pid = _find_participant_ids(rows)
    observations = _selection_observations(data, home_pid, away_pid)
    dists, scope_sources = _scope_distributions(profile, observations)
    candidates: list[FullMarketCandidate] = []
    unmodeled = Counter()

    for key, obs in observations.items():
        if not obs:
            continue
        best = max(obs, key=lambda x: float(x["odds"]))
        best_odds = float(best["odds"])
        market_ps = [
            float(x["market_probability"])
            for x in obs
            if x.get("market_probability") is not None
        ]
        market_p = statistics.median(market_ps) if market_ps else None
        raw_p, raw_ev, settlement = _model_for_key(profile, dists, key, best_odds)
        scope, typ, sel, line = key

        if raw_p is None and raw_ev is None:
            unmodeled[(scope, typ)] += 1
            continue

        p = None
        honest_p = None
        range_low = None
        range_high = None
        calibration_sample = 0
        calibration_confidence = "LOW"
        calibration_source = "unavailable"
        profile_sample = 0
        edge = None
        ev = None
        calibration = None
        if raw_p is not None:
            calibration = calibrate_probability(
                raw_model_probability=raw_p,
                market_probability=market_p,
                profile=profile,
                scope=scope,
                market_type=typ,
                selection=str(sel),
                quality=quality,
                rows=calibration_rows,
            )
            honest_p = float(calibration["honest_probability"])
            p = honest_p
            range_low = float(calibration["range_low"])
            range_high = float(calibration["range_high"])
            calibration_sample = int(calibration["calibration_sample"])
            calibration_confidence = str(calibration["confidence"])
            calibration_source = str(calibration["source"])
            profile_sample = int(calibration["profile_sample"])
            if market_p is not None:
                edge = honest_p - market_p

        if raw_ev is not None:
            if p is not None and not settlement:
                ev = p * best_odds - 1.0
            elif (
                p is not None
                and market_p is not None
                and raw_p is not None
                and abs(raw_p - market_p) > 1e-9
            ):
                # Settlement markets (DNB/Asian) have pushes or half outcomes.
                # Keep their exact raw settlement EV, but shrink it by the same
                # empirical distance-to-market that produced honest_probability.
                ratio = (p - market_p) / (raw_p - market_p)
                ratio = max(0.0, min(1.0, ratio))
                ev = raw_ev * ratio
            else:
                ev = raw_ev * 0.35

        status = "SKIP"
        non_loss = float(settlement.get("non_loss", p or 0.0))
        if quality >= .60 and 1.40 <= best_odds <= 3.25 and ev is not None:
            if (
                p is not None
                and p >= .64
                and ev >= .04
                and (edge is None or edge >= .035)
                and non_loss >= .64
            ):
                status = "BET"
            elif (
                p is not None
                and p >= .56
                and ev >= .015
                and (edge is None or edge >= .015)
                and non_loss >= .56
            ):
                status = "LEAN"

        if typ in {"CORRECT_SCORE", "HALF_FULL_TIME"} and best_odds > 3.25:
            status = "SKIP"

        scope_source = str((scope_sources.get(scope) or {}).get("source") or "")
        if (
            status == "BET"
            and scope in {"FIRST_HALF", "SECOND_HALF"}
            and scope_source == "ft_model_with_market_period_split"
        ):
            # Period split came partly from bookmaker totals. It is useful for
            # comparing markets but not independent enough for a full BET label.
            status = "LEAN"

        if typ == "EUROPEAN_HANDICAP" and line is not None:
            # line is canonical HOME handicap. Show the handicap of the selected
            # side so "AWAY" under HOME -3 is displayed as AWAY +3.
            if str(sel) == "HOME":
                shown_line = float(line)
                label = f"HOME {shown_line:+g}"
            elif str(sel) == "AWAY":
                shown_line = -float(line)
                label = f"AWAY {shown_line:+g}"
            else:
                label = f"DRAW (HOME {float(line):+g})"
        else:
            label = str(sel) if line is None else f"{sel} {float(line):g}"
        candidates.append(FullMarketCandidate(
            scope=scope,
            market_type=typ,
            selection=label,
            odds=best_odds,
            bookmaker=str(best["bookmaker"]),
            raw_model_probability=raw_p,
            model_probability=p,
            honest_probability=honest_p,
            probability_range_low=range_low,
            probability_range_high=range_high,
            calibration_sample=calibration_sample,
            calibration_confidence=calibration_confidence,
            calibration_source=calibration_source,
            profile_sample=profile_sample,
            market_probability=market_p,
            edge=edge,
            raw_expected_value=raw_ev,
            expected_value=ev,
            quality=float(quality),
            status=status,
            settlement={k: float(v) for k, v in settlement.items()},
            scope_source=scope_source or None,
            observations=len(obs),
        ))

    def rank(c: FullMarketCandidate):
        status_rank = {"BET": 2, "LEAN": 1, "SKIP": 0}.get(c.status, 0)
        return (
            status_rank,
            c.expected_value if c.expected_value is not None else -99,
            c.edge if c.edge is not None else -99,
            c.model_probability if c.model_probability is not None else 0,
        )

    candidates.sort(key=rank, reverse=True)
    coverage = Counter((c.scope, c.market_type) for c in candidates)
    return {
        "participant_ids": {"home": home_pid, "away": away_pid},
        "scope_sources": scope_sources,
        "modeled_market_types": [
            {"scope": a, "type": b, "selections": n}
            for (a, b), n in sorted(coverage.items())
        ],
        "unmodeled_market_types": [
            {"scope": a, "type": b, "selections": n}
            for (a, b), n in sorted(unmodeled.items())
        ],
        "candidates": [c.to_dict() for c in candidates],
    }
