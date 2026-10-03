from gool_bot2.prematch_quality import prematch_evidence_quality


def profile(*, sample=8, periods=True, venue=0, h2h=0, sources=None):
    period = {
        "available": periods,
        "pair_sample": sample,
        "home": {"venue_matches": venue},
        "away": {"venue_matches": venue},
        "h2h": {"matches": h2h},
    }
    return {
        "first_half": dict(period),
        "second_half": dict(period),
        "full_match": {"available": True, "pair_sample": sample},
        "sources": list(sources or ["flashscore_h2h"]),
    }


def test_quality_v2_does_not_turn_sample_eight_into_one_hundred_percent():
    q = prematch_evidence_quality(profile(sample=8, periods=True, venue=3, h2h=2))
    assert .70 < q < .90


def test_quality_v2_rewards_deeper_multisource_evidence():
    weak = prematch_evidence_quality(
        profile(sample=8, periods=True, venue=2, h2h=1),
        source_coverage={"flashscore": 8},
    )
    strong = prematch_evidence_quality(
        profile(sample=16, periods=True, venue=5, h2h=5, sources=["flashscore_h2h", "fotmob", "365scores"]),
        source_coverage={"flashscore": 10, "fotmob_embedded": 8, "365scores": 8},
    )
    assert strong > weak
    assert strong >= .95
