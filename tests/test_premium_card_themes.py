from io import BytesIO

from PIL import Image

from gool_bot2 import gool_live_cards, shadow_market_cards, signal_cards


def test_all_five_strategy_colors_are_distinct():
    colors = {
        "another_goal": signal_cards.THEMES["another_goal"][0],
        "two_more_goals": gool_live_cards.LIVE_THEMES["two_more_goals"][0],
        "both_teams_to_score": shadow_market_cards.THEMES["both_teams_to_score"][0],
        "team_to_score": shadow_market_cards.THEMES["team_to_score"][0],
        "goal_before_ht": signal_cards.THEMES["goal_before_ht"][0],
    }
    assert len(set(colors.values())) == 5


def test_team_to_score_premium_card_renders_large_layout(monkeypatch):
    monkeypatch.setattr(signal_cards, "_logo", lambda meta, side: None)
    record = {
        "match": {
            "flashscore_event_id": "m1",
            "home": "Khankendi",
            "away": "Sabail",
            "league": "AZERBAIJAN · I Liga",
            "minute": 38,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": (8, 4),
                    "shots_on_target": (4, 1),
                    "xg": (1.28, 0.54),
                    "dangerous_attacks": (22, 10),
                    "possession": (58, 42),
                    "big_chances": (2, 0),
                },
                "meta": {},
            }
        },
        "live_momentum": {
            "home_shots_last_5m": 3,
            "away_shots_last_5m": 1,
            "home_shots_last_10m": 6,
            "away_shots_last_10m": 2,
        },
    }
    analysis = {
        "head": "team_to_score",
        "team": "Khankendi",
        "selected_side": "home",
        "confidence_score": 0.74,
        "pressure_score": 1.46,
    }
    png = shadow_market_cards.render_shadow_market_card(record, analysis)
    image = Image.open(BytesIO(png))
    assert image.size == (1080, 985)
    assert len(png) > 10000
