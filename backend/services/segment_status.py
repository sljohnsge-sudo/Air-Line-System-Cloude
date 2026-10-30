"""
The airline's own status for each flight on a PNR, read from the
reservation's Receipt[].Confirmation.OfferStatus.StatusAir — e.g.
{"flightRefs": ["Flight_01"], "code": "HK", "value": "Confirmed"}.

A PNR can exist on Travelport while the airline has dropped the flight:
confirmed live 2026-10-30, UL's system cancelled (HX) segments ~10 seconds
after booking when it saw a duplicate traveller (PNRs HNHK1D, HNHK1V). Such
a booking can't take seats/extras and can't be ticketed, so it must not be
shown as Confirmed or sent to payment.
"""
import re

# HK confirmed, KK confirmed by airline, RR reconfirmed, TK schedule change
# (confirmed), KL confirmed from waitlist.
CONFIRMED_CODES = {"HK", "KK", "RR", "TK", "KL"}
# HX cancelled by airline, UN flight not operating, UC unable (no waitlist),
# NO no action taken, XX/XK/XL/XR cancelled.
CANCELLED_CODES = {"HX", "UN", "UC", "NO", "XX", "XK", "XL", "XR"}

AIRLINE_CANCELLED = "Airline Cancelled"
AWAITING_AIRLINE = "Awaiting Airline"


def norm_flight_no(carrier: str | None, number: str | None = None) -> str:
    """"UL0225", ("UL", "0225") and "UL225" are the same flight."""
    text = f"{carrier or ''}{number or ''}".upper().replace(" ", "")
    return re.sub(r"^([A-Z0-9]{2})0+(?=\d)", r"\1", text)


def segment_statuses(reservation: dict) -> dict:
    """normalised flight number ("UL225") -> (code, value), e.g. ("HX", "Cancelled")."""
    flights = {}
    for o in reservation.get("Offer", []) or []:
        for p in o.get("Product", []) or []:
            for fs in p.get("FlightSegment", []) or []:
                f = fs.get("Flight") or {}
                if f.get("id"):
                    flights[f["id"]] = norm_flight_no(f.get("carrier"), f.get("number"))
    out = {}
    for r in reservation.get("Receipt", []) or []:
        for s in (((r.get("Confirmation") or {}).get("OfferStatus") or {}).get("StatusAir") or []):
            for ref in s.get("flightRefs") or []:
                if ref in flights:
                    out[flights[ref]] = (s.get("code"), s.get("value"))
    return out


def is_confirmed(status: tuple | None) -> bool:
    """No status at all (NDC, or nothing in the receipt) counts as confirmed:
    only a status the airline actually sent can hold a booking back."""
    return not status or status[0] in CONFIRMED_CODES or (status[1] or "").lower() == "confirmed"


def is_cancelled(status: tuple | None) -> bool:
    return bool(status) and (status[0] in CANCELLED_CODES or (status[1] or "").lower() == "cancelled")


def booking_problem(statuses: dict) -> dict | None:
    """{"status", "message"} when any flight isn't confirmed by the airline,
    else None. Cancelled wins over awaiting."""
    def _list(flights):
        return ", ".join(f"{f} ({statuses[f][0]})" for f in flights)

    cancelled = [f for f, s in statuses.items() if is_cancelled(s)]
    if cancelled:
        return {"status": AIRLINE_CANCELLED, "message": (
            f"The airline has cancelled {_list(cancelled)} on this booking, so it can't be paid for or ticketed. "
            "No payment has been taken for it. Please search again or contact us.")}
    waiting = [f for f, s in statuses.items() if not is_confirmed(s)]
    if waiting:
        return {"status": AWAITING_AIRLINE, "message": (
            f"The airline hasn't confirmed {_list(waiting)} on this booking yet, so payment can't be taken. "
            "Please try again shortly or contact us.")}
    return None
