"""
Admin → Operations → Ticketing Exceptions (table stuck_booking_notifications).

Each open item gets a type and a priority from the message the system wrote
when it raised it (main._notify_stuck_booking / _notify_airline_problem /
_notify_cancel_failure, services/addon_deadlines._alert), so staff can filter
and see the urgent ones — the customer paid — first.

Items our own records show as fixed are closed automatically (status
'resolved') when the list is opened. Only local data is read here (bookings,
add-on payments, unpaid-extra holds) — never Travelport — so opening the page
stays fast and changes nothing at the airline.
"""
import json
import re
import logging

import database

logger = logging.getLogger(__name__)

# kind -> (label, priority)
KINDS = {
    "paid_no_ticket": ("Paid — ticket not issued", "urgent"),
    "emd_missing": ("Paid seat/extra — EMD not issued", "urgent"),
    "cancel_failed": ("Void / refund / cancel failed", "urgent"),
    "airline_problem": ("Airline didn't confirm a flight", "urgent"),
    "extra_release_broke": ("Removing an unpaid extra changed the booking", "urgent"),
    "unpaid_extra": ("Unpaid seat/extra not removed", "normal"),
    "foreign_emd": ("EMD not issued by us", "normal"),
    "other": ("Other", "normal"),
}
# kind -> short label for the table's Type column
SHORT = {
    "paid_no_ticket": "Paid · no ticket",
    "emd_missing": "Paid extra · no EMD",
    "cancel_failed": "Cancel failed",
    "airline_problem": "Flight not confirmed",
    "extra_release_broke": "Booking changed",
    "unpaid_extra": "Unpaid extra",
    "foreign_emd": "Not our EMD",
    "other": "Other",
}
_CANCELLED = {"Cancelled", "Voided", "Refunded", "Airline Cancelled"}


def classify(row: dict) -> str:
    m = (row.get("error_message") or "")
    up = m.upper()
    if up.startswith("ADD-ON PAID BUT EMD NOT ISSUED") or "PAID SEAT/EXTRA EMD(S) WERE NOT ISSUED" in up:
        return "emd_missing"
    if up.startswith("CANCELLATION FAILED AT TRAVELPORT"):
        return "cancel_failed"
    if up.startswith("AIRLINE HAS NOT CONFIRMED THE FLIGHT"):
        return "airline_problem"
    if up.startswith("URGENT: REMOVING UNPAID"):
        return "extra_release_broke"
    if up.startswith("UNPAID ") and "PASSED ITS PAYMENT DEADLINE" in up:
        return "unpaid_extra"
    if "WERE NOT ISSUED BY US" in up:
        return "foreign_emd"
    if row.get("payment_txn_ref"):
        return "paid_no_ticket"  # a payment was taken and ticketing then failed
    return "other"


def _priority(kind: str, row: dict) -> str:
    if kind == "airline_problem" and "A PAYMENT WAS TAKEN" not in (row.get("error_message") or "").upper():
        return "normal"
    return KINDS[kind][1]


def _raw(booking: dict | None) -> dict:
    try:
        return json.loads((booking or {}).get("raw_ticket_json") or "{}") or {}
    except ValueError:
        return {}


def _fixed(kind: str, row: dict, booking: dict | None) -> str | None:
    """Why this item no longer needs anyone, or None."""
    loc = row.get("locator_code")
    status = (booking or {}).get("status")
    if kind == "paid_no_ticket":
        if booking and (booking.get("ticket_number") or status == "Ticketed"):
            return "The booking was ticketed afterwards."
    elif kind == "cancel_failed":
        if status in _CANCELLED:
            return f"The booking is now {status}."
    elif kind == "emd_missing":
        ref = row.get("payment_txn_ref")
        for p in database.get_addon_payments_for_locator(loc) if loc else []:
            try:
                result = json.loads(p.get("result_json") or "{}") if isinstance(p.get("result_json"), str) else (p.get("result_json") or {})
            except ValueError:
                result = {}
            if p.get("status") == "completed" and result.get("emds") and ref and result.get("payment_reference") == ref:
                return "The EMD was issued afterwards."
        if "PAID SEAT/EXTRA EMD(S) WERE NOT ISSUED" in (row.get("error_message") or "").upper() and _raw(booking).get("emd_status") == "issued":
            return "The EMDs were issued afterwards."
    elif kind == "unpaid_extra":
        holds = database.get_addon_holds(loc) if loc else []
        if holds and not any(h.get("status") in ("unpaid", "release_failed") for h in holds):
            return "No unpaid seat/extra is left on the booking."
    return None


