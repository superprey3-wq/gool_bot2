from gool_bot2.multisport_parlay import build_sport_parlays, eligible_prematch_legs


def _row(event_id, odd=1.7, strength=80, probability=0.64, sport="hockey", family="match_total"):
    return {
        "entry_id": f"{sport}:{event_id}:{family}",
        "event_id": event_id,
        "sport": sport,
        "phase": "PREMATCH",
        "result": "pending",
        "home": f"H{event_id}",
        "away": f"A{event_id}",
        "selection": "ТБ 5.5",
        "odd": odd,
        "strength": strength,
        "fair_probability": probability,
        "market_family": family,
        "scope": "FULL_MATCH",
    }


def test_parlay_uses_prematch_only_and_distinct_events(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MIN_STRENGTH", "70")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MIN_FAIR_PROBABILITY", "0.55")
    rows = [
        _row("1", family="match_total"),
        _row("1", odd=1.9, strength=78, family="handicap"),
        _row("2", odd=1.65, strength=82),
        _row("3", odd=1.55, strength=76),
        {**_row("4"), "phase": "LIVE"},
        {**_row("5"), "sport": "basketball"},
    ]
    legs = eligible_prematch_legs(rows, "hockey")
    assert len([x for x in legs if x["event_id"] == "1"]) == 1
    parlays = build_sport_parlays(rows, "hockey")
    assert parlays
    for parlay in parlays:
        ids = [leg["event_id"] for leg in parlay["legs"]]
        assert len(ids) == len(set(ids))
        assert all(leg["sport"] == "hockey" for leg in parlay["legs"])


def test_parlay_requires_two_confirmed_legs():
    assert build_sport_parlays([_row("1")], "hockey") == []
