from __future__ import annotations

from io import BytesIO

from PIL import Image

from gool_bot2 import multi_card, multi_telegram
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _record(minute: int = 63, hs: int = 1, aws: int = 1) -> dict:
    return {
        "match": {
            "flashscore_event_id": "multi-card-fixture",
            "home": "Real Home",
            "away": "Real Away",
            "league": "Test Premier League",
            "minute": minute,
            "home_score": hs,
            "away_score": aws,
        },
        "providers": {},
        "cards": {"home_red": 0, "away_red": 0},
    }


def _decision() -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:2.5",
        family="match_total",
        strategy="another_goal",
        label="ТБ 2.5",
        odd=1.78,
        model_probability=0.74,
        market_probability=0.58,
        goals_to_win=1,
        correlation_key="any_next_goal",
        source="another_goal_model",
        expert_passed=True,
        market_pressure_pp=4.3,
        data_quality=0.88,
        rating=82.0,
        expected_roi=0.317,
        value_edge_pp=16.0,
    )
    alt = MarketCandidate(
        key="home_total:1.5",
        family="team_total",
        strategy="home_goal",
        label="ИТБ1 1.5",
        odd=2.05,
        model_probability=0.61,
        goals_to_win=1,
        correlation_key="home_next_goal",
        rating=71.0,
        expected_roi=0.25,
        value_edge_pp=12.0,
        data_quality=0.88,
    )
    return RouterDecision(
        status="BET",
        minute=63,
        score=(1, 1),
        winner=winner,
        alternatives=[alt],
        rejected=[],
        reason="GOOL + LIVE + цена 1xBet дают лучший баланс для ещё одного гола.",
    )


def _entry() -> dict:
    return {
        "mode": "active",
        "match_id": "multi-card-fixture",
        "home": "Real Home",
        "away": "Real Away",
        "minute": 63,
        "score": [1, 1],
        "market": "ТБ 2.5",
        "odd": 1.78,
        "probability": 0.74,
        "rating": 82.0,
        "signal_source": "GOOL",
        "virtual_stake_rub": 2000.0,
    }


def test_multi_signal_card_is_png_with_team_badges_and_live_layout(monkeypatch):
    calls = []

    def fake_logo(meta, side):
        calls.append(side)
        return Image.new("RGBA", (48, 48), (255, 255, 255, 255))

    monkeypatch.setattr(multi_card.sc, "_logo", fake_logo)
    png = multi_card.render_multi_card(_record(), _decision(), entry=_entry())
    image = Image.open(BytesIO(png))

    assert image.format == "PNG"
    assert image.size == (1080, 1490)
    assert calls == ["home", "away"]


def test_multi_result_cards_render_won_lost_and_void(monkeypatch):
    monkeypatch.setattr(
        multi_card.sc,
        "_logo",
        lambda meta, side: Image.new("RGBA", (48, 48), (255, 255, 255, 255)),
    )
    base = {
        **_entry(),
        "settled_minute": 71,
        "settled_score": [2, 1],
        "virtual_profit_rub": 1560.0,
    }
    for result in ("won", "lost", "void"):
        row = {**base, "result": result}
        png = multi_card.render_multi_result_card(row, _record(71, 2, 1))
        image = Image.open(BytesIO(png))
        assert image.format == "PNG"
        assert image.size == (1080, 1180)


def test_active_mode_delivers_signal_and_result_as_photos(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    photos = []
    texts = []

    def fake_photo(png, caption="", reply_markup=None):
        photos.append((png, caption))
        return 1

    monkeypatch.setattr(multi_telegram.telegram, "broadcast_photo", fake_photo)
    monkeypatch.setattr(multi_telegram.telegram, "broadcast", lambda text, **kwargs: texts.append(text) or 1)

    sent_signal = multi_telegram.emit_multi_signal(_record(), _decision(), _entry())
    settled = {
        **_entry(),
        "result": "won",
        "settled_minute": 71,
        "settled_score": [2, 1],
        "virtual_profit_rub": 1560.0,
    }
    sent_result = multi_telegram.emit_multi_results(_record(71, 2, 1), [settled])

    assert sent_signal == 1
    assert sent_result == 1
    assert len(photos) == 2
    assert all(png.startswith(b"\x89PNG") for png, _ in photos)
    assert "BEST BET" in photos[0][1]
    assert "ЗАШЁЛ" in photos[1][1]
    assert texts == []


def test_shadow_mode_never_emits_multi_photo(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")
    monkeypatch.setattr(
        multi_telegram.telegram,
        "broadcast_photo",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    assert multi_telegram.emit_multi_signal(_record(), _decision(), _entry()) == 0
    assert multi_telegram.emit_multi_results(_record(), [{**_entry(), "result": "won"}]) == 0


def test_active_mode_hides_telegram_token_only_while_legacy_worker_runs(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")

    with multi_telegram.silence_legacy_telegram():
        assert multi_telegram.os.environ.get("TELEGRAM_BOT_TOKEN") == ""

    assert multi_telegram.os.environ.get("TELEGRAM_BOT_TOKEN") == "123:secret"
