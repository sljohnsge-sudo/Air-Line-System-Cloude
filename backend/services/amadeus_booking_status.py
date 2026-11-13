"""
Customer Portal → Booking Status for an Amadeus booking (13/11/2026).

The same page, in the same shape, as a Travelport booking
(manage_booking_service.lookup → live_booking_service.get_live_booking).
Everything is read live from the Amadeus PNR (PNR_Retrieve, through the
Amadeus backend's servicing view):
- flights, each with the airline's own status code (HK, HX ...);
- the airline's record locator when it differs from the PNR;
- tickets and EMDs from the PNR's FA lines, with their state there
  (ET issued / EV voided / ER refunded);
- passengers, and meal / assistance SSRs with the airline's answer.

Nothing is filled in. The PNR gives a booking class but no cabin, so no
cabin is shown; ticket coupons (flight by flight) and seat elements are not
read from Amadeus yet, so the page says that rather than "none". Seats,
extras and special requests can't be added online to an Amadeus booking yet:
those tabs stay greyed out and say so - no manage token is issued.

As on the Travelport page, nothing is shown unless the last name matches a
passenger Amadeus holds on that booking.
"""
import re
from datetime import datetime, timezone

from services import amadeus_booking_proxy as amadeus
from services import amadeus_customer_bookings as ad_bookings
from services.live_booking_service import _ASSIST_SSRS
from services.segment_status import CANCELLED_CODES, CONFIRMED_CODES, norm_flight_no


class NotFound(Exception):
    pass


class Unavailable(Exception):
    pass


# Our own limits, in our own words (not the airline's).
MANAGE_NOTE = "Choosing seats, adding extras and special requests online aren't available for this booking yet."
SEATS_NOTE = "Seats on this booking aren't shown online yet."
COUPONS_NOTE = "Flight-by-flight ticket details aren't shown for this booking yet."
UNAVAILABLE = ("We couldn't open your booking just now. Your booking is not affected — "
               "please try again in a minute.")

# Amadeus SSR status codes, read the same way as on the Amadeus side
# (amadeus-backend flight_booking_service.ssr_state).
_SSR_CONFIRMED = {"HK", "KK", "KD", "HS", "TK", "RR"}
_SSR_REQUESTED = {"NN", "HN", "PN"}
_SSR_REFUSED = {"UN", "NO", "UC", "UU", "HX", "US", "XX", "XK", "DL", "NA"}
_DOC_LABEL = {"issued": "Issued", "voided": "Voided", "refunded": "Refunded"}


def _norm(text: str | None) -> str:
    return re.sub(r"[^A-Z]", "", (text or "").upper())


def find_locator(query: str) -> str | None:
    """The Amadeus booking made on this website for a PNR or a 13-digit
    ticket number, or None (then it's looked up on Travelport)."""
    q = re.sub(r"[\s-]", "", query or "").upper()
    if re.fullmatch(r"\d{13}", q):
        return ad_bookings.locator_for_ticket(q)
    if re.fullmatch(r"[A-Z0-9]{5,8}", q):
        row = ad_bookings.get(q)
        return row["locator"] if row else None
    return None


def _when(iso: str | None) -> str:
    # "2026-11-14T18:40" -> "2026-11-14 18:40", the Travelport page's format.
    return (iso or "").replace("T", " ").strip()


def _segment_status(code: str | None) -> tuple[str | None, str]:
    code = (code or "").upper()
    if code in CONFIRMED_CODES:
        return "Confirmed", "confirmed"
    if code in CANCELLED_CODES:
        return "Cancelled", "cancelled"
    return None, "pending"


def _ssr_status(code: str | None) -> str | None:
    code = (code or "").upper()
    if code in _SSR_CONFIRMED:
        return "Confirmed"
    if code in _SSR_REQUESTED:
        return "Requested"
    if code in _SSR_REFUSED:
        return "Rejected"
    return None


