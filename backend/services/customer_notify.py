"""
Automatic customer emails and SMS about seats/extras on a booking.

Events (services/addon_deadlines.py, addon_billing.py and main.py call these):
  payment_due  the airline confirmed an extra — items, price, deadline, link
  reminder     extras_reminder_hours before the deadline (admin setting, 6)
  removed      not paid in time, taken off the booking — flight and ticket unchanged
  paid         paid and the EMD was issued
  refused      the airline refused it — nothing to pay

Each message goes at most once per booking + event + item + channel: a row
is claimed in customer_notifications before sending (a unique key), and its
outcome recorded (sent / not_configured / no_address / failed). A message
that couldn't go out (no SMTP / SMS gateway yet) is not retried later, so
nobody gets a stale "payment due" once credentials are added.

Recipient: the booking's customer account (email, phone), else the booking
contact. Phones are normalised to international format (+94…).
Admin switches: notify_email_enabled / notify_sms_enabled (provider settings).
"""
import json
import logging
import os
from datetime import datetime, timedelta

import database
from config.email_config import EmailConfig
from config.travelport_config import TravelportConfig
from services import email_service, sms_service

logger = logging.getLogger(__name__)

BRAND = os.getenv("BRAND_NAME", "Tick and Book")
# Where the customer's Booking Status page lives (frontend).
SITE_URL = os.getenv("PUBLIC_SITE_URL", "http://localhost:5173").rstrip("/")


def booking_status_link(locator: str) -> str:
    return f"{SITE_URL}/account?status={locator}"


def _local(deadline_utc) -> str:
    """UTC deadline -> agency (Sri Lanka) time text."""
    if isinstance(deadline_utc, str):
        deadline_utc = datetime.fromisoformat(deadline_utc.rstrip("Z"))
    local = deadline_utc + timedelta(minutes=TravelportConfig.AGENCY_UTC_OFFSET_MINUTES)
    return local.strftime("%a %d %b %Y, %H:%M") + " (Sri Lanka time)"


def _money(currency, amount) -> str:
    return f"{currency or ''} {float(amount or 0):,.2f}".strip()


def recipient(locator: str) -> dict:
    """{"email", "phone" (+94…), "name"} for a booking."""
    booking = database.get_booking_by_locator(locator) or {}
    try:
        raw = json.loads(booking.get("raw_ticket_json") or "{}") or {}
    except ValueError:
        raw = {}
    customer = database.get_customer_by_id(booking["customer_id"]) if booking.get("customer_id") else None
    traveler = (raw.get("travelers") or [{}])[0] or {}
    email = (customer or {}).get("email") or raw.get("email") or traveler.get("email") or booking.get("passenger_email")
    phone = sms_service.normalise_phone((customer or {}).get("phone")) or sms_service.normalise_phone(raw.get("phone")) \
        or sms_service.normalise_phone(traveler.get("phone"))
    name = (customer or {}).get("full_name") or booking.get("passenger_name") or ""
    return {"email": email, "phone": phone, "name": name}


# ── Message text ─────────────────────────────────────────────────────────────

def _lines(items: list) -> str:
    return "\n".join(f"  - {i['name']}: {_money(i.get('currency'), i.get('price'))}" if i.get("price") else f"  - {i['name']}"
                     for i in items)


