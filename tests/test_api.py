import importlib
import sys

import pytest
from fastapi.testclient import TestClient


def make_client(monkeypatch, tmp_path, env, **extra):
    for k, v in {**env, **extra, "DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    return main, TestClient(main.app)


def test_health_is_open_and_leaks_nothing(monkeypatch, tmp_path, env):
    _, c = make_client(monkeypatch, tmp_path, env, DASHBOARD_TOKEN="s3cret")
    r = c.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "last_success_at"}
    for leak in ("AAA", "BBB", "2099"):
        assert leak not in r.text


@pytest.mark.parametrize("path", ["/", "/api/snapshots"])
def test_token_required_when_set(monkeypatch, tmp_path, env, path):
    _, c = make_client(monkeypatch, tmp_path, env, DASHBOARD_TOKEN="s3cret")
    assert c.get(path).status_code == 403
    assert c.get(path, params={"token": "nope"}).status_code == 403
    assert c.get(path, params={"token": "s3cret"}).status_code == 200


def test_open_when_token_empty_and_dashboard_renders(monkeypatch, tmp_path, env):
    from datetime import datetime, timezone
    from decimal import Decimal
    from app.db import Snapshot

    main, c = make_client(monkeypatch, tmp_path, env, DASHBOARD_TOKEN="", PRICE_FLOOR="100",
                          REMINDER_DATES="2099-01-02", DEADLINE_DATE="2099-01-05")
    assert c.get("/").status_code == 200  # empty state
    main.db.insert_snapshot(Snapshot(None, Decimal("123.45"), "USD", "low", "Air X", 0,
                                     datetime.now(timezone.utc)))
    r = c.get("/")
    assert r.status_code == 200 and "123.45 USD" in r.text and "chart.umd" in r.text
    assert c.get("/api/snapshots").json()[0]["price"] == "123.45"
