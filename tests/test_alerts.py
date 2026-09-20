from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import alerts
from app.config import load_settings
from app.db import Database, Snapshot


def snap(price, id=1, level=None):
    return Snapshot(id, Decimal(price), "USD", level, "Air X", 0, datetime(2099, 1, 1, tzinfo=timezone.utc))


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.init()
    return d


def kinds(xs):
    return sorted(a.kind for a in xs)


def test_floor_ceiling_disabled_by_default(env):
    s = load_settings(env)
    assert alerts.evaluate_price(s, snap("1"), None) == []


def test_floor_and_ceiling(env):
    s = load_settings({**env, "PRICE_FLOOR": "100", "PRICE_CEILING": "200"})
    assert kinds(alerts.evaluate_price(s, snap("99"), None)) == ["floor"]
    assert kinds(alerts.evaluate_price(s, snap("201"), None)) == ["ceiling"]
    assert alerts.evaluate_price(s, snap("100"), None) == []


def test_thresholds_are_per_passenger(env):
    s = load_settings({**env, "PASSENGERS": "3", "PRICE_FLOOR": "850", "PRICE_CEILING": "1000"})
    assert s.price_floor_total == Decimal("2550") and s.price_ceiling_total == Decimal("3000")
    assert alerts.evaluate_price(s, snap("2700"), None) == []          # inside the band
    assert kinds(alerts.evaluate_price(s, snap("2549"), None)) == ["floor"]
    assert kinds(alerts.evaluate_price(s, snap("3001"), None)) == ["ceiling"]


def test_messages_show_total_and_per_passenger(env):
    s = load_settings({**env, "PASSENGERS": "3", "PRICE_FLOOR": "850"})
    msg = alerts.evaluate_price(s, snap("2400"), None)[0].message
    assert "Ahora: 2,400 USD (800 USD/pax)" in msg
    assert "Piso: 2,550 USD (850 USD x 3 pax)" in msg


def test_single_passenger_messages_stay_plain(env):
    s = load_settings({**env, "PRICE_FLOOR": "850"})
    msg = alerts.evaluate_price(s, snap("800"), None)[0].message
    assert "Ahora: 800 USD\nPiso: 850 USD" in msg and "/pax" not in msg


def test_change_threshold_is_strict(env):
    s = load_settings(env)  # 5%
    assert alerts.evaluate_price(s, snap("105", 2), snap("100")) == []
    assert kinds(alerts.evaluate_price(s, snap("105.01", 2), snap("100"))) == ["change"]
    assert kinds(alerts.evaluate_price(s, snap("94", 2), snap("100"))) == ["change"]


def test_calendar(env):
    s = load_settings({**env, "REMINDER_DATES": "2099-01-02", "DEADLINE_DATE": "2099-01-05"})
    assert kinds(alerts.evaluate_calendar(s, date(2099, 1, 2), None)) == ["reminder"]
    assert kinds(alerts.evaluate_calendar(s, date(2099, 1, 5), snap("1"))) == ["deadline"]
    assert alerts.evaluate_calendar(s, date(2099, 1, 3), None) == []


def test_dedupe_same_alert_twice_in_a_row(env, db):
    s = load_settings({**env, "PRICE_FLOOR": "100"})
    tg = FakeTelegram()
    alerts.dispatch(db, tg, alerts.evaluate_price(s, snap("90", 1), None))
    alerts.dispatch(db, tg, alerts.evaluate_price(s, snap("90", 2), None))
    assert len(tg.sent) == 1
    alerts.dispatch(db, tg, alerts.evaluate_price(s, snap("85", 3), None))
    assert len(tg.sent) == 2


def test_reminder_sent_once_per_date(env, db):
    s = load_settings({**env, "REMINDER_DATES": "2099-01-02"})
    tg = FakeTelegram()
    for _ in range(3):  # e.g. two scrapes that day + a restart
        alerts.dispatch(db, tg, alerts.evaluate_calendar(s, date(2099, 1, 2), None))
    assert len(tg.sent) == 1


def test_telegram_failure_is_retried(env, db):
    class Broken:
        def send(self, text):
            raise alerts.TelegramError("boom")

    a = [alerts.Alert("floor", "90", "x")]
    assert alerts.dispatch(db, Broken(), a) == 0
    tg = FakeTelegram()
    assert alerts.dispatch(db, tg, a) == 1
