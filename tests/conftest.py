import pytest

BASE_ENV = {
    "ORIGIN": "AAA",
    "DESTINATION": "BBB",
    "DEPART_DATE": "2099-01-01",
    "RETURN_DATE": "2099-01-15",
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHAT_ID": "1",
}


@pytest.fixture
def env():
    return dict(BASE_ENV)
