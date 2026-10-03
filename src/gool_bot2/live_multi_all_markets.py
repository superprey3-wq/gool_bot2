from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .match_context import card_context, provider_count, provider_pair, xg_or_proxy_pair
from .prematch_goal_profile import build_prematch_goal_profile


@dataclass(frozen=True)
class LiveAllMarketCandidate:
    family: str
    selection: str
    label: str
    odds: float
    model_probability: float
    market_probability: float
    edge: float
    expected_value: float
    rating: float
    thesis: str
    line: float | None
    reasons: tuple[str, ...]
    eligible: bool
    blocks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveAllMarketDecision:
    status: str
    winner: LiveAllMarketCandidate | None
    candidates: tuple[LiveAllMarketCandidate, ...]
    reason: str
    minute: int
    score: tuple[int, int]
    model_context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "winner": None if self.winner is None else self.winner.to_dict(),
            "candidates": [row.to_dict() for row in self.candidates],
            "reason": self.reason,
            "minute": self.minute,
            "score": list(self.score),
            "model_context": dict(self.model_context),
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _pair(record: dict[str, Any], key: str) -> tuple[float, float]:
    home, away = provider_pair(record, key)
    return float(home or 0.0), float(away or 0.0)


def _de_vig(primary: float, opposite: float | None) -> float:
    a = 1.0 / max(1.000001, float(primary))
    if opposite is None or float(opposite) <= 1.0:
        return _clamp(a)
    b = 1.0 / float(opposite)
    return _clamp(a / max(1e-9, a + b))


def _poisson_pmf(lam: float, goals: int) -> float:
    lam = max(0.0, float(lam))
    return math.exp(-lam) * (lam ** goals) / math.factorial(goals)


def _poisson_ge(lam: float, goals: int) -> float:
    if goals <= 0:
        return 1.0
    cdf = sum(_poisson_pmf(lam, k) for k in range(goals))
    return _clamp(1.0 - cdf)


def _is_half_line(line: float) -> bool:
    return abs((float(line) % 1.0) - 0.5) < 1e-7


