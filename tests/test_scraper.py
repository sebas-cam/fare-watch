from app.scraper import extract_price_level


def test_price_level_from_text():
    html = '<div>Prices are currently <span class="x">high</span> for your search</div>'
    assert extract_price_level(html) == "high"
    assert extract_price_level("Prices are currently <b>low</b>") == "low"


def test_price_level_missing():
    assert extract_price_level("<html></html>") is None
