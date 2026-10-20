"""GOOL original Football V5 PREMATCH and LIVE Brain V4 single-process adapter.

Pricing and decisions are performed by unchanged original GOOL modules.
This file only handles the free host's bounded data collection and audit.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .flashscore_odds import fetch_event_odds
from .live_goal_brain_v4 import evaluate_live_goals
from .football_v5_shadow import forecast_from_history
from .lite_football_v5_adapter import original_v5_picks
from .prematch_strategy_scenarios import evaluate_prematch_scenarios
from .providers.flashscore import FlashscoreProvider
from .providers.common import pair_score


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
        self._fotmob_provider = None
        self._scores365_provider = None
        self._fotmob_last_checked: dict[str, float] = {}
        self._last_result = _load(self.state_path, {})
        self._prematch_cursor = int(self._last_result.get("prematch_cursor") or 0)
        self._live_window = int(self._last_result.get("live_window") or 0)
        self._recent_candidates: dict[str, dict[str, Any]] = {
            str(row["event_id"]): row
            for row in (self._last_result.get("prematch_candidates") or [])
            if isinstance(row, dict) and row.get("event_id")
        }

    def _forecast(self, fixture: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Real Football V5 opponent-adjusted forecast; no Lite formula."""
        kickoff = float((fixture.meta or {}).get("scheduled_start_ts") or 0)
        history = self.provider.fetch_match_history(
            str(fixture.provider_match_id), fixture.home, fixture.away, limit=10,
        ) or {}
        forecast = forecast_from_history(
            home=fixture.home, away=fixture.away, kickoff=kickoff, context=history,
        )
        return forecast, history

    def _picks(self, fixture: Any, forecast: dict[str, Any],
               scenarios: dict[str, Any], odds: dict[str, Any],
               history: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """The unchanged GOOL V5 production price gate owns PREMATCH decisions."""
        if forecast.get("status") != "READY" or not odds:
            return []
        rows, details = original_v5_picks(
            history=history or {}, raw_odds=odds,
            event_id=str(fixture.provider_match_id),
            home=fixture.home, away=fixture.away,
            league=fixture.league or "FOOTBALL",
            kickoff=float((fixture.meta or {}).get("scheduled_start_ts") or 0),
            forecast=forecast,
        )
        return sorted(rows, key=lambda r: (r["expected_value"], r["edge"]), reverse=True)[:1]

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
                # Original V5 prices 1X2, BTTS and FT totals irrespective of
                # F01-F05 hypotheses. Never gate the quote behind a scenario.
                priceable = forecast.get("status") == "READY"
                if not priceable:
                    counters["no_history"] += 1
                elif not matched_here:
                    counters["no_scenario"] += 1
                odds = self.odds_fetch(event_id, timeout=6) or {} if priceable else {}
                if priceable and not odds:
                    counters["quote_empty"] += 1
                picks = self._picks(fixture, forecast, scenarios, odds, history=profile)
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
                    "brain": "FOOTBALL_V5",
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
            "scenario_matches": patterns, "priced": priced, "brain": "FOOTBALL_V5",
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
        secondary_budget = 1
        filters = {
            "no_stats": 0, "no_momentum": 0, "insufficient_activity": 0,
            "low_confidence": 0, "low_probability": 0, "brain_bet": 0,
            "no_xg": 0, "no_decision": 0,
            "fotmob_checked": 0, "fotmob_confirmed": 0,
            "fotmob_unmapped": 0, "fotmob_no_shotmap": 0, "fotmob_error": 0,
            "scores365_checked": 0, "scores365_confirmed": 0,
            "scores365_unmapped": 0, "scores365_error": 0,
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
                providers = {"flashscore": {"stats": stats, "meta": {}}}
                record = {
                    "match": {"minute": m.minute, "home_score": m.home_score,
                              "away_score": m.away_score, "is_halftime": m.is_halftime,
                              "home": m.home, "away": m.away},
                    "providers": providers,
                    "live_momentum": momentum,
                }
                # Only spend the expensive secondary request on a game that
                # already has enough fresh pressure to warrant confirmation.
                preliminary = evaluate_live_goals(record)
                pressure_candidate = any(
                    d.probability >= .55 and
                    any(reason == "momentum_ready=1" for reason in d.reasons) and
                    any(reason.startswith("recent_signals=") and
                        int(reason.split("=", 1)[1]) >= 2 for reason in d.reasons)
                    for d in preliminary
                )
                if (secondary_budget and pressure_candidate
                        and now - self._fotmob_last_checked.get(mid, 0) >= 600):
                    secondary_budget -= 1
                    self._fotmob_last_checked[mid] = now
                    # Original V4 is evaluated on independent real provider stats.
                    # Cap additional HTTP requests on the free server.
                    if os.getenv("GOOL_LITE_SCORES365_VERIFY", "1") == "1":
                        filters["scores365_checked"] += 1
                        try:
                            if self._scores365_provider is None:
                                from .providers.scores365 import Scores365Provider
                                self._scores365_provider = Scores365Provider()
                            extra = self._scores365_provider.enrich(m.home, m.away)
                            score = float((extra.meta or {}).get("match_score") or 0) if extra else 0.
                            if (extra and score >= .88 and
                                    any(sum(v) > 0 for v in (extra.stats or {}).values()
                                        if isinstance(v, (tuple, list)) and len(v) == 2)):
                                providers["365scores"] = {"stats": extra.stats, "meta": extra.meta}
                                filters["scores365_confirmed"] += 1
                            else:
                                filters["scores365_unmapped"] += 1
                        except Exception as exc:
                            filters["scores365_error"] += 1
                            print(f"GOOL_LITE_SCORES365_ERROR event={mid} {type(exc).__name__}:{exc}", flush=True)
                    if ("365scores" not in providers and
                            os.getenv("GOOL_LITE_FOTMOB_VERIFY", "1") == "1"):
                        filters["fotmob_checked"] += 1
                        try:
                            if self._fotmob_provider is None:
                                from .providers.fotmob import FotMobProvider
                                self._fotmob_provider = FotMobProvider()
                            extra = self._fotmob_provider.enrich(m.home, m.away)
                            score = float((extra.meta or {}).get("match_score") or 0) if extra else 0.
                            if extra and score >= .88 and (extra.meta or {}).get("has_shotmap") and sum(
                                    (extra.stats or {}).get("shots") or (0, 0)) > 0:
                                providers["fotmob"] = {"stats": extra.stats, "meta": extra.meta}
                                filters["fotmob_confirmed"] += 1
                            elif not extra or score < .88:
                                filters["fotmob_unmapped"] += 1
                            else:
                                filters["fotmob_no_shotmap"] += 1
                        except Exception as exc:
                            filters["fotmob_error"] += 1
                            print(f"GOOL_LITE_SECONDARY_ERROR event={mid} {type(exc).__name__}:{exc}", flush=True)
                # Re-evaluate with the independent source, if actually matched.
                evaluated = evaluate_live_goals(record)
                if not evaluated:
                    filters["no_decision"] += 1
                if not any("xg" in source["stats"] for source in providers.values()):
                    filters["no_xg"] += 1
                for decision in evaluated:
                    reasons = {part.split("=", 1)[0]: part.split("=", 1)[1]
                               for part in decision.reasons if "=" in part}
                    original_bet = decision.decision == "BET"
                    required_confidence = .78
                    policy = "ORIGINAL_LIVE_V4"
                    if original_bet:
                        filters["brain_bet"] += 1
                    else:
                        if reasons.get("momentum_ready") == "0":
                            filters["no_momentum"] += 1
                        elif int(reasons.get("recent_signals", "0")) < 2:
                            filters["insufficient_activity"] += 1
                        elif policy in ("LOW_CONFIDENCE", "SINGLE_SOURCE_INSUFFICIENT"):
                            filters["low_confidence"] += 1
                        elif policy == "LOW_PROBABILITY":
                            filters["low_probability"] += 1
                    decisions.append({
                        "event_id": mid, "match": f"{m.home} — {m.away}",
                        "home": m.home, "away": m.away, "league": m.league or "",
                        "score": [m.home_score, m.away_score],
                        "observed_at": now,
                        "minute": m.minute, "market": decision.market,
                        "decision": decision.decision,
                        "base_decision": decision.decision,
                        "policy": policy, "brain": "ORIGINAL_LIVE_V4",
                        "confidence_required": required_confidence,
                        "confidence": decision.confidence,
                        "probability": decision.probability, "reasons": reasons,
                        "provider_count": len(providers),
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
            "brain": "ORIGINAL_LIVE_V4",
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
