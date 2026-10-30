"""
services/erp_sync_service.py
==============================
Pushes every ticketed booking from this system into the George Steuart
ERP (Frappe, ERP_BASE_URL) as a Sales Invoice under the "Online Booking"
company — so finance sees online bookings alongside every other George
Steuart entity without anyone re-keying them.

One-way push only, by necessity: this backend runs on a developer
machine's localhost, which the ERP server cannot reach, so there is no
pull/webhook from the ERP side — this backend is the only side that can
reach both systems. See main.py's _issue_and_save_ticket for the
automatic call site (fired right after a ticket is issued) and
main.py's /api/admin/erp-sync endpoints for manual retry/backfill.

Best-effort by design: a failed push is logged (and recorded via
database.mark_erp_sync_failed so it's picked up by the next backfill) but
never raises past its caller — the customer's ticket is already real on
Travelport by the time this runs, so an ERP/network hiccup here must
never block or appear to undo the booking itself.
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

# No default host: a missing ERP_BASE_URL used to fall back to the development
# ERP, so production invoices could silently land there. Set it explicitly.
ERP_BASE_URL = os.getenv("ERP_BASE_URL", "").rstrip("/")
ERP_API_KEY = os.getenv("ERP_API_KEY")
ERP_API_SECRET = os.getenv("ERP_API_SECRET")
ERP_COMPANY = os.getenv("ERP_COMPANY", "Online Booking")
ERP_ITEM_CODE = os.getenv("ERP_ITEM_CODE", "Flight Ticket")
ERP_DEFAULT_CUSTOMER_GROUP = os.getenv("ERP_DEFAULT_CUSTOMER_GROUP", "Individual")
ERP_DEFAULT_TERRITORY = os.getenv("ERP_DEFAULT_TERRITORY", "Sri Lanka")


class ErpSyncError(Exception):
    """Raised on any failure pushing a booking to the ERP. Callers (main.py)
    must catch this — see module docstring on why it must never propagate
    as a customer-facing error."""
    pass


def erp_configured() -> bool:
    return bool(ERP_BASE_URL and ERP_API_KEY and ERP_API_SECRET)


def _headers() -> dict:
    if not erp_configured():
        raise ErpSyncError(
            "ERP_API_KEY / ERP_API_SECRET are not set in .env — generate an API "
            "key/secret for the integration user in the ERP (User > API Access > "
            "Generate Keys) and add them before syncing."
        )
    return {
        "Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}",
        "Content-Type": "application/json",
    }


def _find_or_create_customer(client: httpx.Client, passenger_name: str, passenger_email: str) -> str:
    """Looks up an existing ERP Customer by this booking's passenger NAME
    (not email — confirmed live that a shared/generic contact email across
    several different travelers, e.g. an agent's own test address used for
    multiple distinct passengers, silently merged every one of them into
    whichever Customer happened to be created first, so every invoice
    showed the same wrong passenger name). Matching on name reuses one
    Customer for a genuinely repeat passenger while still giving each
    distinct traveler their own record. Returns the Customer's `name`."""
    name = (passenger_name or "").strip()
    if name:
        resp = client.get(
            f"{ERP_BASE_URL}/api/resource/Customer",
            params={
                "filters": f'[["customer_name","=","{name}"]]',
                "limit_page_length": 1,
            },
        )
        resp.raise_for_status()
        existing = resp.json().get("data") or []
        if existing:
            return existing[0]["name"]

    resp = client.post(
        f"{ERP_BASE_URL}/api/resource/Customer",
        json={
            "customer_name": passenger_name or passenger_email or "Online Booking Customer",
            "customer_type": "Individual",
            "customer_group": ERP_DEFAULT_CUSTOMER_GROUP,
            "territory": ERP_DEFAULT_TERRITORY,
            "email_id": passenger_email or None,
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]["name"]


def push_booking_to_erp(booking: dict) -> str:
    """Creates a Sales Invoice in the ERP for one ticketed booking.

    `booking` is the same dict shape database.save_booking()/get_all_bookings()
    produce (locator_code, pnr, ticket_number, passenger_name,
    passenger_email, flight_number, airline, departure_airport,
    arrival_airport, departure_time, total_fare, currency, booking_date...).

    Returns the new Sales Invoice's name. Raises ErpSyncError on any
    failure — the caller is responsible for making this non-fatal.
    """
    locator = booking.get("locator_code") or booking.get("pnr") or ""
    try:
        with httpx.Client(timeout=30, headers=_headers()) as client:
            customer = _find_or_create_customer(
                client,
                booking.get("passenger_name", ""),
                booking.get("passenger_email") or booking.get("email", ""),
            )

            description = (
                f"{booking.get('airline', '')} {booking.get('flight_number', '')} "
                f"{booking.get('departure_airport', '')} → {booking.get('arrival_airport', '')} "
                f"on {str(booking.get('departure_time', ''))[:10]}"
            ).strip()

            posting_date = (booking.get("booking_date") or "")[:10] or None

            payload = {
                "company": ERP_COMPANY,
                "customer": customer,
                "currency": booking.get("currency") or "LKR",
                # Without set_posting_time, ERPNext silently defaults
                # posting_date to today regardless of what's sent here —
                # confirmed live: due_date (correctly set to the booking's
                # own date) then landed before that defaulted-to-today
                # posting_date and the whole create was rejected.
                "set_posting_time": 1,
                "posting_date": posting_date,
                "due_date": posting_date,
                "remarks": (
                    f"Online Booking System — PNR {booking.get('pnr') or locator} | "
                    f"Ticket {booking.get('ticket_number') or 'Pending'} | "
                    f"Locator {locator}"
                ),
                # Custom fields (Sales Invoice-custom_pnr / -custom_ticket_number,
                # added 2026-10-20) so these show as their own list-view columns
                # instead of only being readable inside the Remarks text.
                "custom_pnr": booking.get("pnr") or locator,
                "custom_ticket_number": booking.get("ticket_number") or "",
                "items": [
                    {
                        "item_code": ERP_ITEM_CODE,
                        "qty": 1,
                        "rate": float(booking.get("total_fare") or 0),
                        "description": description,
                    }
                ],
            }

            resp = client.post(f"{ERP_BASE_URL}/api/resource/Sales Invoice", json=payload)
            if resp.status_code >= 400:
                raise ErpSyncError(
                    f"ERP Sales Invoice create failed ({resp.status_code}) for {locator}: {resp.text[:500]}"
                )
            invoice_name = resp.json()["data"]["name"]

        logger.info(f"ERP sync: booking {locator} -> Sales Invoice {invoice_name}")
        return invoice_name

    except ErpSyncError:
        raise
    except httpx.HTTPError as e:
        raise ErpSyncError(f"ERP sync network error for {locator}: {e}") from e
