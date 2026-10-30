"""
A booking exactly as Travelport and the airline hold it right now — for the
customer's "Live status" view in My Bookings. Nothing here comes from our
MySQL copy: Reservation Retrieve gives the flights with the airline's own
status per segment (HK/HX...), the airline record locators, every seat and
extra with its confirmation status and EMD, and the special service
requests; Ticket Retrieve gives each ticket's coupon status per flight.

Prices are left out on purpose: Travelport's figures are the airline's net
amounts, not what the customer paid us.
"""
import logging
from datetime import datetime, timezone

from config.api_endpoints import TravelportEndpoints
# The same raw Travelport call / error readers the cancel flow uses — they
# return Travelport's body even on a 4xx that carries Result.Error.
from services.cancel_service import _call, _errors, _msg, _reservation, CancelError
from services.segment_status import segment_statuses, is_confirmed, is_cancelled, norm_flight_no

logger = logging.getLogger(__name__)

# SSRs worth showing to a customer: meals (xxML) and assistance requests.
# Others on the PNR (TKNE ticket links, contacts, ASVC/EMD lines, passport
# data) are booking plumbing.
_ASSIST_SSRS = {
    "WCHR": "Wheelchair (ramp)", "WCHS": "Wheelchair (steps)", "WCHC": "Wheelchair (cabin seat)",
    "BLND": "Blind passenger", "DEAF": "Deaf passenger", "DPNA": "Disabled passenger needing assistance",
    "MAAS": "Meet and assist", "UMNR": "Unaccompanied minor", "BSCT": "Baby bassinet",
    "PETC": "Pet in cabin", "EXST": "Extra seat", "MEDA": "Medical case",
}


class LiveBookingError(Exception):
    pass


def _flight_label(f: dict) -> str:
    return norm_flight_no(f.get("carrier"), f.get("number"))


def _segment_state(status: tuple | None) -> str:
    if is_cancelled(status):
        return "cancelled"
    return "confirmed" if is_confirmed(status) else "pending"


def _ticket_coupons(number: str) -> dict:
    """Ticket Retrieve: coupon status per flight. GDS only; some carriers'
    pre-prod tickets answer "TICKET IS NOT VALID" — reported, not hidden."""
    try:
        body = _call("GET", TravelportEndpoints.retrieve_ticket_by_number(number))
    except CancelError as e:
        return {"error": str(e)}
    tickets = (body.get("TicketListResponse") or {}).get("TicketID") or []
    if not tickets:
        return {"error": _msg(_errors(body)) or "Travelport returned no ticket"}
    t = tickets[0]
    return {
        "issued": (t.get("AgencyInfo") or {}).get("ticketedDate"),
        "validating_carrier": (t.get("ValidatingCarrier") or {}).get("value") if isinstance(t.get("ValidatingCarrier"), dict) else t.get("ValidatingCarrier"),
        "coupons": [{
            "flight": norm_flight_no(s.get("Carrier"), s.get("Number")),
            "from": (s.get("Departure") or {}).get("location"),
            "to": (s.get("Arrival") or {}).get("location"),
            "date": (s.get("Departure") or {}).get("date"),
            "status": s.get("Status"),
            "flight_status": s.get("FlightStatusCode"),
        } for s in t.get("TicketSegment") or []],
    }


