"""
main.py
=======
FastAPI Application — Travelport TripServices API Gateway
All flight data, bookings, and tickets are sourced live from Travelport.
No mock data exists in this system.

NOTE: There is a separate Amadeus GDS system living at ./amadeus-backend
(port 8002) and its own standalone frontend at the sibling project
Air-Line-System-Amadeus/frontend (port 5175), using Amadeus Web Services
instead of Travelport. amadeus-backend sits inside this repo purely for
filesystem convenience (one folder tree to browse/edit) -- it is still a
fully independent codebase/database (own .env, own DB, no shared imports
with this backend's code). The only connection is an outbound HTTP call
from /api/flights/search to the Amadeus backend's own public search
endpoint (see services/amadeus_aggregator.py), exactly like a browser would
call it -- same as if it were still a sibling folder. Results from both are
merged and tagged offer["source"] = "TP" (Travelport) or "AD" (Amadeus) so
the frontend can show where each fare came from. AD offers ARE bookable
(see services/amadeus_booking_proxy.py and the amadeus_* booking steps in
frontend/src/App.jsx) -- ticket issuance is separately blocked by an
unresolved Amadeus office-authority gate (errorCode 2011), not by code.
An admin can also turn either provider off entirely from the Admin
Portal's Provider Control tab (see database.get_provider_settings).

Booking Workflow:
    POST  /api/flights/search          → STEP 2: Search flights
    POST  /api/bookings/create         → STEPS 4-9: Full booking + ticket issuance
    GET   /api/bookings/retrieve/{pnr} → STEP 8: Retrieve PNR details
    GET   /api/bookings/history        → Local cache of issued tickets
    POST  /api/bookings/{pnr}/cancel   → Cancel reservation on Travelport + local cache
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime
import httpx
from fastapi import FastAPI, HTTPException, Query, status, Depends, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, model_validator
from typing import Optional, List, Literal
import database
import services
import auth
import hotel_database
from services import hotel_search_service, hotel_booking_service
from services.hotel_common import HotelApiError
from services import indigo_search_service, indigo_booking_service
from services.indigo_uapi_client import IndigoApiError

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="George Steuart Travel — Flight Booking API",
    description="Live Travelport TripServices v11 integration. No mock data.",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Stale-offer error classification ─────────────────────────────────────────
# The booking flow prices/validates a real-time GDS offer twice more after
# search (AirPrice, then Add Offer with validateInventoryInd=true) — by
# design, so a class/fare/seat that sold out between search and submit is
# caught before a bad reservation is attempted, rather than silently booking
# the wrong thing. Travelport's own text for that ("CLASS OF SERVICE DOES NOT
# EXIST FOR REQUESTED FLIGHTS", etc.) is meaningless to a customer, so this
# turns it into a clear, actionable message instead of raw GDS jargon — the
# original text is still logged server-side for diagnostics.
_STALE_OFFER_MARKERS = (
    "CLASS OF SERVICE DOES NOT EXIST",
    "FLIGHT IS NOT AVAILABLE",
    "OFFER IS NOT AVAILABLE",
    "FARE IS NOT AVAILABLE",
    "0 AVAIL",
    "SOLD OUT",
    "NOT AVAILABLE AND CANNOT BE BOOKED",
)


def friendly_travelport_error(raw_message: str) -> str:
    """Returns a customer-facing message for a known stale-offer/availability
    error, or the original message unchanged if it doesn't match one."""
    upper = (raw_message or "").upper()
    if any(marker in upper for marker in _STALE_OFFER_MARKERS):
        return (
            "This flight's fare or availability has changed since you searched. "
            "Please go back and search again to see current prices."
        )
    return raw_message