# What staff do next, per type (shown in the table's "Action" column).
ACTIONS = {
    "paid_no_ticket": "Issue the ticket in Smartpoint, or refund the customer",
    "emd_missing": "Issue the EMD in Smartpoint, or refund the seat/extra",
    "cancel_failed": "Finish the void / refund / cancel in Smartpoint",
    "airline_problem": "Check the PNR in Smartpoint; refund if a payment was taken",
    "extra_release_broke": "Check the ticket, flights and paid items in Smartpoint now",
    "unpaid_extra": "Remove the seat / delete the SSR in Smartpoint",
    "foreign_emd": "Check in Smartpoint whether it is billed to the agency",
    "other": "Check the booking in Smartpoint",
}


def _clean(text: str) -> str:
    text = re.sub(r"\s*\(TraceId [^)]*\)", "", text or "")
    text = re.sub(r"\s*\(payment reqid [^)]*\)", "", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def summary(kind: str, row: dict) -> str:
    """The message in one short line for the table (full text kept in error_message)."""
    m = row.get("error_message") or ""
    if kind == "unpaid_extra":
        x = re.match(r"Unpaid (.+?) \((LKR|USD) ([\d,\.]+) airline price\).*?could not be removed through Travelport: (.+?)\. Remove it", m, re.S)
        if x:
            reason = re.sub(r"^Travelport refused Ancillary Cancel:\s*", "", x.group(4).strip())
            return f"Unpaid {x.group(1).strip()} ({x.group(2)} {x.group(3)}) — Travelport refused the removal: {_clean(reason)}"
    if kind == "emd_missing":
        x = re.search(r"Travelport did not issue the EMDs: (.+)", m)
        if x:
            return f"Paid, but the airline refused the EMD: {_clean(x.group(1))}"
        return _clean(m)
    if kind == "paid_no_ticket":
        return f"Paid, but the ticket was refused: {_clean(re.sub(r'^(Travelport: |ValueError: )', '', m))}"
    if kind == "foreign_emd":
        x = re.match(r"EMD\(s\) (.+?) on this booking", m)
        return f"EMD {x.group(1)} on the booking wasn't issued by us" if x else _clean(m)
    if kind == "cancel_failed":
        return _clean(m.replace("Cancellation failed at Travelport", "Cancellation failed"))
    return _clean(m.split(". ")[0])


def review(rows: list) -> tuple[list, int]:
    """Open items with kind, label, priority, a one-line summary, the action to
    take and the booking's ticket / passenger / route added (urgent first,
    newest first), after closing the ones our records show as fixed.
    Returns (open_items, how_many_closed_now)."""
    out, closed = [], 0
    bookings = {}
    for row in rows:
        kind = classify(row)
        loc = row.get("locator_code")
        if loc and loc not in bookings:
            try:
                bookings[loc] = database.get_booking_by_locator(loc)
            except Exception:
                bookings[loc] = None
        booking = bookings.get(loc) or {}
        if row.get("status") == "new":
            try:
                why = _fixed(kind, row, booking or None)
            except Exception as e:
                logger.warning(f"Ticketing exception {row.get('id')}: fix check skipped: {e}")
                why = None
            if why:
                database.resolve_stuck_booking_notification(row["id"], f"Closed automatically: {why}")
                closed += 1
                continue
        route = "–".join(x for x in (booking.get("departure_airport"), booking.get("arrival_airport")) if x)
        out.append({**row, "kind": kind, "kind_label": KINDS[kind][0], "kind_short": SHORT[kind],
                    "priority": _priority(kind, row),
                    "summary": summary(kind, row), "action": ACTIONS[kind],
                    "ticket_number": booking.get("ticket_number"), "passenger_name": booking.get("passenger_name"),
                    "route": route or None, "airline": booking.get("airline"), "flight_number": booking.get("flight_number"),
                    "departure_time": booking.get("departure_time")})
    out.sort(key=lambda r: (r["priority"] != "urgent", -(r.get("created_at").timestamp() if hasattr(r.get("created_at"), "timestamp") else 0)))
    return out, closed