def get_live_booking(locator_code: str, with_coupons: bool = True) -> dict:
    body = _call("GET", TravelportEndpoints.retrieve_reservation(locator_code))
    res = _reservation(body)
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not res.get("Offer") and not res.get("Receipt"):
        errors = _errors(body)
        if any(str(e.get("SourceCode") or e.get("StatusCode") or "") == "4422" or "CANCELLED" in (e.get("Message") or "").upper()
               for e in errors):
            return {"locator_code": locator_code, "retrieved_at": retrieved_at, "booking_status": "cancelled",
                    "message": _msg(errors), "flights": [], "travelers": [], "tickets": [], "seats": [],
                    "extras": [], "special_services": [], "airline_locators": []}
        raise LiveBookingError(_msg(errors) or "Travelport returned no booking for this PNR")

    travelers = {}
    for t in res.get("Traveler") or []:
        name = t.get("PersonName") or {}
        travelers[t.get("id")] = {"id": t.get("id"), "name": f"{name.get('Given', '')} {name.get('Surname', '')}".strip(),
                                  "type": t.get("passengerTypeCode")}

    # ── Flights, with the airline's own status ───────────────────────────────
    seg_status = segment_statuses(res)
    flights, flight_by_ref, cabin_by_seq = [], {}, {}
    for o in res.get("Offer") or []:
        for p in o.get("Product") or []:
            for pf in p.get("PassengerFlight") or []:
                for fp in pf.get("FlightProduct") or []:
                    for seq in fp.get("segmentSequence") or []:
                        cabin_by_seq[seq] = {"cabin": fp.get("cabin"), "class": fp.get("classOfService"),
                                             "fare_basis": fp.get("fareBasisCode")}
            for fs in p.get("FlightSegment") or []:
                f = fs.get("Flight") or {}
                label = _flight_label(f)
                flight_by_ref[f.get("id")] = label
                status = seg_status.get(label)
                dep, arr = f.get("Departure") or {}, f.get("Arrival") or {}
                flights.append({
                    "flight": label, "sequence": fs.get("sequence"),
                    "from": dep.get("location"), "to": arr.get("location"),
                    "departure": f"{dep.get('date', '')} {dep.get('time', '')}".strip(),
                    "arrival": f"{arr.get('date', '')} {arr.get('time', '')}".strip(),
                    "aircraft": f.get("equipment"),
                    **cabin_by_seq.get(fs.get("sequence"), {}),
                    "airline_status_code": status[0] if status else None,
                    "airline_status": status[1] if status else None,
                    "state": _segment_state(status),
                })
    flights.sort(key=lambda x: x.get("sequence") or 0)

    # Airline record locators. A hosted airline's record carries its host
    # system's code (UL -> "1A", Amadeus), so name the airline instead.
    carriers = list(dict.fromkeys(f["flight"][:2] for f in flights))
    airline_locators = []
    for r in res.get("Receipt") or []:
        loc = (r.get("Confirmation") or {}).get("Locator") or {}
        if loc.get("value") and loc.get("source") != "1G" and loc["value"] != locator_code:
            source = loc.get("source")
            airline = carriers[0] if source in ("1A", "1S", "1P", "1V", "1B") and len(carriers) == 1 else source
            airline_locators.append({"airline": airline, "locator": loc["value"]})

    # ── Tickets / EMDs from the payment receipts ─────────────────────────────
    tickets, emd_by_offer = [], {}
    for r in res.get("Receipt") or []:
        if r.get("@type") != "ReceiptPayment":
            continue
        for d in r.get("Document") or []:
            trav = travelers.get((d.get("TravelerIdentifierRef") or {}).get("id"), {})
            if d.get("@type") == "DocumentTicket" and d.get("Number"):
                tickets.append({"number": d["Number"], "passenger": trav.get("name"), "type": trav.get("type")})
            elif d.get("@type") == "DocumentEMD" and d.get("Number"):
                for ref in r.get("OfferRef") or []:
                    emd_by_offer[ref] = d["Number"]
    if with_coupons:
        for t in tickets:
            t.update(_ticket_coupons(t["number"]))

    # ── Seats and extras, each with the airline's confirmation ───────────────
    product_status, traveler_of = {}, {}
    for tp in res.get("TravelerProduct") or []:
        if tp.get("OfferRef"):
            traveler_of[tp["OfferRef"]] = tp.get("TravelerRef")
            if tp.get("ConfirmationStatusEnum"):
                product_status[tp["OfferRef"]] = tp["ConfirmationStatusEnum"]
    for r in res.get("Receipt") or []:
        for st in (((r.get("Confirmation") or {}).get("OfferStatus") or {}).get("StatusAncillary") or []):
            for ref in r.get("OfferRef") or []:
                product_status.setdefault(ref, st.get("value"))

    seats, extras, extra_ssr_codes = [], [], set()
    for o in res.get("Offer") or []:
        for p in o.get("Product") or []:
            if p.get("@type") != "ProductAncillary":
                continue
            anc = p.get("Ancillary") or {}
            extra_ssr_codes.update(d.get("ssrCode") for d in anc.get("Description") or [] if d.get("ssrCode"))
            desc = next((d.get("value") for d in anc.get("Description") or [] if d.get("value") and not d["value"].startswith("**")), None)
            # A requested seat can come back with no TravelerProduct link; on
            # a one-passenger booking it can only be theirs.
            only = next(iter(travelers.values()))["name"] if len(travelers) == 1 else None
            item = {
                "passenger": travelers.get(traveler_of.get(o.get("id")), {}).get("name") or only,
                "flights": [flight_by_ref.get(ref, ref) for ref in anc.get("FlightRef") or []],
                "status": product_status.get(o.get("id")) or "Requested",
                "emd": emd_by_offer.get(o.get("id")),
            }
            if anc.get("@type") == "AncillaryAirSeat":
                seat = ((anc.get("SeatAssignment") or {}).get("Seat") or "").lstrip("0")
                seats.append({**item, "seat": seat, "description": desc})
            else:
                kg = next((m.get("value") for m in anc.get("Measurement") or [] if m.get("measurementType") == "Weight"), None)
                extras.append({**item, "description": desc or anc.get("@type", "").replace("AncillaryAir", "") or "Extra",
                               "weight_kg": kg, "quantity": p.get("Quantity") or anc.get("quantity")})

    # ── Special service requests (meals, assistance) ─────────────────────────
    special_services = []
    for s in res.get("SpecialService") or []:
        code = s.get("SSRCode")
        # A meal bought as an extra also appears here as its SSR — shown once, under Extras.
        if not code or code in extra_ssr_codes or not (code.endswith("ML") or code in _ASSIST_SSRS):
            continue
        seqs = (s.get("AppliesTo") or {}).get("SegmentSequenceList") or []
        special_services.append({
            "code": code, "description": _ASSIST_SSRS.get(code) or f"Special meal ({code})",
            "passenger": travelers.get((s.get("TravelerIdentifier") or {}).get("TravelerRef"), {}).get("name"),
            "flights": [f["flight"] for f in flights if f.get("sequence") in seqs] or [f["flight"] for f in flights],
            "status": (s.get("Status") or {}).get("value") or "Requested",
        })

    states = {f["state"] for f in flights}
    booking_status = ("airline_cancelled" if states == {"cancelled"} else "airline_problem" if states - {"confirmed"}
                      else "ticketed" if tickets else "confirmed") if flights else ("ticketed" if tickets else "unknown")
    return {
        "locator_code": locator_code,
        "retrieved_at": retrieved_at,
        "booking_status": booking_status,
        "airline_locators": airline_locators,
        "travelers": list(travelers.values()),
        "flights": flights,
        "tickets": tickets,
        "seats": seats,
        "extras": extras,
        "special_services": special_services,
    }