def build(locator: str, view: dict) -> dict:
    """The Booking Status data for one Amadeus servicing view."""
    raw_travelers = view.get("travelers") or []
    travelers = []
    for i, t in enumerate(raw_travelers):
        given, surname = (t.get("firstName") or "").strip(), (t.get("lastName") or "").strip()
        travelers.append({"id": str(i + 1), "name": f"{given} {surname}".strip(), "given": given,
                          "surname": surname, "type": t.get("type"), "pax_ref": t.get("paxRef")})

    flights, flight_by_ref, airline_locators = [], {}, []
    for s in view.get("segments") or []:
        label = norm_flight_no(s.get("flight_number") or s.get("carrier_name"))
        try:
            seq = int(s.get("segmentRef"))
        except (TypeError, ValueError):
            seq = len(flights) + 1
        flight_by_ref[s.get("segmentRef")] = label
        status_text, state = _segment_status(s.get("status"))
        flights.append({
            "flight": label, "sequence": seq,
            "from": s.get("departure_airport"), "to": s.get("arrival_airport"),
            "departure": _when(s.get("departure_time")), "arrival": _when(s.get("arrival_time")),
            "class": s.get("bookingClass"),
            "airline_status_code": s.get("status"), "airline_status": status_text, "state": state,
        })
        conf = s.get("airlineConfirmationNumber")
        carrier = s.get("carrier_name") or label[:2]
        if conf and conf.upper() != locator.upper() and {"airline": carrier, "locator": conf} not in airline_locators:
            airline_locators.append({"airline": carrier, "locator": conf})
    flights.sort(key=lambda f: f["sequence"])

    # A lap infant's FA line is filed under its adult (PAX/INF prefix): the
    # infant is the INF passenger listed right after that adult.
    def doc_passenger(d: dict) -> str | None:
        if not d.get("infant"):
            return d.get("passenger")
        for i, t in enumerate(travelers):
            if t["name"] == d.get("passenger") and (t.get("type") or "").upper() != "INF":
                nxt = travelers[i + 1] if i + 1 < len(travelers) else None
                return nxt["name"] if nxt and (nxt.get("type") or "").upper() == "INF" else None
        return None

    tickets, extras = [], []
    for d in view.get("documents") or []:
        row = {"passenger": doc_passenger(d), "status": d.get("status"),
               "status_label": _DOC_LABEL.get(d.get("status"), d.get("status"))}
        if d.get("kind") == "ticket":
            tickets.append({**row, "number": d.get("number"), "issued": d.get("issued_on"),
                            "document_status": d.get("status"), "coupons": [], "coupons_note": COUPONS_NOTE})
        else:
            extras.append({**row, "description": "Paid service (EMD)", "flights": [], "emd": d.get("number")})

    by_ref = {t["pax_ref"]: t for t in travelers if t.get("pax_ref") and (t.get("type") or "").upper() != "INF"}
    special_services = []
    for r in view.get("special_requests") or []:
        code = (r.get("code") or "").upper()
        if not (code.endswith("ML") or code in _ASSIST_SSRS):
            continue
        special_services.append({
            "code": code, "description": _ASSIST_SSRS.get(code) or f"Special meal ({code})",
            "passenger": (by_ref.get(r.get("paxRef")) or {}).get("name"),
            "flights": [flight_by_ref[r["segmentRef"]]] if r.get("segmentRef") in flight_by_ref else [f["flight"] for f in flights],
            "status": _ssr_status(r.get("status")), "status_code": r.get("status"),
        })

    valid_tickets = [t for t in tickets if t["document_status"] == "issued"]
    states = {f["state"] for f in flights}
    if not flights:
        booking_status = "cancelled"
    elif states == {"cancelled"}:
        booking_status = "airline_cancelled"
    elif states - {"confirmed"}:
        booking_status = "airline_problem"
    else:
        booking_status = "ticketed" if valid_tickets else "confirmed"

    return {
        "provider": "AD",
        "locator_code": locator.upper(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "booking_status": booking_status,
        "content_source": None,
        "airline_locators": airline_locators,
        "travelers": [{k: v for k, v in t.items() if k != "pax_ref"} for t in travelers],
        "flights": flights,
        "tickets": tickets,
        "seats": [], "seats_note": SEATS_NOTE,
        "extras": extras,
        "special_services": special_services,
        "ticketed": bool(valid_tickets),
        "can_add_seats_extras": False,
        "can_add_special_requests": False,
        "manage_note": MANAGE_NOTE,
        "manage_token": None,
        "seat_requests": [], "seat_rules": [],
    }


def lookup(locator: str, last_name: str) -> dict:
    try:
        view = amadeus.servicing_view_sync(locator)
    except amadeus.AmadeusBookingError as e:
        if e.status_code == 404:
            raise NotFound() from e
        raise Unavailable(e.message) from e
    want = _norm(last_name)
    if not want or not any(_norm(t.get("lastName")) == want for t in view.get("travelers") or []):
        raise NotFound()
    return build(locator, view)
