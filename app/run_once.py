"""Run a single scrape (with alerts) outside the schedule.

    docker compose exec fare-watch python -m app.run_once
"""

from .jobs import run_once
from .main import db, settings, telegram

if __name__ == "__main__":
    run_once(settings, db, telegram)