# ── Uploaded Images (Admin Portal → Tour Packages poster images) ────────────────
# Saved to disk under backend/uploads/<category>/ and served back at /uploads/...;
# admin_upload_image below is the only writer. Not used for any Travelport data —
# purely a place for admin-curated marketing images (package posters, etc).
UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(os.path.join(UPLOAD_ROOT, "packages"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")


# ── Request / Response Schemas ─────────────────────────────────────────────────

class SearchLeg(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3, description="IATA code e.g. CMB")
    destination: str = Field(..., min_length=3, max_length=3, description="IATA code e.g. DXB")
    departure_date: str = Field(..., description="Date in YYYY-MM-DD format")


class FlightSearchRequest(BaseModel):
    """STEP 2: Flight search parameters."""
    origin: Optional[str] = Field(None, min_length=3, max_length=3, description="IATA code e.g. CMB")
    destination: Optional[str] = Field(None, min_length=3, max_length=3, description="IATA code e.g. DXB")
    departure_date: Optional[str] = Field(None, description="Date in YYYY-MM-DD format")
    legs: Optional[List[SearchLeg]] = Field(None, description="List of search legs for multi-leg search")
    adult_count: int = Field(default=1, ge=1, le=9)
    child_count: int = Field(default=0, ge=0, le=8)
    infant_count: int = Field(default=0, ge=0, le=8)
    cabin_preference: Optional[str] = Field(default=None, description="Economy|Business|First")
    content_source: Optional[str] = Field(default=None, description="Per-request override: 'GDS' or 'NDC' — defaults to server config when omitted")


class TravelerInfo(BaseModel):
    """Passenger details for STEP 6."""
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str = Field(..., min_length=2, max_length=50)
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    gender: str = Field(..., description="Male or Female")
    passport_number: str = Field(..., min_length=5, max_length=20)
    passport_expiry: str = Field(..., description="YYYY-MM-DD")
    nationality: str = Field(default="LK", min_length=2, max_length=3)
    passport_issue_country: str = Field(default="LK", min_length=2, max_length=3, description="TravelDocument.issueCountry — the country that issued the passport, which can differ from nationality")
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    passenger_type: Optional[str] = Field(default="ADT", description="ADT|CNN|INF")

    # ── Optional Travelport TravelDocument / Telephone / Address fields ──────
    # All optional — not required for a booking to succeed (proven by existing
    # live bookings), but Travelport's own sample request includes them.
    birth_place: Optional[str] = Field(default=None, description="TravelDocument.birthPlace")
    document_issue_date: Optional[str] = Field(default=None, description="TravelDocument.issueDate, YYYY-MM-DD")
    issued_for_geo_political_area: Optional[str] = Field(
        default=None, description="TravelDocument.IssuedForGeoPoliticalArea.value — country the document is valid for"
    )
    phone_area_city_code: Optional[str] = Field(default=None, description="Telephone.areaCityCode")
    phone_extension: Optional[str] = Field(default=None, description="Telephone.extension")
    phone_city_code: Optional[str] = Field(default=None, description="Telephone.cityCode (legacy GDS city code)")
    address_street: Optional[str] = Field(default=None, description="TravelDocument.Address.Street")
    address_city: Optional[str] = Field(default=None, description="TravelDocument.Address.City")
    address_state_name: Optional[str] = Field(default=None, description="TravelDocument.Address.StateProv.name")
    address_state_value: Optional[str] = Field(default=None, description="TravelDocument.Address.StateProv.value")
    address_country: Optional[str] = Field(default=None, description="TravelDocument.Address.Country.value")
    address_postal_code: Optional[str] = Field(default=None, description="TravelDocument.Address.PostalCode")


class BookingCreateRequest(BaseModel):
    """
    Full booking request: selected offer + traveler details.
    Triggers STEPS 4 → 9 in one call.
    """
    raw_offering: dict = Field(..., description="Full raw offer object from search response")
    travelers: List[TravelerInfo]
    payment_method: Optional[str] = "card"
    cabin_class: Optional[str] = None
    fare_family: Optional[str] = None
    custom_price: Optional[float] = None


class BookingInitiateRequest(BaseModel):
    """Initiates booking and retrieves seatmap."""
    raw_offering: dict = Field(..., description="Full raw offer object from search response")
    travelers: List[TravelerInfo]


class SelectedSeat(BaseModel):
    """Traveler seat selection choice."""
    passenger_idx: int
    seat_number: str
    price: float
    currency: str = "LKR"
    type: str = "Standard"


class BookingConfirmRequest(BaseModel):
    """
    Confirms booking: creates a fresh GDS workbench at commit time to avoid
    WORKBENCH ID IS NOT VALID errors caused by session TTL expiry.
    raw_offering is used to rebuild the workbench fresh on every confirm.
    """
    raw_offering: dict = Field(..., description="Full raw offer object from search response")
    travelers: List[TravelerInfo]
    selected_seats: List[SelectedSeat]
    payment_method: Optional[str] = "card"
    cabin_class: Optional[str] = None
    fare_family: Optional[str] = None
    custom_price: Optional[float] = None
    reference_payload_only: bool = False
    # Legacy fields — kept for backward compat but no longer used at commit time
    workbench_id: Optional[str] = None
    offer_id: Optional[str] = None


class CancelRequest(BaseModel):
    locator_code: str


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "George Steuart Travel — Live Travelport API",
        "version": "2.0.0",
        "status": "online",
        "data_source": "Travelport TripServices v11",
        "endpoints": {
            "search":   "POST /api/flights/search",
            "book":     "POST /api/bookings/create",
            "retrieve": "GET  /api/bookings/retrieve/{pnr}",
            "history":  "GET  /api/bookings/history",
            "cancel":   "POST /api/bookings/{pnr}/cancel",
            "docs":     "/docs"
        }
    }


# ── Airport Reference Data ───────────────────────────────────────────────────

@app.get("/api/reference/airports")
def get_airports(
    q: str = Query("", description="Search term for airports (IATA code, city, name)"),
    country: str = Query("", description="ISO alpha-2 country code to restrict results to (e.g. AE)"),
):
    """
    Search local airport cache of 7900+ airports. Optional `country` filter
    feeds the Hotel destination Country/City selector.
    """
    try:
        limit = 300 if (country and not q) else 15
        results = database.search_airports(q.strip(), limit=limit, country=country.strip())
        return {"airports": results}
    except Exception as e:
        logger.error(f"Airport search failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Airport reference lookup error: {str(e)}"
        )


@app.get("/api/reference/countries")
def get_airport_countries():
    """
    Countries that have at least one airport/city in our reference data —
    feeds the Hotel destination Country dropdown (see get_airports above for
    the paired City list, filtered by the chosen country).
    """
    try:
        return {"countries": database.get_airport_countries()}
    except Exception as e:
        logger.error(f"Country list lookup failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Country reference lookup error: {str(e)}"
        )


# ── STEP 2: Flight Search ──────────────────────────────────────────────────────


_NOTIFICATION_DEBOUNCE_MINUTES = 15


def _notify_provider_failure(provider: str, error_message: str, route_info: str | None) -> None:
    """Logs a provider_notifications row and, unless the same provider already
    emailed within the debounce window (an outage shouldn't flood the inbox
    with one email per failed search), sends the alert to every active
    notification_recipients address."""
    recent = database.get_recent_provider_notification(provider, _NOTIFICATION_DEBOUNCE_MINUTES)
    recipients = [r["email"] for r in database.get_notification_recipients(active_only=True)]
    should_email = bool(recipients) and recent is None

    email_sent = False
    if should_email:
        sent, err = services.send_provider_failure_email(", ".join(recipients), provider, error_message, route_info)
        email_sent = sent
        if not sent:
            logger.warning(f"Provider-failure notification email not sent: {err}")

    try:
        database.create_provider_notification(provider, error_message, route_info, email_sent)
    except Exception as db_err:
        logger.error(f"Failed to log provider_notifications row: {db_err}")


@app.post("/api/flights/search")
async def search_flights(request: FlightSearchRequest):
    """
    STEP 2 — Search available flights. Merges live Travelport ("TP") results
    with the sibling Amadeus system's live results ("AD") — each offer is
    tagged with .source so the frontend can label where the fare came from.
    An admin can switch this to Travelport-only or Amadeus-only from the
    Admin Portal's Provider Control tab (see database.get_provider_settings) —
    the disabled provider simply isn't queried at all. Amadeus is only wired
    for a plain one-way search today (no legs/round trip) — see
    services/amadeus_aggregator.py for why. If an enabled provider fails,
    that provider's fares are simply missing from this response (the other
    provider's results still come back) and an admin notification is raised
    — see _notify_provider_failure.
    """
    if request.legs:
        leg_str = " | ".join([f"{l.origin}->{l.destination} on {l.departure_date}" for l in request.legs])
        logger.info(f"Flight search (multi-leg): {leg_str}")
        route_info = leg_str
    else:
        logger.info(f"Flight search: {request.origin} → {request.destination} on {request.departure_date}")
        route_info = f"{request.origin} → {request.destination} on {request.departure_date}"

    legs = [l.model_dump() for l in request.legs] if request.legs else None

    # Admin on/off switch (Admin Portal -> Provider Control) -- lets staff
    # take a GDS out of the unified search entirely (contract issue, outage,
    # or just wanting to sell one source only) without touching code.
    search_mode = database.get_provider_settings().get("search_mode", "both")
    tp_enabled = search_mode in ("both", "travelport_only")
    ad_enabled = search_mode in ("both", "amadeus_only")

    def _search_travelport() -> list[dict]:
        raw = services.search_flights(
            origin=request.origin.upper() if request.origin else None,
            destination=request.destination.upper() if request.destination else None,
            departure_date=request.departure_date,
            adult_count=request.adult_count,
            child_count=request.child_count,
            infant_count=request.infant_count,
            cabin_preference=request.cabin_preference,
            legs=legs,
            content_source=request.content_source
        )
        return services.parse_flight_offers(raw, legs=legs)

    tp_task = asyncio.to_thread(_search_travelport) if tp_enabled else None
    # Amadeus is only wired for a plain one-way search right now.
    ad_task = (
        services.search_amadeus_flights(
            origin=request.origin.upper(), destination=request.destination.upper(),
            departure_date=request.departure_date,
            adult_count=request.adult_count, child_count=request.child_count, infant_count=request.infant_count,
        )
        if (ad_enabled and not legs and request.origin and request.destination and request.departure_date)
        else None
    )

    tp_result, ad_result = await asyncio.gather(
        (tp_task if tp_task is not None else asyncio.sleep(0, result=None)),
        (ad_task if ad_task is not None else asyncio.sleep(0, result=None)),
        return_exceptions=True,
    )

    offers: list[dict] = []

    if tp_task is not None and isinstance(tp_result, Exception):
        error_msg = str(tp_result)
        if "400" in error_msg and "Client error" in error_msg:
            detail = (
                "Travelport API returned 400 INVALID INPUT FORMAT (error 1586). "
                "This is a SANDBOX ACCOUNT CONFIGURATION issue — your TripServices v11 "
                "subscription may not be fully activated. "
                "Please verify at https://developer.travelport.com that: "
                "(1) Your account has TripServices v11 access, "
                "(2) PCC 7F3C is enabled for your sandbox, "
                "(3) Your access group is active. "
                f"Raw error: {error_msg[:300]}"
            )
        else:
            detail = f"Travelport search error: {error_msg[:400]}"
        logger.error(f"Flight search (Travelport) failed: {error_msg}")
        _notify_provider_failure("TP", detail, route_info)
    elif tp_task is not None:
        for o in tp_result:
            o["source"] = "TP"
            o["bookable"] = True
        offers.extend(tp_result)

    if ad_task is not None:
        if isinstance(ad_result, Exception):
            logger.error(f"Flight search (Amadeus) failed: {ad_result}")
            _notify_provider_failure("AD", str(ad_result)[:400], route_info)
        elif ad_result:
            offers.extend(ad_result)

    if not offers and tp_task is not None and isinstance(tp_result, Exception):
        # Both providers failed (or AD wasn't queried/enabled) with nothing to
        # show — surface Travelport's error to the caller as before, rather
        # than a silent empty list.
        raise HTTPException(status_code=502, detail=f"Travelport search error: {str(tp_result)[:400]}")

    offers.sort(key=lambda o: (o.get("price") if o.get("price") is not None else float("inf")))
    return {"flights": offers, "count": len(offers)}


# ── Amadeus booking proxy (AD-sourced offers from the unified search) ─────────
# The browser calls these (not the Amadeus backend directly -- its CORS is
# scoped to its own standalone frontend at :5175, not this one) and this
# backend forwards server-to-server to the Amadeus backend's own REST API.
# See services/amadeus_booking_proxy.py for why issue-ticket is expected to
# fail right now (Amadeus office ticketing authority not yet granted).

class AmadeusBookingSegment(BaseModel):
    departureDate: str
    departureTime: str
    arrivalDate: str
    arrivalTime: str
    from_: str = Field(alias="from")
    to: str
    marketingCarrier: str
    flightNumber: str
    bookingClass: str

    model_config = {"populate_by_name": True}


class AmadeusBookingTraveler(BaseModel):
    firstName: str
    lastName: str
    type: str = "adult"


class AmadeusBookingConfirmRequest(BaseModel):
    segments: List[AmadeusBookingSegment]
    travelers: List[AmadeusBookingTraveler]
    contactEmail: EmailStr
    contactPhone: str


@app.post("/api/amadeus-bookings/confirm", status_code=status.HTTP_201_CREATED)
async def amadeus_booking_confirm(request: AmadeusBookingConfirmRequest):
    segments = [
        {
            "departureDate": s.departureDate, "departureTime": s.departureTime,
            "arrivalDate": s.arrivalDate, "arrivalTime": s.arrivalTime,
            "from": s.from_, "to": s.to,
            "marketingCarrier": s.marketingCarrier, "flightNumber": s.flightNumber,
            "bookingClass": s.bookingClass,
        }
        for s in request.segments
    ]
    travelers = [t.model_dump() for t in request.travelers]
    try:
        return await services.confirm_amadeus_booking(segments, travelers, request.contactEmail, request.contactPhone)
    except services.AmadeusBookingError as e:
        logger.error(f"Amadeus booking confirm failed: {e.message}")
        raise HTTPException(status_code=e.status_code, detail=e.message)


@app.post("/api/amadeus-bookings/{locator}/issue-ticket")
async def amadeus_booking_issue_ticket(locator: str):
    try:
        return await services.issue_amadeus_ticket(locator)
    except services.AmadeusBookingError as e:
        logger.error(f"Amadeus ticket issuance failed for {locator}: {e.message}")
        raise HTTPException(status_code=e.status_code, detail=e.message)


def _segments_of_offering(raw_offering: dict) -> list:
    """Flat list of every search-time segment dict in a raw_offering
    (one-way / {"outbound","inbound"} round-trip / {"legs":[...]} multi-city)."""
    if not isinstance(raw_offering, dict):
        return []
    if "outbound" in raw_offering and "inbound" in raw_offering:
        legs = [raw_offering["outbound"], raw_offering["inbound"]]
    elif "legs" in raw_offering:
        legs = raw_offering["legs"] or []
    else:
        legs = [raw_offering]
    return [s for leg in legs for s in (leg.get("segments") or [])]


def _backfill_terminals(ticket: dict, source_segments: list) -> None:
    """
    Travelport's reservation retrieve response has no Departure/Arrival
    `terminal` at all (confirmed live — only the search response carries it),
    so every ticket built from a retrieve came out with no terminals. Copy
    them across from segments that do have them (the search-time offer at
    booking, or the cached booking afterwards), matching on flight number +
    departure date/time, falling back to flight number alone.
    """
    if not source_segments:
        return

    def _norm(t):
        return (t or "").replace("T", " ").strip()[:16]

    by_key, by_flight = {}, {}
    for s in source_segments:
        if not (s.get("departure_terminal") or s.get("arrival_terminal")):
            continue
        by_key[(s.get("flight_number"), _norm(s.get("departure_time")))] = s
        by_flight.setdefault(s.get("flight_number"), s)

    def _fill(segs):
        for seg in segs or []:
            src = by_key.get((seg.get("flight_number"), _norm(seg.get("departure_time")))) or by_flight.get(seg.get("flight_number"))
            if not src:
                continue
            if not seg.get("departure_terminal") and src.get("departure_terminal"):
                seg["departure_terminal"] = src["departure_terminal"]
            if not seg.get("arrival_terminal") and src.get("arrival_terminal"):
                seg["arrival_terminal"] = src["arrival_terminal"]

    _fill(ticket.get("segments"))
    for leg in ticket.get("legs") or []:
        _fill(leg.get("segments"))
        leg_segs = leg.get("segments") or []
        if leg_segs:
            leg["departure_terminal"] = leg.get("departure_terminal") or leg_segs[0].get("departure_terminal")
            leg["arrival_terminal"] = leg.get("arrival_terminal") or leg_segs[-1].get("arrival_terminal")

    segs = ticket.get("segments") or []
    if segs:
        ticket["departure_terminal"] = ticket.get("departure_terminal") or segs[0].get("departure_terminal")
        ticket["arrival_terminal"] = ticket.get("arrival_terminal") or segs[-1].get("arrival_terminal")


def _cached_segments(cached: dict) -> list:
    """Every segment saved on a cached booking (top-level + per-leg)."""
    if not cached:
        return []
    segs = list(cached.get("segments") or [])
    for leg in cached.get("legs") or []:
        segs.extend(leg.get("segments") or [])
    return segs


def _attach_traveler_details(ticket: dict, travelers: list) -> None:
    """
    Map every traveler (Adult/Child/Infant) on this booking/PNR back onto the
    ticket: merges each locally-submitted traveler (phone, nationality, etc.)
    with what Travelport confirmed for that person (parsed into
    ticket["travelers"] by _parse_reservation), matched by passport number —
    falling back to the Adult/Infant/Child order used when they were sent to
    Travelport as a single TravelerListRequest (see run_booking_flow).

    Also mirrors the lead (first) traveler onto the flat top-level ticket
    fields for backward compatibility with existing consumers.
    """
    if not travelers:
        return

    passenger_type_order = {"ADT": 0, "INF": 1, "CNN": 2}
    ordered = sorted(
        travelers,
        key=lambda t: passenger_type_order.get(t.get("passenger_type", "ADT"), 99)
    )

    confirmed_travelers = ticket.get("travelers") or []
    by_passport = {
        c.get("passport_number"): c
        for c in confirmed_travelers if c.get("passport_number")
    }

    merged = []
    for i, t in enumerate(ordered):
        confirmed = by_passport.get(t.get("passport_number"))
        if not confirmed and i < len(confirmed_travelers):
            confirmed = confirmed_travelers[i]
        merged.append({
            **t,
            "confirmed_name": confirmed.get("full_name") if confirmed else None,
        })
    ticket["travelers"] = merged

    lead = ordered[0]
    ticket["passport_number"] = lead.get("passport_number", "")
    ticket["passport_expiry"] = lead.get("passport_expiry", "")
    ticket["nationality"] = lead.get("nationality", "LK")
    ticket["passport_issue_country"] = lead.get("passport_issue_country", "LK")
    ticket["gender"] = lead.get("gender", "Male")
    ticket["phone"] = lead.get("phone", "")
    ticket["date_of_birth"] = lead.get("date_of_birth", "")


# ── STEPS 4-9: Create Full Booking + Issue Ticket ─────────────────────────────

@app.post("/api/bookings/create", status_code=status.HTTP_201_CREATED)
def create_booking(request: BookingCreateRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """
    STEPS 4 through 8 — Booking workflow up to PNR confirmation:
      4. Create workbench
      5. Add selected offer
      6. Add traveler
      7. Commit → generate PNR
      8. Retrieve PNR
    Ticket issuance (STEP 9) is a separate step — see
    POST /api/bookings/{locator_code}/issue-ticket — gated on a successful
    PayCorp payment confirmation. Returns the confirmed (not yet ticketed)
    booking details for the frontend popup.
    """
    travelers = [t.model_dump() for t in request.travelers]
    raw_offering = request.raw_offering

    try:
        # STEPS 4-7: Full booking flow with automatic stale-workbench retry and cleanup.
        commit_result = services.run_booking_flow(raw_offering, travelers)
        locator_code = commit_result["locator_code"]

        # STEP 8 only — ticket issuance (STEP 9) now happens separately via
        # POST /api/bookings/{locator_code}/issue-ticket, gated on a
        # successful PayCorp payment confirmation. Do NOT issue here.
        ticket = services.retrieve_reservation(locator_code)

        # Cache the Offer local id/UUID from the commit response — issue_ticket()
        # falls back to this when a later buildfromlocator doesn't return Offer[]
        # (a real, confirmed gap for NDC content — see commit_workbench's docstring).
        if commit_result.get("offer_local_id"):
            ticket["offer_local_id"] = commit_result["offer_local_id"]
        if commit_result.get("offer_uuid"):
            ticket["offer_uuid"] = commit_result["offer_uuid"]
        if commit_result.get("offer_authority"):
            ticket["offer_authority"] = commit_result["offer_authority"]

        # Apply pricing overrides if selecting custom fare family
        if request.custom_price:
            ticket["total_fare"] = request.custom_price
        if request.cabin_class:
            ticket["cabin_class"] = request.cabin_class
        if request.fare_family:
            ticket["fare_family"] = request.fare_family

        if "outbound" in raw_offering and "inbound" in raw_offering:
            ticket["offer_id"] = raw_offering["outbound"].get("id", "")
        else:
            ticket["offer_id"] = raw_offering.get("id", "")

        # Map every traveler (Adult/Child/Infant) Travelport confirmed back onto
        # this booking, and mirror the lead traveler onto the flat top-level fields.
        _attach_traveler_details(ticket, travelers)

        ticket["seat_charge"] = 0.0

        # Enrich with payment method details
        pay_input = request.payment_method or "card"
        if pay_input == "cash":
            ticket["payment_method"] = "Cash"
        elif pay_input == "bank":
            ticket["payment_method"] = "Bank Transfer"
        else:
            ticket["payment_method"] = "Credit Card"

        ticket["customer_id"] = customer_id

        # Save to local cache
        saved = database.save_booking(ticket)

        return {
            "success": True,
            "ticket": ticket,
            "cached_id": saved.get("id")
        }

    except ValueError as e:
        friendly = friendly_travelport_error(str(e))
        raise HTTPException(status_code=409 if friendly != str(e) else 422, detail=friendly)
    except httpx.HTTPStatusError as e:
        error_msg = str(e)
        status_code = e.response.status_code
        response_text = e.response.text
        if status_code == 500:
            detail = (
                "Travelport API returned 500 INTERNAL SERVER ERROR at the booking commit stage. "
                "This is a common issue in the Travelport pre-production sandbox when using accounts "
                "with limited booking permissions (specifically 'preAuthorized: false' in the JWT token). "
                "Please verify in the Travelport Developer Portal (https://developer.travelport.com) that: "
                "(1) Your sandbox PCC (7F3C) has live GDS/NDC booking and ticketing capability activated. "
                "(2) Your API credentials have the required role permissions to hold/commit reservations. "
                "To troubleshoot, you can provide Travelport support with the request TraceId or the E2ETrackingID "
                f"from your API headers. Raw error response: {response_text[:300]}"
            )
        else:
            detail = f"Travelport booking API error: {error_msg[:400]}"
        logger.error(f"Booking failed with HTTPStatusError: {error_msg}. Details: {response_text[:300]}")
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        logger.error(f"Booking failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Travelport booking error: {str(e)}"
        )


# ── STEPS 4 & 10: Initiate Booking (Workbench & Seatmap) ──────────────────────

@app.post("/api/bookings/initiate")
def initiate_booking(request: BookingInitiateRequest):
    """
    Initiates booking: Creates GDS workbench, adds selected offer, and returns seat map.
    """
    logger.info("Initiating booking and fetching live seat map...")
    raw_offering = request.raw_offering
    workbench_id = None
    # One TraceId for every Travelport call in this initiate flow (create
    # workbench -> add offer -> seat map -> discard) — see auth_service.py.
    trace_token = services.start_flow_trace_id()
    try:
        # STEP 4: Create workbench
        workbench_id = services.create_workbench()

        # STEP 5: Add offer to workbench
        add_result = services.add_offer_to_workbench(workbench_id, raw_offering)

        # Extract offer_id from add offer result
        offer_id = None
        try:
            offer_id = add_result.get("OfferListResponse", {}).get("OfferID", [{}])[0].get("Identifier", {}).get("value")
        except Exception:
            pass

        if not offer_id:
            offer_id = "offer_1"

        # STEP 10: Fetch live seat map(s) — one per flight/leg. A round-trip
        # offer returns two (outbound + return); a one-way offer returns one.
        seat_maps = None
        seat_map_available = False
        try:
            seat_maps = services.get_seat_map(workbench_id, offer_id)
            seat_map_available = bool(seat_maps)
        except Exception as e:
            logger.warning(f"Live seat map retrieval failed: {e}")

        return {
            "workbench_id": workbench_id,
            "offer_id": offer_id,
            # "seat_map" (singular, first leg only) is kept for existing
            # one-way frontend code — "seat_maps" (plural, all legs) is the
            # new field round-trip-aware code should use instead.
            "seat_map": seat_maps[0] if seat_maps else None,
            "seat_maps": seat_maps or [],
            "seat_map_available": seat_map_available
        }
    except Exception as e:
        logger.error(f"Booking initiation failed: {e}")
        friendly = friendly_travelport_error(str(e))
        raise HTTPException(
            status_code=409 if friendly != str(e) else 502,
            detail=friendly if friendly != str(e) else f"Failed to initiate Travelport booking session: {str(e)}"
        )
    finally:
        services.end_flow_trace_id(trace_token)
        if workbench_id:
            logger.info(f"Cleaning up temporary initiate workbench session {workbench_id}...")
            try:
                services.discard_workbench(workbench_id)
            except Exception as ex:
                logger.warning(f"Failed to discard temporary initiate workbench {workbench_id}: {ex}")



# ── STEPS 6-9: Confirm Booking (Add Travelers, Seats, Commit PNR & Ticket) ──

@app.post("/api/bookings/confirm", status_code=status.HTTP_201_CREATED)
def confirm_booking(request: BookingConfirmRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """
    Confirms booking by creating a FRESH GDS workbench at commit time.

    This avoids the 'WORKBENCH ID IS NOT VALID' error (Travelport error 8506)
    which occurs when the workbench created during initiate has expired due to
    the Travelport sandbox short session TTL (~5-8 minutes). The user may spend
    several minutes reviewing the itinerary, selecting seats, and entering
    payment details — long enough for the old workbench to expire.

    Solution: Always create a new workbench at confirm time using raw_offering.
    Steps 4-9 are re-executed atomically in one fresh session.
    """
    logger.info("Confirming booking — creating fresh GDS workbench at commit time...")
    travelers = [t.model_dump() for t in request.travelers]
    raw_offering = request.raw_offering
    selected_seats = request.selected_seats

    # One TraceId for every Travelport call this confirm makes — run_booking_flow()
    # (STEPS 4-7) plus the retrieve_reservation() calls (STEP 8) below all share
    # it, since they're one logical flow from Travelport's point of view. See
    # flow_trace_id()'s idempotency note in auth_service.py — run_booking_flow's
    # own wrap reuses this outer trace_id rather than starting a new one.
    trace_token = services.start_flow_trace_id()
    try:
        # STEPS 4-7: Full booking flow with automatic stale-workbench retry.
        # run_booking_flow() handles Galileo error 4350 (COMMIT OR IGNORE
        # RESERVATION WORKBENCH) at both create and commit stages by DELETing
        # the stale workbench and retrying the entire flow (up to 3 attempts).
        commit_result = services.run_booking_flow(raw_offering, travelers, reference_payload_only=request.reference_payload_only)
        locator_code = commit_result["locator_code"]
        logger.info(f"PNR generated: {locator_code}")

        # STEP 8 only — ticket issuance (STEP 9) now happens separately via
        # POST /api/bookings/{locator_code}/issue-ticket, gated on a
        # successful PayCorp payment confirmation. Do NOT issue here.
        ticket = services.retrieve_reservation(locator_code)

        # Cache the Offer local id/UUID from the commit response — issue_ticket()
        # falls back to this when a later buildfromlocator doesn't return Offer[]
        # (a real, confirmed gap for NDC content — see commit_workbench's docstring).
        if commit_result.get("offer_local_id"):
            ticket["offer_local_id"] = commit_result["offer_local_id"]
        if commit_result.get("offer_uuid"):
            ticket["offer_uuid"] = commit_result["offer_uuid"]
        if commit_result.get("offer_authority"):
            ticket["offer_authority"] = commit_result["offer_authority"]

        # Galileo GDS segment compiler takes a second to generate the airline PNR.
        # If it is null, sleep 1.5 seconds and retrieve reservation again to populate it.
        if not ticket.get("airline_pnr"):
            import time
            time.sleep(1.5)
            updated_ticket = services.retrieve_reservation(locator_code)
            if updated_ticket.get("airline_pnr"):
                ticket["airline_pnr"] = updated_ticket["airline_pnr"]
                ticket["airline_pnr_source"] = updated_ticket["airline_pnr_source"]

        # NDC content: Travelport's commit response (commit_result above)
        # embeds the full Offer[]/Product[]/FlightSegment[] inline, but the
        # separate GET .../reservations/{pnr} that retrieve_reservation() just
        # did does not return Offer[] for NDC bookings the way it does for
        # GDS — confirmed live (the same commit response that produced this
        # locator_code has real flight/segment data that the live retrieve
        # above came back without). Backfill from the commit response itself
        # rather than losing the itinerary entirely.
        if not ticket.get("flight_number"):
            commit_ticket = services.parse_commit_response(commit_result["raw_response"], locator_code)
            for key in ("flight_number", "airline", "airline_code", "departure_airport",
                        "arrival_airport", "departure_time", "arrival_time", "duration",
                        "segments", "legs", "cabin_class", "fare_basis", "fare_source",
                        "baggage_allowance", "total_fare", "currency"):
                if commit_ticket.get(key):
                    ticket[key] = commit_ticket[key]

        # fare_source override: retrieve_reservation()'s parse defaults to
        # "GDS" whenever Travelport's retrieve response doesn't echo back a
        # ContentSource on the Product (confirmed live — an NDC Emirates
        # booking came back tagged "GDS" despite offer_authority being "EK",
        # not "Travelport"). issue_ticket() later reads this exact field from
        # the DB cache to decide the GDS vs NDC ticketing path, so a wrong
        # value here silently sends a real NDC PNR down the GDS ticketing
        # flow. raw_offering is the ground truth of what was actually
        # selected — reuse the same all-legs-GDS rule add_offer_to_workbench
        # uses, so a booking counts as NDC the moment any leg is non-GDS.
        if "outbound" in raw_offering and "inbound" in raw_offering:
            _fs_legs = [raw_offering["outbound"], raw_offering["inbound"]]
        elif "legs" in raw_offering:
            _fs_legs = raw_offering["legs"]
        else:
            _fs_legs = [raw_offering]
        ticket["fare_source"] = "GDS" if all(leg.get("fare_source") == "GDS" for leg in _fs_legs) else (
            _fs_legs[0].get("fare_source") or "NDC"
        )

        _backfill_terminals(ticket, _segments_of_offering(raw_offering))

        # Apply local seat assignments and seat pricing to ticket summary
        seat_numbers = [s.seat_number for s in selected_seats]
        seat_charges = sum([s.price for s in selected_seats])

        if seat_numbers:
            ticket["seat_number"] = ", ".join(seat_numbers)

        if request.custom_price:
            ticket["total_fare"] = request.custom_price + seat_charges
            # NDC content: Travelport's Offer has no Price at all (confirmed
            # live), so ticket["currency"] is still whatever _parse_reservation()
            # defaulted to ("USD") — not the actual currency custom_price is
            # denominated in. raw_offering carries the real currency from the
            # search-time price breakdown; use it so the receipt doesn't show
            # e.g. "USD 82,145" for an LKR amount. raw_offering may be a single
            # leg, {"outbound","inbound"} (round-trip), or {"legs":[...]}
            # (multi-city) — currency is the same across every leg of one offer.
            if "outbound" in raw_offering and "inbound" in raw_offering:
                _currency_src = raw_offering["outbound"]
            elif "legs" in raw_offering:
                _currency_src = raw_offering["legs"][0] if raw_offering["legs"] else {}
            else:
                _currency_src = raw_offering
            if _currency_src.get("currency"):
                ticket["currency"] = _currency_src["currency"]
        else:
            ticket["total_fare"] += seat_charges

        ticket["seat_charge"] = seat_charges

        if request.cabin_class:
            ticket["cabin_class"] = request.cabin_class
        if request.fare_family:
            ticket["fare_family"] = request.fare_family

        if "outbound" in raw_offering and "inbound" in raw_offering:
            ticket["offer_id"] = raw_offering["outbound"].get("id", "")
        else:
            ticket["offer_id"] = raw_offering.get("id", "")

        # Map every traveler (Adult/Child/Infant) Travelport confirmed back onto
        # this booking, and mirror the lead traveler onto the flat top-level fields.
        _attach_traveler_details(ticket, travelers)

        # Enrich with payment method details
        pay_input = request.payment_method or "card"
        if pay_input == "cash":
            ticket["payment_method"] = "Cash"
        elif pay_input == "bank":
            ticket["payment_method"] = "Bank Transfer"
        else:
            ticket["payment_method"] = "Credit Card"

        ticket["customer_id"] = customer_id

        # Save to local MySQL cache
        saved = database.save_booking(ticket)

        return {
            "success": True,
            "ticket": ticket,
            "cached_id": saved.get("id")
        }
    except ValueError as e:
        friendly = friendly_travelport_error(str(e))
        raise HTTPException(status_code=409 if friendly != str(e) else 422, detail=friendly)
    except httpx.HTTPStatusError as e:
        error_msg = str(e)
        status_code = e.response.status_code
        response_text = e.response.text
        logger.error(f"Confirmation failed with HTTPStatusError: {error_msg}. Details: {response_text[:300]}")
        raise HTTPException(status_code=502, detail=f"Travelport confirmation API error: {error_msg[:400]}")
    except Exception as e:
        logger.error(f"Confirmation failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Travelport booking error: {str(e)}"
        )
    finally:
        services.end_flow_trace_id(trace_token)


# ── STEP 9: Issue Ticket — gated on PayCorp payment confirmation ──────────────

class IssueTicketRequest(BaseModel):
    reqid: Optional[str] = Field(
        default=None,
        description="PayCorp reqid from a completed hosted-page payment. "
                     "Omit only for cash/bank-transfer bookings, where payment "
                     "is confirmed offline by agency staff rather than PayCorp."
    )


@app.post("/api/bookings/{locator_code}/issue-ticket")
def issue_ticket_after_payment(locator_code: str, request: IssueTicketRequest):
    """
    STEP 9 — Issue the Travelport ticket for an already-confirmed PNR.

    If reqid is provided (card payments), the PayCorp payment MUST have
    succeeded — Travelport is never contacted for ticket issuance otherwise.
    If reqid is omitted (cash/bank transfer), issues immediately, matching
    the offline-settlement flows that don't go through PayCorp at all.
    """
    locator_code = locator_code.upper().strip()
    payment_result = None

    if request.reqid:
        try:
            payment_result = services.complete_payment(request.reqid)
        except services.PayCorpError as e:
            logger.error(f"PayCorp payment check failed for {locator_code}: {e}")
            raise HTTPException(status_code=502, detail=str(e))

        if not payment_result.get("success"):
            logger.warning(
                f"Ticket issuance refused for {locator_code} — payment not successful "
                f"(code={payment_result.get('response_code')}, reqid={request.reqid})"
            )
            raise HTTPException(
                status_code=402,
                detail=f"Payment not successful (code={payment_result.get('response_code')}): "
                       f"{payment_result.get('response_text')}. Ticket not issued."
            )
        logger.info(f"Payment confirmed for {locator_code} (txnReference={payment_result.get('txn_reference')}) — issuing ticket...")
    else:
        logger.info(f"Issuing ticket for {locator_code} without a PayCorp reqid (cash/bank transfer)...")

    return _issue_and_save_ticket(locator_code, payment_result)


def _issue_and_save_ticket(locator_code: str, payment_result: Optional[dict] = None) -> dict:
    """Shared by the normal PayCorp-gated issue-ticket flow and the admin
    force-issue override below — everything after payment has already been
    decided (succeeded, or deliberately overridden by an admin)."""
    # One TraceId for every Travelport call issue_ticket() makes internally
    # (buildfromlocator -> formofpayment -> payment -> commit-with-ticketing).
    trace_token = services.start_flow_trace_id()
    try:
        ticket = services.issue_ticket(locator_code)
    except Exception as e:
        logger.error(f"Ticket issuance failed for {locator_code}: {e}")
        txn_note = f" (txnReference={payment_result.get('txn_reference')})" if payment_result else ""
        raise HTTPException(
            status_code=502,
            detail=f"Payment succeeded{txn_note} but Travelport ticket issuance failed: {str(e)}"
        )
    finally:
        services.end_flow_trace_id(trace_token)

    if payment_result:
        ticket["payment_txn_reference"] = payment_result.get("txn_reference")
        ticket["payment_auth_code"] = payment_result.get("auth_code")

    # issue_ticket()'s internal retrieve_reservation() only knows Travelport-side
    # data — re-apply the locally-computed fields (seat charge, contact
    # enrichment, adjusted total_fare) that /confirm already saved for this PNR.
    existing = database.get_booking_by_locator(locator_code)
    if existing:
        for key in ("seat_charge", "email", "phone", "date_of_birth", "gender",
                     "nationality", "passport_expiry", "offer_id", "payment_method", "customer_id",
                     "offer_local_id", "offer_uuid", "offer_authority"):
            if existing.get(key) not in (None, "", []):
                ticket.setdefault(key, existing.get(key))
        # NDC bookings: retrieve_reservation() doesn't return Offer[] for NDC
        # PNRs on a later, separate GET (confirmed live — only the original
        # commit response has it), so flight/segment fields already default
        # to "" in the ticket dict and setdefault() above would never fill
        # them. /confirm already captured these from the commit response and
        # saved them to raw_ticket_json — pull them back explicitly here.
        for key in ("flight_number", "airline", "airline_code", "departure_airport",
                     "arrival_airport", "departure_time", "arrival_time", "duration",
                     "segments", "legs", "cabin_class", "fare_basis", "fare_source",
                     "baggage_allowance"):
            if not ticket.get(key) and existing.get(key):
                ticket[key] = existing.get(key)
        # "travelers" is set unconditionally (not setdefault): issue_ticket()'s
        # own retrieve_reservation() already overwrote it with Travelport's
        # sparse echo (passenger_type/given_name/surname/full_name only) —
        # the record /confirm saved has the full local form data (first_name,
        # last_name, gender, nationality, passport_expiry, phone, etc.) plus
        # each traveler's Travelport-confirmed name, so it must win here.
        if existing.get("travelers"):
            ticket["travelers"] = existing["travelers"]
        if existing.get("total_fare"):
            ticket["total_fare"] = existing["total_fare"]
        # Same reasoning as total_fare above: for NDC, issue_ticket()'s own
        # retrieve_reservation() has no Price on the Offer at all (confirmed
        # live), so ticket["currency"] is still whatever _parse_reservation()
        # defaulted to ("USD") regardless of what total_fare actually is
        # denominated in. /confirm already resolved the real currency from
        # the search-time price and saved it — it must win here too, the same
        # way total_fare does, or the receipt shows e.g. "USD 82,145" for an
        # LKR amount.
        if existing.get("currency"):
            ticket["currency"] = existing["currency"]
        # fare_source override: services.issue_ticket()'s own retrieve_reservation()
        # has the same "defaults to GDS" bug the /confirm fix above documents —
        # it's a truthy wrong value, not an empty one, so the setdefault-style
        # loop above (which only fills falsy fields) never corrects it from
        # `existing`. /confirm already resolved this correctly at booking time —
        # that saved value wins here.
        if existing.get("fare_source"):
            ticket["fare_source"] = existing["fare_source"]
        _backfill_terminals(ticket, _cached_segments(existing))

    saved = database.save_booking(ticket)

    # Loyalty points are earned on actual ticketing (a completed, paid
    # transaction), not at PNR creation — save_booking may be called again
    # later (e.g. Sync PNR) but award_loyalty_points is only ever invoked
    # from this one place, so no double-awarding risk.
    try:
        if ticket.get("email"):
            services.award_points_for_booking(
                ticket["email"], float(ticket.get("total_fare", 0)), ticket.get("currency", "USD"), locator_code,
            )
    except Exception as e:
        logger.warning(f"Loyalty points award failed for {locator_code} (non-fatal): {e}")

    return {
        "success": True,
        "ticket": ticket,
        "cached_id": saved.get("id")
    }


# ── Dedicated NDC flow: search → book → issue ticket ───────────────────────────
# A separate, NDC-only path (content source always NDC, cash form of payment,
# Travelport's NDC certification step order). It reuses the same Travelport
# services as the main booking flow; nothing here touches GDS behaviour.

class NdcSearchRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    departure_date: str
    return_date: Optional[str] = Field(default=None, description="Omit for one-way")
    adult_count: int = Field(default=1, ge=1, le=9)
    child_count: int = Field(default=0, ge=0, le=8)
    infant_count: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def _infants_not_more_than_adults(self):
        # NDC carriers allow only one lap infant per adult (Travelport error 2014).
        if self.infant_count > self.adult_count:
            raise ValueError("The number of infants cannot exceed the number of adults for NDC.")
        return self


class NdcBookAndIssueRequest(BaseModel):
    raw_offering: dict = Field(..., description="raw_offering of the chosen fare option from /api/ndc/search")
    travelers: List[TravelerInfo]


# Carriers whose post-commit workbench has no offer to pay against, so they
# must be ticketed with Travelport's NDC Instant Pay workflow instead.
NDC_INSTANT_PAY_CARRIERS = {"EK"}


def _ndc_carrier_of(raw_offering: dict) -> str:
    legs = services.workbench_service._get_leg_offerings(raw_offering)
    return str((legs[0] if legs else {}).get("id", "")).split("_")[0]


def _ndc_instant_pay(raw_offering: dict, travelers: list) -> dict:
    try:
        ip = services.workbench_service.run_instant_pay_flow(raw_offering, travelers)
    except services.workbench_service.InstantPayError as e:
        raise HTTPException(status_code=422, detail=str(e))
    locator = ip["locator_code"]

    ticket = services.retrieve_reservation(locator)
    if not ticket.get("flight_number"):
        commit_ticket = services.parse_commit_response(ip["raw_response"], locator)
        for key in ("flight_number", "airline", "airline_code", "departure_airport", "arrival_airport",
                    "departure_time", "arrival_time", "duration", "segments", "legs", "cabin_class",
                    "fare_basis", "fare_source", "baggage_allowance", "total_fare", "currency"):
            if commit_ticket.get(key):
                ticket[key] = commit_ticket[key]
    ticket["ticket_number"] = ip["ticket_numbers"][0][1]
    ticket["ticket_numbers"] = [{"passenger_type": p, "number": n} for p, n in ip["ticket_numbers"]]
    ticket["status"] = "Ticketed"
    ticket["fare_source"] = "NDC"
    ticket["payment_method"] = "Cash"
    _backfill_terminals(ticket, _segments_of_offering(raw_offering))
    if not ticket.get("total_fare"):
        ticket["total_fare"], ticket["currency"] = ip["total_fare"], ip["currency"]
    _attach_traveler_details(ticket, travelers)
    database.save_booking(ticket)

    for _, number in ip["ticket_numbers"]:
        try:
            services.retrieve_ndc_ticket(locator, number)
        except Exception as e:
            logger.warning(f"NDC ticket retrieve failed for {number} (non-fatal): {e}")

    return {
        "success": True, "locator_code": locator, "airline_pnr": ticket.get("airline_pnr"),
        "status": "Ticketed", "ticket_numbers": ticket["ticket_numbers"],
        "total_fare": ticket.get("total_fare"), "currency": ticket.get("currency"), "message": None,
    }


# Emirates (Instant Pay) has no separate "hold" step — Travelport does not
# store a form of payment added before the ticketing commit, so a PNR can't
# usefully exist before payment. Its raw_offering/travelers are held here,
# in memory only, between /api/ndc/book and the PayCorp return.
_ndc_pending_instant_pay: dict[str, dict] = {}


class NdcInstantPayCompleteRequest(BaseModel):
    pending_id: str
    reqid: str


@app.post("/api/ndc/book")
def ndc_book(request: NdcBookAndIssueRequest):
    """
    STEP 1 of the pay-before-ticket NDC flow: creates the PNR (held, not yet
    ticketed) for carriers whose post-commit workbench can pay normally.
    Emirates (Instant Pay) has no such hold — its request is stashed instead,
    and the PNR + ticket are created together, after payment, by
    /api/ndc/instant-pay/complete.
    """
    if _ndc_carrier_of(request.raw_offering) in NDC_INSTANT_PAY_CARRIERS:
        pending_id = str(uuid.uuid4())
        _ndc_pending_instant_pay[pending_id] = {
            "raw_offering": request.raw_offering,
            "travelers": [t.model_dump() for t in request.travelers],
        }
        return {"requires_prepay": True, "pending_id": pending_id}

    trace_token = services.start_flow_trace_id()
    try:
        booked = confirm_booking(
            BookingConfirmRequest(
                raw_offering=request.raw_offering, travelers=request.travelers,
                selected_seats=[], payment_method="card", reference_payload_only=True,
            ),
            customer_id=None,
        )
    finally:
        services.end_flow_trace_id(trace_token)
    ticket = booked["ticket"]
    return {
        "requires_prepay": False, "locator_code": ticket["locator_code"],
        "airline_pnr": ticket.get("airline_pnr"), "status": ticket.get("status"),
        "total_fare": ticket.get("total_fare"), "currency": ticket.get("currency"),
    }


@app.post("/api/ndc/instant-pay/complete")
def ndc_instant_pay_complete(request: NdcInstantPayCompleteRequest):
    """STEP 3 (Emirates only): payment confirmed — now book and ticket in one Instant Pay commit."""
    pending = _ndc_pending_instant_pay.pop(request.pending_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="This booking has expired or was already completed. Please search and select the flight again.")
    try:
        payment_result = services.complete_payment(request.reqid)
    except services.PayCorpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not payment_result.get("success"):
        raise HTTPException(
            status_code=402,
            detail=f"Payment not successful (code={payment_result.get('response_code')}): "
                   f"{payment_result.get('response_text')}. No booking was made.",
        )
    trace_token = services.start_flow_trace_id()
    try:
        result = _ndc_instant_pay(pending["raw_offering"], pending["travelers"])
    finally:
        services.end_flow_trace_id(trace_token)
    result["payment_txn_reference"] = payment_result.get("txn_reference")
    return result


@app.post("/api/ndc/search")
def ndc_search(request: NdcSearchRequest):
    """NDC-only search (contentSourceList: ["NDC"]); one-way or round trip."""
    o, d = request.origin.upper(), request.destination.upper()
    legs = None
    if request.return_date:
        legs = [
            {"origin": o, "destination": d, "departure_date": request.departure_date},
            {"origin": d, "destination": o, "departure_date": request.return_date},
        ]
    try:
        raw = services.search_flights(
            origin=None if legs else o, destination=None if legs else d,
            departure_date=None if legs else request.departure_date,
            adult_count=request.adult_count, child_count=request.child_count,
            infant_count=request.infant_count, legs=legs, content_source="NDC",
        )
        offers = services.parse_flight_offers(raw, legs=legs)
    except Exception as e:
        logger.error(f"NDC search failed: {e}")
        raise HTTPException(status_code=502, detail=f"Travelport NDC search error: {str(e)[:400]}")
    result = raw.get("CatalogProductOfferingsResponse", {}).get("Result", {}) or {}
    carrier_notes = [f"{e.get('SourceID')}: {e.get('Message')}" for e in result.get("Error", []) or []]
    return {"flights": offers, "count": len(offers), "content_source": "NDC", "carrier_notes": carrier_notes}


@app.post("/api/ndc/book-and-issue")
def ndc_book_and_issue(request: NdcBookAndIssueRequest):
    """
    Books the chosen NDC offer (PNR), then issues the ticket, in Travelport's
    NDC certification order: price → session → add offer → travelers → commit
    → retrieve → post-commit workbench → form of payment → payment →
    ticketing commit → retrieve. Paid by cash form of payment (no card gateway).

    confirm_booking() already retrieves the reservation itself (with one
    conditional re-retrieve only if the airline PNR isn't populated yet —
    see its own docstring); this endpoint used to also call
    retrieve_reservation() again here, unconditionally, and discard the
    result. That extra call never fed into `ticket` or `booked` and only
    added a redundant Travelport call — removed.
    """
    trace_token = services.start_flow_trace_id()
    try:
        if _ndc_carrier_of(request.raw_offering) in NDC_INSTANT_PAY_CARRIERS:
            return _ndc_instant_pay(request.raw_offering, [t.model_dump() for t in request.travelers])
        booked = confirm_booking(
            BookingConfirmRequest(
                raw_offering=request.raw_offering, travelers=request.travelers,
                selected_seats=[], payment_method="cash", reference_payload_only=True,
            ),
            customer_id=None,
        )
        locator = booked["ticket"]["locator_code"]
        issued = issue_ticket_after_payment(locator, IssueTicketRequest())
        ticket = issued["ticket"]
        ticketed = bool(ticket.get("ticket_number"))
        return {
            "success": ticketed,
            "locator_code": locator,
            "airline_pnr": ticket.get("airline_pnr"),
            "status": ticket.get("status"),
            "ticket_numbers": ticket.get("ticket_numbers") or (
                [{"passenger_type": "ADT", "number": ticket["ticket_number"]}] if ticketed else []),
            "total_fare": ticket.get("total_fare"),
            "currency": ticket.get("currency"),
            "message": None if ticketed else
                "PNR created but Travelport did not issue a ticket. Nothing was charged; retry issuing or cancel the PNR.",
        }
    finally:
        services.end_flow_trace_id(trace_token)


@app.get("/api/ndc/tickets/{locator_code}")
def ndc_verify_tickets(locator_code: str, numbers: str = Query(..., description="comma-separated ticket numbers")):
    """Confirms NDC tickets with Travelport's Ticket Retrieve API."""
    out = []
    for number in [n.strip() for n in numbers.split(",") if n.strip()]:
        try:
            res = services.retrieve_ndc_ticket(locator_code.upper(), number)
            segs = (res.get("TicketListResponse", {}).get("TicketID") or [{}])[0].get("TicketSegment", [])
            out.append({"number": number, "valid": True,
                        "segments": [f"{s.get('Carrier')}{s.get('Number')} {s.get('Departure', {}).get('location')}-"
                                     f"{s.get('Arrival', {}).get('location')} {s.get('Status')}" for s in segs]})
        except Exception as e:
            out.append({"number": number, "valid": False, "error": str(e)})
    return {"locator_code": locator_code.upper(), "tickets": out}


# ── Admin: Force Issue Ticket (manual override, audited) ───────────────────────
# For bookings stuck because the payment gateway itself returned a sandbox-only
# failure (PayCorp "(TEST TRANSACTION ONLY)" responses have hit this repeatedly —
# confirmed each time as a gateway-side issue via the correct official test card,
# not a bug here). An admin reviews the booking + gateway response and force-
# issues with one click. Every use is logged to force_issued_tickets (who, why,
# what the gateway said) — this bypasses payment verification, so it is never
# automatic or silent, and is not reachable from the customer-facing flow.

class ForceIssueTicketRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)
    reqid: Optional[str] = Field(
        default=None,
        description="PayCorp reqid from the failed attempt, if any — used only "
                     "to capture the gateway's response code/text/txnReference "
                     "into the audit log, not to gate issuance."
    )


@app.post("/api/admin/bookings/{locator_code}/force-issue-ticket")
def force_issue_ticket(
    locator_code: str,
    request: ForceIssueTicketRequest,
    admin: dict = Depends(auth.get_current_admin),
):
    """
    Admin-only override: issue the ticket for a PNR regardless of payment
    gateway result. Always logged to force_issued_tickets for audit.
    """
    locator_code = locator_code.upper().strip()

    gateway_code = gateway_text = txn_ref = None
    if request.reqid:
        try:
            payment_result = services.complete_payment(request.reqid)
            gateway_code = (
                str(payment_result.get("response_code"))
                if payment_result.get("response_code") is not None else None
            )
            gateway_text = payment_result.get("response_text")
            txn_ref = payment_result.get("txn_reference")
        except services.PayCorpError as e:
            gateway_text = str(e)

    logger.warning(
        f"ADMIN FORCE-ISSUE: {locator_code} by {admin.get('sub')} "
        f"(admin_id={admin.get('admin_id')}) — reason: {request.reason} — "
        f"gateway: code={gateway_code} text={gateway_text}"
    )
    database.log_force_issued_ticket(
        locator_code=locator_code,
        admin_id=admin.get("admin_id"),
        admin_username=admin.get("sub"),
        reason=request.reason,
        gateway_response_code=gateway_code,
        gateway_response_text=gateway_text,
        txn_reference=txn_ref,
    )

    result = _issue_and_save_ticket(locator_code, payment_result=None)
    result["force_issued"] = True
    return result


@app.get("/api/admin/force-issued-tickets")
def list_force_issued_tickets(_admin: dict = Depends(auth.get_current_admin)):
    return {"items": database.get_force_issued_tickets()}


# ── STEP 8: Retrieve PNR ───────────────────────────────────────────────────────


@app.get("/api/bookings/retrieve/{locator_code}")
def retrieve_booking(locator_code: str):
    """
    STEP 8 — Retrieve a reservation's details from Travelport by PNR.
    We fetch live from Travelport to ensure latest status and PNR link,
    then update our local database cache.
    """
    try:
        # Fetch live from Travelport
        ticket = services.retrieve_reservation(locator_code)
        
        # Check if we have a locally cached seat number or passport mapping
        cached = database.get_booking_by_locator(locator_code)
        if cached:
            # Preserve seat number and total fare adjustments from confirmation step if missing in retrieve
            if not ticket.get("seat_number") and cached.get("seat_number"):
                ticket["seat_number"] = cached.get("seat_number")
            # If GDS fare doesn't include the local seat selection fee added dynamically, adjust it
            if cached.get("total_fare") and cached.get("total_fare") > ticket.get("total_fare", 0.0):
                ticket["total_fare"] = cached.get("total_fare")
                # currency must travel with total_fare — for NDC, Travelport's
                # live retrieve_reservation() has no Price at all (confirmed
                # live), so ticket["currency"] is still the "USD" default and
                # would otherwise mislabel this cached LKR (or other) amount.
                if cached.get("currency"):
                    ticket["currency"] = cached.get("currency")
            if cached.get("seat_charge") and not ticket.get("seat_charge"):
                ticket["seat_charge"] = cached.get("seat_charge")
            if cached.get("passport_number") and not ticket.get("passport_number"):
                ticket["passport_number"] = cached.get("passport_number")
            if cached.get("offer_id") and not ticket.get("offer_id"):
                ticket["offer_id"] = cached.get("offer_id")
            # Preserve cached traveler details
            for field in ["passport_expiry", "nationality", "gender", "phone", "date_of_birth", "email"]:
                if cached.get(field) and not ticket.get(field):
                    ticket[field] = cached.get(field)
            # NDC bookings: Travelport's live retrieve_reservation() above
            # doesn't return Offer[] for NDC PNRs (confirmed live — only the
            # original commit response has it), so flight/segment fields come
            # back empty every time this is called. /confirm captured them
            # from the commit response and saved them to raw_ticket_json —
            # pull them back from there instead of losing the itinerary.
            for field in ["flight_number", "airline", "airline_code", "departure_airport",
                          "arrival_airport", "departure_time", "arrival_time", "duration",
                          "segments", "legs", "cabin_class", "fare_basis", "fare_source",
                          "baggage_allowance"]:
                if cached.get(field) and not ticket.get(field):
                    ticket[field] = cached.get(field)
            # fare_source override: live retrieve_reservation() defaults this to
            # "GDS" whenever Travelport's response doesn't echo a ContentSource
            # (confirmed live for NDC PNRs) — a truthy wrong value, so the
            # setdefault-style loop above never corrects it from `cached`. Left
            # unfixed, every retrieve/sync of an NDC booking would overwrite the
            # correct value /confirm saved back to "GDS". The cached value is
            # the ground truth, resolved once at booking time from raw_offering.
            if cached.get("fare_source"):
                ticket["fare_source"] = cached["fare_source"]
            _backfill_terminals(ticket, _cached_segments(cached))

        # Save/update the local cache
        database.save_booking(ticket)
        return ticket
    except Exception as e:
        logger.warning(f"Live retrieval failed for PNR {locator_code}: {e}")
        # Fallback to local cache if offline or error occurs
        cached = database.get_booking_by_locator(locator_code)
        if cached:
            return cached
        raise HTTPException(status_code=404, detail=f"Reservation not found: {str(e)}")


# ── Booking History ────────────────────────────────────────────────────────────

@app.get("/api/bookings/history")
def booking_history(email: Optional[str] = Query(None, description="Filter by passenger email"), _admin: dict = Depends(auth.get_current_admin)):
    """
    Return all locally cached issued tickets (admin only).
    Optionally filter by passenger email.
    """
    try:
        bookings = database.get_all_bookings(email)
        return {"bookings": bookings, "count": len(bookings)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cancel Booking ─────────────────────────────────────────────────────────────

@app.post("/api/bookings/{locator_code}/cancel")
def cancel_booking(locator_code: str):
    """
    Cancel a booking: cancels on Travelport and updates local cache.
    """
    # One TraceId for cancel_reservation()'s internal calls (buildfromlocator
    # -> cancelitems -> commit) — it never raises (returns bool), so no
    # try/finally is needed to guarantee the reset.
    trace_token = services.start_flow_trace_id()
    tp_cancelled = services.cancel_reservation(locator_code)
    services.end_flow_trace_id(trace_token)

    # Update local cache
    db_cancelled = database.cancel_booking(locator_code)

    if not tp_cancelled and not db_cancelled:
        raise HTTPException(
            status_code=404,
            detail="Booking not found or already cancelled."
        )

    return {"message": f"Booking {locator_code} successfully cancelled."}


# ── Invoice Data Retrieval (Travelport Live) ───────────────────────────────────

@app.get("/api/invoice/price-raw/{locator_code}")
def get_raw_price(locator_code: str):
    """Debug: returns raw Offer (Price + Commission + Discount) from Travelport for inspection."""
    from config.api_endpoints import TravelportEndpoints
    from services.auth_service import get_auth_headers
    pnr = locator_code.upper().strip()
    url = TravelportEndpoints.retrieve_reservation(pnr)
    headers = get_auth_headers()
    with httpx.Client(timeout=60) as client:
        r = client.get(url, headers=headers)
        r.raise_for_status()
    raw = r.json()
    reservation = raw.get("Reservation", raw.get("ReservationResponse", {}).get("Reservation", {}))
    offers = reservation.get("Offer", [])
    if not offers:
        return {"raw_offer": {}}
    offer = offers[0]
    return {
        "raw_price":      offer.get("Price", {}),
    }



def _build_invoice_response(pnr: str) -> dict:
    """
    Retrieve full invoice-ready data for a booking.

    PRIMARY source: Travelport live API
      GET /air/book/reservation/reservations/{locator_code}
      → all passengers, segments, fare, PNR receipts, ticket number

    SUPPLEMENT from local booking record (real data — NOT mock):
      → seat_charge: the actual seat selection fee paid (Travelport TotalPrice
        does NOT include seat fees; they are captured at booking time)
      → grand_total = Travelport TotalPrice + seat_charge
      → contact details for secondary passengers (Travelport only stores
        contact info for the lead passenger in the sandbox environment)

    Every field is clearly labelled with its data source in the response.
    """
    try:
        # PRIMARY: Live Travelport API call
        invoice = services.retrieve_invoice_data(pnr)

        # SUPPLEMENT: Real seat charge from local booking record.
        # Travelport's GET /reservations/{pnr} TotalPrice = flight fare only.
        # The seat selection fee is a real charge collected at booking time and
        # stored locally — it is NOT mock data, it is actual money paid.
        cached = database.get_booking_by_locator(pnr)
        seat_charge = 0.0
        if cached:
            seat_charge = float(cached.get("seat_charge") or 0)

            # Supplement per-passenger contact details for secondary passengers.
            # Travelport only stores the lead passenger's contact in the sandbox.
            # The booking form collected details for all passengers at booking time.
            if seat_charge > 0:
                invoice["seat_charge"] = seat_charge
                invoice["seat_charge_source"] = "Local booking record (collected at seat selection)"
            invoice["grand_total"] = round(invoice["total_fare"] + seat_charge, 2)
            invoice["grand_total_note"] = (
                f"Travelport flight fare ({invoice['currency']} {invoice['total_fare']:,.2f}) "
                f"+ seat selection fee ({invoice['currency']} {seat_charge:,.2f})"
            )
        else:
            invoice["seat_charge"] = 0.0
            invoice["grand_total"] = invoice["total_fare"]
            invoice["grand_total_note"] = "Travelport flight fare only (no seat fee record found)"

        invoice["fare_source"] = "Travelport Offer.Price.TotalPrice"
        return {"success": True, "invoice": invoice}

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        if status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"PNR '{pnr}' not found on Travelport. Verify the locator code."
            )
        raise HTTPException(
            status_code=502,
            detail=f"Travelport returned {status_code} for PNR '{pnr}': {e.response.text[:300]}"
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Invoice retrieval failed for PNR {pnr}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to retrieve invoice data from Travelport: {str(e)}"
        )


@app.get("/api/invoice/pnr/{locator_code}")
def get_invoice_by_pnr(locator_code: str, principal: dict = Depends(auth.get_admin_or_customer)):
    """Retrieve invoice data by PNR / locator code. A general 'manage my
    booking' style lookup, same as an airline's own PNR retrieval tool —
    open to any signed-in admin or customer, not restricted to bookings
    tied to the caller's own account. This is intentional: the same person
    may have booked under a different email than their account, so ownership
    can't be assumed. The PNR itself (a random, non-guessable code only the
    booker receives) is the access control here, not account linkage."""
    pnr = locator_code.upper().strip()
    return _build_invoice_response(pnr)


@app.get("/api/invoice/ticket/{ticket_number}")
def get_invoice_by_ticket(ticket_number: str, principal: dict = Depends(auth.get_admin_or_customer)):
    """Retrieve invoice data by issued ticket number — resolves the ticket
    number to its PNR/locator via the local booking cache, then fetches the
    same live Travelport invoice data as the PNR lookup. Same open-lookup
    model as get_invoice_by_pnr — see its docstring."""
    booking = database.get_booking_by_ticket_number(ticket_number.strip())
    if not booking or not booking.get("locator_code"):
        raise HTTPException(status_code=404, detail=f"No booking found for ticket number '{ticket_number}'.")
    pnr = booking["locator_code"].upper().strip()
    return _build_invoice_response(pnr)


@app.get("/api/invoice/report")
def get_invoice_report(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    _admin: dict = Depends(auth.get_current_admin),
):
    """
    Retrieve live Travelport data for all PNRs booked/issued under PCC 7F3C within a date range.
    Queries the local sqlite cache for matching locators, then fetches live PNR data from Travelport.
    NO system-generated mock data. Admin-only — bulk PCC-wide report.
    """
    logger.info(f"Generating live Travelport PNR report for dates {start_date} to {end_date} under PCC 7F3C...")
    
    # Query MySQL database for matching locator codes
    start_dt = f"{start_date} 00:00:00"
    end_dt = f"{end_date} 23:59:59"
    rows = database.get_bookings_in_date_range(start_dt, end_dt)
    locator_codes = [r["locator_code"] for r in rows]
    if not locator_codes:
        return {"success": True, "pax_records": []}
    
    pax_records = []
    
    for pnr in locator_codes:
        try:
            # Live retrieve from Travelport
            invoice = services.retrieve_invoice_data(pnr)
            
            # Supplement from local cache for seat charge
            cached = database.get_booking_by_locator(pnr)
            seat_charge = 0.0
            if cached:
                seat_charge = float(cached.get("seat_charge") or 0.0)
            
            currency = invoice.get("currency", "LKR")
            base_fare_total = invoice.get("fare_summary", {}).get("base_fare_total", 0.0)
            total_taxes = invoice.get("fare_summary", {}).get("total_taxes", 0.0)
            flight_fare = invoice.get("total_fare", 0.0)
            grand_total = round(flight_fare + seat_charge, 2)
            
            # Get list of passengers
            pax_list = invoice.get("all_passengers", [])
            breakdown = invoice.get("price_breakdown", {})
            
            for idx, p in enumerate(pax_list):
                ptc = p.get("passenger_type", "ADT")
                pb = breakdown.get(ptc, {})
                
                pax_records.append({
                    "locator_code": pnr,
                    "airline_pnr": invoice.get("airline_pnr", ""),
                    "status": invoice.get("status", ""),
                    "booking_date": cached.get("booking_date", "") if cached else invoice.get("booking_date", ""),
                    "ticket_number": p.get("ticket_number", invoice.get("ticket_number", "")),
                    "payment_method": cached.get("payment_method", "Credit Card") if cached else "Credit Card",
                    "fare_source": cached.get("fare_source", invoice.get("fare_source", "GDS")) if cached else invoice.get("fare_source", "GDS"),
                    
                    # Flight Info
                    "flight_number": invoice.get("flight_number", ""),
                    "airline": invoice.get("airline", ""),
                    "departure_airport": invoice.get("departure_airport", ""),
                    "arrival_airport": invoice.get("arrival_airport", ""),
                    "departure_time": invoice.get("departure_time", ""),
                    "arrival_time": invoice.get("arrival_time", ""),
                    "cabin_class": invoice.get("cabin_class", ""),
                    "class_of_service": invoice.get("class_of_service", ""),
                    "fare_basis": invoice.get("fare_basis", ""),
                    "baggage_allowance": invoice.get("baggage_allowance", ""),
                    
                    # PNR Totals (Only set on the first passenger to prevent duplicate summary additions in Excel)
                    "base_fare_total": base_fare_total if idx == 0 else 0.0,
                    "total_taxes": total_taxes if idx == 0 else 0.0,
                    "flight_fare": flight_fare if idx == 0 else 0.0,
                    "seat_charge": seat_charge if idx == 0 else 0.0,
                    "grand_total": grand_total if idx == 0 else 0.0,
                    "currency": currency,
                    
                    # Pax type details
                    "passenger_index": p.get("passenger_index"),
                    "passenger_type": ptc,
                    "passenger_type_label": p.get("passenger_type_label"),
                    "pax_base_fare": pb.get("base_price", 0.0),
                    "pax_total_taxes": pb.get("taxes_total", 0.0),
                    "pax_total": pb.get("total_price", 0.0),
                    
                    # Individual taxes
                    "individual_taxes": pb.get("individual_taxes", []),
                    "fare_calculation": pb.get("fare_calculation", ""),
                    "filed_usd_base": pb.get("filed_usd_base"),
                    
                    # Traveler details
                    "full_name": p.get("full_name", ""),
                    "passport_number": p.get("passport_number", ""),
                    "passport_expiry": p.get("passport_expiry", ""),
                    "date_of_birth": p.get("date_of_birth", ""),
                    "gender": p.get("gender", ""),
                    "nationality": p.get("nationality", ""),
                    "email": p.get("email", ""),
                    "phone": p.get("phone", ""),
                })
        except Exception as e:
            # Log and fallback to cached data if Travelport retrieve fails (e.g. expired or purged)
            logger.warning(f"Live report retrieve failed for PNR {pnr}: {e}")
            cached = database.get_booking_by_locator(pnr)
            if cached:
                import json
                raw_t = {}
                try:
                    if cached.get("raw_ticket_json"):
                        raw_t = json.loads(cached["raw_ticket_json"])
                except Exception:
                    pass
                
                cached_pax = raw_t.get("all_passengers", [])
                if not cached_pax:
                    cached_pax = [{
                        "passenger_index": 1,
                        "passenger_type": "ADT",
                        "passenger_type_label": "Adult",
                        "full_name": cached.get("passenger_name", ""),
                        "passport_number": cached.get("passport_number", ""),
                        "passport_expiry": cached.get("passport_expiry", ""),
                        "date_of_birth": cached.get("date_of_birth", ""),
                        "gender": cached.get("gender", ""),
                        "nationality": cached.get("nationality", ""),
                        "email": cached.get("passenger_email", ""),
                        "phone": cached.get("phone", ""),
                    }]
                
                flight_fare = cached.get("total_fare", 0.0)
                seat_charge = cached.get("seat_charge", 0.0)
                grand_total = flight_fare + seat_charge
                currency = cached.get("currency", "LKR")
                payment_method = cached.get("payment_method", "Credit Card")
                
                for idx, p in enumerate(cached_pax):
                    pax_records.append({
                        "locator_code": pnr,
                        "airline_pnr": cached.get("pnr", ""),
                        "status": cached.get("status", "Confirmed"),
                        "booking_date": cached.get("booking_date", ""),
                        "ticket_number": p.get("ticket_number") or cached.get("ticket_number", ""),
                        "payment_method": payment_method,
                        "fare_source": cached.get("fare_source", "GDS"),
                        "flight_number": cached.get("flight_number", ""),
                        "airline": cached.get("airline", ""),
                        "departure_airport": cached.get("departure_airport", ""),
                        "arrival_airport": cached.get("arrival_airport", ""),
                        "departure_time": cached.get("departure_time", ""),
                        "arrival_time": cached.get("arrival_time", ""),
                        "cabin_class": cached.get("cabin_class", "Economy"),
                        "class_of_service": "",
                        "fare_basis": "",
                        "baggage_allowance": "",
                        "base_fare_total": flight_fare if idx == 0 else 0.0,
                        "total_taxes": 0.0,
                        "flight_fare": flight_fare if idx == 0 else 0.0,
                        "seat_charge": seat_charge if idx == 0 else 0.0,
                        "grand_total": grand_total if idx == 0 else 0.0,
                        "currency": currency,
                        "passenger_index": p.get("passenger_index", idx + 1),
                        "passenger_type": p.get("passenger_type", "ADT"),
                        "passenger_type_label": p.get("passenger_type_label", "Adult"),
                        "pax_base_fare": flight_fare / len(cached_pax) if len(cached_pax) > 0 else flight_fare,
                        "pax_total_taxes": 0.0,
                        "pax_total": flight_fare / len(cached_pax) if len(cached_pax) > 0 else flight_fare,
                        "individual_taxes": [],
                        "fare_calculation": "",
                        "filed_usd_base": None,
                        "full_name": p.get("full_name", ""),
                        "passport_number": p.get("passport_number", ""),
                        "passport_expiry": p.get("passport_expiry", ""),
                        "date_of_birth": p.get("date_of_birth", ""),
                        "gender": p.get("gender", ""),
                        "nationality": p.get("nationality", ""),
                        "email": p.get("email") or cached.get("passenger_email", ""),
                        "phone": p.get("phone") or cached.get("phone", ""),
                    })

    return {"success": True, "pax_records": pax_records}


# ── PayCorp (Sampath Bank) Payment Gateway — Hosted Page Flow ──────────────────
# Card details are entered on PayCorp's own hosted page and never touch this
# backend. We only ever see: an init request/response, and a completion
# result keyed by reqid.

class PaymentInitRequestBody(BaseModel):
    amount: float = Field(..., gt=0, description="Amount in major currency units, e.g. 98900.00")
    currency: str = Field(..., description="LKR or USD")
    return_url: str = Field(..., description="Where PayCorp redirects the browser after payment")
    cancel_url: Optional[str] = None
    client_ref: Optional[str] = Field(default=None, description="Our own reference, echoed back unchanged")
    comment: Optional[str] = None


@app.post("/api/payments/init")
def payments_init(request: PaymentInitRequestBody):
    """
    STEP 1: Initiate a hosted-page PayCorp payment.
    Returns a payment_page_url — the frontend must redirect the browser
    there so the customer enters their card on PayCorp's own page.
    """
    try:
        result = services.init_payment(
            amount=request.amount,
            currency=request.currency,
            return_url=request.return_url,
            cancel_url=request.cancel_url,
            client_ref=request.client_ref,
            comment=request.comment,
        )
        return {"success": True, **result}
    except services.PayCorpError as e:
        logger.error(f"PayCorp init failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except httpx.HTTPStatusError as e:
        logger.error(f"PayCorp init HTTP error: {e.response.status_code} — {e.response.text}")
        raise HTTPException(status_code=502, detail=f"PayCorp gateway error: {e.response.status_code}")


@app.get("/api/payments/complete")
def payments_complete(reqid: str = Query(..., description="reqid returned by /api/payments/init")):
    """
    STEP 3: Look up the final result of a hosted-page payment after the
    customer is redirected back. success=true iff response_code == '00'.
    """
    try:
        result = services.complete_payment(reqid)
        return {"success": result["success"], **result}
    except services.PayCorpError as e:
        logger.error(f"PayCorp complete failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except httpx.HTTPStatusError as e:
        logger.error(f"PayCorp complete HTTP error: {e.response.status_code} — {e.response.text}")
        raise HTTPException(status_code=502, detail=f"PayCorp gateway error: {e.response.status_code}")


# ── Admin Portal ────────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    username: str
    password: str


class PricingSettingsRequest(BaseModel):
    ticket_markup_mode: str = Field(..., description="'percent' or 'fixed' -- Travelport's ticket markup; also Amadeus's when markup_scope='shared'")
    ticket_markup_percent: float = Field(default=0.0, ge=0)
    ticket_markup_fixed: float = Field(default=0.0, ge=0)
    seat_markup_mode: str = Field(..., description="'percent' or 'fixed'")
    seat_markup_percent: float = Field(default=0.0, ge=0)
    seat_markup_fixed: float = Field(default=0.0, ge=0)
    markup_scope: Literal["shared", "separate"] = Field(default="shared", description="'shared' = Amadeus reuses ticket_markup_*; 'separate' = Amadeus uses amadeus_ticket_markup_* below")
    amadeus_ticket_markup_mode: str = Field(default="percent", description="'percent' or 'fixed' -- only used when markup_scope='separate'")
    amadeus_ticket_markup_percent: float = Field(default=0.0, ge=0)
    amadeus_ticket_markup_fixed: float = Field(default=0.0, ge=0)


@app.post("/api/admin/login")
def admin_login(request: AdminLoginRequest):
    admin = database.get_admin_by_username(request.username)
    if not admin or not auth.verify_password(request.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_access_token({
        "sub": admin["username"], "role": "admin", "admin_id": admin["id"],
    })
    return {"access_token": token, "token_type": "bearer", "full_name": admin.get("full_name")}


@app.get("/api/admin/pricing-settings")
def get_pricing_settings(_admin: dict = Depends(auth.get_current_admin)):
    return database.get_pricing_settings()


@app.put("/api/admin/pricing-settings")
def put_pricing_settings(request: PricingSettingsRequest, _admin: dict = Depends(auth.get_current_admin)):
    if (request.ticket_markup_mode not in ("percent", "fixed")
            or request.seat_markup_mode not in ("percent", "fixed")
            or request.amadeus_ticket_markup_mode not in ("percent", "fixed")):
        raise HTTPException(status_code=422, detail="markup_mode must be 'percent' or 'fixed'")
    return database.update_pricing_settings(
        request.ticket_markup_mode, request.ticket_markup_percent, request.ticket_markup_fixed,
        request.seat_markup_mode, request.seat_markup_percent, request.seat_markup_fixed,
        request.markup_scope,
        request.amadeus_ticket_markup_mode, request.amadeus_ticket_markup_percent, request.amadeus_ticket_markup_fixed,
    )


@app.get("/api/admin/reports/summary")
def admin_reports_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    _admin: dict = Depends(auth.get_current_admin),
):
    start_dt = f"{start_date} 00:00:00" if start_date else None
    end_dt = f"{end_date} 23:59:59" if end_date else None
    return database.get_sales_summary(start_dt, end_dt)


# ── Loyalty Program (Admin) ───────────────────────────────────────────────────

class LoyaltySettingsRequest(BaseModel):
    points_per_lkr: float = Field(..., ge=0)
    points_per_usd: float = Field(..., ge=0)
    tier_silver_threshold: int = Field(..., ge=0)
    tier_gold_threshold: int = Field(..., ge=0)
    tier_platinum_threshold: int = Field(..., ge=0)


@app.get("/api/admin/loyalty-settings")
def get_loyalty_settings_admin(_admin: dict = Depends(auth.get_current_admin)):
    return database.get_loyalty_settings()


@app.put("/api/admin/loyalty-settings")
def put_loyalty_settings_admin(request: LoyaltySettingsRequest, _admin: dict = Depends(auth.get_current_admin)):
    if request.tier_gold_threshold < request.tier_silver_threshold:
        raise HTTPException(status_code=422, detail="Gold threshold must be >= Silver threshold.")
    if request.tier_platinum_threshold < request.tier_gold_threshold:
        raise HTTPException(status_code=422, detail="Platinum threshold must be >= Gold threshold.")
    return database.update_loyalty_settings(
        request.points_per_lkr, request.points_per_usd,
        request.tier_silver_threshold, request.tier_gold_threshold, request.tier_platinum_threshold,
    )


# ── Manual Booking Assignment (Admin) ─────────────────────────────────────────
# Covers cases auto-link-by-email can't: a booking made under a different
# email than the customer's account (typo, alternate address, shared family
# booking, etc.). Admin verifies ownership through support/other means and
# links it directly.

class AssignBookingRequest(BaseModel):
    customer_email: EmailStr


@app.post("/api/admin/bookings/{locator_code}/assign-customer")
def admin_assign_booking(locator_code: str, request: AssignBookingRequest, _admin: dict = Depends(auth.get_current_admin)):
    customer = database.get_customer_by_email(str(request.customer_email))
    if not customer:
        raise HTTPException(status_code=404, detail=f"No customer account found for {request.customer_email}.")
    updated = database.assign_booking_to_customer(locator_code.upper().strip(), customer["id"])
    if not updated:
        raise HTTPException(status_code=404, detail=f"No booking found for locator '{locator_code}'.")
    return {"success": True, "booking": updated}


# ── Email Change Requests (Admin) ─────────────────────────────────────────────

class EmailChangeReviewRequest(BaseModel):
    admin_note: Optional[str] = None


@app.get("/api/admin/email-change-requests")
def admin_list_email_change_requests(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    return {"requests": database.get_email_change_requests(status_filter)}


@app.post("/api/admin/email-change-requests/{request_id}/approve")
def admin_approve_email_change(request_id: int, body: EmailChangeReviewRequest, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_email_change_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Email change request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    if database.get_customer_by_email(req["new_email"]):
        raise HTTPException(status_code=409, detail="An account with the requested new email now exists — cannot approve.")

    database.update_customer_email(req["customer_id"], req["new_email"])
    database.rekey_loyalty_account(req["old_email"], req["new_email"])
    # Pick up any guest bookings already sitting under the new email.
    database.link_guest_bookings_by_email(req["customer_id"], req["new_email"])
    updated = database.resolve_email_change_request(request_id, "approved", admin["admin_id"], body.admin_note)
    return updated


@app.post("/api/admin/email-change-requests/{request_id}/reject")
def admin_reject_email_change(request_id: int, body: EmailChangeReviewRequest, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_email_change_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Email change request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    updated = database.resolve_email_change_request(request_id, "rejected", admin["admin_id"], body.admin_note)
    return updated


# ── Cancellation Requests (B2C manual-review flow) ────────────────────────────
# Self-service Travelport cancellation is deliberately not exposed to B2C
# customers (see /api/bookings/{locator_code}/cancel, which remains
# admin-only via the Admin Portal). Instead, a customer submits a request
# here and an admin reviews + actually cancels it manually.

class CancellationRequestReview(BaseModel):
    admin_note: Optional[str] = None


@app.get("/api/admin/cancellation-requests")
def admin_list_cancellation_requests(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    return {"requests": database.get_cancellation_requests(status_filter)}


@app.post("/api/admin/cancellation-requests/{request_id}/resolve")
def admin_resolve_cancellation_request(request_id: int, body: CancellationRequestReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_cancellation_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Cancellation request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    updated = database.resolve_cancellation_request(request_id, "resolved", admin["admin_id"], body.admin_note)
    return updated


@app.post("/api/admin/cancellation-requests/{request_id}/reject")
def admin_reject_cancellation_request(request_id: int, body: CancellationRequestReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_cancellation_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Cancellation request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    updated = database.resolve_cancellation_request(request_id, "rejected", admin["admin_id"], body.admin_note)
    return updated


# ── Provider fare-fetch notifications (unified TP+AD search) ──────────────────
# Logged + emailed by _notify_provider_failure() in /api/flights/search
# whenever Travelport or Amadeus fails to return fares. Same manual-review
# pattern as cancellation_requests above (status + admin_note), plus a small
# recipients list the admin can edit without touching .env/code.

class ProviderNotificationReview(BaseModel):
    admin_note: Optional[str] = None


class NotificationRecipientInput(BaseModel):
    email: EmailStr


class NotificationRecipientUpdate(BaseModel):
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


@app.get("/api/admin/notifications")
def admin_list_notifications(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    return {"notifications": database.get_provider_notifications(status_filter)}


@app.post("/api/admin/notifications/{notification_id}/acknowledge")
def admin_acknowledge_notification(notification_id: int, body: ProviderNotificationReview, admin: dict = Depends(auth.get_current_admin)):
    note = database.get_provider_notification_by_id(notification_id)
    if not note:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return database.acknowledge_provider_notification(notification_id, admin["admin_id"], body.admin_note)


@app.get("/api/admin/notification-recipients")
def admin_list_notification_recipients(_admin: dict = Depends(auth.get_current_admin)):
    return {"recipients": database.get_notification_recipients()}


@app.post("/api/admin/notification-recipients", status_code=status.HTTP_201_CREATED)
def admin_add_notification_recipient(body: NotificationRecipientInput, _admin: dict = Depends(auth.get_current_admin)):
    return database.add_notification_recipient(body.email)


@app.put("/api/admin/notification-recipients/{recipient_id}")
def admin_update_notification_recipient(recipient_id: int, body: NotificationRecipientUpdate, _admin: dict = Depends(auth.get_current_admin)):
    updated = database.update_notification_recipient(recipient_id, body.email, body.is_active)
    if not updated:
        raise HTTPException(status_code=404, detail="Recipient not found.")
    return updated


@app.delete("/api/admin/notification-recipients/{recipient_id}")
def admin_delete_notification_recipient(recipient_id: int, _admin: dict = Depends(auth.get_current_admin)):
    database.delete_notification_recipient(recipient_id)
    return {"deleted": True}


# ── Provider settings (unified TP+AD search on/off switch) ────────────────────
# Lets an admin take Travelport or Amadeus out of the unified search entirely
# -- "travelport_only" / "amadeus_only" / "both" -- read by /api/flights/search
# on every request (see search_flights above).

class ProviderSettingsRequest(BaseModel):
    search_mode: Literal["both", "travelport_only", "amadeus_only"]


@app.get("/api/admin/provider-settings")
def admin_get_provider_settings(_admin: dict = Depends(auth.get_current_admin)):
    return database.get_provider_settings()


@app.put("/api/admin/provider-settings")
def admin_put_provider_settings(body: ProviderSettingsRequest, _admin: dict = Depends(auth.get_current_admin)):
    return database.update_provider_settings(body.search_mode)


# ── Tour / Travel Packages (B2C, admin-curated catalog) ───────────────────────
# Fixed packages staff create and price in the Admin Portal — no live
# Travelport search involved. Customers browse the public catalog and submit
# a booking request (same manual-review pattern as cancellation requests
# above); an admin confirms availability/payment with the customer directly.

class TourPackageInput(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    package_type: Literal["tour", "hotel"] = Field(default="tour", description="Hotel packages show in the B2C homepage's Special Hotel Packages section instead of Tour Packages")
    destination: str = Field(..., min_length=2, max_length=150)
    duration_days: int = Field(..., ge=1)
    duration_nights: int = Field(..., ge=0)
    price: float = Field(..., ge=0)
    currency: str = Field(default="LKR", min_length=3, max_length=10)
    image_url: Optional[str] = None
    summary: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    itinerary: Optional[str] = None
    inclusions: Optional[str] = None
    exclusions: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    is_active: bool = True


class PackageBookingRequestCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=30)
    num_travelers: int = Field(default=1, ge=1, le=50)
    preferred_date: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=1000)


class PackageBookingReview(BaseModel):
    admin_note: Optional[str] = None


@app.get("/api/packages")
def list_packages(package_type: Optional[str] = Query(None, alias="type")):
    """Public catalog — active packages only. Pass ?type=tour or ?type=hotel
    to filter; omit to get both (used nowhere currently — every caller passes
    an explicit type so Tour Packages and Special Hotel Packages stay separate)."""
    return {"packages": database.get_tour_packages(active_only=True, package_type=package_type)}


@app.get("/api/packages/{package_id}")
def get_package(package_id: int):
    pkg = database.get_tour_package_by_id(package_id)
    if not pkg or not pkg["is_active"]:
        raise HTTPException(status_code=404, detail="Package not found.")
    return pkg


@app.post("/api/packages/{package_id}/book-request", status_code=status.HTTP_201_CREATED)
def submit_package_booking_request(
    package_id: int,
    request: PackageBookingRequestCreate,
    customer_id: Optional[int] = Depends(auth.get_optional_customer_id),
):
    pkg = database.get_tour_package_by_id(package_id)
    if not pkg or not pkg["is_active"]:
        raise HTTPException(status_code=404, detail="Package not found.")
    return database.create_package_booking_request(
        package_id, customer_id, request.full_name.strip(), str(request.email),
        request.phone.strip(), request.num_travelers, request.preferred_date, request.notes,
    )


ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


@app.post("/api/admin/upload-image")
async def admin_upload_image(request: Request, file: UploadFile = File(...), _admin: dict = Depends(auth.get_current_admin)):
    """Saves an admin-uploaded poster/cover image (e.g. for a Tour Package) to disk
    and returns its public URL. Not tied to any specific package — the returned
    url is meant to be stored in that record's image_url field by the caller."""
    ext = ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported image type — use JPEG, PNG, WEBP, or GIF.")

    contents = await file.read()
    if len(contents) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large — max 5MB.")

    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_ROOT, "packages", filename), "wb") as f:
        f.write(contents)

    # Built from the actual incoming request host, not a hardcoded localhost
    # URL — resolves correctly whether the admin is on this machine or
    # another one on the LAN (see App.jsx's API_BASE, which derives its host
    # the same way).
    return {"url": f"{str(request.base_url).rstrip('/')}/uploads/packages/{filename}"}


@app.get("/api/admin/packages")
def admin_list_packages(_admin: dict = Depends(auth.get_current_admin)):
    """All packages, including inactive — for Admin Portal management."""
    return {"packages": database.get_tour_packages(active_only=False)}


@app.post("/api/admin/packages", status_code=status.HTTP_201_CREATED)
def admin_create_package(request: TourPackageInput, _admin: dict = Depends(auth.get_current_admin)):
    return database.create_tour_package(request.model_dump())


@app.put("/api/admin/packages/{package_id}")
def admin_update_package(package_id: int, request: TourPackageInput, _admin: dict = Depends(auth.get_current_admin)):
    updated = database.update_tour_package(package_id, request.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Package not found.")
    return updated


@app.delete("/api/admin/packages/{package_id}")
def admin_delete_package(package_id: int, _admin: dict = Depends(auth.get_current_admin)):
    deleted = database.delete_tour_package(package_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Package not found.")
    return {"message": f"Package {package_id} deleted."}


@app.get("/api/admin/package-booking-requests")
def admin_list_package_booking_requests(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    return {"requests": database.get_package_booking_requests(status_filter)}


@app.post("/api/admin/package-booking-requests/{request_id}/resolve")
def admin_resolve_package_booking_request(request_id: int, body: PackageBookingReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_package_booking_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Booking request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    return database.resolve_package_booking_request(request_id, "confirmed", admin["admin_id"], body.admin_note)


@app.post("/api/admin/package-booking-requests/{request_id}/reject")
def admin_reject_package_booking_request(request_id: int, body: PackageBookingReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_package_booking_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Booking request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    return database.resolve_package_booking_request(request_id, "rejected", admin["admin_id"], body.admin_note)


# ── Visa Requirements (B2C, admin-curated lookup) ─────────────────────────────
# Staff enter known nationality->destination visa info; customers pick their
# nationality and destination and get a real answer if we have that route
# documented. Not exhaustive by design — a missing pair is a normal, expected
# result, not an error.

class VisaRequirementInput(BaseModel):
    nationality: str = Field(..., min_length=2, max_length=100)
    destination: str = Field(..., min_length=2, max_length=100)
    visa_required: str = Field(..., description="required|not_required|visa_on_arrival|e_visa")
    visa_type: Optional[str] = Field(None, max_length=150)
    processing_time: Optional[str] = Field(None, max_length=100)
    validity: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)


@app.get("/api/visa-requirements/check")
def check_visa_requirement(nationality: str, destination: str):
    """Public lookup — returns {found: false} rather than 404 when the
    specific pair isn't documented, since that's an expected, normal case."""
    req = database.get_visa_requirement(nationality.strip(), destination.strip())
    if not req:
        return {"found": False, "nationality": nationality, "destination": destination}
    return {"found": True, **req}


@app.get("/api/admin/visa-requirements")
def admin_list_visa_requirements(_admin: dict = Depends(auth.get_current_admin)):
    return {"requirements": database.get_visa_requirements()}


@app.post("/api/admin/visa-requirements", status_code=status.HTTP_201_CREATED)
def admin_create_visa_requirement(request: VisaRequirementInput, _admin: dict = Depends(auth.get_current_admin)):
    try:
        return database.create_visa_requirement(request.model_dump())
    except Exception as e:
        if "uq_visa_pair" in str(e) or "Duplicate entry" in str(e):
            raise HTTPException(status_code=409, detail="A visa requirement for this nationality/destination pair already exists — edit it instead.")
        raise


@app.put("/api/admin/visa-requirements/{requirement_id}")
def admin_update_visa_requirement(requirement_id: int, request: VisaRequirementInput, _admin: dict = Depends(auth.get_current_admin)):
    updated = database.update_visa_requirement(requirement_id, request.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Visa requirement not found.")
    return updated


@app.delete("/api/admin/visa-requirements/{requirement_id}")
def admin_delete_visa_requirement(requirement_id: int, _admin: dict = Depends(auth.get_current_admin)):
    deleted = database.delete_visa_requirement(requirement_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Visa requirement not found.")
    return {"message": f"Visa requirement {requirement_id} deleted."}


# ── Visa Consultants (admin-curated officer roster + country coverage) ────────
# Staff maintain a roster of named officers and assign each one to any number
# of destination countries (a country can have several officers covering it).
# The B2C visa consultation flow looks up every active officer assigned to
# the customer's destination so they see who they'd be booking with, and so
# the booking email below routes to all of them (falling back to the general
# VISA_CONSULTANT_EMAIL when a country has no officer assigned).

class VisaConsultantInput(BaseModel):
    consultant_name: str = Field(..., min_length=2, max_length=150)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=30)
    is_active: bool = True


@app.get("/api/visa-consultants/by-country")
def get_visa_consultants_for_country(country: str):
    """Public lookup — used by the B2C form to show who the customer will be booking
    with. Returns only display names, never emails/phones."""
    consultants = database.get_visa_consultants_by_country(country.strip())
    if not consultants:
        return {"found": False, "consultants": []}
    return {
        "found": True,
        "consultants": [{"consultant_name": c["consultant_name"]} for c in consultants],
    }


@app.get("/api/admin/visa-consultants")
def admin_list_visa_consultants(_admin: dict = Depends(auth.get_current_admin)):
    return {"consultants": database.get_visa_consultants()}


@app.post("/api/admin/visa-consultants", status_code=status.HTTP_201_CREATED)
def admin_create_visa_consultant(request: VisaConsultantInput, _admin: dict = Depends(auth.get_current_admin)):
    return database.create_visa_consultant(request.model_dump())


@app.put("/api/admin/visa-consultants/{consultant_id}")
def admin_update_visa_consultant(consultant_id: int, request: VisaConsultantInput, _admin: dict = Depends(auth.get_current_admin)):
    updated = database.update_visa_consultant(consultant_id, request.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Visa consultant not found.")
    return updated


@app.delete("/api/admin/visa-consultants/{consultant_id}")
def admin_delete_visa_consultant(consultant_id: int, _admin: dict = Depends(auth.get_current_admin)):
    deleted = database.delete_visa_consultant(consultant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Visa consultant not found.")
    return {"message": f"Visa consultant {consultant_id} deleted."}


@app.put("/api/admin/visa-consultants/{consultant_id}/countries/{country}")
def admin_assign_visa_consultant_country(consultant_id: int, country: str, _admin: dict = Depends(auth.get_current_admin)):
    database.assign_visa_consultant_country(consultant_id, country.strip())
    return {"message": f"{country} assigned."}


@app.delete("/api/admin/visa-consultants/{consultant_id}/countries/{country}")
def admin_unassign_visa_consultant_country(consultant_id: int, country: str, _admin: dict = Depends(auth.get_current_admin)):
    database.unassign_visa_consultant_country(consultant_id, country.strip())
    return {"message": f"{country} unassigned."}


# ── Visa Consultation Booking (B2C, public) ───────────────────────────────────
# Customer books one of two fixed daily slots (10:30 AM / 3:30 PM) to talk to
# the visa consultant assigned to their destination country. The booking is
# always saved first; the consultant notification email is best-effort on
# top of that — see services/email_service.py.

class VisaConsultationBookingCreate(BaseModel):
    nationality: str = Field(..., min_length=2, max_length=100)
    destination: str = Field(..., min_length=2, max_length=100)
    full_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=30)
    slot_date: str = Field(..., description="YYYY-MM-DD")
    slot_time: str = Field(..., description="One of: 10:30 AM, 3:30 PM")
    notes: Optional[str] = Field(None, max_length=1000)


@app.get("/api/visa-consultation/slots")
def get_visa_consultation_slots(date: str = Query(..., description="YYYY-MM-DD")):
    try:
        requested = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format.")
    if requested < datetime.now().date():
        raise HTTPException(status_code=400, detail="date cannot be in the past.")
    booked = database.get_visa_consultation_booked_slots(date)
    return {
        "date": date,
        "slots": [{"time": t, "available": t not in booked} for t in database.VISA_CONSULTATION_SLOT_TIMES],
    }


@app.post("/api/visa-consultation/book", status_code=status.HTTP_201_CREATED)
def book_visa_consultation(request: VisaConsultationBookingCreate, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    if request.slot_time not in database.VISA_CONSULTATION_SLOT_TIMES:
        raise HTTPException(status_code=400, detail=f"slot_time must be one of: {', '.join(database.VISA_CONSULTATION_SLOT_TIMES)}")
    try:
        requested = datetime.strptime(request.slot_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="slot_date must be in YYYY-MM-DD format.")
    if requested < datetime.now().date():
        raise HTTPException(status_code=400, detail="slot_date cannot be in the past.")

    destination = request.destination.strip()
    consultants = database.get_visa_consultants_by_country(destination)
    consultant_names = ", ".join(c["consultant_name"] for c in consultants) or None
    consultant_emails = ", ".join(c["email"] for c in consultants if c.get("email")) or None

    try:
        booking = database.create_visa_consultation(
            customer_id,
            request.nationality.strip(),
            destination,
            request.full_name.strip(),
            str(request.email),
            request.phone.strip(),
            request.slot_date,
            request.slot_time,
            request.notes.strip() if request.notes else None,
            consultant_names,
            consultant_emails,
        )
    except Exception as e:
        if "uq_visa_slot" in str(e) or "Duplicate entry" in str(e):
            raise HTTPException(status_code=409, detail="That slot was just booked by someone else — please pick another.")
        raise

    sent, email_error = services.send_visa_consultation_email(booking)
    database.mark_visa_consultation_email_sent(booking["id"], sent)
    booking["email_sent"] = sent
    if not sent:
        logger.warning(f"Visa consultation #{booking['id']} email not sent: {email_error}")
    return booking


class VisaConsultationReview(BaseModel):
    admin_note: Optional[str] = None


@app.get("/api/admin/visa-consultations")
def admin_list_visa_consultations(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    return {"consultations": database.get_visa_consultations(status_filter)}


@app.post("/api/admin/visa-consultations/{consultation_id}/resolve")
def admin_resolve_visa_consultation(consultation_id: int, body: VisaConsultationReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_visa_consultation_by_id(consultation_id)
    if not req:
        raise HTTPException(status_code=404, detail="Visa consultation booking not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Booking already {req['status']}.")
    return database.resolve_visa_consultation(consultation_id, "resolved", admin["admin_id"], body.admin_note)


@app.post("/api/admin/visa-consultations/{consultation_id}/reject")
def admin_reject_visa_consultation(consultation_id: int, body: VisaConsultationReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_visa_consultation_by_id(consultation_id)
    if not req:
        raise HTTPException(status_code=404, detail="Visa consultation booking not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Booking already {req['status']}.")
    return database.resolve_visa_consultation(consultation_id, "rejected", admin["admin_id"], body.admin_note)


# ── Customer Portal ─────────────────────────────────────────────────────────────

class CustomerRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: str = Field(..., min_length=2, max_length=150)
    phone: Optional[str] = None


class CustomerLoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/api/customers/register", status_code=status.HTTP_201_CREATED)
def customer_register(request: CustomerRegisterRequest):
    if database.get_customer_by_email(request.email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    password_hash = auth.hash_password(request.password)
    customer = database.create_customer(request.email, password_hash, request.full_name, request.phone)
    # Pick up any guest bookings already made under this email before the account existed.
    database.link_guest_bookings_by_email(customer["id"], customer["email"])
    token = auth.create_access_token({
        "sub": customer["email"], "role": "customer", "customer_id": customer["id"],
    })
    return {
        "access_token": token, "token_type": "bearer",
        "customer": {"email": customer["email"], "full_name": customer["full_name"]},
    }


@app.post("/api/customers/login")
def customer_login(request: CustomerLoginRequest):
    customer = database.get_customer_by_email(request.email)
    if not customer or not auth.verify_password(request.password, customer["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # Pick up any guest bookings made under this email since the last login.
    database.link_guest_bookings_by_email(customer["id"], customer["email"])
    token = auth.create_access_token({
        "sub": customer["email"], "role": "customer", "customer_id": customer["id"],
    })
    return {
        "access_token": token, "token_type": "bearer",
        "customer": {"email": customer["email"], "full_name": customer["full_name"]},
    }


@app.get("/api/customers/me/bookings")
def customer_my_bookings(current: dict = Depends(auth.get_current_customer)):
    bookings = database.get_bookings_for_customer(current["customer_id"])
    return {"bookings": bookings, "count": len(bookings)}


# ── Loyalty Program (Customer) ────────────────────────────────────────────────

@app.get("/api/customers/me/loyalty")
def customer_my_loyalty(current: dict = Depends(auth.get_current_customer)):
    summary = services.get_loyalty_summary(current["sub"])
    summary["recent_transactions"] = database.get_loyalty_transactions(current["sub"], limit=10)
    return summary


# ── Email Change Requests (Customer) ──────────────────────────────────────────

class EmailChangeRequestBody(BaseModel):
    new_email: EmailStr


@app.post("/api/customers/me/email-change-request", status_code=status.HTTP_201_CREATED)
def customer_request_email_change(request: EmailChangeRequestBody, current: dict = Depends(auth.get_current_customer)):
    if database.get_pending_email_change_request(current["customer_id"]):
        raise HTTPException(status_code=409, detail="You already have a pending email change request awaiting admin review.")
    if str(request.new_email).lower() == current["sub"].lower():
        raise HTTPException(status_code=422, detail="That is already your current email address.")
    if database.get_customer_by_email(str(request.new_email)):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    req = database.create_email_change_request(current["customer_id"], current["sub"], str(request.new_email))
    return req


@app.get("/api/customers/me/email-change-request")
def customer_get_email_change_request(current: dict = Depends(auth.get_current_customer)):
    req = database.get_pending_email_change_request(current["customer_id"])
    return {"request": req}


# ── Cancellation Request (Customer-facing, public) ────────────────────────────
# Reachable both by a signed-in customer (pre-filled from their own booking)
# and, unauthenticated, as a standalone "Cancel a Booking" form — see
# AdminPortal.jsx's admin-only /api/admin/cancellation-requests* for the
# review side. customer_id is captured only when a valid customer token is
# presented; a missing/invalid one never blocks submission.

class CancellationRequestCreate(BaseModel):
    booking_locator: str = Field(..., min_length=3, max_length=20)
    travel_date: str = Field(..., description="YYYY-MM-DD")
    requester_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=30)
    all_passengers_cancelling: bool = True


@app.post("/api/cancellation-requests", status_code=status.HTTP_201_CREATED)
def submit_cancellation_request(request: CancellationRequestCreate, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    req = database.create_cancellation_request(
        customer_id,
        request.booking_locator.upper().strip(),
        request.travel_date,
        request.requester_name.strip(),
        str(request.email),
        request.phone.strip(),
        request.all_passengers_cancelling,
    )
    return req


# ═══════════════════════════════════════════════════════════════════════════
# HOTEL (STAYS) API — Travelport TripServices Stays v11/v12
#
# Fully independent of the flight booking endpoints above: separate service
# modules (services/hotel_search_service.py, services/hotel_booking_service.py),
# separate storage (hotel_database.py / hotel_bookings table), no shared code
# with the Air integration beyond OAuth token reuse.
#
# NOTE: The sandbox Travelport account is not yet provisioned for Hotel/Stays
# — every Hotel endpoint currently returns 403 at the Akamai edge (confirmed
# live). These endpoints are wired up per the documented API contract
# (https://developer.travelport.com/apis/stays) and will start working as
# soon as Travelport enables the product on the account, with no further
# code changes needed here.
# ═══════════════════════════════════════════════════════════════════════════

class HotelSearchRequest(BaseModel):
    location_type: str = Field(default="cityIATACode", description="cityIATACode | airportIATACode")
    location_value: str = Field(..., description="IATA code, e.g. DXB")
    check_in_date: str = Field(..., description="YYYY-MM-DD")
    check_out_date: str = Field(..., description="YYYY-MM-DD")
    adults: int = Field(default=1, ge=1, le=9)
    children_ages: Optional[List[int]] = Field(default=None)
    rooms: int = Field(default=1, ge=1, le=9)
    radius_km: int = Field(default=30, ge=1, le=200)
    currency: Optional[str] = None


class HotelGuestInfo(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    country_access_code: Optional[str] = None
    area_city_code: Optional[str] = None


class HotelBookingRequest(BaseModel):
    chain_code: str
    property_code: str
    property_name: Optional[str] = None
    city: Optional[str] = None
    country_code: Optional[str] = None
    booking_code: str
    check_in_date: str
    check_out_date: str
    rooms: int = Field(default=1, ge=1, le=9)
    currency: str
    base_price: float
    total_taxes: float = 0.0
    total_price: float
    room_description: Optional[str] = None
    travelers: List[HotelGuestInfo]


@app.post("/api/hotels/search")
def hotel_search(request: HotelSearchRequest):
    """STEP 1 — Search hotels (property + room + rate) via SearchComplete."""
    try:
        raw = hotel_search_service.search_hotels(
            location_type=request.location_type,
            location_value=request.location_value,
            check_in_date=request.check_in_date,
            check_out_date=request.check_out_date,
            adults=request.adults,
            children_ages=request.children_ages,
            rooms=request.rooms,
            radius_km=request.radius_km,
            currency=request.currency,
        )
        properties = hotel_search_service.parse_hotel_offers(raw)
        return {"properties": properties, "count": len(properties)}
    except HotelApiError as e:
        logger.error(f"Hotel search failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.get("/api/hotels/properties/{chain_code}/{property_code}")
def hotel_property_details(chain_code: str, property_code: str, image_size: Optional[str] = Query(None, description="Large|Medium|Small|Thumbnail|ExtraLarge")):
    """Optional enrichment — property description, images, amenities. Does
    not require a prior search. https://developer.travelport.com/apis/stays/search-and-details/getpropertiesdetail"""
    try:
        raw = hotel_search_service.get_property_details(chain_code, property_code, image_size)
        return hotel_search_service.parse_property_details(raw)
    except HotelApiError as e:
        logger.error(f"Hotel property details failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.post("/api/hotels/book", status_code=status.HTTP_201_CREATED)
def hotel_book(request: HotelBookingRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """STEP 2 — Book a hotel room (full payload Create Reservation)."""
    try:
        raw = hotel_booking_service.create_hotel_reservation(
            chain_code=request.chain_code,
            property_code=request.property_code,
            booking_code=request.booking_code,
            check_in_date=request.check_in_date,
            check_out_date=request.check_out_date,
            rooms=request.rooms,
            guests=len(request.travelers),
            price={
                "currency": request.currency,
                "base": request.base_price,
                "total_taxes": request.total_taxes,
                "total_price": request.total_price,
            },
            travelers=[t.model_dump() for t in request.travelers],
        )
        parsed = hotel_booking_service.parse_hotel_reservation(raw)

        lead = request.travelers[0]
        booking_record = {
            **parsed,
            "customer_id": customer_id,
            "guest_name": f"{lead.first_name} {lead.last_name}",
            "guest_email": lead.email,
            "guest_phone": lead.phone,
            "property_name": parsed.get("property_name") or request.property_name or "",
            "chain_code": parsed.get("chain_code") or request.chain_code,
            "property_code": parsed.get("property_code") or request.property_code,
            "city": request.city or "",
            "country_code": request.country_code or "",
            "check_in_date": parsed.get("check_in_date") or request.check_in_date,
            "check_out_date": parsed.get("check_out_date") or request.check_out_date,
            "rooms": request.rooms,
            "room_description": parsed.get("room_description") or request.room_description or "",
            "total_price": parsed.get("total_price") or request.total_price,
            "currency": parsed.get("currency") or request.currency,
            "payment_method": "Credit Card",
        }
        saved = hotel_database.save_hotel_booking(booking_record)
        return {"success": True, "booking": booking_record, "cached_id": saved.get("id")}
    except HotelApiError as e:
        logger.error(f"Hotel booking failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.get("/api/hotels/retrieve/{locator_code}")
def hotel_retrieve(locator_code: str):
    """STEP 3 — Retrieve a hotel reservation live from Travelport, refreshing the local cache."""
    try:
        raw = hotel_booking_service.retrieve_hotel_reservation(locator_code)
        parsed = hotel_booking_service.parse_hotel_reservation(raw)

        cached = hotel_database.get_hotel_booking_by_locator(locator_code)
        if cached:
            for field in ("guest_name", "guest_email", "guest_phone", "customer_id", "city", "country_code"):
                if cached.get(field) and not parsed.get(field):
                    parsed[field] = cached.get(field)
        return parsed
    except HotelApiError as e:
        logger.warning(f"Live hotel retrieval failed for {locator_code}, falling back to cache: {e}")
        cached = hotel_database.get_hotel_booking_by_locator(locator_code)
        if cached:
            return cached
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.get("/api/hotels/history")
def hotel_booking_history(email: Optional[str] = Query(None, description="Filter by guest email")):
    """Local cache of hotel bookings — mirrors GET /api/bookings/history for flights."""
    try:
        bookings = hotel_database.get_all_hotel_bookings(email)
        return {"bookings": bookings, "count": len(bookings)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hotels/customers/me/bookings")
def hotel_customer_my_bookings(current: dict = Depends(auth.get_current_customer)):
    bookings = hotel_database.get_hotel_bookings_for_customer(current["customer_id"])
    return {"bookings": bookings, "count": len(bookings)}


@app.post("/api/hotels/{locator_code}/cancel")
def hotel_cancel(locator_code: str, supplier_locator: str = Query(..., description="Supplier locator from the booking confirmation")):
    """STEP 4 — Cancel a hotel reservation on Travelport and update the local cache."""
    try:
        hotel_booking_service.cancel_hotel_reservation(locator_code, supplier_locator)
    except HotelApiError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))

    db_cancelled = hotel_database.cancel_hotel_booking(locator_code)
    if not db_cancelled:
        raise HTTPException(status_code=404, detail="Hotel booking not found in local cache.")
    return {"message": f"Hotel booking {locator_code} successfully cancelled."}


@app.get("/api/admin/hotels/reports/summary")
def admin_hotel_reports_summary(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    _admin: dict = Depends(auth.get_current_admin),
):
    """Admin reporting for hotel sales — kept separate from the flight
    reports endpoint (/api/admin/reports/summary) so flight and hotel
    revenue are never silently blended."""
    start_dt = f"{start_date} 00:00:00" if start_date else None
    end_dt = f"{end_date} 23:59:59" if end_date else None
    return hotel_database.get_hotel_sales_summary(start_dt, end_dt)


# ═══════════════════════════════════════════════════════════════════════════
# INDIGO (6E) ANCILLARIES — Travelport legacy Universal API (uAPI)
#
# Fully independent of both the GDS/NDC flight endpoints above (JSON
# TripServices v11) and the Hotel endpoints — separate service modules
# (services/indigo_search_service.py, services/indigo_booking_service.py,
# services/indigo_uapi_client.py), separate credential set (config/
# indigo_config.py's TP_UAPI_*), separate wire format (SOAP-wrapped XML
# request/response bodies over HTTP Basic Auth, not JSON over OAuth). No
# shared code with the Air (GDS/NDC) integration, and nothing above this
# section was changed to add it.
#
# NOTE: TP_UAPI_USERNAME/PASSWORD/TARGET_BRANCH are not yet filled into
# .env — every endpoint below will raise IndigoApiError("...not
# configured...") until Travelport issues legacy Universal API credentials
# for IndiGo/ACH content and they're added there. The transport itself
# (SOAP envelope, endpoint, auth mechanism) was confirmed reachable live on
# 2026-09-29 — see indigo_uapi_client.py's docstring. Built strictly to the
# request/response shapes in the reference logs under "Indigo all ancillary
# service" (also converted to this project's JSON convention at
# backend/reference/indigo_uapi_json/).
# ═══════════════════════════════════════════════════════════════════════════

class IndigoSearchRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    departure_date: str = Field(..., description="YYYY-MM-DD")
    passenger_type: str = Field(default="ADT", description="ADT | CHD | INF — one type per search, see indigo_search_service module docstring")


class IndigoPriceRequest(BaseModel):
    segments_token: str = Field(..., description="From an /api/indigo/search flight result")
    fare_basis: str
    passenger_type: str = Field(default="ADT")


class IndigoPriceWithAncillariesRequest(IndigoPriceRequest):
    ancillary_tokens: List[str] = Field(..., description="`token` values from a prior price response's `ancillaries` list")


class IndigoSeatMapRequest(BaseModel):
    segments_token: str
    first_name: str
    last_name: str


class IndigoTraveler(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    prefix: Optional[str] = None
    gender: str = Field(..., description="Male | Female")
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    passenger_type_code: str = Field(default="ADT")
    nationality: str = Field(..., min_length=2, max_length=2, description="ISO country code")
    passport_number: str
    passport_issue_country: str = Field(..., min_length=2, max_length=2)
    passport_expiry: str = Field(..., description="YYYY-MM-DD")
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None


class IndigoBookingRequest(BaseModel):
    pricing_token: str = Field(..., description="From a price/price-with-ancillaries response")
    travelers: List[IndigoTraveler]
    selected_ancillary_tokens: Optional[List[str]] = None


@app.post("/api/indigo/search")
def indigo_search(request: IndigoSearchRequest):
    """STEP 1 — Search IndiGo (6E) flights via Travelport legacy uAPI (LowFareSearchReq)."""
    try:
        return indigo_search_service.search_flights(
            origin=request.origin.upper(),
            destination=request.destination.upper(),
            departure_date=request.departure_date,
            passenger_type=request.passenger_type,
        )
    except IndigoApiError as e:
        logger.error(f"IndiGo search failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.post("/api/indigo/price")
def indigo_price(request: IndigoPriceRequest):
    """STEP 2a — Price a selected IndiGo flight and discover available ancillaries (AirPriceReq, no services selected yet)."""
    try:
        return indigo_search_service.price_offer(
            segments_token=request.segments_token,
            fare_basis=request.fare_basis,
            passenger_type=request.passenger_type,
        )
    except IndigoApiError as e:
        logger.error(f"IndiGo price failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.post("/api/indigo/price-with-ancillaries")
def indigo_price_with_ancillaries(request: IndigoPriceWithAncillariesRequest):
    """STEP 2b — Re-price with the traveler's selected meals/baggage/seats attached (AirPriceReq, services selected)."""
    try:
        return indigo_search_service.price_offer_with_ancillaries(
            segments_token=request.segments_token,
            fare_basis=request.fare_basis,
            passenger_type=request.passenger_type,
            ancillary_tokens=request.ancillary_tokens,
        )
    except IndigoApiError as e:
        logger.error(f"IndiGo price-with-ancillaries failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.post("/api/indigo/seatmap")
def indigo_seatmap(request: IndigoSeatMapRequest):
    """Seat map for a priced IndiGo itinerary (SeatMapReq)."""
    try:
        return indigo_search_service.get_seat_map(
            segments_token=request.segments_token,
            first_name=request.first_name,
            last_name=request.last_name,
        )
    except IndigoApiError as e:
        logger.error(f"IndiGo seat map failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.post("/api/indigo/book", status_code=status.HTTP_201_CREATED)
def indigo_book(request: IndigoBookingRequest):
    """STEP 3 — Create the IndiGo reservation (AirCreateReservationReq, UniversalRecordService)."""
    try:
        return indigo_booking_service.create_reservation(
            pricing_token=request.pricing_token,
            travelers=[t.model_dump() for t in request.travelers],
            selected_ancillary_tokens=request.selected_ancillary_tokens,
        )
    except IndigoApiError as e:
        logger.error(f"IndiGo booking failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))
