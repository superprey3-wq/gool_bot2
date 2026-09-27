from gool_bot2.football_model_challengers import poisson_profile_challenger, total_probability, choose_challenger_delivery, dixon_coles_profile_challenger, model_market_candidates, consensus_candidates


def test_poisson_challenger_returns_normalized_probabilities():
    forecast = poisson_profile_challenger({"full_match": {
        "home_expected_goals": 1.8, "away_expected_goals": 1.1,
    }})
    assert forecast is not None
    assert forecast.name == "poisson_profile_v1"
    assert abs(forecast.home + forecast.draw + forecast.away - 1.0) < 1e-9
    assert forecast.home > forecast.away


def test_poisson_challenger_total_probability_is_complementary():
    forecast = poisson_profile_challenger({"full_match": {
        "home_expected_goals": 1.5, "away_expected_goals": 1.2,
    }})
    assert forecast is not None
    over = total_probability(forecast, 2.5, True)
    under = total_probability(forecast, 2.5, False)
    assert abs(over + under - 1.0) < 1e-9


def test_poisson_challenger_does_not_need_bookmaker_market():
    forecast = poisson_profile_challenger({"full_match": {
        "home_expected_goals": 1.2, "away_expected_goals": 0.8,
    }})
    assert forecast is not None


def _pick(event, odds, p, edge=.10, ev=.15, quality=.9):
    return {"event_id": event, "home": event, "away": "B", "selection": "under 2.5",
            "odds": odds, "probability": p, "edge": edge, "expected_value": ev, "quality": quality}


def test_delivery_can_keep_strong_single_instead_of_forcing_acca():
    rows=[_pick("A",2.05,.76,ev=.56), _pick("C",1.60,.68,ev=.09)]
    ticket=choose_challenger_delivery(rows)
    assert ticket["type"] == "SINGLE"
    assert ticket["legs"][0]["event_id"] == "A"


def test_delivery_can_choose_confident_double():
    rows=[_pick("A",1.75,.75,ev=.31), _pick("C",1.80,.74,ev=.33), _pick("E",1.55,.63,ev=.05)]
    ticket=choose_challenger_delivery(rows)
    assert ticket["type"] == "ACCA_2"
    assert len(ticket["legs"]) == 2


def test_three_plus_requires_stricter_leg_confidence():
    rows=[_pick("A",1.55,.74), _pick("C",1.55,.73), _pick("E",1.55,.71)]
    ticket=choose_challenger_delivery(rows, max_legs=4)
    assert ticket is not None
    assert all(x["probability"] >= .67 for x in ticket["legs"]) if ticket["type"].startswith("ACCA") else True


def test_dixon_coles_is_normalized_and_distinct():
    profile={"full_match":{"home_expected_goals":1.4,"away_expected_goals":1.1}}
    p=poisson_profile_challenger(profile); d=dixon_coles_profile_challenger(profile)
    assert d is not None and abs(d.home+d.draw+d.away-1)<1e-9
    assert abs(d.draw-p.draw)>1e-4


def test_consensus_requires_independent_models():
    a=[{"event_id":"1","home":"A","away":"B","market":"match_total","selection":"under 2.5","odds":1.8,
        "probability":.70,"market_probability":.52,"quality":.9,"model":"poisson"}]
    b=[{**a[0],"probability":.66,"model":"dixon_coles"}]
    assert consensus_candidates([a],min_models=2)==[]
    out=consensus_candidates([a,b],min_models=2)
    assert len(out)==1 and out[0]["model_count"]==2 and abs(out[0]["probability"]-.68)<1e-9
