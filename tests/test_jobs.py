from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import jobs
from app.config import load_settings
from app.db import Database, Snapshot
from app.scraper import ScrapeError


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


@pytest.fixture
def setup(env, tmp_path, monkeypatch):
    s = load_settings({**env, "DATA_DIR": str(tmp_path)})
    db = Database(s.db_path)
    db.init()
    results = []

    def fake_scrape(_s):
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return Snapshot(None, Decimal(r), "USD", "typical", "Air X", 1, datetime.now(timezone.utc))

    monkeypatch.setattr(jobs, "scrape", fake_scrape)
    return s, db, FakeTelegram(), results


def test_failure_alert_after_two_consecutive_failures(setup):
    s, db, tg, results = setup
    results += [ScrapeError("empty"), ScrapeError("empty"), ScrapeError("empty"), "100"]
    jobs.run_once(s, db, tg)
    assert tg.sent == [] and db.failure_streak()[0] == 1
    jobs.run_once(s, db, tg)
    assert len(tg.sent) == 1 and "FALLO" in tg.sent[0]
    jobs.run_once(s, db, tg)  # same outage -> no repeat
    assert len(tg.sent) == 1
    jobs.run_once(s, db, tg)  # recovery
    assert len(tg.sent) == 2 and "recuper" in tg.sent[1]
    assert db.failure_streak()[0] == 0 and db.last_success_at() is not None


def test_success_stores_snapshot_and_change_alert(setup):
    s, db, tg, results = setup
    results += ["100", "120"]
    jobs.run_once(s, db, tg)
    jobs.run_once(s, db, tg)
    assert [x.price for x in db.all_snapshots()] == [Decimal("100"), Decimal("120")]
    assert len(tg.sent) == 1 and "20.0%" in tg.sent[0]
