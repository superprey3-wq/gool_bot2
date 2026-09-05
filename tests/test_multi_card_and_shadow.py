from __future__ import annotations

from io import BytesIO

from PIL import Image

from gool_bot2.multi_card import _gool_metric_text, render_multi_card
from gool_bot2.multi_experts import build_expert_snapshot
from gool_bot2.multi_router import MarketCandidate, RouterDecision
from gool_bot2.multi_shadow import analyze_and_record


def test_expert_adapter_reuses_current_gool_outputs_without_fabrication():
    experts = build_expert_snapshot(
        model_result={
            "blended": {"another_goal": 0.73},
            "trained_probability": {"goal_before_ht": 0.68},
        },
        two_more_analysis={"confidence_score": 0.57, "passed": True, "pressure_score": 1.24},
    )

    assert experts["another_goal"]["probability"] == 0.73
    assert experts["goal_before_ht"]["probability"] == 0.68
    assert experts["two_more_goals"]["probability"] == 0.57
    assert "home_goal" not in experts
    assert "away_goal" not in experts


def test_public_metric_distinguishes_probability_from_confidence():
    probability = MarketCandidate(
        key="match_total:2.5", family="match_total", label="ТБ 2.5", odd=1.70,
        model_probability=0.74,
    )
    confidence = MarketCandidate(
        key="away_total:1.5", family="team_total", label="ИТБ2 1.5", odd=2.10,
        model_probability=0.84, reason_tags=["confidence_metric"],
    )

    assert _gool_metric_text(probability, 0.74) == "ВЕРОЯТНОСТЬ СОБЫТИЯ 74%"
    assert _gool_metric_text(confidence, 0.84) == "ОЦЕНКА СОБЫТИЯ 84/100"


def test_multi_card_contains_one_winner_and_renders_current_png():
    winner = MarketCandidate(
        key="match_total:4.5", family="match_total", strategy="two_more_goals", label="ТБ 4.5", odd=1.60,
        model_probability=0.75, goals_to_win=2, correlation_key="two_goal_path", data_quality=0.92, rating=87.0,
        expected_roi=0.15, value_edge_pp=9.4, market_pressure_pp=6.5,
    )
    alt = MarketCandidate(
        key="match_total:3.5", family="match_total", strategy="another_goal", label="ТБ 3.5", odd=1.50,
        model_probability=0.82, goals_to_win=1, correlation_key="any_next_goal",
        data_quality=0.92, rating=79.0, expected_roi=0.06, value_edge_pp=5.1,
    )
    decision = RouterDecision(
        status="BET", minute=54, score=(1, 2), winner=winner, alternatives=[alt], rejected=[],
        reason="Internal diagnostic reason must not be printed on the public card.",
    )
    record = {
        "match": {
            "flashscore_event_id": "fixture-card",
            "home": "Home U20",
            "away": "Away U20",
            "league": "International U20",
            "minute": 54,
            "home_score": 1,
            "away_score": 2,
        },
        "providers": {},
        "cards": {},
    }

    png = render_multi_card(record, decision)
    image = Image.open(BytesIO(png))

    assert image.format == "PNG"
    assert image.size == (1080, 1120)


def test_shadow_analyzer_writes_decision_without_telegram(tmp_path):
    record = {
        "match": {
            "flashscore_event_id": "fixture-shadow",
            "home": "Home",
            "away": "Away",
            "league": "Test League",
            "minute": 54,
            "home_score": 1,
            "away_score": 2,
        }
    }
    market = {
        "score_home": 1,
        "score_away": 2,
        "markets": {
            "match_total": [
                {"line": 3.5, "over": 1.50, "under": 3.00},
                {"line": 4.5, "over": 2.00, "under": 1.75},
            ],
            "first_half_total": [],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {"match_total:4.5": {"prob_delta_pp": 6.0}},
    }
    journal = tmp_path / "multi-shadow.jsonl"

    decision = analyze_and_record(
        record,
        market,
        {"another_goal": 0.80, "two_more_goals": 0.54},
        journal,
        data_quality=0.9,
    )

    assert decision.status in {"BET", "WAIT"}
    text = journal.read_text("utf-8")
    assert '"mode":"shadow"' in text
    assert '"match_id":"fixture-shadow"' in text
    assert "telegram" not in text.lower()
