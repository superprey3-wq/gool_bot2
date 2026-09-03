from io import BytesIO

from PIL import Image

from gool_bot2 import shadow_market_cards as cards


def _row():
    return {
        "match_id": "match-1",
        "head": "team_to_score",
        "result": "lost",
        "home": "Khankendi",
        "away": "Sabail",
        "league": "AZERBAIJAN: I Liga",
        "minute": 38,
        "score": [1, 1],
        "settled_minute": 90,
        "settled_score": [1, 1],
        "selected_side": "home",
        "team": "Khankendi",
    }


def test_shadow_result_card_recovers_flashscore_assets_from_entry_cache(monkeypatch):
    seen = []
    cached_meta = {
        "home_logo_file": "home-logo.png",
        "away_logo_file": "away-logo.png",
        "home_team_id": "h1",
        "away_team_id": "a1",
    }
    cached_stats = {
        "xg": "0.82 : 0.44",
        "shots": "8 : 5",
        "sot": "4 : 2",
        "big_chances": "2 : 1",
    }

    monkeypatch.setattr(
        cards.sc,
        "_read_assets",
        lambda: {"match-1": {"flashscore_meta": cached_meta, "stats_snapshot": cached_stats}},
    )

    def fake_logo(meta, side):
        seen.append((side, dict(meta)))
        return None

    monkeypatch.setattr(cards.sc, "_logo", fake_logo)

    png = cards.render_shadow_market_result_card(_row())
    image = Image.open(BytesIO(png))

    assert image.size == (cards.W, cards.H)
    assert seen == [("home", cached_meta), ("away", cached_meta)]


def test_shadow_result_card_recovers_logos_from_flashscore_master_for_legacy_row(monkeypatch):
    seen = []
    recovered = {
        "home_logo_file": "khankendi.png",
        "away_logo_file": "sabail.png",
        "home_team_id": "kh1",
        "away_team_id": "sa1",
    }

    monkeypatch.setattr(cards.sc, "_read_assets", lambda: {})
    monkeypatch.setattr(cards, "_master_meta", lambda match_id: recovered if match_id == "match-1" else {})

    def fake_logo(meta, side):
        seen.append((side, dict(meta)))
        return None

    monkeypatch.setattr(cards.sc, "_logo", fake_logo)

    png = cards.render_shadow_market_result_card(_row())
    image = Image.open(BytesIO(png))

    assert image.size == (cards.W, cards.H)
    assert seen == [("home", recovered), ("away", recovered)]
