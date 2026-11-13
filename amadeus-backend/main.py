"""
main.py
=======
FastAPI application -- Amadeus Web Services (SOAP/XML) flight gateway.

This system is fully independent from the Travelport-based
Air-Line-System-Cloude project even though this folder now lives INSIDE
that repo (Air-Line-System-Cloude/amadeus-backend, moved there purely for
filesystem convenience): separate codebase, separate database
(`amadeus_system`), separate ports, no shared imports or network calls.
Do not merge or cross-link the two systems (shared routing, shared UI,
combined workbench, etc.) unless the user explicitly requests it. The
Travelport backend's unified search/booking (services/amadeus_aggregator.py,
services/amadeus_booking_proxy.py) only ever talks to this one over plain
HTTP on localhost:8002, exactly like a browser would -- the same as when
this was a separate sibling folder.

Booking workflow (mirrors the Travelport system's shape, adapted to
Amadeus's own session-based SOAP requirements):
    GET  /api/flights/search              -> live flight search
    POST /api/bookings/confirm            -> sell + PNR + price + TST (one
                                              Amadeus session, confirmed live)
    POST /api/bookings/{locator}/issue-ticket -> DocIssuance_IssueTicket
    GET  /api/bookings/{locator}/servicing -> tickets/EMDs and what staff may do
    POST /api/bookings/{locator}/cancel    -> PNR_Cancel (no valid tickets left)
    POST /api/bookings/{locator}/void      -> Ticket_CancelDocument (day of issue)
    POST /api/bookings/{locator}/refund-quote, /refund -> Ticket_*Refund 3.0
                                              (all staff-only, internal key)
    GET  /api/bookings/history             -> local cache of confirmed bookings
    GET  /api/bookings/lookup?query=...    -> "Check My Ticket Status": resolves a
                                              PNR/ticket number against our own
                                              bookings, then PNR_Retrieve for the
                                              live answer (called by the Travelport
                                              backend's unified /api/ticket-status,
                                              not directly by the browser)

Hotel booking is blocked until Hotel_* operations are activated on the
WSAP (see project notes) -- not implemented here, no mock data.
"""

import logging
from datetime import date
from typing import Optional, List

import os
import secrets

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

import database
from services.amadeus_soap_client import AmadeusSoapError
from services import flight_search_service, flight_booking_service, fare_rules_service, seat_map_service, ancillary_service, ancillary_booking_service, servicing_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Amadeus Flight Booking API",
    description="Live Amadeus Web Services (SOAP/XML) integration via WSAP 1ASIWGSTMLR.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    # :5175 is this backend's own standalone dev frontend. :5173 is the main
    # unified app (Travelport + Amadeus) -- added so its browser can call the
    # customer-facing Amadeus-only features (seat maps, fare rules, cancel)
    # directly, without a proxy through the Travelport backend.
    # 12/11/2026: the website moved to :5176 on 09/11 (test copies :5186/
    # :5187) and is also opened by LAN/server address, so origins are matched
    # by port on any host instead of the two old localhost URLs.
    allow_origin_regex=r"^https?://[^/:]+(:(5173|5175|5176|5186|5187))?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ticket issue, extras purchase, EMD issue, the booking list and the lookup
# carry money or passport data, so only the website's own backend may call
# them (server to server), never a browser: it sends this shared key. With no
# key configured they stay locked (fail closed). Added 12/11/2026 -- until
# then anyone who could reach this port could issue a ticket for any PNR.
INTERNAL_KEY = os.getenv("AMADEUS_INTERNAL_KEY", "").strip()


def require_internal_key(x_internal_key: str | None = Header(default=None)):
    if not INTERNAL_KEY:
        raise HTTPException(status_code=503, detail="Locked: AMADEUS_INTERNAL_KEY is not configured on the Amadeus backend.")
    if not x_internal_key or not secrets.compare_digest(x_internal_key, INTERNAL_KEY):
        raise HTTPException(status_code=403, detail="Not allowed.")


@app.on_event("startup")
def on_startup():
    database.init_db()


@app.exception_handler(AmadeusSoapError)
async def amadeus_error_handler(request, exc: AmadeusSoapError):
    logger.error("Amadeus SOAP error: %s", exc.message)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.get("/")
def root():
    return {"service": "Amadeus Flight Booking API", "status": "ok"}


