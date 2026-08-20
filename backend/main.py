"""
main.py
=======
FastAPI Application — Travelport TripServices API Gateway
All flight data, bookings, and tickets are sourced live from Travelport.
No mock data exists in this system.

Booking Workflow:
    POST  /api/flights/search          → STEP 2: Search flights
    POST  /api/bookings/create         → STEPS 4-9: Full booking + ticket issuance
    GET   /api/bookings/retrieve/{pnr} → STEP 8: Retrieve PNR details
    GET   /api/bookings/history        → Local cache of issued tickets
    POST  /api/bookings/{pnr}/cancel   → Cancel reservation on Travelport + local cache
"""

import logging
import httpx
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
import database
import services

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


class TravelerInfo(BaseModel):
    """Passenger details for STEP 6."""
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str = Field(..., min_length=2, max_length=50)
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    gender: str = Field(..., description="Male or Female")
    passport_number: str = Field(..., min_length=5, max_length=20)
    passport_expiry: str = Field(..., description="YYYY-MM-DD")
    nationality: str = Field(default="LK", min_length=2, max_length=3)
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
def get_airports(q: str = Query("", description="Search term for airports (IATA code, city, name)")):
    """
    Search local airport cache of 7900+ airports.
    """
    try:
        results = database.search_airports(q.strip())
        return {"airports": results}
    except Exception as e:
        logger.error(f"Airport search failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Airport reference lookup error: {str(e)}"
        )


# ── STEP 2: Flight Search ──────────────────────────────────────────────────────


@app.post("/api/flights/search")
def search_flights(request: FlightSearchRequest):
    """
    STEP 2 — Search available flights via Travelport catalog.
    Returns a list of parsed flight offers for the frontend.
    """
    if request.legs:
        leg_str = " | ".join([f"{l.origin}->{l.destination} on {l.departure_date}" for l in request.legs])
        logger.info(f"Flight search (multi-leg): {leg_str}")
    else:
        logger.info(f"Flight search: {request.origin} → {request.destination} on {request.departure_date}")

    try:
        legs = [l.model_dump() for l in request.legs] if request.legs else None
        raw = services.search_flights(
            origin=request.origin.upper() if request.origin else None,
            destination=request.destination.upper() if request.destination else None,
            departure_date=request.departure_date,
            adult_count=request.adult_count,
            child_count=request.child_count,
            infant_count=request.infant_count,
            cabin_preference=request.cabin_preference,
            legs=legs
        )
        offers = services.parse_flight_offers(raw, legs=legs)
        return {"flights": offers, "count": len(offers)}

    except Exception as e:
        error_msg = str(e)
        status_code = 502

        # Provide a helpful message for the sandbox account configuration issue
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

        logger.error(f"Flight search failed: {error_msg}")
        raise HTTPException(status_code=status_code, detail=detail)



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
    ticket["gender"] = lead.get("gender", "Male")
    ticket["phone"] = lead.get("phone", "")
    ticket["date_of_birth"] = lead.get("date_of_birth", "")


# ── STEPS 4-9: Create Full Booking + Issue Ticket ─────────────────────────────

@app.post("/api/bookings/create", status_code=status.HTTP_201_CREATED)
def create_booking(request: BookingCreateRequest):
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

        # Save to local cache
        saved = database.save_booking(ticket)

        return {
            "success": True,
            "ticket": ticket,
            "cached_id": saved.get("id")
        }

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
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

        # STEP 10: Fetch live seat map
        seat_map = None
        seat_map_available = False
        try:
            seat_map = services.get_seat_map(workbench_id, offer_id)
            seat_map_available = True
        except Exception as e:
            logger.warning(f"Live seat map retrieval failed: {e}")

        return {
            "workbench_id": workbench_id,
            "offer_id": offer_id,
            "seat_map": seat_map,
            "seat_map_available": seat_map_available
        }
    except Exception as e:
        logger.error(f"Booking initiation failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to initiate Travelport booking session: {str(e)}"
        )
    finally:
        if workbench_id:
            logger.info(f"Cleaning up temporary initiate workbench session {workbench_id}...")
            try:
                services.discard_workbench(workbench_id)
            except Exception as ex:
                logger.warning(f"Failed to discard temporary initiate workbench {workbench_id}: {ex}")



