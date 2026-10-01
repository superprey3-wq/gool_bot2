from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .full_market_decode import _find_participant_ids, _selection_observations
from .full_market_math import (
    _asian_handicap_net,
    _asian_total_net,
    _half_full_prob,
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
    market_probability: float | None
    edge: float | None
    raw_expected_value: float | None
    expected_value: float | None
    quality: float
    status: str
    settlement: dict[str, float]
    observations: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scope_distributions(profile: dict[str, Any]) -> dict[str, dict[tuple[int, int], float]]:
    out = {}
    for scope in ("FULL_TIME", "FIRST_HALF", "SECOND_HALF"):
        pair = _period_lambdas(profile, scope)
        if pair is not None:
            out[scope] = score_distribution(*pair)
    return out


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
) -> dict[str, Any]:
    rows = [r for r in (data.get("odds") or []) if isinstance(r, dict)]
    home_pid, away_pid = _find_participant_ids(rows)
    observations = _selection_observations(data, home_pid, away_pid)
    dists = _scope_distributions(profile)
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
        edge = None
        ev = None
        if raw_p is not None:
            if market_p is not None:
                p = model_weight * raw_p + (1 - model_weight) * market_p
                edge = p - market_p
            else:
                p = raw_p

        if raw_ev is not None:
            ev = model_weight * raw_ev
            if p is not None and not settlement:
                ev = p * best_odds - 1.0

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

        label = str(sel) if line is None else f"{sel} {float(line):g}"
        candidates.append(FullMarketCandidate(
            scope=scope,
            market_type=typ,
            selection=label,
            odds=best_odds,
            bookmaker=str(best["bookmaker"]),
            raw_model_probability=raw_p,
            model_probability=p,
            market_probability=market_p,
            edge=edge,
            raw_expected_value=raw_ev,
            expected_value=ev,
            quality=float(quality),
            status=status,
            settlement={k: float(v) for k, v in settlement.items()},
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
