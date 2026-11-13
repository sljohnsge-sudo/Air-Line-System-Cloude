"""
services/amadeus_aggregator.py
================================
Calls the sibling Amadeus system's own backend (Air-Line-System-Amadeus,
default http://localhost:8002) over HTTP and normalizes its offers into the
same shape the frontend already renders for Travelport offers, tagged
source="AD" (Travelport offers are tagged source="TP" by the caller).

The two systems stay fully independent codebases/databases (see the note at
the top of main.py) -- this module only ever makes an outbound HTTP call to
the Amadeus backend's public search endpoint, exactly like a browser would.

Each offer carries its raw (unconverted, Amadeus-native DDMMYY/HHMM) segment
list in fare_options[0].raw_offering.segments -- the exact shape
/api/amadeus-bookings/confirm (this backend's proxy to the Amadeus backend's
own /api/bookings/confirm) needs to sell it. Note: Amadeus ticket issuance is
still blocked by errorCode 2011 "ENTRY NOT AUTHORISED" (an unresolved
office-authority gate on the Amadeus account, confirmed live, not a code
defect) -- a PNR will confirm, but issuing the actual ticket will likely fail
until Amadeus grants that authority. The frontend's Amadeus booking flow
handles that outcome explicitly rather than treating it as a crash.
"""

import os
from datetime import date
import httpx

from services.search_service import IATA_AIRLINE_NAMES
from services.pricing_service import get_settings as get_pricing_settings, apply_markup

AMADEUS_BACKEND_URL = os.getenv("AMADEUS_BACKEND_URL", "http://localhost:8002")


def _to_iso(date_ddmmyy: str | None, time_hhmm: str | None) -> str:
    """Amadeus dates are DDMMYY, times HHMM (e.g. '151026', '0910') -- convert
    to the 'YYYY-MM-DDTHH:MM' shape the frontend's ItineraryRow already parses."""
    if not date_ddmmyy or len(date_ddmmyy) != 6:
        return ""
    dd, mm, yy = date_ddmmyy[0:2], date_ddmmyy[2:4], date_ddmmyy[4:6]
    t = (time_hhmm or "0000").zfill(4)
    return f"20{yy}-{mm}-{dd}T{t[0:2]}:{t[2:4]}"


def _norm_segments(segs: list[dict]) -> list[dict]:
    norm_segs = []
    for s in segs:
        carrier_code = s.get("marketingCarrier") or ""
        norm_segs.append({
            "departure_airport": s.get("from"),
            "arrival_airport": s.get("to"),
            "departure_time": _to_iso(s.get("departureDate"), s.get("departureTime")),
            "arrival_time": _to_iso(s.get("arrivalDate"), s.get("arrivalTime")),
            # Field names match Travelport's own segment shape exactly (see
            # ticket_service.py's _parse_reservation_product) so the SAME
            # frontend segment table renders both sources identically --
            # previously dropped here even though flight_search_service.py
            # already parses them from the raw Amadeus response.
            "departure_terminal": s.get("departureTerminal"),
            "arrival_terminal": s.get("arrivalTerminal"),
            "carrier_name": IATA_AIRLINE_NAMES.get(carrier_code, carrier_code),
            "flight_number": f"{carrier_code}{s.get('flightNumber', '')}",
        })
    return norm_segs


def _leg_fields(segs: list[dict]) -> dict:
    """The flight fields of one leg (or of a whole one-way trip), in the same
    shape as a Travelport one-way offer -- the results card draws a round
    trip's legs with the same ItineraryRow as a one-way trip."""
    norm_segs = _norm_segments(segs)
    first, last = norm_segs[0], norm_segs[-1]
    carrier_code = segs[0].get("marketingCarrier") or ""
    return {
        "source": "AD",
        "airline": IATA_AIRLINE_NAMES.get(carrier_code, carrier_code),
        "airline_code": carrier_code,
        "flight_number": first["flight_number"],
        "aircraft_type": segs[0].get("equipment"),
        "stops": len(norm_segs) - 1,
        "departure_airport": first["departure_airport"],
        "arrival_airport": last["arrival_airport"],
        "departure_time": first["departure_time"],
        "arrival_time": last["arrival_time"],
        # Promoted to top level too (not just inside segments) because
        # non-stop offers send segments=None below -- the receipt's
        # single-segment fallback display reads these top-level fields
        # instead, same pattern as departure_airport/arrival_airport.
        "departure_terminal": first["departure_terminal"],
        "arrival_terminal": last["arrival_terminal"],
        "segments": norm_segs if len(norm_segs) > 1 else None,
        "fare_source": "GDS",
    }


# Amadeus cabin codes (MasterPricer cabinProduct/cabin) -> the cabin names the
# website uses for Travelport fares (14/11/2026). Before this the booking
# class letter (e.g. "O") was shown as the cabin.
_CABIN_NAMES = {"M": "Economy", "Y": "Economy", "W": "Premium Economy", "C": "Business", "F": "First"}


def _cabin_name(segs: list[dict]) -> str | None:
    codes = [str(s.get("cabin") or "").upper() for s in segs if s.get("cabin")]
    if not codes:
        return None
    # The fare's main cabin: the highest one any flight is booked in.
    order = ["F", "C", "W", "M", "Y"]
    best = min(codes, key=lambda c: order.index(c) if c in order else len(order))
    return _CABIN_NAMES.get(best, best)


