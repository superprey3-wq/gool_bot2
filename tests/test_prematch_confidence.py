from gool_bot2.prematch_confidence import select_confident_prematch_rows


def row(
    event_id,
    *,
    p=.70,
    sample=10,
    agreement=.78,
    separation=.04,
    rank=.45,
    brain=.72,
    quality=1.0,
):
    return {
        "match": type("M", (), {"provider_match_id": event_id})(),
        "sample": sample,
        "quality": quality,
        "brain_score": brain,
        "primary_trend": {
            "name": "FT_OVER_2.5",
            "probability": p,
            "sample": sample,
            "agreement": agreement,
            "separation": separation,
            "rank_score": rank,
        },
    }


def test_shortlist_rejects_weak_profiles_and_keeps_strong():
    rows = [
        row("strong"),
        row("small", sample=6),
        row("lowp", p=.61),
        row("ambiguous", separation=.016),
        row("disagree", agreement=.66),
    ]
    selected, stats = select_confident_prematch_rows(rows, max_rows=120)
    assert [x["match"].provider_match_id for x in selected] == ["strong"]
    assert stats["input"] == 5
    assert stats["selected"] == 1
    assert stats["rejected_sample"] == 1
    assert stats["rejected_probability"] == 1
    assert stats["rejected_separation"] == 1
    assert stats["rejected_agreement"] == 1


def test_shortlist_caps_and_ranks_by_confidence():
    rows = [
        row("a", rank=.50, separation=.05),
        row("b", rank=.58, separation=.03),
        row("c", rank=.54, separation=.06),
    ]
    selected, stats = select_confident_prematch_rows(rows, max_rows=2)
    assert [x["match"].provider_match_id for x in selected] == ["b", "c"]
    assert stats["qualified"] == 3
    assert stats["selected"] == 2
    assert stats["cap"] == 2


def test_shortlist_quality_v2_gate_is_realistic():
    selected, stats = select_confident_prematch_rows([
        row("good", quality=.64),
        row("thin", quality=.60),
    ], max_rows=120)
    assert [x["match"].provider_match_id for x in selected] == ["good"]
    assert stats["rejected_quality"] == 1
    assert stats["min_quality"] == .62
