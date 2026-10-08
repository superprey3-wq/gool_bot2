from gool_bot2.multisport_parlay import build_sport_parlays, eligible_prematch_legs
from gool_bot2.multisport_parlay_card import render_multisport_parlay_card
from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS


def _row(event_id, odd=1.49, strength=80, probability=0.72, sport="hockey", family="match_total"):
    return {
        "entry_id": f"{sport}:{event_id}:{family}",
        "event_id": event_id,
        "flashscore_event_id": event_id,
        "sport": sport,
        "phase": "PREMATCH",
        "result": "pending",
        "home": f"H{event_id}",
        "away": f"A{event_id}",
        "selection": "ТБ 5.5",
        "direction": "over",
        "line": 5.5,
        "odd": odd,
        "strength": strength,
        "fair_probability": probability,
        "edge": 0.08,
        "market_family": family,
        "scope": "FULL_MATCH",
    }


def test_parlay_uses_prematch_only_and_distinct_events(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MIN_STRENGTH", "70")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MIN_FAIR_PROBABILITY", "0.55")
    rows = [
        _row("1", family="match_total"),
        _row("1", odd=1.9, strength=78, family="handicap"),
        _row("2", odd=1.49, strength=82),
        _row("3", odd=1.48, strength=76),
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
    rows = [_row("1"), _row("2", odd=1.49, strength=84)]
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

    parlay = build_sport_parlays([_row("1"), _row("2", odd=1.49, strength=84)], "hockey")[0]
    worker = MultiSportSteamWorker(tmp_path)

    assert worker._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 1
    assert worker._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 0
    assert len(sent) == 1
    assert sent[0][0].startswith(b"\x89PNG")

    restarted = MultiSportSteamWorker(tmp_path)
    assert restarted._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 0
    assert len(sent) == 1

def test_three_multisport_parlays_do_not_reuse_same_match(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_RESULTS", "3")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_EVENT_REUSE", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MIN_COMBINED_ODD", "2.20")
    rows = [
        _row(str(idx), odd=1.49, strength=100 - idx, probability=0.80 - idx * 0.01)
        for idx in range(1, 7)
    ]

    parlays = build_sport_parlays(rows, "hockey")

    assert len(parlays) == 3
    all_ids = [leg["event_id"] for parlay in parlays for leg in parlay["legs"]]
    assert len(all_ids) == len(set(all_ids))


def test_parlay_logo_metadata_is_enriched_from_flashscore(tmp_path):
    worker = MultiSportSteamWorker(tmp_path)
    row = {
        **_row("1"),
        "flashscore_event_id": "FSLOGO01",
        "home_logo_file": "",
        "away_logo_file": "",
        "home_team_id": "",
        "away_team_id": "",
    }
    fs = [{
        "flashscore_event_id": "FSLOGO01",
        "home": row["home"],
        "away": row["away"],
        "home_logo_file": "home-logo.png",
        "away_logo_file": "away-logo.png",
        "home_team_id": "HOME1",
        "away_team_id": "AWAY1",
        "home_team_slug": "home-one",
        "away_team_slug": "away-one",
    }]

    enriched = worker._enrich_parlay_source_rows([row], fs, SPORTS["hockey"])

    assert enriched[0]["home_logo_file"] == "home-logo.png"
    assert enriched[0]["away_logo_file"] == "away-logo.png"
    assert enriched[0]["home_team_id"] == "HOME1"
    assert enriched[0]["away_team_id"] == "AWAY1"

def test_two_leg_parlay_card_has_footer_below_second_leg():
    from io import BytesIO
    from PIL import Image

    parlay = build_sport_parlays([
        _row("1", sport="basketball"),
        _row("2", odd=1.49, strength=84, sport="basketball"),
    ], "basketball")[0]
    png = render_multisport_parlay_card(parlay, "basketball")
    image = Image.open(BytesIO(png))

    # Old renderer was 700px high and the footer overlapped leg #2.
    assert image.height >= 800
    assert image.width == 1080



def test_parlay_drops_started_and_imminent_matches(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MIN_LEAD_SECONDS", "180")
    now = 1_800_000_000.0
    rows = [
        {**_row("started"), "start_ts": now - 30},
        {**_row("soon"), "start_ts": now + 120},
        {**_row("future1"), "start_ts": now + 600},
        {**_row("future2"), "start_ts": now + 900},
    ]

    legs = eligible_prematch_legs(rows, "hockey", now_ts=now)

    assert {leg["event_id"] for leg in legs} == {"future1", "future2"}
    parlays = build_sport_parlays(rows, "hockey", now_ts=now)
    assert parlays
    assert all(
        leg["event_id"] not in {"started", "soon"}
        for parlay in parlays
        for leg in parlay["legs"]
    )


def test_parlay_prefers_safer_alternative_over_higher_odd_single(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_ODD", "1.90")
    rows = [
        {
            **_row("1", odd=1.83, probability=0.70, strength=87),
            "entry_id": "single-1",
            "selection": "1-я половина: ТМ 118.5",
            "parlay_safe": False,
        },
        {
            **_row("1", odd=1.55, probability=0.79, strength=84),
            "entry_id": "safe-1",
            "selection": "1-я половина: ТМ 122.5",
            "parlay_safe": True,
        },
        _row("2", odd=1.49, probability=0.76, strength=83),
    ]

    legs = eligible_prematch_legs(rows, "hockey", now_ts=0)

    first = next(leg for leg in legs if leg["event_id"] == "1")
    assert first["entry_id"] == "safe-1"
    assert first["selection"] == "1-я половина: ТМ 122.5"
    assert first["odd"] == 1.55



def test_parlay_delivery_rejects_same_match_when_market_changes_across_restart(tmp_path, monkeypatch):
    import copy
    import gool_bot2.xbet_multisport_steam as steam

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_DAILY_PER_SPORT", "3")
    sent = []
    monkeypatch.setattr(steam, "render_multisport_parlay_card", lambda *_args, **_kwargs: b"png")
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": sent.append((png, caption)) or 1)

    first = build_sport_parlays([_row("FS1"), _row("FS2", odd=1.49)], "hockey")[0]
    worker = MultiSportSteamWorker(tmp_path)
    assert worker._deliver_new_parlays(SPORTS["hockey"], [first]) == 1

    changed = copy.deepcopy(first)
    changed["legs"][0]["entry_id"] = "hockey:parlay-safe:FS1:FULL_MATCH:home_total:ИТМ1 2.5"
    changed["legs"][0]["market_family"] = "home_total"
    changed["legs"][0]["selection"] = "ИТМ1 2.5"
    changed["legs"][0]["direction"] = "under"
    changed["legs"][0]["line"] = 2.5
    changed["legs"][1]["event_id"] = "FS3"
    changed["legs"][1]["flashscore_event_id"] = "FS3"
    changed["legs"][1]["entry_id"] = "hockey:parlay-safe:FS3:FULL_MATCH:match_total:ТМ 6.5"
    changed["legs"][1]["home"] = "HFS3"
    changed["legs"][1]["away"] = "AFS3"

    restarted = MultiSportSteamWorker(tmp_path)
    assert restarted._deliver_new_parlays(SPORTS["hockey"], [changed]) == 0
    assert len(sent) == 1


def test_parlay_delivery_migrates_old_signature_only_state_and_blocks_reused_fixture(tmp_path, monkeypatch):
    import json
    import gool_bot2.xbet_multisport_steam as steam

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_DAILY_PER_SPORT", "3")
    worker = MultiSportSteamWorker(tmp_path)
    worker.parlay_delivery_path.parent.mkdir(parents=True, exist_ok=True)
    worker.parlay_delivery_path.write_text(json.dumps({
        "signatures": [
            "hockey|hockey:parlay-safe:BOOKOLD1:FULL_MATCH:match_total:ТМ 5.5|"
            "hockey:parlay-safe:BOOKOLD2:FULL_MATCH:match_total:ТБ 4.5"
        ]
    }, ensure_ascii=False), encoding="utf-8")

    sent = []
    monkeypatch.setattr(steam, "render_multisport_parlay_card", lambda *_args, **_kwargs: b"png")
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": sent.append(1) or 1)

    reused = {**_row("FS-CANON-1"), "event_id": "BOOKOLD1", "flashscore_event_id": "FS-CANON-1"}
    fresh = {**_row("FS-CANON-NEW"), "event_id": "BOOKNEW", "flashscore_event_id": "FS-CANON-NEW"}
    candidate = build_sport_parlays([reused, fresh], "hockey")[0]
    assert worker._deliver_new_parlays(SPORTS["hockey"], [candidate]) == 0
    assert sent == []


def test_parlay_delivery_has_daily_per_sport_cap(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_DAILY_PER_SPORT", "1")
    sent = []
    monkeypatch.setattr(steam, "render_multisport_parlay_card", lambda *_args, **_kwargs: b"png")
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": sent.append(1) or 1)

    worker = MultiSportSteamWorker(tmp_path)
    first = build_sport_parlays([_row("A"), _row("B", odd=1.49)], "hockey")[0]
    second = build_sport_parlays([_row("C"), _row("D", odd=1.49)], "hockey")[0]

    assert worker._deliver_new_parlays(SPORTS["hockey"], [first]) == 1
    assert worker._deliver_new_parlays(SPORTS["hockey"], [second]) == 0
    assert len(sent) == 1


def test_delivered_parlay_is_journaled_and_settled(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam
    from gool_bot2.multisport_journal import load_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_MAX_DAILY_PER_SPORT", "1")
    monkeypatch.setattr(steam, "render_multisport_parlay_card", lambda *_args, **_kwargs: b"png")
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": 1)

    worker = MultiSportSteamWorker(tmp_path)
    parlay = build_sport_parlays([_row("SET1"), _row("SET2", odd=1.49)], "hockey")[0]
    assert worker._deliver_new_parlays(SPORTS["hockey"], [parlay]) == 1

    rows = load_journal(worker.journal_path)
    parents = [row for row in rows if row.get("origin") == "multisport_parlay"]
    assert len(parents) == 1
    assert parents[0]["result"] == "pending"
    assert len(parents[0]["legs"]) == 2

    states = {
        "SET1": {"coarse_status": "3", "score": [4, 2]},
        "SET2": {"coarse_status": "3", "score": [3, 3]},
    }
    assert worker._settle(SPORTS["hockey"], states) == 1

    settled = next(row for row in load_journal(worker.journal_path) if row.get("origin") == "multisport_parlay")
    assert settled["result"] == "won"
    assert settled["profit_units"] > 0
    assert all(leg.get("result") == "won" for leg in settled["legs"])



def test_default_delivery_cap_allows_three_distinct_parlays(tmp_path, monkeypatch):
    import gool_bot2.xbet_multisport_steam as steam

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "active")
    monkeypatch.setenv("XBET_MULTISPORT_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("GOOL_MULTISPORT_PARLAY_CARDS_ENABLED", "1")
    monkeypatch.delenv("GOOL_MULTISPORT_PARLAY_MAX_DAILY_PER_SPORT", raising=False)
    sent = []
    monkeypatch.setattr(steam, "render_multisport_parlay_card", lambda *_args, **_kwargs: b"png")
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": sent.append(1) or 1)

    rows = [
        _row(str(idx), odd=1.49, strength=100 - idx, probability=0.80 - idx * 0.01)
        for idx in range(1, 7)
    ]
    parlays = build_sport_parlays(rows, "hockey")
    assert len(parlays) == 3

    worker = MultiSportSteamWorker(tmp_path)
    assert worker._deliver_new_parlays(SPORTS["hockey"], parlays) == 3
    assert len(sent) == 3

    all_ids = [leg["event_id"] for parlay in parlays for leg in parlay["legs"]]
    assert len(all_ids) == len(set(all_ids))


def test_default_parlay_leg_band_is_1_45_to_1_50(monkeypatch):
    monkeypatch.delenv("GOOL_MULTISPORT_PARLAY_MIN_ODD", raising=False)
    monkeypatch.delenv("GOOL_MULTISPORT_PARLAY_MAX_ODD", raising=False)
    rows = [
        _row("low", odd=1.44, probability=0.90, strength=99),
        _row("ok1", odd=1.45, probability=0.80, strength=90),
        _row("ok2", odd=1.50, probability=0.79, strength=89),
        _row("high", odd=1.51, probability=0.95, strength=100),
    ]
    legs = eligible_prematch_legs(rows, "hockey", now_ts=0)
    assert {leg["event_id"] for leg in legs} == {"ok1", "ok2"}