@app.get("/api/flights/search")
async def search_flights(
    origin: str,
    destination: str,
    departureDate: date,
    returnDate: Optional[date] = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    maxResults: int = 20,
    cabin: Optional[str] = Query(None, pattern=r"^[YMWCF]$"),
):
    return await flight_search_service.search_flights(
        origin=origin,
        destination=destination,
        departure_date=departureDate,
        return_date=returnDate,
        adults=adults,
        children=children,
        infants=infants,
        max_results=maxResults,
        cabin=cabin,
    )


# ── Booking ──────────────────────────────────────────────────────────────

class Segment(BaseModel):
    departureDate: str
    departureTime: str
    arrivalDate: str
    arrivalTime: str
    from_: str = Field(alias="from")
    to: str
    marketingCarrier: str
    flightNumber: str
    bookingClass: str
    # From the search (12/11/2026): Amadeus prices seats and extras by the
    # fare basis (+ ticket designator), not the booking class.
    fareBasis: Optional[str] = None
    ticketDesignator: Optional[str] = None

    model_config = {"populate_by_name": True}


class Traveler(BaseModel):
    firstName: str
    lastName: str
    type: str = "adult"  # adult | child | infant
    dateOfBirth: Optional[str] = None  # ISO YYYY-MM-DD
    gender: Optional[str] = None  # "Male" | "Female"
    # Passport/document fields -- all optional, all-or-nothing in practice
    # (see flight_booking_service.add_document_elements): a traveler missing
    # any one of these simply gets no SSR DOCS element, same as today. Needed
    # for routes/carriers that inhibit ticketing without it (confirmed live:
    # errorCode 27791 "TICKETING INHIBITED-SSR DOCS MISSING FOR P1" on a
    # Doha-connecting Qatar Airways itinerary, PNR 9OV45B).
    passportNumber: Optional[str] = None
    passportExpiry: Optional[str] = None  # ISO YYYY-MM-DD
    passportIssueCountry: Optional[str] = None  # ISO 2-letter country code
    nationality: Optional[str] = None  # ISO 2-letter country code
    # 14/11/2026: special requests (SSRs to each airline, the airline answers)
    # and the residence address (SSR DOCA). All optional.
    mealCode: Optional[str] = Field(None, max_length=4)
    wheelchair: Optional[str] = Field(None, max_length=4)
    addressStreet: Optional[str] = Field(None, max_length=80)
    addressCity: Optional[str] = Field(None, max_length=40)
    addressState: Optional[str] = Field(None, max_length=40)
    addressCountry: Optional[str] = Field(None, max_length=3)
    addressPostalCode: Optional[str] = Field(None, max_length=17)


class BookingConfirmRequest(BaseModel):
    segments: List[Segment]
    travelers: List[Traveler]
    contactEmail: EmailStr
    contactPhone: str


@app.post("/api/bookings/confirm")
async def confirm_booking(request: BookingConfirmRequest):
    segments = [
        {
            "departureDate": s.departureDate,
            "departureTime": s.departureTime,
            "arrivalDate": s.arrivalDate,
            "arrivalTime": s.arrivalTime,
            "from": s.from_,
            "to": s.to,
            "marketingCarrier": s.marketingCarrier,
            "flightNumber": s.flightNumber,
            "bookingClass": s.bookingClass,
            "fareBasis": s.fareBasis,
            "ticketDesignator": s.ticketDesignator,
        }
        for s in request.segments
    ]
    travelers = [t.model_dump() for t in request.travelers]

    result = await flight_booking_service.confirm_booking(
        segments=segments,
        travelers=travelers,
        email=request.contactEmail,
        phone=request.contactPhone,
    )

    database.save_booking(
        order_id=result["locator"],
        origin=segments[0]["from"],
        destination=segments[-1]["to"],
        departure_date=database.amadeus_date_to_iso(segments[0]["departureDate"]),
        return_date=_return_date(segments),
        passenger_name=f"{travelers[0]['firstName']} {travelers[0]['lastName']}",
        passenger_email=request.contactEmail,
        raw_offer={"segments": segments},
        raw_order=result,
    )

    return result


# ── Fare rules (change/cancellation policy text) ───────────────────────────

class FareRulesRequest(BaseModel):
    segments: List[Segment]
    paxCount: int = 1


