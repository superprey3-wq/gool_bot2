from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from gool_bot2 import multi_card
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _shield(side: str) -> Image.Image:
    img = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (53, 214, 255, 255) if side == "home" else (255, 184, 48, 255)
    draw.rounded_rectangle((18, 8, 102, 108), 24, fill=fill)
    draw.ellipse((36, 28, 84, 76), fill=(5, 10, 18, 255))
    return img


def _record(minute: int = 64, hs: int = 1, aws: int = 1) -> dict:
    stats = {
        "xg": [1.34, 0.88],
        "xgot": [1.12, 0.61],
        "shots": [12, 8],
        "shots_on_target": [5, 3],
        "shots_inside_box": [7, 4],
        "big_chances": [3, 1],
        "corners": [6, 3],
        "dangerous_attacks": [48, 35],
    }
    return {
        "match": {
            "flashscore_event_id": "preview",
            "home": "Real Sociedad",
            "away": "Celta Vigo",
            "league": "Spain · LaLiga",
            "minute": minute,
            "home_score": hs,
            "away_score": aws,
        },
        "providers": {"flashscore": {"stats": stats}},
        "cards": {"home_red": 0, "away_red": 0},
    }


def _decision() -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:2.5", family="match_total", strategy="another_goal",
        label="ТБ 2.5", odd=1.78, model_probability=0.742, market_probability=0.574,
        goals_to_win=1, correlation_key="any_next_goal", source="another_goal_model",
        expert_passed=True, market_pressure_pp=4.8, data_quality=0.91,
        rating=84.0, expected_roi=0.321, value_edge_pp=16.8,
    )
    alt = MarketCandidate(
        key="home_total:1.5", family="team_total", strategy="home_goal",
        label="ИТБ1 1.5", odd=2.10, model_probability=0.61,
        goals_to_win=1, correlation_key="home_next_goal", rating=73.0,
        expected_roi=0.281, value_edge_pp=13.4, data_quality=0.91,
    )
    return RouterDecision(
        status="BET", minute=64, score=(1, 1), winner=winner, alternatives=[alt], rejected=[],
        reason="MODEL + PREMATCH + LIVE подтверждают гол, а 1xBet даёт положительный VALUE без сильного движения против ставки.",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="artifacts/gool_multi_cards")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    original_logo = multi_card.sc._logo
    multi_card.sc._logo = lambda meta, side: _shield(side)
    try:
        entry = {
            "mode": "active", "match_id": "preview", "home": "Real Sociedad", "away": "Celta Vigo",
            "minute": 64, "score": [1, 1], "market": "ТБ 2.5", "odd": 1.78,
            "probability": 0.742, "rating": 84.0, "signal_source": "GOOL",
            "virtual_stake_rub": 2000.0,
        }
        (out / "signal.png").write_bytes(multi_card.render_multi_card(_record(), _decision(), entry=entry))
        won = {
            **entry, "result": "won", "settled_minute": 72, "settled_score": [2, 1],
            "virtual_profit_rub": 1560.0,
        }
        lost = {
            **entry, "result": "lost", "settled_minute": 90, "settled_score": [1, 1],
            "virtual_profit_rub": -2000.0,
        }
        (out / "result_won.png").write_bytes(multi_card.render_multi_result_card(won, _record(72, 2, 1)))
        (out / "result_lost.png").write_bytes(multi_card.render_multi_result_card(lost, _record(90, 1, 1)))
    finally:
        multi_card.sc._logo = original_logo


if __name__ == "__main__":
    main()
