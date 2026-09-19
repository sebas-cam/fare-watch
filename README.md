# fare-watch

Self-hosted flight price tracker. It scrapes Google Flights on a schedule, stores
every price in SQLite, sends Telegram alerts, and serves a small dashboard with
the price history.

- **Stack:** Python 3.12 · FastAPI · SQLite · APScheduler · Docker (single container)
- **Data source:** [`fast-flights`](https://github.com/AWeirdDev/fast-flights) v3 (Google Flights scraper, no API key)
- **Configuration:** environment variables only. No config files, nothing route-specific in the repo.

## Quick start

```bash
cp .env.example .env      # fill in your values; .env is git-ignored
docker compose up -d --build
open http://localhost:8000/
```

Force a scrape without waiting for the schedule:

```bash
docker compose exec fare-watch python -m app.run_once
```

## Configuration

The app validates every variable at startup and refuses to start, listing each
problem, if something is missing or malformed.

### Required

| Variable | Example | Notes |
|---|---|---|
| `ORIGIN` | `AAA` | IATA code |
| `DESTINATION` | `BBB` | IATA code |
| `DEPART_DATE` | `2099-01-01` | `YYYY-MM-DD` |
| `RETURN_DATE` | `2099-01-15` | Must be defined; **empty = one-way** |
| `TELEGRAM_BOT_TOKEN` | `123456:ABC…` | From BotFather (see below) |
| `TELEGRAM_CHAT_ID` | `123456789` | See below |

### Optional

| Variable | Default | Notes |
|---|---|---|
| `PASSENGERS` | `1` | Adults, 1–9 |
| `CABIN_CLASS` | `economy` | `economy`, `premium-economy`, `business`, `first` |
| `CURRENCY` | `USD` | ISO 4217 |
| `PRICE_FLOOR` | *(empty)* | Alert when price **<** this. Empty disables it |
| `PRICE_CEILING` | *(empty)* | Alert when price **>** this. Empty disables it |
| `CHANGE_THRESHOLD_PCT` | `5` | Alert when price moves **more than** this % vs the previous snapshot |
| `REMINDER_DATES` | *(empty)* | Comma-separated dates, e.g. `2099-01-01,2099-01-08` |
| `DEADLINE_DATE` | *(empty)* | Final "buy now" message on that date. Empty disables it |
| `SCRAPE_TIMES` | `09:00,21:00` | Local times (per `TZ`), comma-separated `HH:MM` |
| `TZ` | `UTC` | IANA name, e.g. `Europe/Madrid` |
| `DASHBOARD_TOKEN` | *(empty)* | If set, `/` and `/api/snapshots` require `?token=<value>` (403 otherwise) |
| `LOG_LEVEL` | `INFO` | |
| `DATA_DIR` | `/app/data` | Where the SQLite file lives; only change for local dev |

## Telegram setup

### 1. Create the bot (BotFather)

1. In Telegram, open a chat with **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`, choose a display name and a username ending in `bot`.
3. BotFather replies with a token like `123456789:AAH…`. That's `TELEGRAM_BOT_TOKEN`.
   Treat it as a password.

### 2. Get your `chat_id`

1. Open a chat with your new bot and send it any message (e.g. `hi`).
   Bots can't message you until you do this.
2. Open this URL in a browser (replace the token):

   ```
   https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
   ```

3. Find `"chat":{"id": 123456789, …}` in the response. That number is `TELEGRAM_CHAT_ID`.
   - For a **group**: add the bot to the group, send a message there, and use the
     group's id (it's negative, e.g. `-1001234567890`).
   - If `result` is empty, send the bot another message and reload.

Test it:

```bash
curl -s -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
     -d chat_id=<CHAT_ID> -d text="fare-watch test"
```

## Alerts

All alerts go out with Telegram's `sendMessage` over HTTP POST (no webhook, no
inbound traffic needed).

| # | Trigger | Repeats? |
|---|---|---|
| 1 | Price < `PRICE_FLOOR` | Not while the price stays the same; again if it changes while still below |
| 2 | Price > `PRICE_CEILING` | Same as above |
| 3 | \|change\| > `CHANGE_THRESHOLD_PCT` vs the previous snapshot | Once per snapshot |
| 4 | Today is one of `REMINDER_DATES` | Once per date |
| 5 | Today is `DEADLINE_DATE` | Once |
| — | 2 consecutive failed/empty scrapes | Once per outage, plus a "recovered" message afterwards |

Sent alerts are recorded in the `alerts_sent` table so the same alert is not
sent twice in a row. If Telegram is unreachable, the alert is not recorded and
the next run retries it.

"Today" means the current date in `TZ`. Reminder and deadline checks run as part
of each scheduled scrape.

## Dashboard

- `GET /` shows price vs. time for every snapshot. Dashed lines mark the floor and
  ceiling, vertical markers show reminder and deadline dates, and each point is
  colored by Google's price level (low = green, typical = gray, high = red,
  hollow = unknown). The header shows the current price, the historical min and
  max, and the days left until the deadline. A collapsible table lists every
  snapshot. Chart.js is loaded from a CDN (pinned, with SRI); there is no build step.
- `GET /api/snapshots` returns the raw snapshots as JSON.
- `GET /health` is always open. It returns only
  `{"status": "ok|degraded|failing|starting", "last_success_at": "…"}`, never the
  route, dates or prices. The Docker `HEALTHCHECK` calls it.

> If you leave `DASHBOARD_TOKEN` empty and expose port 8000, anyone can see your
> route and prices. Set a long random value (`openssl rand -hex 24`) and
> bookmark `https://your-host/?token=<value>`.

## How scraping works

- `create_query()` + `get_flights()` from fast-flights, with `language=en-US` and
  your `CURRENCY`. The cheapest result becomes the snapshot (`price`, `airline`,
  `stops` of the outbound leg).
- **`current_price` (low/typical/high):** fast-flights v3 no longer exposes the
  `result.current_price` that v2 had. fare-watch reads the same HTML that
  `get_flights()` parses and takes it from Google's "Prices are currently …"
  indicator. If that text is missing, it compares the price against Google's
  typical range in the embedded data. If neither is available the field is
  `NULL`, which is normal for some routes.
- **Empty responses:** Google sometimes changes its fingerprinting and
  fast-flights comes back with empty fields. When that happens, the first 2,000
  characters of the raw response go to the log and the full HTML is saved to
  `data/raw_responses/` (the 10 most recent are kept). The run counts as failed,
  and two failed runs in a row send a **FALLO** alert.

## Deploying on Coolify

1. **New Resource → Docker Compose**, point it at your fork of this repo.
2. In **Environment Variables**, add the required variables (Coolify detects them
   from `docker-compose.yml`; the required ones are marked with `:?`) and any
   optional ones.
3. The named volume `fare-watch-data` is mounted at `/app/data`, so the SQLite
   database survives redeploys.
4. Assign a domain to the service on port `8000`, and set `DASHBOARD_TOKEN`.
5. Keep a **single replica**. The scheduler runs inside the web process, so
   two replicas would scrape and alert twice.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest

# run locally (needs the required env vars; DATA_DIR avoids /app/data)
set -a; . ./.env; set +a
DATA_DIR=./data uvicorn app.main:app --reload
```

### Layout

```
app/
  config.py      env parsing & validation (Settings)
  scraper.py     fast-flights query, price-level extraction, raw-response dumps
  db.py          SQLite: snapshots, alerts_sent, scrape_runs
  alerts.py      alert rules + de-duplication
  telegram.py    sendMessage client
  jobs.py        scheduled job & APScheduler setup
  main.py        FastAPI app: /, /api/snapshots, /health
  run_once.py    manual one-off scrape
  templates/index.html
tests/
```

## Notes

- Scraping Google Flights is unofficial and can break without notice. Pin and
  upgrade `fast-flights` deliberately.
- fast-flights 3.1.0 imports `typing_extensions` without declaring it as a
  dependency, so `requirements.txt` pins it explicitly.
