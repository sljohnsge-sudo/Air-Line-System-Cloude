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


def _normalize_offer(raw_offer: dict, idx: int, pricing_settings: dict) -> dict | None:
    segs = raw_offer.get("segments") or []
    if not segs:
        return None

    norm_segs = []
    for s in segs:
        carrier_code = s.get("marketingCarrier") or ""
        norm_segs.append({
            "departure_airport": s.get("from"),
            "arrival_airport": s.get("to"),
            "departure_time": _to_iso(s.get("departureDate"), s.get("departureTime")),
            "arrival_time": _to_iso(s.get("arrivalDate"), s.get("arrivalTime")),
            "carrier_name": IATA_AIRLINE_NAMES.get(carrier_code, carrier_code),
            "flight_number": f"{carrier_code}{s.get('flightNumber', '')}",
        })

    first, last = norm_segs[0], norm_segs[-1]
    carrier_code = segs[0].get("marketingCarrier") or ""
    try:
        net_price = float(raw_offer.get("totalPrice") or 0)
    except (TypeError, ValueError):
        net_price = 0.0

    # Admin-configurable markup (Admin Portal -> Pricing & Markup). Amadeus
    # fares were never marked up at all before this -- 'shared' reuses
    # Travelport's own ticket markup (category "ticket"), 'separate' uses
    # its own amadeus_ticket_markup_* values (category "amadeus_ticket").
    category = "amadeus_ticket" if pricing_settings.get("markup_scope") == "separate" else "ticket"
    price, _scale = apply_markup(net_price, pricing_settings, category)

    return {
        "offer_id": f"AD-{idx}",
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
        "segments": norm_segs if len(norm_segs) > 1 else None,
        "fare_source": "GDS",
        "price": price,
        "currency": raw_offer.get("currency") or "LKR",
        "bookable": True,
        "fare_options": [{
            "price": price,
            "currency": raw_offer.get("currency") or "LKR",
            "cabin_class": segs[0].get("bookingClass") or "Economy",
            "brand_name": None,
            "baggage_allowance": [],
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
    max_results: int = 20,
) -> list[dict]:
    """One-way search only (the Amadeus backend's flat segment list can't yet
    be split back into outbound/return legs for round-trip display -- see the
    note in flight_search_service._parse_reply on the Amadeus side). Raises
    on any failure; the caller (main.py) turns that into an admin notification
    rather than swallowing it, since a silent gap here means real fares are
    missing from the comparison without anyone knowing."""
    params = {
        "origin": origin,
        "destination": destination,
        "departureDate": departure_date,
        "adults": adult_count,
        "children": child_count,
        "infants": infant_count,
        "maxResults": max_results,
    }
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.get(f"{AMADEUS_BACKEND_URL}/api/flights/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    pricing_settings = get_pricing_settings()
    offers = data.get("offers") or []
    normalized = [_normalize_offer(o, i, pricing_settings) for i, o in enumerate(offers)]
    return [o for o in normalized if o is not None]
