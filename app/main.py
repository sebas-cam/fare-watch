"""FastAPI app: dashboard, JSON API, healthcheck, and the in-process scheduler."""

from __future__ import annotations

import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .alerts import fmt_money, per_passenger, route_label
from .config import ConfigError, Settings, load_settings
from .db import Database
from .jobs import build_scheduler
from .telegram import Telegram

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("primp").setLevel(logging.WARNING)
log = logging.getLogger("fare_watch")

try:
    settings: Settings = load_settings()
except ConfigError as e:
    log.critical("%s", e)
    sys.exit(1)

os.makedirs(settings.data_dir, exist_ok=True)
db = Database(settings.db_path)
db.init()
telegram = Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["money"] = fmt_money
templates.env.filters["localtime"] = lambda dt: dt.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M")


@asynccontextmanager
async def lifespan(_: FastAPI):
    sched = build_scheduler(settings, db, telegram)
    sched.start()
    for job in sched.get_jobs():
        log.info("Scheduled %s, next run %s", job.id, job.next_run_time)
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="fare-watch", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def require_token(token: str | None = Query(default=None)) -> None:
    expected = settings.dashboard_token
    if expected is None:
        return
    if token is None or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/health")
def health() -> dict:
    # Deliberately exposes nothing about the route, dates or prices.
    last_success = db.last_success_at()
    last_run = db.last_run()
    streak, _ = db.failure_streak()
    if last_run is None:
        status = "starting"
    elif streak >= 2:
        status = "failing"
    elif streak == 1:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "last_success_at": last_success.isoformat() if last_success else None,
    }


@app.get("/api/snapshots", dependencies=[Depends(require_token)])
def api_snapshots() -> list[dict]:
    return [s.to_json() for s in db.all_snapshots()]


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_token)])
def dashboard(request: Request):
    snaps = db.all_snapshots()
    tz = settings.tz
    today = datetime.now(tz).date()
    prices = [s.price for s in snaps]
    latest = snaps[-1] if snaps else None

    points = [
        {
            # Wall-clock time in TZ, no offset: the browser plots it as-is.
            "x": s.scraped_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S"),
            "y": float(s.price),
            "level": s.current_price,
            "airline": s.airline,
            "stops": s.stops,
        }
        for s in snaps
    ]
    markers = [{"date": d.isoformat(), "label": "Recordatorio", "kind": "reminder"}
               for d in settings.reminder_dates]
    if settings.deadline_date:
        markers.append({"date": settings.deadline_date.isoformat(), "label": "Deadline", "kind": "deadline"})

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "route": route_label(settings),
            "currency": settings.currency,
            "cabin": settings.cabin_class,
            "passengers": settings.passengers,
            "tz": str(tz),
            "latest": latest,
            "latest_per_pax": (
                per_passenger(latest.price, settings.passengers) if latest and settings.passengers > 1 else None
            ),
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "days_left": (settings.deadline_date - today).days if settings.deadline_date else None,
            "deadline": settings.deadline_date,
            "snapshots": [s for s in reversed(snaps)],
            "chart": {
                "points": points,
                # Thresholds are configured per passenger; the chart plots party totals.
                "floor": float(settings.price_floor_total) if settings.price_floor is not None else None,
                "ceiling": float(settings.price_ceiling_total) if settings.price_ceiling is not None else None,
                "markers": markers,
                "currency": settings.currency,
            },
        },
    )