# ── STEPS 6-9: Confirm Booking (Add Travelers, Seats, Commit PNR & Ticket) ──

@app.post("/api/bookings/confirm", status_code=status.HTTP_201_CREATED)
def confirm_booking(request: BookingConfirmRequest):
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

    try:
        # STEPS 4-7: Full booking flow with automatic stale-workbench retry.
        # run_booking_flow() handles Galileo error 4350 (COMMIT OR IGNORE
        # RESERVATION WORKBENCH) at both create and commit stages by DELETing
        # the stale workbench and retrying the entire flow (up to 3 attempts).
        commit_result = services.run_booking_flow(raw_offering, travelers)
        locator_code = commit_result["locator_code"]
        logger.info(f"PNR generated: {locator_code}")

        # STEP 8 only — ticket issuance (STEP 9) now happens separately via
        # POST /api/bookings/{locator_code}/issue-ticket, gated on a
        # successful PayCorp payment confirmation. Do NOT issue here.
        ticket = services.retrieve_reservation(locator_code)

        # Galileo GDS segment compiler takes a second to generate the airline PNR.
        # If it is null, sleep 1.5 seconds and retrieve reservation again to populate it.
        if not ticket.get("airline_pnr"):
            import time
            time.sleep(1.5)
            updated_ticket = services.retrieve_reservation(locator_code)
            if updated_ticket.get("airline_pnr"):
                ticket["airline_pnr"] = updated_ticket["airline_pnr"]
                ticket["airline_pnr_source"] = updated_ticket["airline_pnr_source"]

        # Apply local seat assignments and seat pricing to ticket summary
        seat_numbers = [s.seat_number for s in selected_seats]
        seat_charges = sum([s.price for s in selected_seats])

        if seat_numbers:
            ticket["seat_number"] = ", ".join(seat_numbers)

        if request.custom_price:
            ticket["total_fare"] = request.custom_price + seat_charges
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

        # Save to local MySQL cache
        saved = database.save_booking(ticket)

        return {
            "success": True,
            "ticket": ticket,
            "cached_id": saved.get("id")
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
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

    try:
        ticket = services.issue_ticket(locator_code)
    except Exception as e:
        logger.error(f"Ticket issuance failed for {locator_code}: {e}")
        txn_note = f" (txnReference={payment_result.get('txn_reference')})" if payment_result else ""
        raise HTTPException(
            status_code=502,
            detail=f"Payment succeeded{txn_note} but Travelport ticket issuance failed: {str(e)}"
        )

    if payment_result:
        ticket["payment_txn_reference"] = payment_result.get("txn_reference")
        ticket["payment_auth_code"] = payment_result.get("auth_code")

    # issue_ticket()'s internal retrieve_reservation() only knows Travelport-side
    # data — re-apply the locally-computed fields (seat charge, contact
    # enrichment, adjusted total_fare) that /confirm already saved for this PNR.
    existing = database.get_booking_by_locator(locator_code)
    if existing:
        for key in ("seat_charge", "email", "phone", "date_of_birth", "gender",
                     "nationality", "passport_expiry", "offer_id", "payment_method"):
            if existing.get(key) not in (None, "", []):
                ticket.setdefault(key, existing.get(key))
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

    saved = database.save_booking(ticket)

    return {
        "success": True,
        "ticket": ticket,
        "cached_id": saved.get("id")
    }


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
def booking_history(email: Optional[str] = Query(None, description="Filter by passenger email")):
    """
    Return all locally cached issued tickets.
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
    # Cancel on Travelport
    tp_cancelled = services.cancel_reservation(locator_code)

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



@app.get("/api/invoice/pnr/{locator_code}")
def get_invoice_data(locator_code: str):
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
    pnr = locator_code.upper().strip()
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
        logger.error(f"Invoice retrieval failed for PNR {locator_code}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to retrieve invoice data from Travelport: {str(e)}"
        )


@app.get("/api/invoice/report")
def get_invoice_report(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD")
):
    """
    Retrieve live Travelport data for all PNRs booked/issued under PCC 7F3C within a date range.
    Queries the local sqlite cache for matching locators, then fetches live PNR data from Travelport.
    NO system-generated mock data.
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

