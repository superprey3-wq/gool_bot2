from gool_bot2.multisport_parlay import build_sport_parlays, eligible_prematch_legs
from gool_bot2.multisport_parlay_card import render_multisport_parlay_card
from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS


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

def test_multisport_parlay_card_renders_png():
    rows = [_row("1"), _row("2", odd=1.8, strength=84)]
    parlay = build_sport_parlays(rows, "hockey")[0]

    png = render_multisport_parlay_card(parlay, "hockey")

    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


def test_multisport_parlay_card_is_delivered_once_and_persists_across_restart(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", "1")
    sent = []
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": sent.append((png, caption)) or 1)

    parlay = build_sport_parlays([_row("1"), _row("2", odd=1.8, strength=84)], "hockey")[0]
    worker = MultiSportSteamWorker(tmp_path)

    assert worker._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 1
    assert worker._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 0
    assert len(sent) == 1
    assert sent[0][0].startswith(b"\x89PNG")

    restarted = MultiSportSteamWorker(tmp_path)
    assert restarted._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 0
    assert len(sent) == 1

