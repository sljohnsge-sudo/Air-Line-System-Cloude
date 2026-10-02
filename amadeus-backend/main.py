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
    GET  /api/bookings/history             -> local cache of confirmed bookings

Hotel booking is blocked until Hotel_* operations are activated on the
WSAP (see project notes) -- not implemented here, no mock data.
"""

import logging
from datetime import date
from typing import Optional, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

import database
from services.amadeus_soap_client import AmadeusSoapError
from services import flight_search_service, flight_booking_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Amadeus Flight Booking API",
    description="Live Amadeus Web Services (SOAP/XML) integration via WSAP 1ASIWGSTMLR.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    model_config = {"populate_by_name": True}


class Traveler(BaseModel):
    firstName: str
    lastName: str
    type: str = "adult"  # adult | child | infant


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
        departure_date=segments[0]["departureDate"],
        return_date=None,
        passenger_name=f"{travelers[0]['firstName']} {travelers[0]['lastName']}",
        passenger_email=request.contactEmail,
        raw_offer={"segments": segments},
        raw_order=result,
    )

    return result


@app.post("/api/bookings/{locator}/issue-ticket")
async def issue_ticket(locator: str):
    return await flight_booking_service.issue_ticket(locator.upper().strip())


@app.get("/api/bookings/history")
def booking_history():
    return database.list_bookings()