def _compose(event: str, locator: str, items: list, info: dict) -> tuple[str, str, str]:
    """(email subject, email body, sms text)."""
    link = booking_status_link(locator)
    names = ", ".join(i["name"] for i in items)
    total = _money(items[0].get("currency"), sum(float(i.get("price") or 0) for i in items)) if items else ""
    deadline = _local(info["deadline_utc"]) if info.get("deadline_utc") else None
    unchanged = "Your flight booking and ticket are not affected."
    if event == "payment_due":
        subject = f"Payment due for your extras — booking {locator}"
        body = (f"The airline has confirmed these extras on booking {locator}:\n{_lines(items)}\n\nTotal: {total}\n"
                + (f"Please pay by {deadline}. If they aren't paid by then, they are removed from your booking and "
                   f"can't be used at the airport. {unchanged}\n" if deadline else "")
                + f"\nPay here (Booking Status): {link}\n")
        sms = f"{BRAND}: booking {locator} - {names} confirmed, {total}." + (f" Pay by {deadline} or it is removed." if deadline else "") + f" {link}"
    elif event == "reminder":
        subject = f"Reminder: pay for your extras by {deadline} — booking {locator}"
        body = (f"This is a reminder that these extras on booking {locator} are still unpaid:\n{_lines(items)}\n\n"
                f"Total: {total}\nPay by {deadline}, or they are removed from your booking and can't be used at the "
                f"airport. {unchanged}\n\nPay here (Booking Status): {link}\n")
        sms = f"{BRAND}: reminder - booking {locator}: {names} ({total}) unpaid. Pay by {deadline} or it is removed. {link}"
    elif event == "removed":
        subject = f"Unpaid extras removed — booking {locator}"
        body = (f"These extras on booking {locator} weren't paid by their deadline, so they have been removed:\n"
                f"{_lines(items)}\n\nYou have not been charged for them. Your flight booking and ticket are unchanged "
                f"and still valid. You can add extras again from Booking Status: {link}\n")
        sms = f"{BRAND}: booking {locator} - unpaid {names} removed, not charged. Your flight and ticket are unchanged. {link}"
    elif event == "paid":
        emds = ", ".join(info.get("emds") or [])
        subject = f"Payment received — your extras are confirmed (booking {locator})"
        body = (f"Thank you — we received your payment for these extras on booking {locator}:\n{_lines(items)}\n\n"
                + (f"The airline issued document(s) {emds}.\n" if emds else "")
                + f"You can see them on Booking Status: {link}\n")
        sms = f"{BRAND}: booking {locator} - payment received, {names} confirmed" + (f" (EMD {emds})" if emds else "") + "."
    elif event == "refused":
        subject = f"The airline couldn't confirm your extras — booking {locator}"
        body = (f"The airline didn't confirm these extras you requested on booking {locator}:\n{_lines(items)}\n\n"
                f"There is nothing to pay for them. {unchanged} You can try other options on Booking Status: {link}\n")
        sms = f"{BRAND}: booking {locator} - the airline couldn't confirm {names}. Nothing to pay. Your flight is unchanged."
    else:
        raise ValueError(event)
    signed = body + f"\n{BRAND}\n"
    return subject, signed, sms


# ── Sending ──────────────────────────────────────────────────────────────────

def notify(locator: str, event: str, items: list, **info) -> dict:
    """items: [{"key", "name", "price"?, "currency"?}]. Sends one email and one
    SMS covering the items not already notified for this event. Never
    raises. Returns {channel: status}."""
    out = {}
    try:
        settings = database.get_provider_settings()
        to = recipient(locator)
        for channel, enabled in (("email", settings["notify_email_enabled"]), ("sms", settings["notify_sms_enabled"])):
            if not enabled:
                continue
            fresh = [i for i in items if database.claim_notification(locator, event, i["key"], channel)]
            if not fresh:
                continue
            subject, body, sms = _compose(event, locator, fresh, info)
            keys = [i["key"] for i in fresh]
            if channel == "email":
                address = to["email"]
                if not address:
                    status, error = "no_address", "no email address on the booking"
                elif not EmailConfig.smtp_configured():
                    status, error = "not_configured", "Email not configured (SMTP_USER / SMTP_PASSWORD)"
                    logger.info(f"{locator}: Email not configured — '{event}' email recorded, not sent")
                else:
                    sent, error = email_service._send(address, subject, body)
                    status = "sent" if sent else "failed"
                database.finish_notifications(locator, event, keys, channel, status, address, subject, body, error)
            else:
                address = to["phone"]
                if not address:
                    status, error = "no_address", "no usable phone number on the booking"
                else:
                    sent, error = sms_service.send_sms(address, sms)
                    status = "sent" if sent else ("not_configured" if error == sms_service.NOT_CONFIGURED else "failed")
                database.finish_notifications(locator, event, keys, channel, status, address, None, sms, error)
            out[channel] = status
            logger.info(f"{locator}: '{event}' {channel} for {len(fresh)} item(s): {status}")
    except Exception as e:
        logger.error(f"{locator}: '{event}' notification failed: {e}")
    return out
