from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from PIL import Image

from gool_bot2 import multi_telegram
from gool_bot2.multi_autonomous_steam import apply_autonomous_steam
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _blank_png() -> bytes:
    image = Image.new("RGB", (32, 32))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _wait() -> RouterDecision:
    return RouterDecision(
        status="WAIT",
        minute=50,
        score=(0, 0),
        winner=None,
        alternatives=[],
        rejected=[],
        reason="no gool",
    )


def _record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "m1",
            "home": "Home",
            "away": "Away",
            "minute": 50,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
        }
    }


def _market(odd: float = 1.55) -> dict:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "score_home": 0,
        "score_away": 0,
        "score_verified": True,
        "score_desync": False,
        "repricing_guard": False,
        "markets": {
            "match_total": [
                {"line": 0.5, "over": odd, "under": 2.45},
                {"line": 1.5, "over": 2.35, "under": 1.55},
            ],
            "home_total": [],
            "away_total": [],
            "first_half_total": [],
            "btts": {"yes": 2.05, "no": 1.70},
        },
        "pressure": {
            "match_total:0.5": {
                # With no related-market breadth, autonomous STEAM deliberately
                # requires the extreme single-market path: >=12pp and >=4 moves.
                "prob_delta_pp": 12.5,
                "one_way_moves": 4,
                "old_odd": 1.78,
            },
        },
    }


def test_strong_steam_can_promote_wait_to_bet(monkeypatch) -> None:
    monkeypatch.setenv("GOOL_MULTI_AUTONOMOUS_STEAM", "1")
    decision = apply_autonomous_steam(
        _wait(),
        _record(),
        _market(),
        data_quality=0.80,
    )
    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.key == "match_total:0.5"
    assert decision.winner.source == "1xbet:autonomous_steam"
    assert decision.winner.rating >= 70.0
    assert "autonomous_steam" in decision.winner.reason_tags
    assert "confidence_metric" in decision.winner.reason_tags


def test_autonomous_steam_keeps_price_and_repricing_guards(monkeypatch) -> None:
    monkeypatch.setenv("GOOL_MULTI_AUTONOMOUS_STEAM", "1")
    low_price = apply_autonomous_steam(
        _wait(),
        _record(),
        _market(1.25),
        data_quality=0.80,
    )
    assert low_price.status == "WAIT"

    guarded = _market()
    guarded["repricing_guard"] = True
    after_goal = apply_autonomous_steam(
        _wait(),
        _record(),
        guarded,
        data_quality=0.80,
    )
    assert after_goal.status == "WAIT"


def _winner() -> MarketCandidate:
    return MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        label="ТБ 0.5",
        odd=1.55,
        model_probability=0.80,
        rating=80.0,
        market_pressure_pp=12.5,
        market_level="AUTONOMOUS_STEAM",
        source="1xbet:autonomous_steam",
        reason_tags=["confidence_metric", "autonomous_steam"],
    )


def test_multi_signal_photo_has_no_duplicate_caption(monkeypatch) -> None:
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    seen: dict[str, object] = {}

    def fake_photo(png: bytes, caption: str = "", reply_markup=None) -> int:
        seen["png"] = png
        seen["caption"] = caption
        return 1

    monkeypatch.setattr(multi_telegram.telegram, "broadcast_photo", fake_photo)
    monkeypatch.setattr(multi_telegram, "render_multi_signal_card", lambda *a, **k: _blank_png())
    monkeypatch.setattr(
        multi_telegram.telegram,
        "broadcast",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("text fallback should not run")),
    )

    decision = RouterDecision(
        status="BET",
        minute=50,
        score=(0, 0),
        winner=_winner(),
        alternatives=[],
        rejected=[],
        reason="steam",
    )
    entry = {
        "match_id": "m1",
        "home": "Home",
        "away": "Away",
        "minute": 50,
        "score": [0, 0],
        "market": "ТБ 0.5",
        "odd": 1.55,
        "probability": 0.80,
        "reason_tags": ["confidence_metric", "autonomous_steam"],
    }
    assert multi_telegram.emit_multi_signal(_record(), decision, entry, market_row=_market()) == 1
    assert seen["caption"] == ""


def test_multi_result_photo_has_no_duplicate_caption(monkeypatch) -> None:
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    seen: dict[str, object] = {}

    def fake_photo(png: bytes, caption: str = "", reply_markup=None) -> int:
        seen["caption"] = caption
        return 1

    monkeypatch.setattr(multi_telegram.telegram, "broadcast_photo", fake_photo)
    monkeypatch.setattr(multi_telegram, "render_multi_result_card", lambda *a, **k: _blank_png())
    monkeypatch.setattr(
        multi_telegram.telegram,
        "broadcast",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("text fallback should not run")),
    )
    rows = [{
        "match_id": "m1",
        "home": "Home",
        "away": "Away",
        "market": "ТБ 0.5",
        "odd": 1.55,
        "probability": 0.80,
        "mode": "active",
        "telegram_sent": True,
        "result": "won",
        "settled_minute": 63,
        "settled_score": [1, 0],
    }]
    assert multi_telegram.emit_multi_results(_record(), rows) == 1
    assert seen["caption"] == ""