def _market_age_seconds(row: dict[str, Any]) -> float | None:
    try:
        dt = datetime.fromisoformat(str(row.get("captured_at") or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _prematch_profile(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record.get("prematch_context"), dict) or not record.get("prematch_context"):
        return {}
    try:
        return build_prematch_goal_profile(record)
    except Exception:
        return {}


def _prematch_total(profile: dict[str, Any], period: str = "full_match") -> float | None:
    try:
        value = (profile.get(period) or {}).get("expected_total")
        return None if value is None else max(0.1, float(value))
    except (TypeError, ValueError):
        return None


def _side_share(record: dict[str, Any], profile: dict[str, Any]) -> float:
    xh, xa, _, _ = xg_or_proxy_pair(record)
    live_parts: list[tuple[float, float]] = []
    if xh is not None and xa is not None and float(xh) + float(xa) > 0.05:
        live_parts.append((float(xh) / (float(xh) + float(xa)), 0.48))

    for key, weight in (("shots_on_target", 0.22), ("shots_inside_box", 0.16), ("big_chances", 0.14)):
        h, a = _pair(record, key)
        if h + a > 0:
            live_parts.append((h / (h + a), weight))

    live_share = None
    if live_parts:
        w = sum(weight for _, weight in live_parts)
        live_share = sum(value * weight for value, weight in live_parts) / max(1e-9, w)

    ft = profile.get("full_match") or {}
    hp = ft.get("home_expected_goals")
    ap = ft.get("away_expected_goals")
    prematch_share = None
    try:
        hpv, apv = float(hp), float(ap)
        if hpv + apv > 0.05:
            prematch_share = hpv / (hpv + apv)
    except (TypeError, ValueError):
        pass

    if live_share is not None and prematch_share is not None:
        return _clamp(0.72 * live_share + 0.28 * prematch_share, 0.12, 0.88)
    if live_share is not None:
        return _clamp(live_share, 0.12, 0.88)
    if prematch_share is not None:
        return _clamp(prematch_share, 0.12, 0.88)
    return 0.5


def _remaining_goal_model(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    match = record.get("match") or {}
    minute = max(1, int(match.get("minute") or 1))
    if minute >= 90 or bool(match.get("is_finished")):
        return None

    xh, xa, xg_source, evidence = xg_or_proxy_pair(record)
    if xh is None or xa is None:
        return None

    xg = max(0.0, float(xh) + float(xa))
    shots = sum(_pair(record, "shots"))
    sot = sum(_pair(record, "shots_on_target"))
    big = sum(_pair(record, "big_chances"))
    box = sum(_pair(record, "touches_box"))
    corners = sum(_pair(record, "corners"))

    elapsed = max(8.0, float(minute))
    live_threat = xg + 0.025 * shots + 0.070 * sot + 0.17 * big + 0.005 * box + 0.008 * corners
    live_rate = live_threat / elapsed

    ft_total = _prematch_total(profile, "full_match")
    prematch_rate = (ft_total / 90.0) if ft_total is not None else (2.45 / 90.0)
    sources = min(3, provider_count(record))
    reliability = _clamp(
        0.38
        + (0.16 if xg_source == "provider_xg" else 0.07)
        + 0.05 * min(int(evidence or 0), 4)
        + 0.04 * sources,
        0.42,
        0.82,
    )
    rate = reliability * live_rate + (1.0 - reliability) * prematch_rate

    momentum = record.get("live_momentum") or {}
    epoch = float(momentum.get("minutes_in_epoch") or 0.0)
    xg5 = momentum.get("xg_total_last_5m")
    shots5 = momentum.get("shots_total_last_5m")
    sot5 = momentum.get("sot_total_last_5m")
    big5 = momentum.get("big_total_last_5m")
    ready = epoch >= 5.0 and any(value is not None for value in (xg5, shots5, sot5, big5))
    signals = sum((
        int(xg5 is not None and float(xg5) >= 0.16),
        int(shots5 is not None and float(shots5) >= 3.0),
        int(sot5 is not None and float(sot5) >= 1.0),
        int(big5 is not None and float(big5) >= 1.0),
    ))
    if ready:
        if signals >= 3:
            rate *= 1.16
        elif signals == 2:
            rate *= 1.07
        elif signals == 0:
            rate *= 0.78
        else:
            rate *= 0.92

    cards = card_context(record)
    red_total = int(cards.get("home_red") or 0) + int(cards.get("away_red") or 0)
    if red_total:
        # Red cards make direction less stable. Keep the estimate but shrink
        # it toward the prematch baseline rather than assuming "red = goals".
        rate = 0.70 * rate + 0.30 * prematch_rate

    remaining = max(0.0, 90.0 - minute)
    total_lambda = max(0.02, rate * remaining)
    share = _side_share(record, profile)
    home_lambda = total_lambda * share
    away_lambda = total_lambda * (1.0 - share)

    quality = _clamp(
        0.34
        + (0.18 if xg_source == "provider_xg" else 0.08)
        + 0.08 * sources
        + 0.05 * min(int(evidence or 0), 4)
        + (0.12 if ready else 0.0)
        - (0.10 if red_total else 0.0),
        0.0,
        0.95,
    )
    return {
        "total_lambda": total_lambda,
        "home_lambda": home_lambda,
        "away_lambda": away_lambda,
        "home_share": share,
        "quality": quality,
        "xg_source": xg_source,
        "evidence": int(evidence or 0),
        "sources": sources,
        "momentum_ready": ready,
        "recent_signals": signals,
        "prematch_total": ft_total,
        "live_rate": live_rate,
        "blended_rate": rate,
    }


def _first_half_lambda(record: dict[str, Any], profile: dict[str, Any], model: dict[str, Any]) -> float:
    match = record.get("match") or {}
    minute = max(1, int(match.get("minute") or 1))
    remaining = max(0.0, 45.0 - minute)
    if remaining <= 0:
        return 0.0

    first_total = _prematch_total(profile, "first_half")
    base_rate = (first_total / 45.0) if first_total is not None else (1.05 / 45.0)
    live_rate = float(model.get("blended_rate") or base_rate)
    return max(0.01, (0.70 * live_rate + 0.30 * base_rate) * remaining)


def _outcome_probabilities(hs: int, aws: int, home_lambda: float, away_lambda: float) -> dict[str, float]:
    out = {"home": 0.0, "draw": 0.0, "away": 0.0}
    max_goals = 9
    for hg in range(max_goals + 1):
        ph = _poisson_pmf(home_lambda, hg)
        for ag in range(max_goals + 1):
            p = ph * _poisson_pmf(away_lambda, ag)
            hfinal, afinal = hs + hg, aws + ag
            if hfinal > afinal:
                out["home"] += p
            elif hfinal < afinal:
                out["away"] += p
            else:
                out["draw"] += p
    total = sum(out.values())
    if total > 0:
        out = {key: _clamp(value / total) for key, value in out.items()}
    return out


def _candidate(
    *,
    family: str,
    selection: str,
    label: str,
    odds: float,
    market_probability: float,
    model_probability: float,
    thesis: str,
    line: float | None,
    quality: float,
    pressure_pp: float = 0.0,
    market_age: float | None,
    min_probability: float = 0.60,
    min_edge: float = 0.06,
) -> LiveAllMarketCandidate:
    p = _clamp(model_probability)
    mp = _clamp(market_probability)
    edge = p - mp
    ev = p * float(odds) - 1.0
    blocks: list[str] = []
    if float(odds) < 1.45 or float(odds) > 3.25:
        blocks.append("price_outside_1.45_3.25")
    if p < min_probability:
        blocks.append("model_probability_below_floor")
    if edge < min_edge:
        blocks.append("edge_below_6pp")
    if ev < 0.03:
        blocks.append("ev_below_3pct")
    if quality < 0.55:
        blocks.append("live_quality_below_55")
    if market_age is None:
        blocks.append("market_timestamp_missing")
    elif market_age > 40.0:
        blocks.append("market_stale")

    probability_score = p * 100.0
    edge_score = _clamp(edge / 0.16) * 100.0
    ev_score = _clamp(ev / 0.20) * 100.0
    market_alignment = max(-12.0, min(12.0, float(pressure_pp))) * 0.45
    rating = (
        0.48 * probability_score
        + 0.27 * edge_score
        + 0.15 * ev_score
        + 0.10 * quality * 100.0
        + market_alignment
    )
    reasons = (
        f"p={p:.3f}",
        f"market_p={mp:.3f}",
        f"edge_pp={edge*100:+.1f}",
        f"ev={ev:+.3f}",
        f"quality={quality:.2f}",
        f"pressure_pp={pressure_pp:+.1f}",
    )
    return LiveAllMarketCandidate(
        family=family,
        selection=selection,
        label=label,
        odds=float(odds),
        model_probability=round(p, 6),
        market_probability=round(mp, 6),
        edge=round(edge, 6),
        expected_value=round(ev, 6),
        rating=round(rating, 2),
        thesis=thesis,
        line=line,
        reasons=reasons,
        eligible=not blocks,
        blocks=tuple(blocks),
    )


def _pressure(row: dict[str, Any], family: str, line_or_side: Any, selection: str) -> float:
    pressure = row.get("pressure") or {}
    if family in {"match_total", "home_total", "away_total", "first_half_total"}:
        key = f"{family}:{line_or_side}"
        raw = float((pressure.get(key) or {}).get("prob_delta_pp") or 0.0)
        return raw if selection == "over" else -raw
    if family == "btts":
        raw = float((pressure.get("btts_yes:None") or {}).get("prob_delta_pp") or 0.0)
        return raw if selection == "yes" else -raw
    if family == "match_1x2":
        return float((pressure.get(f"match_1x2:{selection}") or {}).get("prob_delta_pp") or 0.0)
    return 0.0


def analyze_live_all_markets(record: dict[str, Any], market_row: dict[str, Any] | None) -> LiveAllMarketDecision:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    score = (hs, aws)

    if minute < 10 or minute >= 90 or bool(match.get("is_finished")) or not market_row:
        return LiveAllMarketDecision("WAIT", None, (), "outside_window_or_market_missing", minute, score, {})
    if int(market_row.get("score_home") or 0) != hs or int(market_row.get("score_away") or 0) != aws:
        return LiveAllMarketDecision("WAIT", None, (), "score_desync", minute, score, {})

    profile = _prematch_profile(record)
    model = _remaining_goal_model(record, profile)
    if model is None:
        return LiveAllMarketDecision("WAIT", None, (), "live_goal_model_unavailable", minute, score, {})

    markets = market_row.get("markets") or {}
    age = _market_age_seconds(market_row)
    quality = float(model.get("quality") or 0.0)
    candidates: list[LiveAllMarketCandidate] = []

    total_now = hs + aws
    for family, current, lam, prefix in (
        ("match_total", total_now, float(model["total_lambda"]), ""),
        ("home_total", hs, float(model["home_lambda"]), "1"),
        ("away_total", aws, float(model["away_lambda"]), "2"),
    ):
        for item in markets.get(family) or []:
            try:
                line = float(item.get("line"))
            except (TypeError, ValueError):
                continue
            if not _is_half_line(line):
                continue
            over, under = item.get("over"), item.get("under")
            if not over or not under:
                continue
            needed = math.floor(line - current) + 1
            over_p = _poisson_ge(lam, needed)
            under_p = 1.0 - over_p
            over_mp = _de_vig(float(over), float(under))
            under_mp = 1.0 - over_mp
            if family == "match_total":
                over_label, under_label = f"ТБ {line:g}", f"ТМ {line:g}"
            else:
                over_label, under_label = f"ИТБ{prefix} {line:g}", f"ИТМ{prefix} {line:g}"
            candidates.append(_candidate(
                family=family, selection="over", label=over_label, odds=float(over),
                market_probability=over_mp, model_probability=over_p, thesis="goals_up",
                line=line, quality=quality,
                pressure_pp=_pressure(market_row, family, line, "over"), market_age=age,
            ))
            candidates.append(_candidate(
                family=family, selection="under", label=under_label, odds=float(under),
                market_probability=under_mp, model_probability=under_p, thesis="goals_down",
                line=line, quality=quality,
                pressure_pp=_pressure(market_row, family, line, "under"), market_age=age,
            ))

    if minute <= 42 and not bool(match.get("is_halftime")):
        half_lambda = _first_half_lambda(record, profile, model)
        for item in markets.get("first_half_total") or []:
            try:
                line = float(item.get("line"))
            except (TypeError, ValueError):
                continue
            if not _is_half_line(line):
                continue
            over, under = item.get("over"), item.get("under")
            if not over or not under:
                continue
            needed = math.floor(line - total_now) + 1
            over_p = _poisson_ge(half_lambda, needed)
            under_p = 1.0 - over_p
            over_mp = _de_vig(float(over), float(under))
            candidates.append(_candidate(
                family="first_half_total", selection="over", label=f"1Т ТБ {line:g}", odds=float(over),
                market_probability=over_mp, model_probability=over_p, thesis="goals_up",
                line=line, quality=quality,
                pressure_pp=_pressure(market_row, "first_half_total", line, "over"), market_age=age,
            ))
            candidates.append(_candidate(
                family="first_half_total", selection="under", label=f"1Т ТМ {line:g}", odds=float(under),
                market_probability=1.0-over_mp, model_probability=under_p, thesis="goals_down",
                line=line, quality=quality,
                pressure_pp=_pressure(market_row, "first_half_total", line, "under"), market_age=age,
            ))

    btts = markets.get("btts") or {}
    yes, no = btts.get("yes"), btts.get("no")
    if yes and no:
        ph = 1.0 - math.exp(-float(model["home_lambda"]))
        pa = 1.0 - math.exp(-float(model["away_lambda"]))
        if hs > 0 and aws > 0:
            btts_p = 1.0
        elif hs > 0:
            btts_p = pa
        elif aws > 0:
            btts_p = ph
        else:
            btts_p = ph * pa
        yes_mp = _de_vig(float(yes), float(no))
        candidates.append(_candidate(
            family="btts", selection="yes", label="ОЗ — Да", odds=float(yes),
            market_probability=yes_mp, model_probability=btts_p, thesis="goals_up",
            line=None, quality=quality,
            pressure_pp=_pressure(market_row, "btts", None, "yes"), market_age=age,
        ))
        candidates.append(_candidate(
            family="btts", selection="no", label="ОЗ — Нет", odds=float(no),
            market_probability=1.0-yes_mp, model_probability=1.0-btts_p, thesis="goals_down",
            line=None, quality=quality,
            pressure_pp=_pressure(market_row, "btts", None, "no"), market_age=age,
        ))

    one = markets.get("match_1x2") or {}
    fair = one.get("fair") or {}
    if all(one.get(side) for side in ("home", "draw", "away")) and all(fair.get(side) is not None for side in ("home", "draw", "away")):
        probs = _outcome_probabilities(hs, aws, float(model["home_lambda"]), float(model["away_lambda"]))
        labels = {"home": "П1", "draw": "Ничья", "away": "П2"}
        for side in ("home", "draw", "away"):
            candidates.append(_candidate(
                family="match_1x2", selection=side, label=labels[side], odds=float(one[side]),
                market_probability=float(fair[side]), model_probability=float(probs[side]),
                thesis=f"result_{side}", line=None, quality=quality,
                pressure_pp=_pressure(market_row, "match_1x2", side, side), market_age=age,
                min_probability=0.55, min_edge=0.06,
            ))

    ranked = tuple(sorted(candidates, key=lambda row: (row.eligible, row.rating, row.edge, row.expected_value), reverse=True))
    eligible = [row for row in ranked if row.eligible]
    if not eligible:
        return LiveAllMarketDecision(
            "WAIT", None, ranked,
            "no_market_passes_probability_edge_value_quality",
            minute, score, {**model, "profile_available": bool(profile), "market_age_seconds": age},
        )

    winner = eligible[0]
    if len(eligible) > 1:
        runner = eligible[1]
        opposite_goal_thesis = {winner.thesis, runner.thesis} == {"goals_up", "goals_down"}
        if opposite_goal_thesis and winner.rating - runner.rating < 6.0:
            return LiveAllMarketDecision(
                "WAIT", None, ranked,
                "conflicting_goal_theses_too_close",
                minute, score, {**model, "profile_available": bool(profile), "market_age_seconds": age},
            )
        if winner.family == runner.family and winner.selection != runner.selection and winner.rating - runner.rating < 5.0:
            return LiveAllMarketDecision(
                "WAIT", None, ranked,
                "same_market_opposition_too_close",
                minute, score, {**model, "profile_available": bool(profile), "market_age_seconds": age},
            )

    return LiveAllMarketDecision(
        "BET", winner, ranked,
        "single_best_market_after_full_live_comparison",
        minute, score, {**model, "profile_available": bool(profile), "market_age_seconds": age},
    )


__all__ = ["LiveAllMarketCandidate", "LiveAllMarketDecision", "analyze_live_all_markets"]
