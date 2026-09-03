from gool_bot2 import multi_product


def test_multi_analysis_escapes_comparison_signs_for_telegram_html(monkeypatch):
    monkeypatch.setattr(
        multi_product,
        "_analysis_text",
        lambda *args, **kwargs: "PRICE<1.40 · RATING<62 · <b>GOOL MULTI</b>",
    )

    text = multi_product._analysis_text_safe()

    assert "PRICE&lt;1.40" in text
    assert "RATING&lt;62" in text
    assert "<b>GOOL MULTI</b>" in text
    assert "PRICE<1.40" not in text
    assert "RATING<62" not in text
