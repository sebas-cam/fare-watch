"""SQLite persistence. One short-lived connection per operation keeps this
safe to call from both the scheduler thread and request handlers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    price         TEXT    NOT NULL,          -- Decimal stored as text, no float drift
    currency      TEXT    NOT NULL,
    current_price TEXT,                      -- Google's price level: low | typical | high | NULL
    airline       TEXT,
    stops         INTEGER,
    scraped_at    TEXT    NOT NULL           -- ISO-8601, UTC
);
CREATE INDEX IF NOT EXISTS idx_snapshots_scraped_at ON snapshots (scraped_at);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,                  -- floor | ceiling | change | reminder | deadline | failure | recovery
    key      TEXT NOT NULL,                  -- identity of the alert within its kind
    message  TEXT NOT NULL,
    sent_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_kind ON alerts_sent (kind, id);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ok       INTEGER NOT NULL,
    error    TEXT,
    ran_at   TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Snapshot:
    id: int | None
    price: Decimal
    currency: str
    current_price: str | None
    airline: str | None
    stops: int | None
    scraped_at: datetime

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "price": str(self.price),
            "currency": self.currency,
            "current_price": self.current_price,
            "airline": self.airline,
            "stops": self.stops,
            "scraped_at": self.scraped_at.isoformat(),
        }


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    # --- snapshots ---

    @staticmethod
    def _row_to_snapshot(r: sqlite3.Row) -> Snapshot:
        return Snapshot(
            id=r["id"],
            price=Decimal(r["price"]),
            currency=r["currency"],
            current_price=r["current_price"],
            airline=r["airline"],
            stops=r["stops"],
            scraped_at=datetime.fromisoformat(r["scraped_at"]),
        )

    def insert_snapshot(self, s: Snapshot) -> Snapshot:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO snapshots (price, currency, current_price, airline, stops, scraped_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(s.price), s.currency, s.current_price, s.airline, s.stops,
                 s.scraped_at.isoformat()),
            )
            return Snapshot(**{**s.__dict__, "id": cur.lastrowid})

    def latest_snapshots(self, n: int) -> list[Snapshot]:
        """Most recent first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM snapshots ORDER BY scraped_at DESC, id DESC LIMIT ?", (n,)
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def all_snapshots(self) -> list[Snapshot]:
        """Oldest first."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM snapshots ORDER BY scraped_at, id").fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    # --- alerts ---

    def last_alert_key(self, kind: str) -> str | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT key FROM alerts_sent WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
            ).fetchone()
        return r["key"] if r else None

    def alert_exists(self, kind: str, key: str) -> bool:
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM alerts_sent WHERE kind = ? AND key = ? LIMIT 1", (kind, key)
            ).fetchone()
        return r is not None

    def record_alert(self, kind: str, key: str, message: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO alerts_sent (kind, key, message, sent_at) VALUES (?, ?, ?, ?)",
                (kind, key, message, utcnow().isoformat()),
            )

    # --- scrape runs (healthcheck) ---

    def record_run(self, ok: bool, error: str | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO scrape_runs (ok, error, ran_at) VALUES (?, ?, ?)",
                (1 if ok else 0, error, utcnow().isoformat()),
            )
            return cur.lastrowid

    def failure_streak(self) -> tuple[int, int | None]:
        """(number of consecutive failed runs at the tail, id of the first one)."""
        with self._conn() as c:
            last_ok = c.execute("SELECT MAX(id) FROM scrape_runs WHERE ok = 1").fetchone()[0] or 0
            r = c.execute(
                "SELECT COUNT(*), MIN(id) FROM scrape_runs WHERE ok = 0 AND id > ?", (last_ok,)
            ).fetchone()
        return r[0], r[1]

    def last_success_at(self) -> datetime | None:
        with self._conn() as c:
            r = c.execute("SELECT MAX(ran_at) FROM scrape_runs WHERE ok = 1").fetchone()
        return datetime.fromisoformat(r[0]) if r and r[0] else None

    def last_run(self) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1").fetchone()
