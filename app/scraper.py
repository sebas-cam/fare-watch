"""Google Flights scraping via fast-flights v3.

fast-flights v3 dropped `Result.current_price` (it existed in v2), so the
price level (low / typical / high) is recovered here from the same HTML that
`get_flights()` parses. The HTML is captured with a `FetchIntegration`, which
also lets us log/save the raw response when the parse comes back empty.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from fast_flights import FlightQuery, Passengers, create_query, get_flights
from fast_flights.fetcher import URL
from fast_flights.integrations.base import FetchIntegration
from fast_flights.querying import Query
from primp import Client

from .config import Settings
from .db import Snapshot, utcnow

log = logging.getLogger(__name__)

RAW_DIR_NAME = "raw_responses"
RAW_KEEP = 10
RAW_LOG_CHARS = 2000

_LEVEL_TEXT = re.compile(
    r"Prices are currently\s*(?:<[^>]*>\s*)*(low|typical|high)\b", re.IGNORECASE
)


class ScrapeError(Exception):
    """The scrape failed or returned unusable (empty) data."""


# Pre-accepted consent cookies. From EU IPs Google otherwise answers with the
# consent wall (consent.google.com, "ConsentUi") instead of the flights page.
_CONSENT_COOKIES = {
    "CONSENT": "YES+cb",
    "SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiAo_CmBg",
}


class ConsentWallError(ScrapeError):
    """Google served its cookie-consent page instead of results."""


def is_consent_wall(html: str) -> bool:
    return "ConsentUi" in html[:5000] or "consent.google.com" in html[:5000]


class _CapturingFetcher(FetchIntegration):
    """fast-flights' default fetch plus consent cookies; keeps the HTML."""

    def __init__(self) -> None:
        self.html: str | None = None

    def fetch_html(self, q: Query | str, /) -> str:
        client = Client(
            impersonate="chrome_145",
            impersonate_os="macos",
            referer=True,
            cookie_store=True,
        )
        params = q.params() if isinstance(q, Query) else {"q": q}
        params.setdefault("gl", "US")
        res = client.get(URL, params=params, cookies=_CONSENT_COOKIES)
        self.html = res.text
        if is_consent_wall(self.html):
            raise ConsentWallError("Google returned the cookie-consent page")
        return self.html


def build_query(s: Settings) -> Query:
    legs = [
        FlightQuery(
            date=s.depart_date.isoformat(),
            from_airport=s.origin,
            to_airport=s.destination,
        )
    ]
    if s.return_date:
        legs.append(
            FlightQuery(
                date=s.return_date.isoformat(),
                from_airport=s.destination,
                to_airport=s.origin,
            )
        )
    return create_query(
        flights=legs,
        trip="round-trip" if s.is_round_trip else "one-way",
        seat=s.cabin_class,  # type: ignore[arg-type]  (validated in config)
        passengers=Passengers(adults=s.passengers),
        currency=s.currency,
        language="en-US",  # English page so the price-level text is parseable
    )


def extract_price_level(html: str) -> str | None:
    """Google's "Prices are currently low/typical/high" indicator.

    Primary source is the visible text. Fallback: the price-insights block in
    the embedded JS payload, `[5, [_, current], _, _, [_, typical_low], [_, typical_high], ...]`.
    """
    m = _LEVEL_TEXT.search(html)
    if m:
        return m.group(1).lower()
    try:
        from selectolax.lexbor import LexborHTMLParser

        script = LexborHTMLParser(html).css_first(r"script.ds\:1")
        payload = json.loads(script.text().split("data:", 1)[1].rsplit(",", 1)[0])
        insights = payload[5]
        current, lo, hi = insights[1][1], insights[4][1], insights[5][1]
        if current < lo:
            return "low"
        if current > hi:
            return "high"
        return "typical"
    except Exception:
        return None


def _dump_raw(html: str | None, data_dir: str, reason: str) -> None:
    """Log an excerpt and keep the full response on disk (last RAW_KEEP only)."""
    text = html or ""
    log.warning(
        "fast-flights returned unusable data (%s). Raw response: %d chars, excerpt:\n%s",
        reason, len(text), text[:RAW_LOG_CHARS] or "<empty>",
    )
    try:
        raw_dir = os.path.join(data_dir, RAW_DIR_NAME)
        os.makedirs(raw_dir, exist_ok=True)
        path = os.path.join(raw_dir, f"{utcnow():%Y%m%dT%H%M%SZ}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        log.warning("Full raw response saved to %s", path)
        for old in sorted(os.listdir(raw_dir))[:-RAW_KEEP]:
            os.remove(os.path.join(raw_dir, old))
    except OSError:
        log.exception("Could not save raw response")


def scrape(s: Settings, now: datetime | None = None) -> Snapshot:
    fetcher = _CapturingFetcher()
    try:
        results = get_flights(build_query(s), integration=fetcher)
    except Exception as e:
        _dump_raw(fetcher.html, s.data_dir, f"{type(e).__name__}: {e}")
        raise ScrapeError(f"{type(e).__name__}: {e}") from e

    valid = [f for f in results if f.price and f.price > 0]
    if not valid:
        _dump_raw(fetcher.html, s.data_dir, f"{len(results)} results, none with a price")
        raise ScrapeError(f"empty result ({len(results)} flights, none with a price)")

    cheapest = min(valid, key=lambda f: f.price)
    if not cheapest.airlines or not cheapest.flights:
        _dump_raw(fetcher.html, s.data_dir, "cheapest flight has empty airline/segments")
        raise ScrapeError("empty fields in cheapest flight (airline/segments)")

    level = extract_price_level(fetcher.html or "")
    if level is None:
        log.info("Price level (low/typical/high) not present in this response")

    return Snapshot(
        id=None,
        price=Decimal(cheapest.price),
        currency=s.currency,
        current_price=level,
        airline=", ".join(cheapest.airlines),
        stops=len(cheapest.flights) - 1,
        scraped_at=now or utcnow(),
    )
