"""Memory-bounded, model-free football discovery for GOOL Lite.

Uses the existing Flashscore provider, historical goal profile and F01-F05
scenario hypotheses. A matched hypothesis is *not* an automatically valid bet:
only real paired quotes, independently estimated probabilities and conservative
gates can create a priced PREMATCH candidate. LIVE is diagnostic-only until
a verifiably LIVE 1xBet quote adapter has been tested on this hosting tier.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .flashscore_odds import TREND_MARKET, fetch_event_odds
from .live_goal_brain_v4 import evaluate_live_goals
from .prematch_goal_profile import build_prematch_goal_profile
from .prematch_strategy_scenarios import evaluate_prematch_scenarios
from .providers.flashscore import FlashscoreProvider


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _over(lam: float, threshold: int) -> float:
    # P(Poisson(lam) >= threshold), model estimate, NOT calibrated probability.
    return 1 - sum(math.exp(-lam) * lam**k / math.factorial(k) for k in range(threshold))


MARKETS = {
    "F01": [("1H_OVER_0.5", "Гол в первом тайме", lambda h, a, fh: _over(fh, 1))],
    "F02": [
        ("FT_OVER_2.5", "ТБ 2.5", lambda h, a, fh: _over(h + a, 3)),
        ("BTTS_YES", "Обе забьют — да", lambda h, a, fh: (1 - math.exp(-h)) * (1 - math.exp(-a))),
    ],
    "F03": [
        ("FT_UNDER_2.5", "ТМ 2.5", lambda h, a, fh: 1 - _over(h + a, 3)),
        ("BTTS_NO", "Обе забьют — нет", lambda h, a, fh: 1 - (1 - math.exp(-h)) * (1 - math.exp(-a))),
    ],
    # F04/F05 still evaluated and displayed as scenarios, but are not sent:
    # this cheap source has no verified paired 1X2 quote implementation.
}


def _paired_trend_price(data: dict[str, Any], target: str) -> dict[str, Any] | None:
    """Require both sides from the SAME bookmaker and the SAME handicap."""
    choice = TREND_MARKET.get(target)
    if not choice:
        return None
    scope, kind, line, side = choice
    bookmakers = {
        str((x.get("bookmaker") or {}).get("id")): str((x.get("bookmaker") or {}).get("name") or "")
        for x in ((data.get("settings") or {}).get("bookmakers") or [])
        if isinstance(x, dict)
    }
    offers: list[dict[str, Any]] = []
    for market in data.get("odds") or []:
        if (not isinstance(market, dict) or market.get("bettingScope") != scope
                or market.get("bettingType") != kind):
            continue
        odds: dict[str, float] = {}
        for item in market.get("odds") or []:
            if not isinstance(item, dict):
                continue
            try:
                odd = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            if not (1.01 < odd < 500):
                continue
            if kind == "OVER_UNDER":
                try:
                    if abs(float((item.get("handicap") or {}).get("value")) - float(line)) > .0001:
                        continue
                except (TypeError, ValueError):
                    continue
                direction = str(item.get("selection") or "").upper()
            else:
                yesno = item.get("bothTeamsToScore")
                direction = ("YES" if yesno is True else "NO" if yesno is False
                             else str(yesno).upper())
            if direction in ("OVER", "UNDER", "YES", "NO"):
                odds[direction] = odd
        opposite = "UNDER" if side == "OVER" else "OVER" if side == "UNDER" else "NO" if side == "YES" else "YES"
        a, b = odds.get(side), odds.get(opposite)
        if not a or not b:
            continue
        fair = (1 / a) / ((1 / a) + (1 / b))
        offers.append({
            "best_odds": a,
            "opposite_odds": b,
            "market_probability": fair,
            "bookmaker": bookmakers.get(str(market.get("bookmakerId"))) or str(market.get("bookmakerId") or ""),
        })
    return max(offers, key=lambda x: x["best_odds"]) if offers else None


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), "utf-8")
    temp.replace(path)


class LiteFootballWorker:
    def __init__(self, runtime: Path, *, provider: Any = None,
                 odds_fetch: Callable[..., dict[str, Any]] | None = None) -> None:
        self.root = runtime / "live"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "gool_lite_football_state.json"
        self.journal_path = self.root / "gool_lite_football_journal.json"
        self.provider = provider if provider is not None else FlashscoreProvider()
        self.odds_fetch = odds_fetch or fetch_event_odds
        self._last_pre = 0.0
        self._last_live = 0.0
        self._seen_pre: dict[str, float] = {}
        self._live: dict[str, deque[dict[str, Any]]] = {}
        # Rotate football fixtures, rather than repeatedly sampling only the first
        # 4 live / first 5 prematch matches on a crowded matchday.
        self._prematch_cursor = 0
        self._live_window = 0
        self._live_window_scans = 0
        self._last_result = _load(self.state_path, {})
        self._prematch_cursor = int(self._last_result.get("prematch_cursor") or 0)
        self._live_window = int(self._last_result.get("live_window") or 0)
        self._recent_candidates: dict[str, dict[str, Any]] = {
            str(row["event_id"]): row
            for row in (self._last_result.get("prematch_candidates") or [])
            if isinstance(row, dict) and row.get("event_id")
        }

    def _forecast(self, fixture: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        event_id = str(fixture.provider_match_id)
        history = self.provider.fetch_match_history(event_id, fixture.home, fixture.away, limit=10)
        record = {
            "match": {"home": fixture.home, "away": fixture.away},
            "prematch_context": history,
        }
        profile = build_prematch_goal_profile(record)
        full = profile.get("full_match") or {}
        first = profile.get("first_half") or {}
        n = int(full.get("pair_sample") or 0)
        home, away = full.get("home_expected_goals"), full.get("away_expected_goals")
        forecast = {"status": "WAIT_HISTORY", "quality": 0.0}
        if home is not None and away is not None and n >= 6:
            forecast = {
                "status": "READY",
                "home_lambda": _clamp(float(home), .05, 4.5),
                "away_lambda": _clamp(float(away), .05, 4.5),
                "quality": round(.50 + .045 * min(n, 10), 3),
                "first_half_lambda": first.get("expected_total"),
                "sample": n,
            }
        return forecast, profile

    def _picks(self, fixture: Any, forecast: dict[str, Any],
               scenarios: dict[str, Any], odds: dict[str, Any]) -> list[dict[str, Any]]:
        if forecast.get("status") != "READY" or not odds:
            return []
        h, a = float(forecast["home_lambda"]), float(forecast["away_lambda"])
        fh = forecast.get("first_half_lambda")
        matched = {
            str(item.get("id"))
            for item in scenarios.get("scenarios") or []
            if item.get("status") == "MATCH"
        }
        result: list[dict[str, Any]] = []
        for scenario in matched:
            for target, label, probability in MARKETS.get(scenario, []):
                if target == "1H_OVER_0.5" and fh is None:
                    continue
                price = _paired_trend_price(odds, target)
                if not price:
                    continue
                odd = float(price.get("best_odds") or 0)
                fair = float(price.get("market_probability") or 0)
                p = _clamp(float(probability(h, a, float(fh or 0))))
                if not (1.4 <= odd <= 3.5 and 0 < fair < 1):
                    continue
                edge = p - fair
                ev = p * odd - 1.0
                if p < 0.70 or edge < 0.07 or ev < 0.06:
                    continue
                result.append({
                    "sport": "football", "phase": "PREMATCH",
                    "event_id": str(fixture.provider_match_id),
                    "flashscore_event_id": str(fixture.provider_match_id),
                    "home": fixture.home, "away": fixture.away,
                    "league": fixture.league or "FOOTBALL",
                    "start_ts": float((fixture.meta or {}).get("scheduled_start_ts") or 0),
                    "selection": label, "market": target, "market_family": target,
                    "odd": round(odd, 3), "bookmaker": str(price.get("bookmaker") or ""),
                    "model_probability": round(p, 4), "market_probability": round(fair, 4),
                    "edge": round(edge, 4), "expected_value": round(ev, 4),
                    "data_quality": float(forecast["quality"]),
                    "strength": round(p * 100, 2),
                    "scenario": scenario,
                    "parlay_safe": bool(p >= 0.74 and 1.3 <= odd <= 1.5),
                    "flashscore_meta": dict(fixture.meta or {}),
                })
        result.sort(key=lambda x: (x["expected_value"], x["data_quality"]), reverse=True)
        return result[:1]  # only one PREMATCH pick per fixture

    def scan_prematch(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        if now - self._last_pre < max(300, int(os.getenv("GOOL_LITE_FOOTBALL_PREMATCH_INTERVAL", "600"))):
            return {"cached": True}
        self._last_pre = now
        matches = self.provider.scheduled_matches_for_day(0)
        upcoming = [
            m for m in matches
            if 300 <= float((m.meta or {}).get("scheduled_start_ts") or 0) - now <= 4 * 3600
        ]
        upcoming.sort(key=lambda m: float((m.meta or {}).get("scheduled_start_ts") or 0))
        max_scan = max(1, min(8, int(os.getenv("GOOL_LITE_FOOTBALL_PREMATCH_MAX", "5"))))
        analysed, patterns, priced, failures = 0, 0, 0, 0
        counters = {
            "no_history": 0, "no_scenario": 0, "not_priceable_scenario": 0,
            "quote_empty": 0, "no_value": 0, "priced": 0,
        }
        observations: list[dict[str, Any]] = []
        seen_now = set()
        # Stagger successive scans across the full 4-hour upcoming window.
        selection = (
            [upcoming[(self._prematch_cursor + i) % len(upcoming)]
             for i in range(min(max_scan, len(upcoming)))]
            if upcoming else []
        )
        if upcoming:
            self._prematch_cursor = (self._prematch_cursor + len(selection)) % len(upcoming)
        for fixture in selection:
            event_id = str(fixture.provider_match_id)
            seen_now.add(event_id)
            try:
                forecast, profile = self._forecast(fixture)
                scenarios = evaluate_prematch_scenarios("football", football_forecast=forecast)
                matched_here = int(scenarios.get("matched_count") or 0)
                patterns += matched_here
                priceable = any(
                    s.get("status") == "MATCH" and s.get("id") in MARKETS
                    for s in scenarios.get("scenarios") or []
                )
                if forecast.get("status") != "READY":
                    counters["no_history"] += 1
                elif not matched_here:
                    counters["no_scenario"] += 1
                elif not priceable:
                    counters["not_priceable_scenario"] += 1
                odds = self.odds_fetch(event_id, timeout=6) or {} if priceable else {}
                if priceable and not odds:
                    counters["quote_empty"] += 1
                picks = self._picks(fixture, forecast, scenarios, odds)
                if priceable and odds and not picks:
                    counters["no_value"] += 1
                analysed += 1
                if picks:
                    self._recent_candidates[event_id] = picks[0]
                    priced += 1
                    counters["priced"] += 1
                observations.append({
                    "event_id": event_id, "match": f"{fixture.home} — {fixture.away}",
                    "kickoff": (fixture.meta or {}).get("scheduled_start_ts"),
                    "status": forecast.get("status"), "sample": forecast.get("sample", 0),
                    "scenario_matches": [s["id"] for s in scenarios.get("scenarios", [])
                                         if s.get("status") == "MATCH"],
                    "priced": bool(picks),
                    "quote_available": bool(odds),
                    "priceable_scenario": priceable,
                })
            except Exception as exc:
                failures += 1
                print(f"GOOL_LITE_FOOTBALL_PRE_ERROR event={event_id} {type(exc).__name__}:{exc}", flush=True)
        self._recent_candidates = {
            event_id: item for event_id, item in self._recent_candidates.items()
            if float(item.get("start_ts") or 0) > now + 120
            and float(item.get("start_ts") or 0) <= now + 12 * 3600
        }
        # Football-only host does not need the cross-sport SUPER 10 pool.
        # Avoid importing its heavy cards and writing extra disk state.
        if os.getenv("GOOL_LITE_PUBLISH_SUPER10", "0") == "1":
            from .global_super10 import publish_candidates
            publish_candidates("football", self._recent_candidates.values())
        result = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "upcoming": len(upcoming), "analysed": analysed,
            "scenario_matches": patterns, "priced": priced,
            "errors": failures, "filters": counters,
            "selected": len(selection), "prematch_cursor": self._prematch_cursor,
            "observations": observations[:10],
            "prematch_candidates": list(self._recent_candidates.values())[:20],
        }
        print("GOOL_LITE_FOOTBALL_PRE " + json.dumps({
            k: v for k, v in result.items() if k not in {"observations", "prematch_candidates"}
        }, ensure_ascii=False), flush=True)
        return result

    def scan_live(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        if now - self._last_live < 150:
            return {"cached": True}
        self._last_live = now
        matches = self.provider.live_matches()
        relevant = [m for m in matches if m.minute and 10 <= m.minute <= 85 and not m.is_halftime]
        # Limit detailed per-match requests on the 25% CPU free tier.
        relevant.sort(key=lambda m: str(m.provider_match_id))
        max_live = max(1, min(8, int(os.getenv("GOOL_LITE_FOOTBALL_LIVE_MAX", "4"))))
        batches = max(1, (len(relevant) + max_live - 1) // max_live)
        batch = self._live_window % batches
        selected = relevant[batch * max_live:(batch + 1) * max_live]
        self._live_window_scans += 1
        # Keep a small set of the same games for 3 cycles (5-minute momentum),
        # then rotate. Avoid treating cumulative stats as 'recent' every cycle.
        if self._live_window_scans >= 3:
            self._live_window_scans = 0
            self._live_window = (batch + 1) % batches
        decisions: list[dict[str, Any]] = []
        filters = {
            "no_stats": 0, "no_momentum": 0, "insufficient_activity": 0,
            "low_confidence": 0, "low_probability": 0, "brain_bet": 0,
            "no_xg": 0, "no_decision": 0,
        }
        failures = 0
        seen = set()
        for m in selected:
            mid = str(m.provider_match_id)
            seen.add(mid)
            try:
                stats = self.provider.fetch_stats(mid)
                if not stats:
                    filters["no_stats"] += 1
                history = self._live.setdefault(mid, deque(maxlen=12))
                row = {"ts": now, "minute": m.minute,
                       "score": [m.home_score, m.away_score], "stats": stats}
                past = next((x for x in reversed(history) if now - x["ts"] >= 300
                             and x["minute"] <= m.minute), None)
                history.append(row)
                momentum: dict[str, Any] = {}
                if past is not None:
                    momentum["minutes_in_epoch"] = max(0, m.minute - past["minute"])
                    for key, alias in (("shots", "shots"), ("shots_on_target", "sot"),
                                       ("big_chances", "big"), ("xg", "xg")):
                        old = past["stats"].get(key)
                        new = stats.get(key)
                        if old is not None and new is not None:
                            momentum[f"{alias}_total_last_5m"] = max(
                                0.0, sum(float(x) for x in new) - sum(float(x) for x in old)
                            )
                record = {
                    "match": {"minute": m.minute, "home_score": m.home_score,
                              "away_score": m.away_score, "is_halftime": m.is_halftime,
                              "home": m.home, "away": m.away},
                    "providers": {"flashscore": {"stats": stats, "meta": {}}},
                    "live_momentum": momentum,
                }
                evaluated = evaluate_live_goals(record)
                if not evaluated:
                    filters["no_decision"] += 1
                if "xg" not in stats:
                    filters["no_xg"] += 1
                for decision in evaluated:
                    reasons = {part.split("=", 1)[0]: part.split("=", 1)[1]
                               for part in decision.reasons if "=" in part}
                    if decision.decision == "BET":
                        filters["brain_bet"] += 1
                    else:
                        if reasons.get("momentum_ready") == "0":
                            filters["no_momentum"] += 1
                        elif int(reasons.get("recent_signals", "0")) < 2:
                            filters["insufficient_activity"] += 1
                        elif decision.confidence < .78:
                            filters["low_confidence"] += 1
                        elif decision.probability < float(reasons.get("threshold", "0")):
                            filters["low_probability"] += 1
                    decisions.append({
                        "event_id": mid, "match": f"{m.home} — {m.away}",
                        "home": m.home, "away": m.away, "league": m.league or "",
                        "score": [m.home_score, m.away_score],
                        "observed_at": now,
                        "minute": m.minute, "market": decision.market,
                        "decision": decision.decision, "confidence": decision.confidence,
                        "probability": decision.probability, "reasons": reasons,
                        "priced": False,  # LIVE odds not guaranteed by Flashscore quote API
                        "publishable": False,
                    })
            except Exception as exc:
                failures += 1
                print(f"GOOL_LITE_FOOTBALL_LIVE_ERROR event={mid} {type(exc).__name__}:{exc}", flush=True)
        for key in list(self._live):
            rows = self._live[key]
            if not rows or now - rows[-1]["ts"] > 2400:
                del self._live[key]
        # Guard memory when hundreds of fixtures are active.
        if len(self._live) > 64:
            for key, _ in sorted(self._live.items(), key=lambda item: item[1][-1]["ts"])[:len(self._live) - 64]:
                del self._live[key]
        result = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "live_total": len(matches), "eligible": len(relevant),
            "analysed": len(seen), "errors": failures, "selected": len(selected),
            "batch": batch + 1, "batch_count": batches, "filters": filters,
            "decisions": decisions[:20],
        }
        print("GOOL_LITE_FOOTBALL_LIVE " + json.dumps({
            k: v for k, v in result.items() if k != "decisions"
        }, ensure_ascii=False), flush=True)
        return result

    def collect_once(self) -> dict[str, Any]:
        pre = self.scan_prematch()
        live = self.scan_live()
        result = dict(self._last_result)
        if not pre.get("cached"):
            result["prematch"] = {k: v for k, v in pre.items() if k != "prematch_candidates"}
            result["prematch_candidates"] = pre.get("prematch_candidates", [])
        if not live.get("cached"):
            result["live"] = live
        result["prematch_cursor"] = self._prematch_cursor
        result["live_window"] = self._live_window
        result["captured_at"] = datetime.now(timezone.utc).isoformat()
        result["mode"] = os.getenv("GOOL_MULTISPORT_MODE", "shadow")
        # This module is analysis-only pending a verified LIVE-priced decision
        # workflow and independent settlement test.
        result["delivery"] = "disabled_unverified_football_lite"
        _save(self.state_path, result)
        self._last_result = result
        return result
