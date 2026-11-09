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


# Our markup (customer price − airline price) on its own invoice line, so
# finance sees the airline cost and the margin separately.
ERP_MARKUP_ITEM_CODE = os.getenv("ERP_MARKUP_ITEM_CODE", "Booking Markup (Service Fee)")


def _money(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _ticket_lines(booking: dict, description: str) -> list:
    """Invoice lines for a ticket: the airline's price (Travelport's ticketed
    total), seats/extras paid with the ticket at the airline's price, and our
    markup as the rest. The lines always add up to what the customer paid; if
    the airline price isn't known (or the numbers don't fit), one line as before."""
    import json as _json
    total = _money(booking.get("total_fare"))
    raw = booking
    if not booking.get("fare_breakdown") and booking.get("raw_ticket_json"):
        try:
            raw = {**_json.loads(booking["raw_ticket_json"] or "{}"), **booking}
        except ValueError:
            raw = booking
    fb = raw.get("fare_breakdown") or {}
    airline = _money(fb.get("total"))
    extras = []
    for x in ((raw.get("addon_results") or {}).get("seats") or []) + ((raw.get("addon_results") or {}).get("extras") or []):
        if x.get("status") == "confirmed" and _money(x.get("price")) > 0:
            label = f"Seat {x['seat_number']}" if x.get("seat_number") else (x.get("name") or x.get("airline_name") or "Extra")
            extras.append({"label": f"{label}{' · ' + x['flight_number'] if x.get('flight_number') else ''}",
                           "supplier": _money(x.get("supplier_price") or x.get("price"))})
    markup = round(total - airline - sum(e["supplier"] for e in extras), 2)
    if airline <= 0 or markup < -0.5:
        logger.warning(f"ERP sync {booking.get('locator_code')}: airline price not split "
                       f"(total {total}, airline {airline}, extras {sum(e['supplier'] for e in extras)})")
        return [{"item_code": ERP_ITEM_CODE, "qty": 1, "rate": total, "description": description}]
    parts = [f"base {_money(fb.get('base_fare')):,.2f}"]
    taxes = _money(fb.get("fuel_surcharge")) + _money(fb.get("other_taxes"))
    if taxes:
        parts.append(f"taxes {taxes:,.2f}")
    if _money(fb.get("total_fees")):
        parts.append(f"fees {_money(fb.get('total_fees')):,.2f}")
    lines = [{"item_code": ERP_ITEM_CODE, "qty": 1, "rate": airline,
              "description": f"{description} — airline fare ({', '.join(parts)})"}]
    for e in extras:
        lines.append({"item_code": ERP_ADDON_ITEM_CODE, "qty": 1, "rate": e["supplier"],
                      "description": f"{e['label']} — airline price (booked with the ticket)"})
    lines.append({"item_code": ERP_MARKUP_ITEM_CODE, "qty": 1, "rate": max(markup, 0.0),
                  "description": f"Tick and Book markup on PNR {booking.get('locator_code') or booking.get('pnr') or ''}"})
    return lines


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
                "items": _ticket_lines(booking, description),
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


# Seats/extras paid after booking (Manage My Booking -> card payment -> EMD).
# Their own ERP item, so finance can tell extras from tickets.
ERP_ADDON_ITEM_CODE = os.getenv("ERP_ADDON_ITEM_CODE", "Airline Extras (EMD)")


def push_addon_to_erp(invoice: dict, booking: dict) -> str:
    """Creates a Sales Invoice in the ERP for one paid seat/extra payment.

    `invoice` is main._addon_invoice()'s dict (what the customer's own invoice
    shows: lines with description, passenger, flights, EMD and amount);
    `booking` the saved booking (for the passenger / ERP customer). One line
    per seat/extra. Returns the Sales Invoice name; raises ErpSyncError."""
    locator = invoice.get("locator") or ""
    try:
        with httpx.Client(timeout=30, headers=_headers()) as client:
            customer = _find_or_create_customer(
                client,
                booking.get("passenger_name", ""),
                booking.get("passenger_email") or booking.get("email", ""),
            )
            items, emds = [], []
            for line in invoice.get("lines") or []:
                route = ", ".join(" ".join(x for x in (
                    f.get("flight"),
                    f"{f['from']}→{f['to']}" if f.get("from") and f.get("to") else None,
                    f.get("date")) if x) for f in line.get("flights") or [])
                if line.get("emd"):
                    emds.append(line["emd"])
                description = " · ".join(x for x in (line.get("description"), line.get("passenger"), route,
                                                      f"EMD {line['emd']}" if line.get("emd") else None) if x)
                # The airline's price on the line; our markup goes on its own line below.
                supplier = _money(line.get("supplier_amount")) if line.get("supplier_amount") is not None else _money(line.get("amount"))
                items.append({"item_code": ERP_ADDON_ITEM_CODE, "qty": 1,
                              "rate": supplier, "description": f"{description} — airline price"[:1000]})
            if not items:
                raise ErpSyncError(f"Add-on {invoice.get('invoice_number')} for {locator} has no lines to invoice")
            markup = round(_money(invoice.get("total")) - sum(i["rate"] for i in items), 2)
            if markup < -0.5:  # numbers don't fit: charge lines at what the customer paid instead
                for i, line in zip(items, invoice.get("lines") or []):
                    i["rate"] = _money(line.get("amount"))
            else:
                items.append({"item_code": ERP_MARKUP_ITEM_CODE, "qty": 1, "rate": max(markup, 0.0),
                              "description": f"Tick and Book markup on seats/extras, PNR {locator}"})
            posting_date = (invoice.get("issued_at") or "")[:10] or None
            payload = {
                "company": ERP_COMPANY,
                "customer": customer,
                "currency": invoice.get("currency") or "LKR",
                "set_posting_time": 1,
                "posting_date": posting_date,
                "due_date": posting_date,
                "remarks": (
                    f"Online Booking System — seats/extras paid after booking | PNR {locator} | "
                    f"Payment {invoice.get('invoice_number')} | Card ref {(invoice.get('payment') or {}).get('reference') or '-'} | "
                    f"EMD {', '.join(emds) or 'pending'} | Ticket {', '.join(invoice.get('tickets') or []) or '-'}"
                ),
                # Same PNR and e-ticket number as the flight's own invoice, so
                # finance finds the extras under that ticket; this is a new,
                # separate invoice, and each line carries its EMD number.
                "custom_pnr": locator,
                "custom_ticket_number": (", ".join(invoice.get("tickets") or []) or booking.get("ticket_number") or "")[:140],
                "items": items,
            }
            resp = client.post(f"{ERP_BASE_URL}/api/resource/Sales Invoice", json=payload)
            if resp.status_code >= 400:
                raise ErpSyncError(
                    f"ERP Sales Invoice (extras) create failed ({resp.status_code}) for {locator}: {resp.text[:400]}"
                )
            invoice_name = resp.json()["data"]["name"]
        logger.info(f"ERP sync: extras {invoice.get('invoice_number')} ({locator}) -> Sales Invoice {invoice_name}")
        return invoice_name
    except ErpSyncError:
        raise
    except httpx.HTTPError as e:
        raise ErpSyncError(f"ERP sync network error for extras {invoice.get('invoice_number')} ({locator}): {e}") from e
