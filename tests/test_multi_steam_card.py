from __future__ import annotations

from io import BytesIO

from PIL import Image

from gool_bot2 import multi_steam_card, multi_telegram
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _decision(pressure: float = 6.4, probability: float = 0.74) -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:2.5",
        family="match_total",
        strategy="another_goal",
        label="ТБ 2.5",
        odd=1.78,
        model_probability=probability,
        market_probability=0.58,
        goals_to_win=1,
        correlation_key="any_next_goal",
        source="another_goal_model",
        expert_passed=True,
        market_pressure_pp=pressure,
        market_level="STRONG_STEAM" if pressure >= 6.0 else "PRESSURE",
        data_quality=0.88,
        rating=82.0,
        expected_roi=0.31,
        value_edge_pp=12.0,
    )
    return RouterDecision(
        status="BET",
        minute=63,
        score=(1, 1),
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="Сильное движение 1xBet подтверждает рынок.",
    )


def _market() -> dict:
    return {
        "score_home": 1,
        "score_away": 1,
        "pressure": {
            "match_total:2.5": {
                "prob_delta_pp": 6.4,
                "one_way_moves": 5,
                "old_odd": 2.16,
                "new_odd": 1.78,
            }
        },
    }


def _record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "steam-card",
            "home": "Home",
            "away": "Away",
            "league": "TEST: League",
            "minute": 63,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {},
        "cards": {},
    }


def _entry(probability: float = 0.74) -> dict:
    return {
        "mode": "active",
        "match_id": "steam-card",
        "home": "Home",
        "away": "Away",
        "minute": 63,
        "score": [1, 1],
        "market": "ТБ 2.5",
        "odd": 1.78,
        "probability": probability,
        "rating": 82.0,
        "signal_source": "GOOL",
        "reason_tags": [],
    }


def _blank_png() -> bytes:
    out = BytesIO()
    Image.new("RGBA", (1080, 760), (8, 16, 28, 255)).save(out, format="PNG")
    return out.getvalue()


def test_strong_steam_snapshot_exposes_real_bookmaker_move():
    snap = multi_steam_card.steam_snapshot(_market(), _decision())

    assert snap["strong"] is True
    assert snap["pressure_pp"] == 6.4
    assert snap["moves"] == 5
    assert snap["old_odd"] == 2.16
    assert snap["new_odd"] == 1.78


def test_strong_steam_has_dedicated_compact_png_variant(monkeypatch):
    monkeypatch.setattr(multi_steam_card, "render_multi_card", lambda *args, **kwargs: _blank_png())

    png = multi_steam_card.render_multi_signal_card(
        _record(),
        _decision(),
        entry=_entry(),
        market_row=_market(),
    )
    image = Image.open(BytesIO(png))

    assert image.format == "PNG"
    assert image.size == (1080, 760)
    assert png != _blank_png()


def test_regular_pressure_keeps_normal_card(monkeypatch):
    base = _blank_png()
    monkeypatch.setattr(multi_steam_card, "render_multi_card", lambda *args, **kwargs: base)

    png = multi_steam_card.render_multi_signal_card(
        _record(),
        _decision(4.2),
        entry=_entry(),
        market_row=_market(),
    )

    assert png == base


def test_telegram_photo_has_no_duplicate_caption(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    photos = []
    monkeypatch.setattr(multi_telegram, "render_multi_signal_card", lambda *args, **kwargs: _blank_png())
    monkeypatch.setattr(
        multi_telegram.telegram,
        "broadcast_photo",
        lambda png, caption="", **kwargs: photos.append((png, caption)) or 1,
    )

    sent = multi_telegram.emit_multi_signal(
        _record(),
        _decision(),
        _entry(),
        market_row=_market(),
    )

    assert sent == 1
    assert photos[0][1] == ""


def test_public_multi_card_is_suppressed_below_70(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    photos = []
    monkeypatch.setattr(
        multi_telegram.telegram,
        "broadcast_photo",
        lambda *args, **kwargs: photos.append(args) or 1,
    )

    sent = multi_telegram.emit_multi_signal(
        _record(),
        _decision(probability=0.699),
        _entry(probability=0.699),
        market_row=_market(),
    )

    assert sent == 0
    assert photos == []