@app.post("/api/flights/fare-rules")
async def fare_rules(request: FareRulesRequest):
    segments = [
        {
            "departureDate": s.departureDate,
            "departureTime": s.departureTime,
            "arrivalDate": s.arrivalDate,
            "arrivalTime": s.arrivalTime,
            "from": s.from_,
            "to": s.to,
            "marketingCarrier": s.marketingCarrier,
            "flightNumber": s.flightNumber,
            "bookingClass": s.bookingClass,
            "fareBasis": s.fareBasis,
            "ticketDesignator": s.ticketDesignator,
        }
        for s in request.segments
    ]
    return await fare_rules_service.get_fare_rules(segments, total_pax=request.paxCount)


# ── Seat map ────────────────────────────────────────────────────────────────

@app.post("/api/flights/seat-map")
async def seat_map(request: Segment):
    segment = {
        "departureDate": request.departureDate,
        "departureTime": request.departureTime,
        "arrivalDate": request.arrivalDate,
        "arrivalTime": request.arrivalTime,
        "from": request.from_,
        "to": request.to,
        "marketingCarrier": request.marketingCarrier,
        "flightNumber": request.flightNumber,
        "bookingClass": request.bookingClass,
        "fareBasis": request.fareBasis,
        "ticketDesignator": request.ticketDesignator,
    }
    return await seat_map_service.get_seat_map(segment)


# ── Ancillaries (extra baggage, meal preferences) ──────────────────────────

class AncillaryRequest(BaseModel):
    segments: List[Segment]
    adults: int = 1
    children: int = 0


@app.post("/api/flights/ancillaries")
async def ancillaries(request: AncillaryRequest):
    segments = [
        {
            "departureDate": s.departureDate,
            "departureTime": s.departureTime,
            "arrivalDate": s.arrivalDate,
            "arrivalTime": s.arrivalTime,
            "from": s.from_,
            "to": s.to,
            "marketingCarrier": s.marketingCarrier,
            "flightNumber": s.flightNumber,
            "bookingClass": s.bookingClass,
            "fareBasis": s.fareBasis,
            "ticketDesignator": s.ticketDesignator,
        }
        for s in request.segments
    ]
    items = await ancillary_service.get_ancillary_offers(segments, adults=request.adults, children=request.children)
    return {"ancillaries": items, "ancillaries_available": bool(items)}


def _return_date(segments: list[dict]) -> str | None:
    """First departure of the second origin-destination (return or next
    leg), if the trip has one."""
    groups = flight_booking_service._od_groups(segments)
    for seg, g in zip(segments, groups):
        if g > 1:
            return database.amadeus_date_to_iso(seg["departureDate"])
    return None


@app.post("/api/bookings/{locator}/issue-ticket", dependencies=[Depends(require_internal_key)])
async def issue_ticket(locator: str):
    locator = locator.upper().strip()
    result = await flight_booking_service.issue_ticket(locator)
    if result.get("ticketNumbers"):
        database.mark_ticketed(locator, result["ticketNumbers"])
    return result


# ── Paid extras, seats and EMDs on an existing PNR (Amadeus sample flow) ──
# Website backend only (internal key). Order for a paid booking: confirm ->
# add extras/seats (airline prices them, nothing charged) -> customer pays the
# airline-confirmed total -> issue ticket -> issue EMDs.

class PassengerRef(BaseModel):
    lastName: str
    firstName: Optional[str] = None


class ExtraItem(BaseModel):
    ssr_code: str
    rfic: str
    rfisc: str
    airline_code: Optional[str] = None
    name: Optional[str] = None
    free_text: Optional[str] = None
    flight: Optional[str] = None
    traveler: Optional[PassengerRef] = None


class SeatItem(BaseModel):
    seat: str
    flight: Optional[str] = None
    traveler: PassengerRef


class PurchaseRequest(BaseModel):
    extras: List[ExtraItem] = []
    seats: List[SeatItem] = []
    receivedFrom: str = "WEB"


@app.post("/api/bookings/{locator}/extras", dependencies=[Depends(require_internal_key)])
async def add_extras(locator: str, request: PurchaseRequest):
    if not request.extras and not request.seats:
        raise HTTPException(status_code=400, detail="Nothing to add.")
    return await ancillary_booking_service.purchase_on_pnr(
        locator.upper().strip(),
        extras=[x.model_dump() for x in request.extras],
        seats=[s.model_dump() for s in request.seats],
        received_from=request.receivedFrom[:30].upper() or "WEB",
    )


