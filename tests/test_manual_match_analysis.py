from gool_bot2 import manual_match_analysis as mma


class M:
    def __init__(self, eid, home, away):
        self.provider_match_id=eid; self.home=home; self.away=away
        self.league="Test"; self.minute=0; self.home_score=0; self.away_score=0
        self.meta={"scheduled_start_ts": 1}


class FS:
    def scheduled_matches_for_day(self, day=0):
        return [M("a","Arsenal","Manchester City"),M("b","Arsenal Women","Chelsea Women"),M("c","Real Madrid","Barcelona")]
    def live_matches(self):
        return []


def test_find_today_matches_by_one_or_two_team_names(monkeypatch):
    monkeypatch.setattr(mma,"FlashscoreProvider",FS)
    assert [m.provider_match_id for m in mma.find_today_matches("Real")] == ["c"]
    assert mma.find_today_matches("Arsenal Manchester City")[0].provider_match_id == "a"
    assert len(mma.find_today_matches("Arsenal")) == 2


def test_match_choices_are_bounded():
    rows=[M(str(i),f"Home {i}",f"Away {i}") for i in range(10)]
    kb=mma.match_choices(rows)
    assert len(kb["inline_keyboard"]) == 6
    assert kb["inline_keyboard"][0][0]["callback_data"] == "ma:0"


class AnalysisFS(FS):
    def fetch_match_history(self, event_id, home, away, limit=10):
        return {"home_recent": [], "away_recent": [], "h2h": []}
    def _same_team(self, a, b):
        return a.casefold() == b.casefold()


def test_analyse_match_text_builds_v4_thought_without_name_error(monkeypatch):
    monkeypatch.setattr(mma, "FlashscoreProvider", AnalysisFS)
    monkeypatch.setattr(mma, "_analyse_fixtures", lambda fs, matches: ([{
        "primary_trend": {},
        "trends": [],
        "sample": 10,
        "quality": 1.0,
    }], {}))
    text = mma.analyse_match_text(M("a", "Norway", "Portugal"))
    assert "Мысль V4" in text
    assert "NO BET" in text
