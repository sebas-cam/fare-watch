"""Configuration loaded exclusively from environment variables.

Everything is validated at import-free call time via `load_settings()`; the
app refuses to start with a single message listing every problem found.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CABIN_CLASSES = ("economy", "premium-economy", "business", "first")
_IATA = re.compile(r"^[A-Z]{3}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class ConfigError(Exception):
    """Raised when one or more environment variables are missing or invalid."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(
            "Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors)
        )


@dataclass(frozen=True)
class Settings:
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    telegram_bot_token: str
    telegram_chat_id: str

    passengers: int
    cabin_class: str
    currency: str
    price_floor: Decimal | None
    price_ceiling: Decimal | None
    change_threshold_pct: Decimal
    reminder_dates: tuple[date, ...]
    deadline_date: date | None
    scrape_times: tuple[time, ...]
    tz: ZoneInfo
    dashboard_token: str | None

    data_dir: str

    @property
    def is_round_trip(self) -> bool:
        return self.return_date is not None

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "fare-watch.db")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    errors: list[str] = []

    def required(name: str, *, allow_empty: bool = False) -> str:
        if name not in env:
            errors.append(f"{name} is required but not set")
            return ""
        value = env[name].strip()
        if not value and not allow_empty:
            errors.append(f"{name} is required but empty")
        return value

    def optional(name: str, default: str = "") -> str:
        return env.get(name, default).strip()

    def parse_date(name: str, raw: str) -> date | None:
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            errors.append(f"{name}={raw!r} is not a valid date (expected YYYY-MM-DD)")
            return None

    def parse_decimal(name: str, raw: str, *, positive: bool = True) -> Decimal | None:
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            errors.append(f"{name}={raw!r} is not a valid number")
            return None
        if not value.is_finite() or (positive and value <= 0):
            errors.append(f"{name}={raw!r} must be a positive number")
            return None
        return value

    def parse_iata(name: str, raw: str) -> str:
        value = raw.upper()
        if raw and not _IATA.match(value):
            errors.append(f"{name}={raw!r} must be a 3-letter IATA code")
        return value

    # --- required ---
    origin = parse_iata("ORIGIN", required("ORIGIN"))
    destination = parse_iata("DESTINATION", required("DESTINATION"))
    depart_raw = required("DEPART_DATE")
    depart_date = parse_date("DEPART_DATE", depart_raw)
    return_date = parse_date("RETURN_DATE", required("RETURN_DATE", allow_empty=True))
    bot_token = required("TELEGRAM_BOT_TOKEN")
    chat_id = required("TELEGRAM_CHAT_ID")

    if origin and origin == destination:
        errors.append("ORIGIN and DESTINATION must be different")
    if depart_date and return_date and return_date < depart_date:
        errors.append("RETURN_DATE must be on or after DEPART_DATE")

    # --- optional ---
    passengers_raw = optional("PASSENGERS", "1")
    passengers = 1
    try:
        passengers = int(passengers_raw)
        if not 1 <= passengers <= 9:
            raise ValueError
    except ValueError:
        errors.append(f"PASSENGERS={passengers_raw!r} must be an integer between 1 and 9")

    cabin_class = optional("CABIN_CLASS", "economy").lower() or "economy"
    if cabin_class not in CABIN_CLASSES:
        errors.append(
            f"CABIN_CLASS={cabin_class!r} must be one of: {', '.join(CABIN_CLASSES)}"
        )

    currency = (optional("CURRENCY", "USD") or "USD").upper()
    if not _CURRENCY.match(currency):
        errors.append(f"CURRENCY={currency!r} must be a 3-letter ISO 4217 code")

    price_floor = parse_decimal("PRICE_FLOOR", optional("PRICE_FLOOR"))
    price_ceiling = parse_decimal("PRICE_CEILING", optional("PRICE_CEILING"))
    if price_floor is not None and price_ceiling is not None and price_floor >= price_ceiling:
        errors.append("PRICE_FLOOR must be lower than PRICE_CEILING")

    threshold = parse_decimal(
        "CHANGE_THRESHOLD_PCT", optional("CHANGE_THRESHOLD_PCT", "5") or "5"
    )

    reminder_dates: list[date] = []
    for i, part in enumerate(optional("REMINDER_DATES").split(",")):
        part = part.strip()
        if part:
            d = parse_date(f"REMINDER_DATES[{i}]", part)
            if d:
                reminder_dates.append(d)

    deadline_date = parse_date("DEADLINE_DATE", optional("DEADLINE_DATE"))

    scrape_times: list[time] = []
    scrape_raw = optional("SCRAPE_TIMES", "09:00,21:00") or "09:00,21:00"
    for part in scrape_raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hh, mm = part.split(":")
            scrape_times.append(time(int(hh), int(mm)))
        except ValueError:
            errors.append(f"SCRAPE_TIMES entry {part!r} is not a valid HH:MM time")
    if not scrape_times and not any("SCRAPE_TIMES" in e for e in errors):
        errors.append("SCRAPE_TIMES must contain at least one HH:MM time")

    tz_name = optional("TZ", "UTC") or "UTC"
    tz = ZoneInfo("UTC")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"TZ={tz_name!r} is not a valid IANA timezone (e.g. Europe/Madrid)")

    dashboard_token = optional("DASHBOARD_TOKEN") or None

    if errors:
        raise ConfigError(errors)

    assert depart_date is not None and threshold is not None
    return Settings(
        origin=origin,
        destination=destination,
        depart_date=depart_date,
        return_date=return_date,
        telegram_bot_token=bot_token,
        telegram_chat_id=chat_id,
        passengers=passengers,
        cabin_class=cabin_class,
        currency=currency,
        price_floor=price_floor,
        price_ceiling=price_ceiling,
        change_threshold_pct=threshold,
        reminder_dates=tuple(sorted(set(reminder_dates))),
        deadline_date=deadline_date,
        scrape_times=tuple(sorted(set(scrape_times))),
        tz=tz,
        dashboard_token=dashboard_token,
        data_dir=optional("DATA_DIR", "/app/data") or "/app/data",
    )
