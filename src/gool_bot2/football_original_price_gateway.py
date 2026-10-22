"""LIVE pricing adapter for the original football-only GOOL.

Preserves old 1xBet robust fetch, score parser and cautious match identity
checks. No ML models; quote source and score must be current.
"""
from __future__ import annotations
import os
from pathlib import Path
from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import Any
from .providers.common import pair_score, norm_team

def _numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _game_score(game: dict[str, Any]) -> tuple[int, int] | None:
    """Same exact score parser as the original GOOL 1xBet epoch guard.

    1xBet often omits zero-score sides: SC.FS={} really means 0:0.
    Rejecting absent S1/S2 was a Lite-only regression that could suppress
    live odds even though the original bookmaker pipeline accepted the game.
    """
    from .xbet_score_epoch_guard import extract_xbet_score
    return extract_xbet_score(game)


def _index_match_scores(fixture: Any, index: list[dict[str, Any]]) -> list[tuple[float, float, float, dict[str, Any]]]:
    """Rank ordered 1xBet names; the original GOOL accepted pair_score >= .68.

    Averages alone can confuse a namesake with an unrelated opponent.
    Unlike pair_score (which allows reversed sides), LIVE needs home and away
    aligned to the separately verified score. Never switch team orientation.
    """
    h = norm_team(str(fixture.home))
    a = norm_team(str(fixture.away))
    if not h or not a:
        return []
    rows: list[tuple[float, float, float, dict[str, Any]]] = []
    for item in index or []:
        other_h = norm_team(str(item.get("home") or ""))
        other_a = norm_team(str(item.get("away") or ""))
        if not other_h or not other_a:
            continue
        hs = SequenceMatcher(None, h, other_h).ratio()
        aw = SequenceMatcher(None, a, other_a).ratio()
        rows.append(((hs + aw) / 2.0, hs, aw, item))
    return sorted(rows, key=lambda item: item[0], reverse=True)


def _select_live_index_match(fixture: Any, index: list[dict[str, Any]],
                             collector: Any) -> tuple[dict[str, Any] | None, str]:
    """Use original collector mapping + distinct-team/ambiguity safeguards."""
    scored = _index_match_scores(fixture, index)
    if not scored:
        return None, "MATCH_NOT_FOUND"
    # Reuse old GOOL _map which has its own stable match-id cache and 0.68
    # lower bound. Do not accept reverse fixtures or ambiguous namesakes.
    if callable(getattr(collector, "_map", None)):
        event_id = collector._map(fixture, index)
        if not event_id:
            return None, "NAME_MISMATCH"
        selected = next((v for v in scored if str(v[3].get("event_id")) == str(event_id)), None)
        if selected is None:
            return None, "MATCH_NOT_FOUND"
    else:
        selected = scored[0]
    avg, hs, aw, match = selected
    min_score = max(.68, min(.88, float(os.getenv("XBET_MATCH_MIN_SCORE", ".68"))))
    if avg < min_score or min(hs, aw) < .57:
        return None, "NAME_MISMATCH"
    # The old pair_score can overrate names sharing a city but not the club
    # (Manchester United vs Manchester City), or a youth/reserve variant.
    def side_ok(original: str, bookmaker: str) -> bool:
        left, right = norm_team(original), norm_team(bookmaker)
        lparts, rparts = left.split(), right.split()
        if not lparts or not rparts:
            return False
        # Team name categories that must match on both sides, including
        # "II", "B", academy, women and age groups. NFKD normalization
        # strips "Women/W", so inspect both raw and cleaned forms.
        import re
        suffix = r"(?<![a-z0-9])(u(?:17|18|19|20|21|22|23)|ii|reserves?|women|w|femenino|femenil|youth)(?![a-z0-9])"
        if set(re.findall(suffix, original.casefold())) != set(re.findall(suffix, bookmaker.casefold())):
            return False
        if SequenceMatcher(None, left, right).ratio() >= .93:
            return True
        if len(lparts) >= 2 and len(rparts) >= 2:
            if SequenceMatcher(None, lparts[-1], rparts[-1]).ratio() < .60:
                return False
        return True
    if not side_ok(fixture.home, str(match["home"])) or not side_ok(fixture.away, str(match["away"])):
        return None, "NAME_SIDE_MISMATCH"
    # Disallow near-ties even with a matching score: the other fixture can
    # coincidentally be at the same 0:0 or 1:0.
    challengers = [row for row in scored if str(row[3].get("event_id")) != str(match.get("event_id"))]
    if challengers and challengers[0][0] >= avg - .07:
        return None, "MATCH_AMBIGUOUS"
    return match, "MATCH_OK"


