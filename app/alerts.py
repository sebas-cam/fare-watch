"""Alert rules. `evaluate_*` functions are pure; `dispatch` sends + records.

De-duplication: an alert is skipped when the most recent alert of the same
kind has the same key ("never the same alert twice in a row").
  floor / ceiling  key = price       -> re-alerts only if the price moves
  change           key = snapshot id -> naturally unique
  reminder         key = date        -> once per date, ever
  deadline         key = date        -> once, ever
  failure          key = first failed run id of the streak -> once per outage
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .config import Settings
from .db import Database, Snapshot
from .telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

ONCE_EVER_KINDS = {"reminder", "deadline"}


@dataclass(frozen=True)
class Alert:
    kind: str
    key: str
    message: str


def fmt_money(amount: Decimal, currency: str) -> str:
    q = amount.quantize(Decimal("1")) if amount == amount.to_integral() else amount.quantize(Decimal("0.01"))
    return f"{q:,} {currency}"


def route_label(s: Settings) -> str:
    arrow = "⇄" if s.is_round_trip else "→"
    dates = s.depart_date.isoformat() + (f" / {s.return_date.isoformat()}" if s.return_date else "")
    return f"{s.origin} {arrow} {s.destination} ({dates})"


def evaluate_price(s: Settings, current: Snapshot, previous: Snapshot | None) -> list[Alert]:
    alerts: list[Alert] = []
    price = fmt_money(current.price, current.currency)
    level = f" · Google: {current.current_price}" if current.current_price else ""
    detail = f"{current.airline or '?'} · {current.stops} escala(s){level}"

    if s.price_floor is not None and current.price < s.price_floor:
        alerts.append(Alert(
            "floor", str(current.price),
            f"🟢 Precio bajo el mínimo\n{route_label(s)}\n"
            f"Ahora: {price} (piso {fmt_money(s.price_floor, s.currency)})\n{detail}",
        ))

    if s.price_ceiling is not None and current.price > s.price_ceiling:
        alerts.append(Alert(
            "ceiling", str(current.price),
            f"🔴 Precio sobre el máximo\n{route_label(s)}\n"
            f"Ahora: {price} (techo {fmt_money(s.price_ceiling, s.currency)})\n{detail}",
        ))

    if previous is not None and previous.price > 0:
        pct = (current.price - previous.price) / previous.price * 100
        if abs(pct) > s.change_threshold_pct:
            arrow = "📉 Bajó" if pct < 0 else "📈 Subió"
            pct_s = pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            alerts.append(Alert(
                "change", str(current.id),
                f"{arrow} {abs(pct_s)}%\n{route_label(s)}\n"
                f"{fmt_money(previous.price, previous.currency)} → {price}\n{detail}",
            ))

    return alerts


def evaluate_calendar(s: Settings, today: date, latest: Snapshot | None) -> list[Alert]:
    alerts: list[Alert] = []
    now_line = (
        f"Precio actual: {fmt_money(latest.price, latest.currency)}"
        if latest else "Todavía no hay precios registrados."
    )

    if today in s.reminder_dates:
        extra = ""
        if s.deadline_date:
            extra = f"\nFaltan {(s.deadline_date - today).days} día(s) para el deadline ({s.deadline_date})."
        alerts.append(Alert(
            "reminder", today.isoformat(),
            f"⏰ Recordatorio\n{route_label(s)}\n{now_line}{extra}",
        ))

    if s.deadline_date and today == s.deadline_date:
        alerts.append(Alert(
            "deadline", today.isoformat(),
            f"🚨 Hoy es el deadline para comprar\n{route_label(s)}\n{now_line}",
        ))

    return alerts


def failure_alert(streak: int, first_failed_id: int, error: str | None) -> Alert:
    return Alert(
        "failure", str(first_failed_id),
        f"⚠️ FALLO: {streak} corridas consecutivas del scraper fallaron o "
        f"devolvieron campos vacíos.\nÚltimo error: {error or 'desconocido'}\n"
        "Revisa los logs (se guarda el raw response en data/raw_responses).",
    )


def should_send(db: Database, alert: Alert) -> bool:
    if alert.kind in ONCE_EVER_KINDS:
        return not db.alert_exists(alert.kind, alert.key)
    return db.last_alert_key(alert.kind) != alert.key


def dispatch(db: Database, tg: Telegram, alerts: list[Alert]) -> int:
    sent = 0
    for a in alerts:
        if not should_send(db, a):
            log.info("Skipping duplicate %s alert (key=%s)", a.kind, a.key)
            continue
        try:
            tg.send(a.message)
        except TelegramError as e:
            log.error("Telegram send failed for %s alert: %s", a.kind, e)
            continue  # not recorded -> will be retried next run
        db.record_alert(a.kind, a.key, a.message)
        log.info("Sent %s alert", a.kind)
        sent += 1
    return sent
