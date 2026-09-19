from datetime import date, time
from decimal import Decimal

import pytest

from app.config import ConfigError, load_settings


def test_defaults(env):
    s = load_settings(env)
    assert s.origin == "AAA" and s.destination == "BBB"
    assert s.depart_date == date(2099, 1, 1) and s.return_date == date(2099, 1, 15)
    assert s.passengers == 1 and isinstance(s.passengers, int)
    assert s.cabin_class == "economy" and s.currency == "USD"
    assert s.price_floor is None and s.price_ceiling is None
    assert s.change_threshold_pct == Decimal("5")
    assert s.reminder_dates == () and s.deadline_date is None
    assert s.scrape_times == (time(9), time(21))
    assert str(s.tz) == "UTC" and s.dashboard_token is None


def test_empty_return_date_is_one_way(env):
    env["RETURN_DATE"] = ""
    assert not load_settings(env).is_round_trip


def test_full_parse(env):
    env.update(
        PASSENGERS="2", PRICE_FLOOR="100.50", PRICE_CEILING="900", CHANGE_THRESHOLD_PCT="7.5",
        REMINDER_DATES="2099-01-03, 2099-01-02", DEADLINE_DATE="2099-01-10",
        SCRAPE_TIMES="6:30,18:00", TZ="Europe/Madrid", CABIN_CLASS="Business", CURRENCY="eur",
    )
    s = load_settings(env)
    assert s.price_floor == Decimal("100.50") and isinstance(s.price_floor, Decimal)
    assert s.reminder_dates == (date(2099, 1, 2), date(2099, 1, 3))
    assert s.scrape_times == (time(6, 30), time(18))
    assert s.cabin_class == "business" and str(s.tz) == "Europe/Madrid"


def test_missing_required_lists_all(env):
    del env["ORIGIN"], env["TELEGRAM_CHAT_ID"], env["RETURN_DATE"]
    with pytest.raises(ConfigError) as e:
        load_settings(env)
    msg = str(e.value)
    assert "ORIGIN" in msg and "TELEGRAM_CHAT_ID" in msg and "RETURN_DATE" in msg


@pytest.mark.parametrize("key,value", [
    ("ORIGIN", "AA1"), ("DEPART_DATE", "01/01/2099"), ("RETURN_DATE", "2098-12-31"),
    ("PASSENGERS", "0"), ("PASSENGERS", "x"), ("CABIN_CLASS", "coach"), ("PRICE_FLOOR", "abc"),
    ("SCRAPE_TIMES", "25:00"), ("TZ", "Mars/Olympus"), ("REMINDER_DATES", "2099-13-01"),
])
def test_invalid_values(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError, match=key):
        load_settings(env)


def test_floor_must_be_below_ceiling(env):
    env.update(PRICE_FLOOR="500", PRICE_CEILING="400")
    with pytest.raises(ConfigError, match="PRICE_FLOOR"):
        load_settings(env)
