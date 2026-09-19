"""The scheduled job: scrape → store → alert, plus calendar and health checks."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import alerts
from .config import Settings
from .db import Database
from .scraper import ScrapeError, scrape
from .telegram import Telegram

log = logging.getLogger(__name__)

FAILURE_STREAK_ALERT = 2
_run_lock = threading.Lock()


def run_once(s: Settings, db: Database, tg: Telegram) -> None:
    if not _run_lock.acquire(blocking=False):
        log.warning("A scrape is already running; skipping this trigger")
        return
    try:
        _run(s, db, tg)
    finally:
        _run_lock.release()


def _run(s: Settings, db: Database, tg: Telegram) -> None:
    log.info("Scrape started")
    previous = next(iter(db.latest_snapshots(1)), None)
    pending: list[alerts.Alert] = []

    try:
        snap = scrape(s)
    except Exception as e:
        if isinstance(e, ScrapeError):
            error = str(e)
            log.error("Scrape failed: %s", error)
        else:  # unexpected bug: still counts as a failed run
            error = f"{type(e).__name__}: {e}"
            log.exception("Unexpected error during scrape")
        db.record_run(ok=False, error=error)
        streak, first_id = db.failure_streak()
        if streak >= FAILURE_STREAK_ALERT and first_id is not None:
            pending.append(alerts.failure_alert(streak, first_id, error))
        latest = previous
    else:
        streak, _ = db.failure_streak()
        snap = db.insert_snapshot(snap)
        db.record_run(ok=True)
        log.info(
            "Snapshot #%s: %s %s (%s, %s stops, level=%s)",
            snap.id, snap.price, snap.currency, snap.airline, snap.stops, snap.current_price,
        )
        if streak >= FAILURE_STREAK_ALERT:
            pending.append(alerts.Alert(
                "recovery", str(snap.id),
                f"✅ El scraper se recuperó tras {streak} corridas fallidas.",
            ))
        pending += alerts.evaluate_price(s, snap, previous)
        latest = snap

    today = datetime.now(s.tz).date()
    pending += alerts.evaluate_calendar(s, today, latest)
    alerts.dispatch(db, tg, pending)
    log.info("Scrape finished")


def build_scheduler(s: Settings, db: Database, tg: Telegram) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=s.tz)
    for t in s.scrape_times:
        sched.add_job(
            run_once,
            CronTrigger(hour=t.hour, minute=t.minute, timezone=s.tz),
            args=(s, db, tg),
            id=f"scrape-{t:%H%M}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=15 * 60,
        )
    return sched
