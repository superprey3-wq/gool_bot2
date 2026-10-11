"""Basketball V5: conservative PREMATCH agreement gate.

History 10+10 is only a screening signal, never a probability. V2 offers an
independent scoring-distribution hypothesis; both must agree on the *same*
actual bookmaker quote. Historical V3 remains available for audit/rollback.
A prospective chronological evaluation is still required for calibration.
"""
from __future__ import annotations

import math
import os
from typing import Any

from .basketball_brain_v2 import prematch_signal as dynamic_prematch_signal


def _threshold(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def basketball_v5_prematch_signal(
    historical: dict[str, Any] | None,
    lane: dict[str, Any],
    features: dict[str, Any],
    league: str,
    segment_memory: dict[str, Any] | None = None,
    *,
    odds_range: tuple[float, float] | None = None,
) -> dict[str, Any] | None:
    """Produce no public pick if history, matchup, market quote disagree."""
    if not historical or historical.get("historical_tier") != "PASS_8":
        return None
    if historical.get("direction") not in {"over", "under"}:
        return None
    if not historical.get("historical_coefficient_available"):
        return None
    dynamic = dynamic_prematch_signal(
        lane, features, league, segment_memory, odds_range=odds_range,
    )
    if not dynamic or dynamic.get("direction") != historical.get("direction"):
        return None
    try:
        h_odd = float(historical["odd"])
        d_odd = float(dynamic["odd"])
        historical_p = float(historical["model_probability"])
        dynamic_p = float(dynamic["model_probability"])
        book_fair = float(dynamic["market_probability"])
        quality = float(dynamic["data_quality"])
        line = float(lane["line"])
        hist_line = float(historical["line"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (h_odd, d_odd, historical_p, dynamic_p, book_fair, quality, line, hist_line))):
        return None
    if abs(h_odd - d_odd) > 0.00001 or abs(line - hist_line) > 0.00001 or h_odd < 1.40:
        return None
    if quality < _threshold("GOOL_BASKETBALL_V5_MIN_QUALITY", .65):
        return None
    if not (0 < book_fair < 1 and 0 < dynamic_p < 1 and 0 < historical_p < 1):
        return None

    # No probability may be presented as trained/calibrated: conservative
    # lower-of-two agreement estimate shrunk toward the bookmaker's fair price.
    lower = min(dynamic_p, historical_p)
    blended = book_fair + 0.65 * max(0.0, lower - book_fair)
    # 1/odd, rather than de-vigged market probability, is the true break-even.
    edge_after_vig = blended - (1.0 / h_odd)
    minimum = _threshold("GOOL_BASKETBALL_V5_MIN_NET_EDGE", .025)
    if edge_after_vig < minimum or blended < _threshold("GOOL_BASKETBALL_V5_MIN_PROB", .60):
        return None
    return {
        **historical,
        "brain_mode": "basketball_prematch_v5_consensus",
        "model_probability": round(blended, 6),
        "fair_probability": round(blended, 6),
        "market_probability": round(book_fair, 6),
        "edge": round(blended - book_fair, 6),
        "value_after_vig_proxy": round(edge_after_vig, 6),
        "data_quality": round(quality, 3),
        "basketball_v5_dynamic_probability": round(dynamic_p, 6),
        "basketball_v5_historical_proxy": round(historical_p, 6),
        "basketball_v5_historical_agrees": True,
        "basketball_v5_market_quote_verified": True,
        "historical_probability_uncalibrated": True,
        "probabilities_calibrated": False,
        "strength": round(min(88, 60 + 20 * quality + 45 * max(0.0, edge_after_vig)), 1),
    }