def customer_price(net_price: float, pricing_settings: dict, tickets: int = 1) -> float:
    """The price a customer pays for an Amadeus fare: the Amadeus fare plus
    the admin's markup (Admin Portal -> Pricing & Markup). Used for the
    search results AND when the booking is held (13/11/2026), so the amount
    charged is worked out the same way as the amount shown. A fixed markup is
    per ticket (one per passenger), the same rule as Travelport fares."""
    category = "amadeus_ticket" if pricing_settings.get("markup_scope") == "separate" else "ticket"
    return apply_markup(float(net_price or 0), pricing_settings, category, max(int(tickets or 1), 1))[0]


def _normalize_offer(raw_offer: dict, idx: int, pricing_settings: dict, round_trip: bool = False,
                     tickets: int = 1) -> dict | None:
    segs = raw_offer.get("segments") or []
    if not segs:
        return None

    # 13/11/2026: a round trip comes back as one flat list of flights, each
    # tagged with its leg (1 = outbound, 2 = return) by the Amadeus backend.
    # It is shown as two legs, like a Travelport round trip, and booked as
    # one PNR with every flight (raw_offering.segments keeps them all).
    legs = None
    if round_trip:
        outbound = [s for s in segs if s.get("leg") == 1]
        inbound = [s for s in segs if s.get("leg") == 2]
        if not outbound or not inbound or len(outbound) + len(inbound) != len(segs):
            return None  # can't tell the two legs apart -- never show half a trip
        legs = [_leg_fields(outbound), _leg_fields(inbound)]
        flight = dict(legs[0])
    else:
        flight = _leg_fields(segs)
    try:
        net_price = float(raw_offer.get("totalPrice") or 0)
    except (TypeError, ValueError):
        net_price = 0.0

    # Admin-configurable markup (Admin Portal -> Pricing & Markup). Amadeus
    # fares were never marked up at all before this -- 'shared' reuses
    # Travelport's own ticket markup (category "ticket"), 'separate' uses
    # its own amadeus_ticket_markup_* values (category "amadeus_ticket").
    price = customer_price(net_price, pricing_settings, tickets)
    # Seats left in the booked class (Amadeus avlStatus, per flight): the
    # lowest across the trip, shown like Travelport's "seats left".
    seats = [int(s["seatsAvailable"]) for s in segs if str(s.get("seatsAvailable") or "").isdigit()]
    seats_left = min(seats) if seats else None

    round_trip_fields = {"is_round_trip": True, "legs": legs} if legs else {}
    return {
        **flight,
        **round_trip_fields,
        "offer_id": f"AD-{idx}",
        "price": price,
        "currency": raw_offer.get("currency") or "LKR",
        "bookable": True,
        "fare_options": [{
            "price": price,
            "currency": raw_offer.get("currency") or "LKR",
            # Real cabin name from Amadeus; no fare-family (brand) name until
            # Amadeus turns fare families on for this office.
            "cabin_class": _cabin_name(segs),
            "brand_name": None,
            "fare_basis_codes": list(dict.fromkeys(s.get("fareBasis") for s in segs if s.get("fareBasis"))),
            "classes_of_service": list(dict.fromkeys(s.get("bookingClass") for s in segs if s.get("bookingClass"))),
            "seats_remaining": seats_left,
            # Master Pricer returns baggage in one shared serviceFeesGrp
            # block (not nested per-recommendation) -- the reference chain
            # back to a specific offer WAS found (recommendation's own
            # itemNumber/itemNumberId/number equals the service item's "F"
            # reference), confirmed against a live response; see
            # flight_search_service.py's _parse_baggage_by_fare_ref for the
            # full reconstruction. Only the first few (cheapest)
            # recommendations get this enrichment from the GDS -- an empty
            # list for the rest is normal, not a parsing gap.
            "baggage_allowance": raw_offer.get("baggageAllowance") or [],
            "change_policy": None,
            "cancel_policy": None,
            # Raw Amadeus-native segments (DDMMYY/HHMM, as returned by the
            # Amadeus backend's own search) -- sent as-is to
            # /api/amadeus-bookings/confirm at booking time.
            "raw_offering": {"segments": segs},
        }],
    }


async def search_amadeus_flights(
    origin: str, destination: str, departure_date: str,
    adult_count: int = 1, child_count: int = 0, infant_count: int = 0,
    max_results: int = 20, return_date: str | None = None,
) -> list[dict]:
    """One-way, or a round trip when return_date is given (13/11/2026: the
    Amadeus backend now tags each flight with its leg, so the reply can be
    split into outbound and return). Raises on any failure; the caller
    (main.py) turns that into an admin notification rather than swallowing
    it, since a silent gap here means real fares are missing from the
    comparison without anyone knowing."""
    params = {
        "origin": origin,
        "destination": destination,
        "departureDate": departure_date,
        "adults": adult_count,
        "children": child_count,
        "infants": infant_count,
        "maxResults": max_results,
    }
    if return_date:
        params["returnDate"] = return_date
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.get(f"{AMADEUS_BACKEND_URL}/api/flights/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    pricing_settings = get_pricing_settings()
    offers = data.get("offers") or []
    tickets = (adult_count or 0) + (child_count or 0) + (infant_count or 0) or 1
    normalized = [_normalize_offer(o, i, pricing_settings, round_trip=bool(return_date), tickets=tickets)
                  for i, o in enumerate(offers)]
    return [o for o in normalized if o is not None]
