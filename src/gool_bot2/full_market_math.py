from __future__ import annotations

import math
from typing import Any

def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _odd(item: dict[str, Any]) -> float | None:
    x = _num(item.get("value"))
    return x if x is not None and x > 1.0 and bool(item.get("active", True)) else None


def _handicap(item: dict[str, Any]) -> float | None:
    return _num((item.get("handicap") or {}).get("value"))


def _poisson(lam: float, max_goals: int = 10) -> list[float]:
    lam = max(0.01, min(6.0, float(lam)))
    probs = [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(max_goals + 1)]
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def score_distribution(home_lambda: float, away_lambda: float, max_goals: int = 10) -> dict[tuple[int, int], float]:
    hp = _poisson(home_lambda, max_goals)
    ap = _poisson(away_lambda, max_goals)
    return {(h, a): ph * pa for h, ph in enumerate(hp) for a, pa in enumerate(ap)}


def _period_lambdas(profile: dict[str, Any], scope: str) -> tuple[float, float] | None:
    key = {"FULL_TIME": "full_match", "FIRST_HALF": "first_half", "SECOND_HALF": "second_half"}.get(scope)
    if key is None:
        return None
    row = profile.get(key) or {}
    h = _num(row.get("home_expected_goals"))
    a = _num(row.get("away_expected_goals"))
    if h is not None and a is not None and h >= 0 and a >= 0:
        return max(.01, h), max(.01, a)
    if scope == "FULL_TIME":
        one = profile.get("first_half") or {}
        two = profile.get("second_half") or {}
        h1 = _num(one.get("home_expected_goals"))
        a1 = _num(one.get("away_expected_goals"))
        h2 = _num(two.get("home_expected_goals"))
        a2 = _num(two.get("away_expected_goals"))
        if None not in (h1, a1, h2, a2):
            return max(.01, float(h1) + float(h2)), max(.01, float(a1) + float(a2))
    return None


def _result_probs(dist: dict[tuple[int, int], float]) -> tuple[float, float, float]:
    h = sum(p for (x, y), p in dist.items() if x > y)
    d = sum(p for (x, y), p in dist.items() if x == y)
    a = sum(p for (x, y), p in dist.items() if x < y)
    z = h + d + a
    return (h / z, d / z, a / z) if z else (1 / 3, 1 / 3, 1 / 3)


def _pair_fair(a: float, b: float) -> tuple[float, float]:
    x, y = 1 / a, 1 / b
    z = x + y
    return (x / z, y / z) if z > 0 else (.5, .5)


def _multi_fair(odds: list[float]) -> list[float]:
    inv = [1 / x for x in odds]
    z = sum(inv)
    return [x / z for x in inv] if z > 0 else [1 / len(odds)] * len(odds)


def _scope_distributions(profile: dict[str, Any]) -> dict[str, dict[tuple[int, int], float]]:
    out = {}
    for scope in ("FULL_TIME", "FIRST_HALF", "SECOND_HALF"):
        pair = _period_lambdas(profile, scope)
        if pair is not None:
            out[scope] = score_distribution(*pair)
    return out


def _simple_prob(
    dist: dict[tuple[int, int], float],
    market_type: str,
    selection: str,
    line: float | None = None,
) -> float | None:
    h, d, a = _result_probs(dist)
    if market_type == "HOME_DRAW_AWAY":
        return {"HOME": h, "DRAW": d, "AWAY": a}.get(selection)
    if market_type == "DOUBLE_CHANCE":
        return {"HOME_OR_DRAW": h + d, "HOME_OR_AWAY": h + a, "DRAW_OR_AWAY": d + a}.get(selection)
    if market_type == "BOTH_TEAMS_TO_SCORE":
        yes = sum(p for (x, y), p in dist.items() if x > 0 and y > 0)
        return yes if selection == "YES" else 1 - yes if selection == "NO" else None
    if market_type == "ODD_OR_EVEN":
        odd = sum(p for (x, y), p in dist.items() if (x + y) % 2 == 1)
        return odd if selection == "ODD" else 1 - odd if selection == "EVEN" else None
    if market_type == "CORRECT_SCORE":
        try:
            x, y = (int(v) for v in selection.split(":", 1))
        except Exception:
            return None
        return dist.get((x, y), 0.0)
    if market_type == "OVER_UNDER" and line is not None:
        if abs(line * 2 - round(line * 2)) < 1e-9 and int(round(line * 2)) % 2 == 1:
            over = sum(p for (x, y), p in dist.items() if x + y > line)
            return over if selection == "OVER" else 1 - over if selection == "UNDER" else None
    return None