@app.post("/api/bookings/{locator}/issue-emd", dependencies=[Depends(require_internal_key)])
async def issue_emd(locator: str):
    locator = locator.upper().strip()
    result = await ancillary_booking_service.issue_emds(locator)
    if result.get("emd_numbers"):
        database.mark_emds(locator, result["emd_numbers"])
    return result


# ── Staff: cancel, void and refund (services/servicing_service.py) ─────
# Website backend only (internal key); each action also has its own on/off
# switch on the website. Cancel is refused while a ticket or EMD is still
# valid, so no booking loses its seats while the money is still with the
# airline.

class ServicingRequest(BaseModel):
    staff: str = Field(default="STAFF", max_length=60)
    documents: List[str] = []                 # refund only: empty = every valid ticket and EMD
    fees: dict[str, float] = {}               # refund only: airline cancellation fee per document
    cancelFlights: bool = True
    confirmEmds: bool = False                 # cancel: go ahead although a seat/bag EMD is still valid


def _rf(staff: str) -> str:
    return ("ADMIN " + "".join(ch for ch in (staff or "STAFF").upper() if ch.isalnum() or ch == " "))[:30]


def _note(action: str, staff: str, detail: str) -> str:
    return f"{action} by {staff or 'staff'}: {detail}"


@app.get("/api/bookings/{locator}/servicing", dependencies=[Depends(require_internal_key)])
async def servicing_view(locator: str):
    return await servicing_service.get_servicing_view(locator.upper().strip())


@app.post("/api/bookings/{locator}/cancel", dependencies=[Depends(require_internal_key)])
async def cancel_booking(locator: str, request: ServicingRequest):
    locator = locator.upper().strip()
    result = await servicing_service.cancel_booking(locator, _rf(request.staff), confirm_emds=request.confirmEmds)
    if result.get("success"):
        database.mark_servicing(locator, "cancelled", _note("Cancelled", request.staff, result.get("message", "")))
    return result


@app.post("/api/bookings/{locator}/void", dependencies=[Depends(require_internal_key)])
async def void_booking(locator: str, request: ServicingRequest):
    locator = locator.upper().strip()
    result = await servicing_service.void_booking(locator, _rf(request.staff), cancel_flights=request.cancelFlights)
    voided = [r["number"] for r in result.get("results", []) if r.get("voided")]
    if voided:
        database.mark_servicing(locator, "voided" if result.get("success") else "partly voided",
                                _note("Voided", request.staff, ", ".join(voided)))
    return result


@app.post("/api/bookings/{locator}/refund-quote", dependencies=[Depends(require_internal_key)])
async def refund_quote(locator: str, request: ServicingRequest):
    return await servicing_service.refund_quote(locator.upper().strip(), request.documents or None, request.fees)


@app.post("/api/bookings/{locator}/refund", dependencies=[Depends(require_internal_key)])
async def refund_booking(locator: str, request: ServicingRequest):
    locator = locator.upper().strip()
    result = await servicing_service.refund_booking(locator, _rf(request.staff), request.documents or None,
                                                    request.fees, cancel_flights=request.cancelFlights)
    if result.get("success"):
        lines = "; ".join(f"{l['number']} {l['refundable']:.0f} {l['refund_currency']}"
                          + (f" (fee {l['airline_fee']:.0f})" if l.get("airline_fee") else "") for l in result["lines"])
        database.mark_servicing(locator, "refunded", _note("Refunded", request.staff, lines))
    return result


@app.get("/api/bookings/history", dependencies=[Depends(require_internal_key)])
def booking_history():
    return database.list_bookings()


@app.get("/api/bookings/lookup", dependencies=[Depends(require_internal_key)])
async def lookup_booking(query: str):
    """"Check My Ticket Status" support -- called by the Travelport
    backend's unified /api/ticket-status endpoint (never directly by the
    browser; CORS here is scoped to :5175 only). Resolves the PNR/ticket
    number against OUR OWN bookings as a local index only, then re-fetches
    the actual data live from Amadeus via PNR_Retrieve -- the response body
    is never served from the DB. 404 if this isn't one of our bookings."""
    query = query.strip().upper()
    match = database.find_booking_by_pnr_or_ticket(query)
    if not match:
        raise HTTPException(status_code=404, detail="No Amadeus booking found for that PNR or ticket number.")
    return await flight_booking_service.get_booking_status(match["order_id"])
