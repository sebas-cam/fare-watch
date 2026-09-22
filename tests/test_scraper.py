from app.scraper import extract_price_level


def test_price_level_from_text():
    html = '<div>Prices are currently <span class="x">high</span> for your search</div>'
    assert extract_price_level(html) == "high"
    assert extract_price_level("Prices are currently <b>low</b>") == "low"


def test_price_level_missing():
    assert extract_price_level("<html></html>") is None


def test_consent_wall_detected():
    from app.scraper import is_consent_wall

    assert is_consent_wall("window['ppConfig'] = {productName: 'ConsentUi', deleteIsEnforced: true}")
    assert not is_consent_wall("<html><div>Prices are currently low</div></html>")
