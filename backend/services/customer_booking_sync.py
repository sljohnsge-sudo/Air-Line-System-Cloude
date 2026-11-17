"""
services/customer_booking_sync.py
=================================
My Bookings (Customer Portal), 17/11/2026: each booking on the customer's
account is checked live with Travelport every time they open their account
(signing in included), and what Travelport says is SAVED here, because
bookings are also changed outside Tick and Book (Smartpoint, other Travelport
systems, the airline):

  * a ticket whose coupons are all Void / Refunded there   -> Voided / Refunded
  * a ticketed reservation cancelled there (not travelled)  -> Cancelled
  * an unpaid booking the airline has released              -> Cancelled
  * a booking we hold as un-ticketed that has a ticket there -> Ticketed
    (and it earns its loyalty points once)

Every cancellation goes through database.record_cancellation, which also
takes back the booking's loyalty points.

Read-only towards Travelport: one Reservation Retrieve per booking and, for a
ticketed one, one Ticket Retrieve per ticket (coupon status). Nothing is
changed at Travelport.
"""
import logging
import threading
import time
from datetime import datetime

import database
from services import live_booking_service, loyalty_service
from services.segment_status import AIRLINE_CANCELLED, AWAITING_AIRLINE

logger = logging.getLogger(__name__)

# One booking is not read again within this many seconds: the portal loads
# its list again after most actions.
_TTL_SECONDS = 300
_recent: dict = {}
_recent_lock = threading.Lock()

_LIST_STATUS = {"airline_cancelled": AIRLINE_CANCELLED, "airline_problem": AWAITING_AIRLINE, "cancelled": "Cancelled"}
# A coupon in one of these states was used (or swapped for a new ticket):
# the trip is not "cancelled" even if the reservation later is.
_USED = {"Used", "Flown", "Exchanged", "OtherAirlineControl"}


def pay_ref(b: dict) -> str:
    """The booking's reference in the Customer Portal and at the payment
    gateway while it has no ticket - never the PNR."""
    return f"TBK{b['id']}"


def ticket_outcome(coupons: dict, reservation_cancelled: bool) -> str | None:
    """Voided / Refunded / Cancelled when Travelport shows the ticket can no
    longer be used; None while it stands or can't be read in full.
    coupons: {ticket number: [coupon status, ...] or None when unreadable}."""
    statuses = [s for v in coupons.values() if v for s in v]
    every_ticket_read = bool(coupons) and all(v for v in coupons.values())
    if every_ticket_read and all(s == "Void" for s in statuses):
        return "Voided"
    if every_ticket_read and all(s in ("Void", "Refunded") for s in statuses):
        return "Refunded"
    if reservation_cancelled and not any(s in _USED for s in statuses):
        return "Cancelled"
    return None


def _coupons(numbers: list, locator: str, ndc: bool) -> dict:
    out = {}
    for n in numbers:
        detail = live_booking_service._ticket_coupons(n, locator, ndc)
        out[n] = None if detail.get("error") else [c.get("status") for c in detail.get("coupons") or [] if c.get("status")]
    return out


def check(b: dict, upcoming: bool, save: bool = True) -> dict:
    """Read one booking live and (save=True) save what changed. Returns the
    portal's live-status entry: {checked, status, retrieved_at, flights,
    missing, error, changed}. save=False only reports (would_change_to)."""
    locator = b["locator_code"]
    now = time.time()
    if save:
        with _recent_lock:
            hit = _recent.get(locator)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    result = _check(b, upcoming, save)
    if save and result.get("checked"):
        with _recent_lock:
            # Kept without "changed": the list reloads once after a change,
            # and that reload must not be told to reload again.
            _recent[locator] = (now, {**result, "changed": False})
    return result


def _check(b: dict, upcoming: bool, save: bool) -> dict:
    locator = b["locator_code"]
    ndc = (b.get("fare_source") or "GDS").upper() == "NDC"
    try:
        live, error = live_booking_service.get_live_booking(locator, with_coupons=False), ""
    except Exception as e:
        live, error = None, str(e)
    reservation_cancelled = bool(live) and live.get("booking_status") == "cancelled"
    reservation_gone = "DOES NOT EXIST" in error.upper()
    live_numbers = [t["number"] for t in (live or {}).get("tickets") or [] if t.get("number")]
    ticketed_here = database.has_ticket(b)
    status = b.get("status")
    out = {
        "checked": bool(live), "status": status,
        "retrieved_at": (live or {}).get("retrieved_at") or datetime.now().isoformat(sep=" ", timespec="seconds"),
        "flights": {f["flight"]: f.get("airline_status_code") for f in (live or {}).get("flights") or []},
        "missing": reservation_gone and upcoming, "error": error[:200] or None, "changed": False,
    }
    change, how, coupons = None, "", {}

    if ticketed_here or live_numbers:
        numbers = database.ticket_numbers_of(b) or live_numbers
        coupons = _coupons(numbers, locator, ndc) if numbers else {}
        out["checked"] = out["checked"] or any(v for v in coupons.values())
        change = ticket_outcome(coupons, reservation_cancelled)
        how = f"Found on Travelport ({'ticket ' + change.lower() if change != 'Cancelled' else 'reservation cancelled'})" if change else ""
        if not change and not ticketed_here and live_numbers:
            # Ticketed in another Travelport system: saved as ticketed.
            status = "Ticketed"
            if save and database.set_ticketed_from_travelport(locator, live_numbers[0]):
                out["changed"] = True
                logger.info(f"{locator}: Travelport holds ticket {live_numbers[0]} - saved as ticketed")
                email = b.get("passenger_email") or b.get("email")
                if email:
                    try:
                        loyalty_service.award_points_for_booking(email, float(b.get("total_fare") or 0),
                                                                 b.get("currency") or "LKR", locator)
                    except Exception as e:
                        logger.warning(f"{locator}: loyalty points not awarded: {e}")
        elif not change and live:
            # Airline problems on a ticketed trip are shown, not saved (as before).
            mapped = _LIST_STATUS.get(live.get("booking_status"))
            if mapped and mapped != "Cancelled":
                status = mapped
    else:
        paid = database.payment_taken_for(locator, (pay_ref(b),))
        if (reservation_cancelled or reservation_gone) and not paid:
            change, how = "Cancelled", "Not paid - released by the airline"
        elif live:
            mapped = _LIST_STATUS.get(live.get("booking_status"))
            new = mapped if mapped in database.AIRLINE_STATUSES else ("Confirmed" if status in database.AIRLINE_STATUSES else status)
            if new != status and new in database.AIRLINE_STATUSES:
                if save:
                    try:
                        database.set_airline_status(locator, new)
                    except Exception as e:
                        logger.error(f"Could not save live status for {locator}: {e}")
            status = new

    if change:
        out["would_change_to"] = change
        if save:
            database.record_cancellation(locator, change, how, None, None, {
                "found": "Customer Portal live check", "at": out["retrieved_at"],
                "reservation": "cancelled" if reservation_cancelled else ("not on Travelport" if reservation_gone else "live"),
                "coupons": coupons,
            })
            out["changed"] = True
            logger.info(f"{locator}: {how} - saved as {change}")
        status = change
    out["status"] = status
    return out