def _split_quarter(line: float) -> tuple[float, ...]:
    q = round(line * 4)
    if abs(line * 4 - q) > 1e-7:
        return (line,)
    mod = q % 4
    if mod == 1:
        base = math.floor(line)
        return (float(base), float(base) + .5)
    if mod == 3:
        base = math.floor(line)
        return (float(base) + .5, float(base) + 1.0)
    return (line,)


def _single_total_net(total: int, line: float, over: bool, odds: float) -> float:
    if over:
        if total > line:
            return odds - 1.0
        if total < line:
            return -1.0
        return 0.0
    if total < line:
        return odds - 1.0
    if total > line:
        return -1.0
    return 0.0


def _asian_total_net(total: int, line: float, over: bool, odds: float) -> float:
    parts = _split_quarter(line)
    return sum(_single_total_net(total, x, over, odds) for x in parts) / len(parts)


def _single_handicap_net(diff: int, handicap: float, odds: float) -> float:
    adjusted = diff + handicap
    if adjusted > 0:
        return odds - 1.0
    if adjusted < 0:
        return -1.0
    return 0.0


def _asian_handicap_net(diff: int, handicap: float, odds: float) -> float:
    parts = _split_quarter(handicap)
    return sum(_single_handicap_net(diff, x, odds) for x in parts) / len(parts)


def _settlement_summary(dist: dict[tuple[int, int], float], net_fn) -> dict[str, float]:
    buckets = {"full_win": 0.0, "half_win": 0.0, "push": 0.0, "half_loss": 0.0, "loss": 0.0}
    ev = 0.0
    for score, p in dist.items():
        net = float(net_fn(score))
        ev += p * net
        if net > .75:
            buckets["full_win"] += p
        elif net > 1e-9:
            buckets["half_win"] += p
        elif net < -.75:
            buckets["loss"] += p
        elif net < -1e-9:
            buckets["half_loss"] += p
        else:
            buckets["push"] += p
    buckets["raw_ev"] = ev
    buckets["non_loss"] = buckets["full_win"] + buckets["half_win"] + buckets["push"]
    return buckets


def _half_full_prob(profile: dict[str, Any], winner: str) -> float | None:
    first = _period_lambdas(profile, "FIRST_HALF")
    second = _period_lambdas(profile, "SECOND_HALF")
    if first is None or second is None or "/" not in winner:
        return None
    target_ht, target_ft = winner.split("/", 1)
    d1 = score_distribution(*first, max_goals=8)
    d2 = score_distribution(*second, max_goals=8)

    def state(x: int, y: int) -> str:
        return "1" if x > y else "X" if x == y else "2"

    p = 0.0
    for (h1, a1), p1 in d1.items():
        if state(h1, a1) != target_ht:
            continue
        for (h2, a2), p2 in d2.items():
            if state(h1 + h2, a1 + a2) == target_ft:
                p += p1 * p2
    return p


def _half_full_prob_from_distributions(
    first_dist: dict[tuple[int, int], float],
    second_dist: dict[tuple[int, int], float],
    winner: str,
) -> float | None:
    if "/" not in str(winner):
        return None
    target_ht, target_ft = str(winner).split("/", 1)

    def state(x: int, y: int) -> str:
        return "1" if x > y else "X" if x == y else "2"

    if target_ht not in {"1", "X", "2"} or target_ft not in {"1", "X", "2"}:
        return None
    p = 0.0
    for (h1, a1), p1 in first_dist.items():
        if state(h1, a1) != target_ht:
            continue
        for (h2, a2), p2 in second_dist.items():
            if state(h1 + h2, a1 + a2) == target_ft:
                p += p1 * p2
    return p