class OriginalFootballPriceGateway:
    """Look up only promising live matches; never price from a stale index alone."""

    def __init__(self, data_root: Path, collector: Any = None):
        self.collector = collector
        self.data_root = data_root
        self.last_reject_reason: str | None = None
        self.last_match_debug: dict[str, Any] | None = None

    def _get_collector(self):
        if self.collector is None:
            from .xbet_market_robust import RobustXBetMarketCollector
            root = self.data_root / "live"
            self.collector = RobustXBetMarketCollector(
                root / "football_lite_xbet_unused_state.json",
                root / "football_lite_xbet_unused_history.jsonl",
            )
        return self.collector

    def _reject(self, reason: str) -> None:
        # Do not log potentially sensitive raw bookmaker payloads.
        self.last_reject_reason = reason
        return None

    def price(self, decision: dict[str, Any], index: list[dict[str, Any]], *, now: float) -> dict[str, Any] | None:
        """Fail closed unless bookmaker identity, score and both live prices agree."""
        self.last_reject_reason = None
        self.last_match_debug = None
        if decision.get("decision") != "BET":
            return self._reject("NO_BRAIN_BET")
        if now - _numeric(decision.get("observed_at")) > 60:
            return self._reject("STALE_DECISION")
        scores = decision.get("score") or []
        if len(scores) != 2:
            return self._reject("SCORE_MISSING")
        try:
            scoreboard = (int(scores[0]), int(scores[1]))
        except (ValueError, TypeError):
            return self._reject("SCORE_INVALID")
        c = self._get_collector()
        fixture = SimpleNamespace(
            provider_match_id=str(decision.get("event_id") or ""),
            home=str(decision.get("home") or ""),
            away=str(decision.get("away") or ""),
        )
        best, match_reason = _select_live_index_match(fixture, index, c)
        if best is None:
            scored = _index_match_scores(fixture, index)
            top = scored[0] if scored else None
            self.last_match_debug = {
                "reason": match_reason,
                "flashscore": f"{fixture.home} — {fixture.away}"[:140],
                "closest_1xbet": (f"{top[3].get('home')} — {top[3].get('away')}"[:140]
                                 if top else "нет в индексе"),
                "similarity": round(top[0], 3) if top else 0,
                "index_rows": len(index),
            }
            return self._reject(match_reason)
        book_id = str(best.get("event_id") or "")
        if not book_id:
            return self._reject("BOOK_ID_MISSING")
        # Every quote and score are fetched anew. Never trust historical index odds.
        game = c._game(book_id)
        if not isinstance(game, dict) or _game_score(game) != scoreboard:
            return self._reject("GAME_SCORE_MISMATCH")
        minute = int(decision.get("minute") or 0)
        market = str(decision.get("market") or "")
        if market == "GOAL_BEFORE_HT":
            if not (10 <= minute <= 45):
                return self._reject("WRONG_1H_MINUTE")
            # The old market collector decodes the embedded first-half subgame
            # in the SAME verified GetGameZip response. Lite needlessly
            # fetched the event again, mixing two score/market snapshots.
            from .xbet_market_robust import decode_standard_markets
            lines = decode_standard_markets(game).get("first_half_total") or []
            if not lines:
                # Some 1xBet events expose the half as a separate child game.
                lines = (c._markets_for_event(book_id, minute, sum(scoreboard)) or {}).get("first_half_total") or []
            family = "first_half_total"
        elif market == "ANOTHER_GOAL":
            if not (46 <= minute <= 85):
                return self._reject("WRONG_2H_MINUTE")
            from .xbet_market_robust import decode_standard_markets
            lines = decode_standard_markets(game).get("match_total") or []
            family = "match_total"
        else:
            return self._reject("UNSUPPORTED_MARKET")
        target = float(sum(scoreboard)) + 0.5
        matching = next((row for row in lines
                         if abs(_numeric(row.get("line"), -999.0) - target) < 0.001), None)
        if not matching:
            return self._reject("TARGET_LINE_MISSING")
        over = _numeric(matching.get("over"))
        under = _numeric(matching.get("under"))
        if not (1.40 <= over <= 3.25 and under > 1.01):
            return self._reject("ODDS_PAIR_INVALID")
        fair = (1 / over) / ((1 / over) + (1 / under))
        p = _numeric(decision.get("probability"))
        confidence = _numeric(decision.get("confidence"))
        required_confidence = max(0.62, float(decision.get("confidence_required") or 0.78))
        # Original Brain V3 has a separately validated STRONG_LIVE route
        # (typically 0.64-0.69). Lite's fixed p>=.70 used to veto it
        # AFTER the original V3 had already approved BET. Keep the original
        # decision's guarded bet_min; do NOT relax bookmaker/value checks.
        if decision.get("brain") == "ORIGINAL_BRAIN_V3":
            min_probability = max(0.62, required_confidence)
        else:
            min_probability = 0.70
        if (p < min_probability or confidence < required_confidence
                or p - fair < 0.06 or p * over - 1 < 0.05):
            return self._reject("MODEL_VALUE_GATE")
        return {
            "event_id": fixture.provider_match_id,
            "book_event_id": book_id,
            "home": fixture.home, "away": fixture.away,
            "league": str(decision.get("league") or ""),
            "phase": "LIVE", "market": market, "market_family": family,
            "line": target, "selection": f"ТБ {target:g}" + (" (1-й тайм)" if minute <= 45 else ""),
            "odd": round(over, 3), "bookmaker": "1xBet LIVE",
            "score": list(scoreboard), "minute": minute,
            "observed_at": now, "entry_total": sum(scoreboard),
            "model_probability": round(p, 4), "market_probability": round(fair, 4),
            "confidence": confidence, "edge": round(p - fair, 4),
            "expected_value": round(p * over - 1, 4),
        }

