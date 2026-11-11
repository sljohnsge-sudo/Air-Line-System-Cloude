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
    POST  /api/bookings/{pnr}/cancel/quote → Staff: void / refund / cancel quote from Travelport
    POST  /api/bookings/{pnr}/cancel   → Staff: void / refund / cancel on Travelport + local record
"""

import asyncio
import gzip
import hashlib
import json
import zipfile
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
import httpx
from fastapi import FastAPI, HTTPException, Query, status, Depends, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field, model_validator, field_validator
from typing import Optional, List, Literal
import database
import services
import auth
import hotel_database
import insights
from utils.dates import fmt_date, fmt_datetime
from services import seat_rules, fare_rule_service, seat_request_log
from services.special_services import MEAL_SSR_TYPES, merge_ssr_status
from services import segment_status
from services import live_booking_service
from services import manage_booking_service
from services import addon_billing
from services import customer_notify
from services import addon_deadlines
from services import ticketing_exceptions
from services import payment_reconciliation
from services import go_live_switches
from services import supplier_alerts
from services import email_service, sms_service
from services import cybersource_service
from services import hotel_search_service, hotel_booking_service, hotel_availability_service, hotel_rules_service
from services.hotel_common import HotelApiError
from services import indigo_search_service, indigo_booking_service
from services.indigo_uapi_client import IndigoApiError
from config.travelport_config import TravelportConfig
from config.auth_config import AuthConfig

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


@app.on_event("startup")
async def _bigger_worker_pool():
    """Each live Travelport search holds a worker thread for its whole wait
    (asyncio.to_thread). Python's default pool is only cpu+4 threads (16
    here), so with ~20 customers searching at once the rest queued for a
    free thread and waited up to ~15 s extra (load test 2026-11-03). The
    threads mostly wait on the network, so a larger pool is cheap."""
    from concurrent.futures import ThreadPoolExecutor
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=int(os.getenv("API_WORKER_THREADS", "64")), thread_name_prefix="api"))


@app.on_event("startup")
def _start_extras_deadline_sweep():
    """Unpaid seats/extras on ticketed bookings are released when their
    payment deadline passes even if the customer never comes back
    (services/addon_deadlines.py). EXTRAS_SWEEP_MINUTES=0 turns it off —
    run one process with it on if the API is scaled to several workers."""
    addon_deadlines.start_sweeper(int(os.getenv("EXTRAS_SWEEP_MINUTES", "15")))


@app.on_event("startup")
def _ensure_insight_tables():
    try:
        insights.ensure_tables()
    except Exception as e:
        logger.error(f"Admin insight tables could not be created: {e}")

# ── Go-live module gate (server-side enforcement of the feature flags) ────────
# Nov 1 launch ships Travelport flights only. Hotels/Visa/Packages are built
# but hidden from customers until each is ready (see ProviderSettingsRequest/
# public_feature_flags above) -- hiding the nav tab isn't enough on its own,
# since the underlying route would otherwise still work for anyone who calls
# it directly. This blocks the customer-facing routes for a disabled module
# with a clear 503 instead; /api/admin/* management endpoints for the same
# modules are deliberately NOT in this list, so staff can keep preparing
# hotel/visa/package content behind the scenes while it's hidden from
# customers. Checked by exact path prefix, each only ever matching the
# customer-facing routes (confirmed against every /api/hotels*, /api/visa-*,
# /api/packages* route in this file -- none of their admin equivalents share
# these prefixes, they all live under /api/admin/...).
#
# Registered BEFORE add_middleware(CORSMiddleware) below on purpose: Starlette
# builds its middleware stack so the LAST one added ends up OUTERMOST. CORS
# must be outermost so it still adds Access-Control-Allow-Origin to this
# middleware's own short-circuit 503 responses -- registering this gate after
# CORS instead put CORS innermost, so a disabled-module 503 (returned here
# without ever reaching CORS) came back with no CORS headers at all and the
# browser reported it as an opaque network failure instead of a readable 503
# (confirmed live: a disabled Packages toggle looked like a CORS/connectivity
# bug on the frontend until this was reordered).
FEATURE_GATED_PREFIXES = {
    "hotels_enabled": ("/api/hotels",),
    "visa_enabled": ("/api/visa-", "/api/customers/me/visa-"),
    "packages_enabled": ("/api/packages",),
}


# Phase 1 sells Travelport flights only. The Amadeus booking proxy follows the
# same Provider Control search_mode that already keeps Amadeus out of search,
# and the IndiGo (Travelport uAPI) routes stay off unless INDIGO_ENABLED=true —
# both used to answer requests even while their modules were switched off.
AMADEUS_ROUTE_PREFIXES = ("/api/amadeus-bookings",)
INDIGO_ROUTE_PREFIXES = ("/api/indigo",)
INDIGO_ENABLED = os.getenv("INDIGO_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _module_unavailable() -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "This feature is temporarily unavailable."})


@app.middleware("http")
async def _enforce_module_flags(request: Request, call_next):
    path = request.url.path
    if path.startswith(INDIGO_ROUTE_PREFIXES) and not INDIGO_ENABLED:
        return _module_unavailable()
    # MySQL reads in a worker thread: this middleware runs on every request,
    # so any wait here would hold up the whole server.
    if path.startswith(AMADEUS_ROUTE_PREFIXES):
        if (await asyncio.to_thread(database.get_provider_settings)).get("search_mode", "travelport_only") not in ("both", "amadeus_only"):
            return _module_unavailable()
    for flag_name, prefixes in FEATURE_GATED_PREFIXES.items():
        if any(path.startswith(p) for p in prefixes):
            settings = await asyncio.to_thread(database.get_provider_settings)
            if not settings.get(flag_name, True):
                return _module_unavailable()
            break
    return await call_next(request)


# Customer-facing booking routes an admin-blocked IP may not use.
BLOCKABLE_PREFIXES = ("/api/flights/search", "/api/bookings/initiate", "/api/bookings/confirm",
                      "/api/ndc/book", "/api/payments/init")


@app.middleware("http")
async def _journey_and_ip(request: Request, call_next):
    """Binds the caller's IP and booking-journey id (X-Journey-Id header) for
    insights.py, refuses admin-blocked IPs on the booking routes, and records
    each booking-flow endpoint as a passed/failed journey step — a failure
    keeps the exact message the customer saw."""
    tokens = insights.bind_request(request)
    try:
        path = request.url.path
        if path.startswith(BLOCKABLE_PREFIXES) and await asyncio.to_thread(insights.is_blocked, insights.current_ip()):
            return JSONResponse(status_code=403, content={
                "detail": "We can't process requests from your network right now. Please contact Tick and Book."})
        step = insights.endpoint_step(request.method, path) if insights.current_journey() else None
        if step == "Search" and request.headers.get("x-search-kind"):
            step = None   # a ±days check or fare re-check, not the customer's own search
        response = await call_next(request)
        if step:
            if response.status_code < 400:
                await asyncio.to_thread(insights.mark, step, "pass")
            else:
                body = b"".join([chunk async for chunk in response.body_iterator])
                try:
                    detail = json.loads(body).get("detail")
                    detail = detail if isinstance(detail, str) else json.dumps(detail)[:1000]
                except Exception:
                    detail = body[:300].decode("utf-8", "replace")
                await asyncio.to_thread(insights.mark, step, "fail", f"HTTP {response.status_code}: {detail}")
                response = Response(content=body, status_code=response.status_code,
                                    headers=dict(response.headers), media_type=response.media_type)
        return response
    finally:
        insights.unbind_request(tokens)


# CORS: only the site's own origins may call this API from a browser. Set
# CORS_ALLOWED_ORIGINS (comma-separated, e.g. https://book.example.lk) in
# production. Unset, it allows localhost and private-network (LAN) origins on
# any port, so the dev setup and LAN testing keep working — never the whole
# internet. No cookies are used (tokens travel in the Authorization header),
# so credentials stay off.
_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None if _cors_origins else (
        r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$"
    ),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Go-live readiness ──────────────────────────────────────────────────────────
# Settings that must be right before real customers book. Logged as warnings
# at startup and shown to admins at GET /api/admin/go-live-checks.
def go_live_checks() -> list[dict]:
    from utils import tp_logger
    from config.hotel_config import HotelConfig
    checks = []

    def add(ok: bool, item: str, detail: str):
        checks.append({"ok": bool(ok), "item": item, "detail": detail})

    prod = TravelportConfig.is_production()
    add(prod, "Travelport environment", f"{TravelportConfig.API_BASE} ({'production' if prod else 'pre-production'})")
    add(TravelportConfig.ACCESS_GROUP, "Travelport access group", "set" if TravelportConfig.ACCESS_GROUP else "TP_ACCESS_GROUP is empty")
    add(_cors_origins, "Allowed website origins (CORS)",
        ", ".join(_cors_origins) or "CORS_ALLOWED_ORIGINS not set — only localhost/LAN origins can call the API")
    add(database.MYSQL_PASSWORD, "MySQL password", "set" if database.MYSQL_PASSWORD else "MYSQL_PASSWORD is empty")
    erp = os.getenv("ERP_BASE_URL", "")
    add(erp and "-dev" not in erp, "ERP server", erp or "ERP_BASE_URL not set (ERP sync off)")
    add(not tp_logger._unredacted(), "Log masking",
        "on" if not tp_logger._unredacted() else "TP_LOG_UNREDACTED is on — logs hold tokens and passport data")
    try:
        mode = database.get_provider_settings().get("search_mode")
    except Exception:
        mode = "unknown"
    add(mode == "travelport_only", "Provider Control", f"search_mode = {mode}")
    add(not INDIGO_ENABLED, "IndiGo routes", "off" if not INDIGO_ENABLED else "INDIGO_ENABLED is on")
    holder = HotelConfig.GUARANTEE_CARD_HOLDER_NAME or ""
    add("test" not in holder.lower(), "Hotel guarantee card",
        "looks like a test card — replace before enabling hotels" if "test" in holder.lower() else "ok")
    return checks


try:
    for _c in go_live_checks():
        if not _c["ok"]:
            logger.warning(f"GO-LIVE CHECK: {_c['item']}: {_c['detail']}")
except Exception as _e:
    logger.warning(f"Go-live checks could not run: {_e}")


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
    "UNAVAILABLE IN THE REQUESTED CLASS",   # AirPrice: the booking class sold out (MH / TG, 2026-11-02)
)


# Shown when Travelport / the airline errors while making the booking (before
# any payment). The real error is logged and kept on the booking journey.
BOOKING_NOT_CONFIRMED_DETAIL = ("The airline couldn't confirm your booking just now. You have not been charged — "
                                "please try again in a moment, or choose another flight.")
# The same failure, worded for its real cause (a schedule change has its own
# message, _schedule_changed_detail). Last 7 days to 09/11/2026: 33 of 61 were
# "REQUESTED CABIN HAS NO AVAILABLE FARES", 21 a 504 timeout.
FARE_GONE_DETAIL = ("This fare is no longer available from the airline. You have not been charged — "
                    "please choose another flight or fare.")
AIRLINE_TIMEOUT_DETAIL = ("The airline didn't answer in time, so your booking wasn't confirmed. You have not been "
                          "charged — please try again in a moment.")
_FARE_GONE_MARKERS = ("NO AVAILABLE FARES", "NO FARES AVAILABLE", "NO VALID FARE", "UNABLE TO SELL",
                      "CLASS NOT AVAILABLE", "NO SEATS AVAILABLE", "SOLD OUT")
_TIMEOUT_MARKERS = ("504", "GATEWAY TIME", "TIMED OUT", "TIMEOUT", "DIDN'T RESPOND IN TIME")


def _not_confirmed_detail(error) -> str:
    """Customer message for a booking the airline/Travelport didn't confirm,
    picked from the real error (which stays in the log and the journey)."""
    text = str(error or "").upper()
    if any(m in text for m in _FARE_GONE_MARKERS):
        return FARE_GONE_DETAIL
    if isinstance(error, httpx.TimeoutException) or any(m in text for m in _TIMEOUT_MARKERS):
        return AIRLINE_TIMEOUT_DETAIL
    return BOOKING_NOT_CONFIRMED_DETAIL


def friendly_travelport_error(raw_message: str) -> str:
    """Returns a customer-facing message for a known stale-offer/availability
    error, or the original message unchanged if it doesn't match one."""
    upper = (raw_message or "").upper()
    if "504" in upper or "GATEWAY TIME-OUT" in upper or "GATEWAY TIMEOUT" in upper or "TIMED OUT" in upper:
        insights.mark("Travelport availability check", "fail", "Travelport took too long to answer", raw_error=raw_message)
        return ("The airline's system is taking longer than usual to answer. Please try again in a moment — "
                "you have not been charged.")
    if any(marker in upper for marker in _STALE_OFFER_MARKERS):
        friendly = (
            "This flight's fare or availability has changed since you searched. "
            "Please go back and search again to see current prices."
        )
        # Keep Travelport's own words on the booking journey for the admin.
        insights.mark("Travelport availability check", "fail",
                      "The fare/seat offered in the search is no longer available", raw_error=raw_message)
        return friendly
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
    student_count: int = Field(default=0, ge=0, le=9, description="passengerTypeCode STU — Travelport-only, requires a carrier that files student fares to see a discount")
    cabin_preference: Optional[str] = Field(default=None, description="Economy|Business|First")
    content_source: Optional[str] = Field(default=None, description="Per-request override: 'GDS' or 'NDC' — defaults to server config when omitted")


class TravelerInfo(BaseModel):
    """Passenger details for STEP 6."""
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str = Field(..., min_length=2, max_length=50)
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    gender: str = Field(..., pattern="^(Male|Female)$", description="Male or Female")
    passport_number: str = Field(..., min_length=5, max_length=20)
    passport_expiry: str = Field(..., description="YYYY-MM-DD, must be a future date")
    nationality: str = Field(..., min_length=2, max_length=3, description="ISO country code — required, never defaulted")
    passport_issue_country: str = Field(..., min_length=2, max_length=3, description="TravelDocument.issueCountry — the country that issued the passport, which can differ from nationality")
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    passenger_type: Optional[str] = Field(default="ADT", pattern="^(ADT|CNN|INF|STU)$", description="ADT|CNN|INF|STU")

    # These two fields were previously only validated in the frontend (format,
    # "must be a future date" for passport_expiry) — a direct API call bypassing
    # the UI could submit an expired passport or a malformed/nonsense date.
    # Mirrors the same checks frontend/src/App.jsx already runs before submit.
    @field_validator("date_of_birth")
    @classmethod
    def _validate_date_of_birth(cls, v: str) -> str:
        try:
            dob = datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("date_of_birth must be a valid date in YYYY-MM-DD format")
        if dob > datetime.now().date():
            raise ValueError("date_of_birth cannot be in the future")
        if dob.year < 1900:
            raise ValueError("date_of_birth is not a plausible date")
        return v

    @field_validator("passport_expiry")
    @classmethod
    def _validate_passport_expiry(cls, v: str) -> str:
        try:
            expiry = datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("passport_expiry must be a valid date in YYYY-MM-DD format")
        if expiry <= datetime.now().date():
            raise ValueError("passport_expiry must be a future date")
        return v

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

    # ── Special service requests (sent to the airline before commit) ────────
    meal_code: Optional[str] = Field(default=None, description="IATA special meal SSR code, e.g. VGML")
    wheelchair: Optional[str] = Field(default=None, pattern="^(WCHR|WCHS|WCHC)$", description="WCHR | WCHS | WCHC")

    @field_validator("meal_code")
    @classmethod
    def _validate_meal_code(cls, v: Optional[str]) -> Optional[str]:
        if v and v not in MEAL_SSR_TYPES:
            raise ValueError("meal_code must be one of " + ", ".join(MEAL_SSR_TYPES))
        return v or None


class FareRulesRequest(BaseModel):
    raw_offering: dict = Field(..., description="The raw offer from the search response")


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
    # The booking flow books and tickets the flight only; seats and extras
    # are added after ticketing in Manage My Booking (paid once the airline
    # confirms them). False skips the seat map and ancillary shop here.
    include_addons: bool = True


class SelectedSeat(BaseModel):
    """Traveler seat selection choice. leg_index distinguishes an outbound
    vs. return seat on a round trip (0 = outbound/first leg, 1 = return) —
    Travelport returns a separate seat map per leg (see
    services/workbench_service.py's parse_seat_map_response), and a
    passenger can pick a different seat on each."""
    passenger_idx: int
    leg_index: int = 0
    seat_number: str
    price: float
    currency: str = "LKR"
    type: str = "Standard"
    # Which direction + flight this seat map was for ("Departure", "AI282").
    # leg_index is really a per-FLIGHT index (Travelport returns one seat
    # map per flight), so on a connecting round trip index 1 can still be
    # a departure flight — these let the saved label say what it really is.
    direction: Optional[str] = None
    flight_number: Optional[str] = None


def _offering_is_gds(raw_offering: dict) -> bool:
    """True when every leg of the selected offer is GDS content — the same
    all-legs rule confirm_booking uses to set fare_source."""
    if "outbound" in raw_offering and "inbound" in raw_offering:
        legs = [raw_offering["outbound"], raw_offering["inbound"]]
    elif "legs" in raw_offering:
        legs = raw_offering["legs"]
    else:
        legs = [raw_offering]
    return all(leg.get("fare_source") == "GDS" for leg in legs)


# Phase 1 books paid seats and extras for GDS only (Travelport: NDC seats must
# be booked inside the initial booking and NDC extras need Ancillary Price
# first — Phase 2). Shown instead of a seat map/extras list for other fares.
NON_GDS_ADDONS_NOTE = "Seat and extras selection isn't available online for this fare yet. You can request them from the airline after booking."


def _format_seat_numbers(selected_seats: List[SelectedSeat]) -> List[str]:
    """One display entry per seat selection for ticket["seat_number"]:
    plain "14A" for a single-flight booking, otherwise "Departure AI282:
    14A" so each seat can be matched back to its own flight on the ticket.
    The frontend only sends flight_number when there was more than one seat
    map; older clients that don't send it fall back to the leg_index guess."""
    if not any(s.leg_index or s.flight_number for s in selected_seats):
        return [s.seat_number for s in selected_seats]
    out = []
    for s in selected_seats:
        direction = s.direction or ("Departure" if s.leg_index == 0 else "Return")
        label = f"{direction} {s.flight_number}" if s.flight_number else direction
        out.append(f"{label}: {s.seat_number}")
    return out


class SelectedAncillary(BaseModel):
    """Traveler ancillary (baggage/meal) selection choice."""
    passenger_idx: int
    name: str
    price: float
    currency: str = "LKR"
    service_type: Optional[str] = None
    # Identify the item again in a fresh Ancillary Shop on the PNR (ids like
    # "anc_off1" are only valid in the workbench that returned them).
    airline_name: Optional[str] = None
    segment_numbers: List[int] = []
    catalog_offering_id: Optional[str] = None


class BookingConfirmRequest(BaseModel):
    """
    Confirms booking: creates a fresh GDS workbench at commit time to avoid
    WORKBENCH ID IS NOT VALID errors caused by session TTL expiry.
    raw_offering is used to rebuild the workbench fresh on every confirm.
    """
    raw_offering: dict = Field(..., description="Full raw offer object from search response")
    travelers: List[TravelerInfo]
    selected_seats: List[SelectedSeat]
    selected_ancillaries: List[SelectedAncillary] = []
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


def _travelport_result_messages(raw: dict) -> list[str]:
    """Travelport's own warnings/errors in a search response (Result.Warning /
    Result.Error), kept with the search for Airline Rates Logs."""
    res = ((raw or {}).get("CatalogProductOfferingsResponse") or {}).get("Result") or {}
    out = []
    for kind in ("Error", "Warning"):
        for m in res.get(kind) or []:
            text = (m.get("Message") or "").strip() if isinstance(m, dict) else str(m)
            if text and text not in out:
                out.append(f"{kind}: {text}"[:300])
    return out


def _notify_provider_failure(provider: str, error_message: str, route_info: str | None) -> None:
    """Logs a provider_notifications row and, unless the same provider already
    emailed within the debounce window (an outage shouldn't flood the inbox
    with one email per failed search), sends the alert to every active
    notification_recipients address."""
    recent = database.get_recent_provider_notification(provider, _NOTIFICATION_DEBOUNCE_MINUTES)
    recipients = [r["email"] for r in database.get_notification_recipients(active_only=True)]
    should_email = bool(recipients) and recent is None

    email_sent = False
    # Who it went to and what happened (Airline Rates Logs › notification delivery).
    if not recipients:
        delivery = {"result": "no_recipients", "recipients": []}
    elif recent is not None:
        delivery = {"result": "skipped_recent", "recipients": recipients,
                    "note": f"An email for this supplier already went out at {recent.get('created_at')}"}
    else:
        delivery = {"result": "sent", "recipients": recipients}
    if should_email:
        sent, err = services.send_provider_failure_email(", ".join(recipients), provider, error_message, route_info)
        email_sent = sent
        if not sent:
            delivery = {"result": "failed", "recipients": recipients, "note": (err or "")[:300]}
            logger.warning(f"Provider-failure notification email not sent: {err}")

    try:
        row = database.create_provider_notification(provider, error_message, route_info, email_sent)
        database.set_provider_notification_trace(row["id"], insights.current_journey(), delivery)
    except Exception as db_err:
        logger.error(f"Failed to log provider_notifications row: {db_err}")


def _notify_stuck_booking(locator_code: str, error_message: str, payment_txn_ref: str | None) -> None:
    """Logs a stuck_booking_notifications row and emails every active
    notification_recipients address whenever a booking's payment step
    succeeded (or needed no online payment) but Travelport's ticketing
    commit then failed -- the booking exists but was never ticketed, with
    no automatic retry. No debounce here (unlike provider failures): each
    stuck booking is a distinct, specific transaction staff need to see, not
    a repeating symptom of one outage."""
    recipients = [r["email"] for r in database.get_notification_recipients(active_only=True)]
    email_sent = False
    if recipients:
        sent, err = services.send_stuck_booking_email(", ".join(recipients), locator_code, error_message, payment_txn_ref)
        email_sent = sent
        if not sent:
            logger.warning(f"Stuck-booking notification email not sent: {err}")

    try:
        database.create_stuck_booking_notification(locator_code, error_message, payment_txn_ref, email_sent)
    except Exception as db_err:
        logger.error(f"Failed to log stuck_booking_notifications row: {db_err}")


def _notify_airline_problem(locator_code: str, seg_codes: dict, txn_ref: Optional[str] = None) -> None:
    """Stuck-booking alert: the airline hasn't confirmed (or has cancelled) a
    flight on this PNR. txn_ref is set when a payment was already taken."""
    flights = ", ".join(f"{f} {c}" for f, c in seg_codes.items()
                        if not segment_status.is_confirmed((c, None)))
    note = (f"Airline has not confirmed the flight on this booking ({flights}). "
            + ("A payment WAS taken — refund it. " if txn_ref else "No payment taken. ")
            + "Check the PNR in Smartpoint.")
    try:
        database.create_stuck_booking_notification(locator_code, note, txn_ref, False)
    except Exception as db_err:
        logger.error(f"Failed to log stuck_booking_notifications row: {db_err}")


def _live_airline_problem(locator_code: str) -> Optional[dict]:
    """Retrieve the PNR now and return segment_status.booking_problem (with
    the flight codes) if the airline hasn't confirmed every flight, keeping
    the saved booking's status in step either way. Raises HTTPException 503
    if Travelport can't be asked — better than charging for a flight that
    may be gone."""
    return _live_airline_check(locator_code)[0]


# Shown when the airline changed the schedule after booking, before payment.
# Not a fare re-check: Travelport's search keeps offering the old time, so a
# re-check would offer the same flight and loop (Jetstar JQ6 CMB-MEL,
# 2026-11-06: 19:55 in search, 19:50 at the airline). The booking pages show
# "choose another flight" for this message.
SCHEDULE_CHANGED_DETAIL = ("The airline has changed the timing of this flight, so it can't be booked online right now. "
                           "You have not been charged — please choose another flight.")


def _schedule_changed_detail(segs: list) -> str:
    """SCHEDULE_CHANGED_DETAIL naming the re-timed flight(s), e.g. "flight
    GF144 (BAH → CMB, 10/11/2026)" — on a return trip the customer otherwise
    changes the other direction and hits the same block again (Gulf Air
    GF144, 2026-11-09). Keeps the "can't be booked online right now" wording
    the booking pages look for."""
    names = []
    for sg in segs:
        flight = segment_status.norm_flight_no(sg.get("flight_number") or f"{sg.get('carrier') or ''}{sg.get('number') or ''}")
        if not flight:
            continue
        route = " → ".join(x for x in (sg.get("departure_airport") or sg.get("origin"),
                                       sg.get("arrival_airport") or sg.get("destination")) if x)
        when = fmt_date(str(sg.get("departure_time") or "")[:10])
        extra = ", ".join(x for x in (route, when) if x)
        name = f"{flight} ({extra})" if extra else flight
        if name not in names:
            names.append(name)
    if not names:
        return SCHEDULE_CHANGED_DETAIL
    what = f"flight {names[0]}" if len(names) == 1 else "flights " + " and ".join(names)
    return (f"The airline has changed the schedule of {what}, so it can't be booked online right now. "
            "You have not been charged — please choose another flight.")

# Flights re-timed by the airline after booking stay blocked for this long, so
# customers aren't booked and released again on every try (each try made and
# cancelled a PNR). In memory: one backend process (see start_backend_production.bat).
SCHEDULE_BLOCK_HOURS = 12
_schedule_blocked: dict = {}  # "JQ6|2026-11-07" -> blocked until (UTC)


def _schedule_block_key(flight: str, departure_time: str | None) -> str:
    return f"{segment_status.norm_flight_no(flight)}|{str(departure_time or '')[:10]}"


def _block_schedule_changed(locator_code: str, flights: list) -> None:
    until = datetime.utcnow() + timedelta(hours=SCHEDULE_BLOCK_HOURS)
    cached = database.get_booking_by_locator(locator_code) or {}
    raw = json.loads(cached.get("raw_ticket_json") or "{}") or dict(cached)
    segs = [sg for leg in (raw.get("legs") or [raw]) for sg in (leg.get("segments") or [leg])]
    for f in flights:
        dates = {str(sg.get("departure_time") or "")[:10] for sg in segs
                 if segment_status.norm_flight_no(sg.get("flight_number")) == segment_status.norm_flight_no(f)}
        for d in (dates or {""}):
            _schedule_blocked[_schedule_block_key(f, d)] = until
    logger.warning(f"{locator_code}: blocked {flights} for {SCHEDULE_BLOCK_HOURS} h after the airline's schedule change")


def _refuse_if_schedule_blocked(raw_offering: dict) -> None:
    now = datetime.utcnow()
    for k, until in list(_schedule_blocked.items()):
        if until <= now:
            _schedule_blocked.pop(k, None)
    blocked = []
    for sg in _segments_of_offering(raw_offering):
        flight = sg.get("flight_number") or f"{sg.get('carrier') or ''}{sg.get('number') or ''}"
        if _schedule_block_key(flight, sg.get("departure_time")) in _schedule_blocked \
                or _schedule_block_key(flight, "") in _schedule_blocked:
            blocked.append(sg)
    if blocked:
        raise HTTPException(status_code=409, detail=_schedule_changed_detail(blocked))


def _release_unpaid_booking(locator_code: str, reason: str) -> None:
    """Cancel an unpaid booking at Travelport before any payment and save it
    as Cancelled, so My Bookings doesn't keep showing it as Confirmed.
    (set_airline_status refused the old "... released before payment" wording,
    so Jetstar JJ27K3/KB/KD/KV of 2026-11-06 stayed "Confirmed".) Saved only
    when Travelport confirmed the cancel; otherwise the unpaid hold lapses."""
    try:
        services.cancel_reservation(locator_code)
    except Exception as e:
        logger.warning(f"Could not release unpaid PNR {locator_code} ({reason}): {e}")
        return
    try:
        database.record_cancellation(locator_code, "Cancelled", "Released before payment", None, None,
                                     {"reason": reason, "charged": False})
    except Exception as db_err:
        logger.error(f"Could not save the released status for {locator_code}: {db_err}")


def _release_if_schedule_changed(locator_code: str, codes: dict) -> None:
    """Never take payment for a booking the airline re-timed after booking
    (segment status TK): its stored fare is for the old schedule and
    Travelport refuses to ticket it ("NO FARES" / "FILED FARE IS NO LONGER
    VALID") — Jetstar JJ0VDH, JJ0V8S and JJ0VRB on 2026-11-02 were charged
    first and then could not be ticketed. The unpaid PNR is released, the
    flight is blocked for a while (Travelport's search keeps offering the old
    time) and the customer is asked to choose another flight; nothing is
    charged."""
    if "TK" not in set(codes.values()):
        return
    changed = ", ".join(f for f, c in codes.items() if c == "TK")
    logger.warning(f"{locator_code}: airline schedule change ({changed}) before payment — releasing the unpaid booking")
    try:
        _block_schedule_changed(locator_code, [f for f, c in codes.items() if c == "TK"])
    except Exception as e:
        logger.warning(f"Could not block the re-timed flight(s) of {locator_code}: {e}")
    insights.mark("Airline schedule check", "fail", f"Airline changed the schedule of {changed} after booking — "
                  "booking released before payment, customer asked to choose another flight",
                  raw_error=json.dumps(codes))
    _release_unpaid_booking(locator_code, f"Airline changed the schedule of {changed}")
    detail = SCHEDULE_CHANGED_DETAIL
    try:
        cached = database.get_booking_by_locator(locator_code) or {}
        raw = json.loads(cached.get("raw_ticket_json") or "{}") or dict(cached)
        segs = [sg for leg in (raw.get("legs") or [raw]) for sg in (leg.get("segments") or [leg])]
        tk = {segment_status.norm_flight_no(f) for f, c in codes.items() if c == "TK"}
        detail = _schedule_changed_detail(
            [sg for sg in segs if segment_status.norm_flight_no(sg.get("flight_number")) in tk]
            or [{"flight_number": f} for f in sorted(tk)])
    except Exception as e:
        logger.warning(f"Could not name the re-timed flight(s) of {locator_code}: {e}")
    raise HTTPException(status_code=409, detail=detail)


FARE_CHANGED_DETAIL = ("This fare has changed at the airline since you booked, so it must be checked again. "
                       "You have not been charged.")

# A GDS stored fare is guaranteed only until 23:59 agency time on the day it
# was booked. In the last 15 minutes before midnight (Sri Lanka) every new
# booking's guarantee runs out too soon to pay and ticket, so a fare re-check
# books the same flight again with the same 23:59 guarantee and loops — 8
# bookings (TG, MH, SQ, GF, AI, EK, EY to CDG/FRA) went round 39 times on
# 2026-11-06 23:47–23:59. Not a fare change: the customer is told to book
# after midnight instead. Must not match the frontend's isFareChangedError /
# isScheduleBlockedError wording.
FARE_GUARANTEE_DAY_END_DETAIL = ("Airline fares for today close at midnight Sri Lanka time, so a booking can't be "
                                 "paid for in the last few minutes before midnight. Your booking was released and "
                                 "you have not been charged — please book again after 12:00 midnight.")
FARE_GUARANTEE_ENDING_DETAIL = ("The airline's price for this booking runs out in the next few minutes, so it "
                                "can't be paid for now. Your booking was released and you have not been charged — "
                                "please book again in a little while.")
_GUARANTEE_PROBLEM = "The stored fare's guarantee ends at"  # services.pre_payment_fare_check wording


def _agency_day_ends_soon(minutes: int = 15) -> bool:
    """True in the last `minutes` before midnight agency time (Sri Lanka)."""
    local = datetime.utcnow() + timedelta(minutes=TravelportConfig.AGENCY_UTC_OFFSET_MINUTES)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - local <= timedelta(minutes=minutes)


def _release_if_fare_unticketable(locator_code: str, fare_source: Optional[str]) -> None:
    """Last check before payment: the held booking's stored fare must still
    be ticketable (services.pre_payment_fare_check — read-only retrieve +
    AirPrice). If not, the unpaid booking is released and the customer goes
    to the live fare re-check — never charged for a ticket Travelport would
    refuse. If Travelport can't be asked, no payment is taken either."""
    try:
        problem = services.pre_payment_fare_check(locator_code, fare_source)
    except services.FareCheckUnavailable as e:
        logger.error(f"{locator_code}: pre-payment fare check unavailable: {e}")
        raise HTTPException(status_code=503, detail="We couldn't confirm your fare with the airline just now. "
                                                    "No payment has been taken — please try again in a moment.")
    if not problem:
        insights.mark("Fare check before payment", "pass", "Stored fare still valid at Travelport")
        return
    logger.warning(f"{locator_code}: fare not ticketable before payment — {problem}; releasing the unpaid booking")
    if problem.startswith(_GUARANTEE_PROBLEM):
        # A re-check would make a new booking with the same guarantee end — no re-check, no loop.
        day_end = _agency_day_ends_soon()
        insights.mark("Fare check before payment", "fail",
                      "Fare guarantee runs out " + ("at midnight" if day_end else "within 15 minutes")
                      + " — booking released before payment, customer asked to book again later",
                      raw_error=problem)
        _release_unpaid_booking(locator_code, f"Fare guarantee ending: {problem}")
        raise HTTPException(status_code=409,
                            detail=FARE_GUARANTEE_DAY_END_DETAIL if day_end else FARE_GUARANTEE_ENDING_DETAIL)
    insights.mark("Fare check before payment", "fail",
                  "Fare no longer ticketable — booking released before payment, customer sent to the fare re-check",
                  raw_error=problem)
    _release_unpaid_booking(locator_code, f"Fare no longer ticketable: {problem}")
    raise HTTPException(status_code=409, detail=FARE_CHANGED_DETAIL)


def _live_airline_check(locator_code: str) -> tuple:
    """(problem or None, {flight: status code}) from a live PNR retrieve."""
    try:
        live = services.retrieve_reservation(locator_code)
    except Exception as e:
        logger.error(f"Airline status check for {locator_code} failed: {e}")
        raise HTTPException(status_code=503, detail="We couldn't confirm this booking with the airline just now. "
                                                    "No payment has been taken — please try again in a moment.")
    codes = live.get("segment_statuses") or {}
    problem = segment_status.booking_problem({f: (c, None) for f, c in codes.items()})
    try:
        database.set_airline_status(locator_code, problem["status"] if problem else "Confirmed")
    except Exception as db_err:
        logger.error(f"Could not save airline status for {locator_code}: {db_err}")
    return ({**problem, "codes": codes} if problem else None), codes


def _notify_cancel_failure(locator_code: str, error_message: str, recorded_status: Optional[str]) -> None:
    """Same stuck-booking queue + email as _notify_stuck_booking, for a staff
    cancellation (void / refund / cancel) that Travelport didn't complete.
    recorded_status is what was saved because Travelport had already
    confirmed it (e.g. 'Voided'), or None when nothing changed."""
    recipients = [r["email"] for r in database.get_notification_recipients(active_only=True)]
    email_sent = False
    if recipients:
        sent, err = services.send_cancel_failure_email(", ".join(recipients), locator_code, error_message, recorded_status)
        email_sent = sent
        if not sent:
            logger.warning(f"Cancel-failure notification email not sent: {err}")

    note = f"Cancellation failed at Travelport ({'saved as ' + recorded_status if recorded_status else 'nothing changed locally'}): {error_message}"
    try:
        database.create_stuck_booking_notification(locator_code, note, None, email_sent)
    except Exception as db_err:
        logger.error(f"Failed to log stuck_booking_notifications row: {db_err}")


def _require_switch(name: str) -> None:
    """Super-admin go-live switch (services/go_live_switches.py). Only ever
    called BEFORE a customer pays — a paid booking is never blocked."""
    if not go_live_switches.is_on(name):
        raise HTTPException(status_code=503, detail=go_live_switches.CUSTOMER_MESSAGES[name])


@app.post("/api/flights/search")
async def search_flights(request: FlightSearchRequest, http_request: Request,
                         customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    await asyncio.to_thread(_require_switch, "flight_search_enabled")
    kind = http_request.headers.get("x-search-kind")
    kind = kind if kind in ("nearby", "recheck") else "search"
    result = await _search_core(request, kind=kind, customer_id=customer_id,
                                user_agent=http_request.headers.get("user-agent"))
    # A full result list is 1-9 MB of JSON. Building (and gzipping) it on the
    # event loop made every other customer's request wait behind it, so it is
    # done on a worker thread; gzip cuts the download to about a tenth.
    gzip_ok = "gzip" in (http_request.headers.get("accept-encoding") or "").lower()
    body, headers = await asyncio.to_thread(_encode_search_reply, result, gzip_ok)
    return Response(content=body, media_type="application/json", headers=headers)


def _encode_search_reply(result: dict, gzip_ok: bool) -> tuple[bytes, dict]:
    body = json.dumps(result, separators=(",", ":"), default=str).encode("utf-8")
    if gzip_ok and len(body) > 2048:
        return gzip.compress(body, compresslevel=5), {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
    return body, {"Vary": "Accept-Encoding"}


async def _search_core(request: FlightSearchRequest, kind: str = "search", customer_id: Optional[int] = None,
                       user_agent: Optional[str] = None):
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
    started = time.monotonic()

    # Admin on/off switch (Admin Portal -> Provider Control) -- lets staff
    # take a GDS out of the unified search entirely (contract issue, outage,
    # or just wanting to sell one source only) without touching code.
    search_mode = (await asyncio.to_thread(database.get_provider_settings)).get("search_mode", "travelport_only")
    tp_enabled = search_mode in ("both", "travelport_only")
    ad_enabled = search_mode in ("both", "amadeus_only")

    tp_messages: list[str] = []  # Travelport's own warnings/errors, kept with the search record

    def _search_travelport() -> list[dict]:
        # Travelport's PricingModifiersAir.currencyCode override (see
        # search_service.py) reliably reprices GDS content, but confirmed live
        # (2026-10-16, CMB-SIN) that NDC content occasionally ignores it on a
        # given request -- Singapore Airlines NDC brands came back in SGD while
        # an identical search moments earlier/later came back all in LKR. Since
        # this system shows no mock/fabricated data, the fix is to re-query
        # live Travelport (not invent an FX rate) until every offer matches the
        # agency's configured currency, same backoff-retry pattern already used
        # for flaky Travelport responses elsewhere (see workbench_service.py
        # ancillary-shop 504 retries).
        last_offers: list[dict] = []
        for attempt in range(3):
            tp_messages.clear()
            raw = services.search_flights(
                origin=request.origin.upper() if request.origin else None,
                destination=request.destination.upper() if request.destination else None,
                departure_date=request.departure_date,
                adult_count=request.adult_count,
                child_count=request.child_count,
                infant_count=request.infant_count,
                student_count=request.student_count,
                cabin_preference=request.cabin_preference,
                legs=legs,
                content_source=request.content_source,
                max_upsells=0 if kind == "nearby" else None,
            )
            tp_messages.extend(_travelport_result_messages(raw))
            last_offers = [o for o in services.parse_flight_offers(
                raw, legs=legs,
                origin=request.origin.upper() if request.origin else None,
                destination=request.destination.upper() if request.destination else None,
            ) if not _offer_carriers(o) & PAUSED_CARRIERS]
            mismatched = [o for o in last_offers if o.get("currency") and o["currency"] != TravelportConfig.DEFAULT_CURRENCY]
            if not mismatched:
                return last_offers
            logger.warning(
                f"Search attempt {attempt + 1}/3: {len(mismatched)} offer(s) still priced outside "
                f"{TravelportConfig.DEFAULT_CURRENCY} (e.g. {mismatched[0].get('flight_number')} "
                f"in {mismatched[0].get('currency')}) -- retrying" if attempt < 2 else
                f"{len(mismatched)} offer(s) still priced outside {TravelportConfig.DEFAULT_CURRENCY} "
                f"after 3 attempts -- returning as-is, each correctly labeled with its own currency"
            )
        return last_offers

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
            env_name = "production" if TravelportConfig.is_production() else "pre-production"
            detail = (
                "Travelport API returned 400 INVALID INPUT FORMAT (error 1586). "
                f"This usually means the {env_name} account isn't fully set up — "
                "your TripServices v11 subscription may not be fully activated. "
                "Please verify with Travelport that: "
                "(1) Your account has TripServices v11 access, "
                f"(2) PCC {TravelportConfig.PCC} is enabled for {env_name}, "
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

    # What each provider was asked and what it gave back — kept with the
    # search record so it can be shown later exactly as it happened.
    def _provider_result(task, result, tag):
        if task is None:
            return "not queried"
        if isinstance(result, Exception):
            return f"error: {str(result)[:300]}"
        return f"{len([o for o in offers if o.get('source') == tag])} offers"
    providers = {"TP": _provider_result(tp_task, tp_result, "TP"), "AD": _provider_result(ad_task, ad_result, "AD")}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    # A supplier that answered without an error but with no fares is also
    # "rates not coming" for a customer search: staff get the same Supplier
    # Alert (and email, at most every 15 minutes) as for an error.
    if kind == "search":
        for tag, name in (("TP", "Travelport"), ("AD", "Amadeus")):
            if providers[tag] == "0 offers":
                _notify_provider_failure(tag, f"{name} returned no fares for this search (0 offers, no error)", route_info)
            elif providers[tag].endswith(" offers"):
                # One channel missing (e.g. no NDC fares) on a route where that
                # channel had fares in the last 7 days: staff alert, so a
                # supplier problem is caught before a supplier says "our fare
                # was cheaper". Routes that never have NDC don't alert.
                mine = [o for o in offers if o.get("source") == tag]
                o_code = (request.origin or (legs[0]["origin"] if legs else "") or "").upper()
                d_code = (request.destination or (legs[0]["destination"] if legs else "") or "").upper()
                for ch in insights.CHANNELS:
                    if not any((o.get("fare_source") or "GDS").upper() == ch for o in mine) \
                            and insights.channel_seen_recently(o_code, d_code, tag, ch):
                        _notify_provider_failure(tag, f"{name} returned no {ch} fares for this search "
                                                      f"({ch} fares came back for this route in the last 7 days)",
                                                 route_info)
    messages = {"TP": tp_messages[:20]} if tp_messages else None

    if not offers and tp_task is not None and isinstance(tp_result, Exception):
        # Both providers failed (or AD wasn't queried/enabled) with nothing to
        # show — surface Travelport's error to the caller as before, rather
        # than a silent empty list.
        await asyncio.to_thread(insights.log_search, request, [], providers, elapsed_ms, str(tp_result)[:400],
                                customer_id, user_agent, kind, messages)
        raise HTTPException(status_code=502, detail=f"Travelport search error: {str(tp_result)[:400]}")

    offers.sort(key=lambda o: (o.get("price") if o.get("price") is not None else float("inf")))
    await asyncio.to_thread(insights.log_search, request, offers, providers, elapsed_ms, None,
                            customer_id, user_agent, kind, messages)
    if kind == "nearby":
        # The date strip / price calendar only needs the cheapest fare. Sending
        # all ~150 offers (several MB each) made the server spend seconds
        # encoding every reply, which queued the parallel date searches.
        cheapest = next((o for o in offers if (o.get("price") or 0) > 0), None)
        return {"flights": [{"price": cheapest["price"], "currency": cheapest.get("currency")}] if cheapest else [],
                "count": len(offers)}
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


_PTC_LABEL = {"ADT": "an Adult", "STU": "a Student", "CNN": "a Child", "INF": "an Infant"}


def _journey_booking_info(travelers: list, raw_offering: dict) -> None:
    """Lead passenger + flight for the admin's booking-journey record."""
    try:
        lead = travelers[0] if travelers else {}
        lead = lead.model_dump() if hasattr(lead, "model_dump") else lead
        segs = _segments_of_offering(raw_offering)
        insights.journey_info(
            customer_name=f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip().upper(),
            customer_email=lead.get("email"), customer_phone=lead.get("phone"),
            flight=" ".join(s.get("flight_number") or "" for s in segs)[:80],
            airline=(segs[0].get("carrier_name") or segs[0].get("carrier")) if segs else None,
        )
    except Exception as e:
        logger.warning(f"journey info not recorded: {e}")


def _check_travelers_for_trip(travelers: list, raw_offering: dict) -> None:
    """Refuses, BEFORE any payment, passenger details the airline will reject
    at ticketing. A 7-month-old's date of birth booked as an Adult (age 0)
    reached Emirates on 2026-11-02 and the Instant Pay commit failed with
    HTTP 500 after the customer had paid. Ages are worked out on the travel
    dates: Adult/Student 12+ and Child 2–11 on the first flight, Infant under
    2 on the last flight; the passport must be valid for the whole trip."""
    dates = sorted(str(s.get("departure_time") or "")[:10] for s in _segments_of_offering(raw_offering)
                   if s.get("departure_time"))
    if not dates:
        return
    first = datetime.strptime(dates[0], "%Y-%m-%d").date()
    last = datetime.strptime(dates[-1], "%Y-%m-%d").date()

    def age_on(dob, day):
        return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))

    fmt = lambda d: d.strftime("%d/%m/%Y")
    problems = []
    for i, t in enumerate(travelers, 1):
        t = t.model_dump() if hasattr(t, "model_dump") else t
        who = f"Passenger {i} ({(t.get('first_name') or '').upper()} {(t.get('last_name') or '').upper()})".replace(" ()", "")
        ptc = (t.get("passenger_type") or "ADT").upper()
        try:
            dob = datetime.strptime(t.get("date_of_birth") or "", "%Y-%m-%d").date()
        except ValueError:
            continue  # format errors are reported by TravelerInfo itself
        age_first, age_last = age_on(dob, first), age_on(dob, last)
        if ptc in ("ADT", "STU") and age_first < 12:
            problems.append(f"{who}: born {fmt(dob)}, so {age_first} years old on {fmt(first)} — "
                            f"{_PTC_LABEL[ptc]} must be 12 or older. Please check the date of birth.")
        elif ptc == "CNN" and not (2 <= age_first < 12):
            problems.append(f"{who}: born {fmt(dob)}, so {age_first} years old on {fmt(first)} — "
                            f"a Child must be 2 to 11 years old. Please check the date of birth.")
        elif ptc == "INF" and (dob > first or age_last >= 2):
            problems.append(f"{who}: born {fmt(dob)} — an Infant must be born before {fmt(first)} and "
                            f"under 2 years old on {fmt(last)}. Please check the date of birth.")
        try:
            expiry = datetime.strptime(t.get("passport_expiry") or "", "%Y-%m-%d").date()
            if expiry < last:
                problems.append(f"{who}: passport expires {fmt(expiry)}, before the last flight on {fmt(last)}. "
                                f"A passport valid for the whole trip is needed.")
        except ValueError:
            pass
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))


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

    passenger_type_order = {"ADT": 0, "STU": 1, "INF": 2, "CNN": 3}
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
    ticket["nationality"] = lead.get("nationality", "")
    ticket["passport_issue_country"] = lead.get("passport_issue_country", "")
    ticket["gender"] = lead.get("gender", "")
    ticket["phone"] = lead.get("phone", "")
    ticket["date_of_birth"] = lead.get("date_of_birth", "")


def _attach_ssr_status(ticket: dict, ssr_requests: Optional[list]) -> None:
    """Meal / wheelchair requests made at booking (kept on our record —
    NDC PNRs don't carry them) and the airline's status for each from the
    live PNR (ticket["special_services"])."""
    if ssr_requests is not None:
        ticket["ssr_requests"] = ssr_requests
    ticket["ssr_status"] = merge_ssr_status(ticket.get("ssr_requests"), ticket.get("special_services"))


def _quoted_currency(raw_offering: dict) -> Optional[str]:
    """Currency of the price the customer was shown, from the selected offer.
    raw_offering is one leg, {"outbound","inbound"} (round trip) or
    {"legs":[...]} (multi-city) — every leg of one offer shares a currency."""
    if "outbound" in raw_offering and "inbound" in raw_offering:
        src = raw_offering["outbound"]
    elif "legs" in raw_offering:
        src = raw_offering["legs"][0] if raw_offering["legs"] else {}
    else:
        src = raw_offering
    return src.get("currency")


def _price_changed_response(e: "services.workbench_service.PriceChangedError") -> HTTPException:
    """The fare to charge is always Travelport's own AirPrice total plus our
    markup (see workbench_service.charge_for_priced_fare) — the browser's
    custom_price is only the quote the customer saw, and a lower one is
    refused before any booking is made. Covers one-way, round-trip and
    multi-city alike; the old check only covered one-way, and compared two
    numbers that both came from the browser.

    The airline's own price (AirPrice + markup) travels with the refusal, so
    the fare re-check can offer the same fare at that price instead of the
    search's stale lower one, which the airline refuses every time (13
    routes on 2026-11-07, e.g. SVO MU 603,070 in search, 2,883,839 at the
    airline). Booking it again with that quote is checked the same way."""
    logger.warning(f"Booking refused before any workbench was created: {e}")
    if e.expected and e.currency:
        return HTTPException(status_code=409, detail={
            "message": str(e), "new_price": round(float(e.expected), 2), "currency": e.currency})
    return HTTPException(status_code=409, detail=str(e))


# ── Duplicate-submission guard for PNR creation ────────────────────────────────
# /api/bookings/create and /api/bookings/confirm have no locator_code to key
# an idempotency check on -- the PNR doesn't exist until the call succeeds.
# The frontend already guards against a same-tab double-click (an in-memory
# ref flag), but that does nothing for two browser tabs, or a network-level
# retry, submitting the identical offer+travelers within moments of each
# other -- each would otherwise create its own separate Travelport PNR/
# workbench. This keeps a short-lived fingerprint of each in-flight/recent
# submission (single-process in-memory state; a 20-second window doesn't
# need to survive a restart) and refuses an exact repeat within the
# window rather than letting Travelport create a second PNR for one action.
_recent_booking_fingerprints: dict[str, float] = {}
_BOOKING_DEDUP_WINDOW_SECONDS = 20


def _booking_fingerprint(raw_offering: dict, travelers: list[dict]) -> str:
    offer_id = (
        raw_offering.get("id")
        or raw_offering.get("CatalogProductOfferingsIdentifier")
        or str(raw_offering.get("Identifier", ""))
    )
    traveler_key = "|".join(sorted(
        f"{t.get('first_name', '')}:{t.get('last_name', '')}:{t.get('passport_number', '')}"
        for t in travelers
    ))
    return hashlib.sha256(f"{offer_id}:{traveler_key}".encode("utf-8")).hexdigest()


def _claim_booking_submission(raw_offering: dict, travelers: list[dict]) -> None:
    """Raises 409 if this exact offer+traveler combination was already
    submitted within the dedup window; otherwise records it and lets the
    request through."""
    fp = _booking_fingerprint(raw_offering, travelers)
    now = time.time()
    for key, ts in list(_recent_booking_fingerprints.items()):
        if now - ts > _BOOKING_DEDUP_WINDOW_SECONDS:
            del _recent_booking_fingerprints[key]
    if fp in _recent_booking_fingerprints:
        logger.warning("Duplicate booking submission blocked (same offer+travelers submitted again within the dedup window).")
        raise HTTPException(
            status_code=409,
            detail="This exact booking was just submitted. Please wait a moment and check your bookings before trying again.",
        )
    _recent_booking_fingerprints[fp] = now


def _release_booking_submission(raw_offering: dict, travelers: list[dict]) -> None:
    """Frees the guard after a submission that booked nothing (Travelport
    refused it), so the customer can go straight on — e.g. "Continue with this
    fare" after the fare re-check. The re-checked offer often has the same
    offer id as the refused one ("o1"), which the guard used to block for 20
    s as "This exact booking was just submitted" (Batik Air OD298 CMB-MEL,
    2026-11-06). A submission that made a PNR keeps the guard."""
    _recent_booking_fingerprints.pop(_booking_fingerprint(raw_offering, travelers), None)


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
    _require_switch("pnr_create_enabled")
    travelers = [t.model_dump() for t in request.travelers]
    raw_offering = request.raw_offering
    _claim_booking_submission(raw_offering, travelers)

    try:
        # STEPS 4-7: Full booking flow with automatic stale-workbench retry and cleanup.
        # The quote is checked against AirPrice before anything is booked.
        commit_result = services.run_booking_flow(
            raw_offering, travelers,
            quoted_price=request.custom_price, quoted_currency=_quoted_currency(raw_offering),
        )
        locator_code = commit_result["locator_code"]

        # STEP 8 only — ticket issuance (STEP 9) now happens separately via
        # POST /api/bookings/{locator_code}/issue-ticket, gated on a
        # successful PayCorp payment confirmation. Do NOT issue here.
        ticket = _read_new_booking(locator_code, commit_result)

        # Cache the Offer local id/UUID from the commit response — issue_ticket()
        # falls back to this when a later buildfromlocator doesn't return Offer[]
        # (a real, confirmed gap for NDC content — see commit_workbench's docstring).
        if commit_result.get("offer_local_id"):
            ticket["offer_local_id"] = commit_result["offer_local_id"]
        if commit_result.get("offer_uuid"):
            ticket["offer_uuid"] = commit_result["offer_uuid"]
        if commit_result.get("offer_authority"):
            ticket["offer_authority"] = commit_result["offer_authority"]

        # The fare to charge: Travelport's AirPrice total + markup, never the browser's number.
        ticket["total_fare"] = commit_result["charge_amount"]
        ticket["currency"] = commit_result["charge_currency"]
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
        _attach_ssr_status(ticket, commit_result.get("ssr_requests"))

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

    except services.workbench_service.PriceChangedError as e:
        raise _price_changed_response(e)
    except ValueError as e:
        friendly = friendly_travelport_error(str(e))
        raise HTTPException(status_code=409 if friendly != str(e) else 422, detail=friendly)
    except httpx.HTTPStatusError as e:
        error_msg = str(e)
        status_code = e.response.status_code
        response_text = e.response.text
        if status_code == 500:
            env_name = "production" if TravelportConfig.is_production() else "pre-production"
            detail = (
                "Travelport API returned 500 INTERNAL SERVER ERROR at the booking commit stage. "
                "This usually means the account has limited booking permissions "
                "(specifically 'preAuthorized: false' in the JWT token). "
                "Please verify with Travelport that: "
                f"(1) PCC {TravelportConfig.PCC} has GDS/NDC booking and ticketing enabled in {env_name}. "
                "(2) Your API credentials have the required role permissions to hold/commit reservations. "
                "To troubleshoot, you can provide Travelport support with the request TraceId or the E2ETrackingID "
                f"from your API headers. Raw error response: {response_text[:300]}"
            )
        else:
            detail = f"Travelport booking API error: {error_msg[:400]}"
        logger.error(f"Booking failed with HTTPStatusError: {error_msg}. Details: {response_text[:300]}")
        insights.mark("Travelport booking", "fail", "Airline/Travelport did not confirm the booking", raw_error=detail)
        raise HTTPException(status_code=502, detail=_not_confirmed_detail(f"HTTP {status_code}: {response_text}"))
    except Exception as e:
        logger.error(f"Booking failed: {e}")
        insights.mark("Travelport booking", "fail", "Airline/Travelport did not confirm the booking", raw_error=str(e))
        raise HTTPException(status_code=502, detail=_not_confirmed_detail(e))


# ── STEPS 4 & 10: Initiate Booking (Workbench & Seatmap) ──────────────────────

@app.post("/api/bookings/initiate")
def initiate_booking(request: BookingInitiateRequest):
    """
    Initiates booking: Creates GDS workbench, adds selected offer, and returns seat map.
    """
    _require_switch("pnr_create_enabled")
    logger.info("Initiating booking and fetching live seat map...")
    if {str(s.get("carrier") or "").upper() for s in _segments_of_offering(request.raw_offering)} & PAUSED_CARRIERS:
        raise HTTPException(status_code=409, detail=PAUSED_CARRIER_DETAIL)
    _refuse_if_schedule_blocked(request.raw_offering)
    _journey_booking_info(request.travelers, request.raw_offering)
    _check_travelers_for_trip(request.travelers, request.raw_offering)
    raw_offering = request.raw_offering
    travelers = [t.model_dump() for t in request.travelers]
    if not request.include_addons:
        # Booking flow (flight only): the checks above are all this step
        # needs. Travelport is asked once, at Review & Pay (AirPrice + book),
        # which reports a changed fare or closed class there — a second
        # add-offer here only added a slow call that could time out (flydubai
        # 504 after 31 s, 2026-11-06).
        return {"workbench_id": None, "offer_id": None,
                "seat_map": None, "seat_maps": [], "seat_map_available": False,
                "ancillaries": [], "ancillaries_available": False}
    workbench_id = None
    if not _offering_is_gds(raw_offering):
        # Not bookable with Travelport in Phase 1 — offering seats/extras the
        # customer would pay for but the airline never receives is exactly
        # the display-only gap this replaced.
        return {
            "workbench_id": None, "offer_id": None,
            "seat_map": None, "seat_maps": [], "seat_map_available": False,
            "ancillaries": [], "ancillaries_available": False,
            "addons_note": NON_GDS_ADDONS_NOTE,
        }
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
            seat_rules.annotate_seat_conditions(seat_maps)  # airline conditions (e.g. closes N h before departure)
            seat_map_available = any(m.get("seat_selection_available") for m in seat_maps)
        except Exception as e:
            logger.warning(f"Live seat map retrieval failed: {e}")

        # STEP 10b: Fetch bookable ancillaries (extra baggage, meals, etc.)
        # on the same temporary workbench, same best-effort pattern as the
        # seat map above — not every carrier/fare publishes ancillaries, so
        # a failure here must not block booking initiation. Confirmed live:
        # unlike the seat map call, Ancillary Shop rejects with "TRAVELER
        # DETAILS MUST BE INCLUDED" unless travelers are on the workbench.
        ancillaries = []
        ancillaries_available = False
        try:
            services.add_travelers_to_workbench(workbench_id, travelers)
            ancillaries = services.get_ancillary_offers(workbench_id, offer_id)
            ancillaries_available = bool(ancillaries)
        except Exception as e:
            logger.warning(f"Live ancillary offers retrieval failed: {e}")

        return {
            "workbench_id": workbench_id,
            "offer_id": offer_id,
            # "seat_map" (singular, first leg only) is kept for existing
            # one-way frontend code — "seat_maps" (plural, all legs) is the
            # new field round-trip-aware code should use instead.
            "seat_map": seat_maps[0] if seat_maps else None,
            "seat_maps": seat_maps or [],
            "seat_map_available": seat_map_available,
            "ancillaries": ancillaries,
            "ancillaries_available": ancillaries_available
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



# ── Fare Rules (change / cancel conditions) ───────────────────────────────────

@app.post("/api/fare-rules")
def fare_rules_for_offer(request: FareRulesRequest):
    """Fare rules of a searched fare, live from Travelport (short text +
    structured penalties per leg) — shown on fare selection and review."""
    try:
        return {"fares": fare_rule_service.get_fare_rules_for_offering(request.raw_offering)}
    except Exception as e:
        logger.error(f"Fare rules failed: {e}")
        raise HTTPException(status_code=502, detail="Fare rules couldn't be loaded from the airline right now.")


@app.get("/api/bookings/{locator_code}/fare-rules")
def fare_rules_for_booking(locator_code: str):
    """Fare rules of a booking made through this site (Travelport: the
    booking must have been made via TripServices with a valid fare)."""
    cached = database.get_booking_by_locator(locator_code)
    if not cached:
        raise HTTPException(status_code=404, detail="Booking not found.")
    try:
        is_gds = (cached.get("fare_source") or "GDS") == "GDS"
        return {"fares": fare_rule_service.get_fare_rules_for_reservation(locator_code, is_gds)}
    except Exception as e:
        logger.error(f"Fare rules for {locator_code} failed: {e}")
        raise HTTPException(status_code=502, detail="Fare rules couldn't be loaded from the airline right now.")


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
    _require_switch("pnr_create_enabled")
    logger.info("Confirming booking — creating fresh GDS workbench at commit time...")
    travelers = [t.model_dump() for t in request.travelers]
    raw_offering = request.raw_offering
    selected_seats = request.selected_seats
    # Emirates sells paid seats only after ticketing ("PAID SEAT IS NOT
    # AVAILABLE" on a held PNR, JJ0VZ9) — never try one at booking; the
    # website offers them in Manage Booking after the ticket is issued.
    carriers = {str(s.get("carrier") or "").upper() for s in _segments_of_offering(raw_offering)}
    if carriers & PAUSED_CARRIERS:
        raise HTTPException(status_code=409, detail=PAUSED_CARRIER_DETAIL)
    _refuse_if_schedule_blocked(raw_offering)
    if carriers & PAID_SEATS_AFTER_TICKET_CARRIERS and any((s.price or 0) > 0 for s in selected_seats or []):
        logger.info(f"Dropping paid seat(s) for {sorted(carriers)} — sold only after ticketing")
        selected_seats = [s for s in selected_seats if not (s.price or 0) > 0]
    _journey_booking_info(travelers, raw_offering)
    _check_travelers_for_trip(travelers, raw_offering)
    _claim_booking_submission(raw_offering, travelers)
    booked_locator = None  # set once Travelport has made the PNR

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
        # The customer's quote (custom_price) is checked against Travelport's
        # AirPrice total + markup inside the flow, before anything is booked.
        commit_result = services.run_booking_flow(
            raw_offering, travelers, reference_payload_only=request.reference_payload_only,
            quoted_price=request.custom_price, quoted_currency=_quoted_currency(raw_offering),
        )
        locator_code = commit_result["locator_code"]
        booked_locator = locator_code
        logger.info(f"PNR generated: {locator_code}")

        # STEP 8 only — ticket issuance (STEP 9) now happens separately via
        # POST /api/bookings/{locator_code}/issue-ticket, gated on a
        # successful PayCorp payment confirmation. Do NOT issue here.
        ticket = _read_new_booking(locator_code, commit_result)

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
            time.sleep(1.5)
            try:
                updated_ticket = services.retrieve_reservation(locator_code)
            except Exception as read_err:
                logger.warning(f"{locator_code}: airline reference re-read failed: {read_err}")
                updated_ticket = {}
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

        # Book the chosen seats/extras on the new PNR (Travelport only allows
        # GDS paid seats and extras on an existing booking). Only what the
        # airline CONFIRMS is recorded and charged, at Travelport's live price
        # — never the price the browser sent. Paid for (EMD) at ticketing.
        selected_ancillaries = request.selected_ancillaries
        addon_results = {"seats": [], "extras": []}
        if selected_seats or selected_ancillaries:
            if ticket["fare_source"] == "GDS":
                try:
                    addon_results = services.add_seats_and_extras_to_pnr(
                        locator_code, travelers,
                        [s.model_dump() for s in selected_seats],
                        [a.model_dump() for a in selected_ancillaries],
                    )
                except Exception as e:
                    logger.error(f"Seats/extras for {locator_code} could not be added: {e}")
                    addon_results = {
                        "seats": [{**s.model_dump(), "status": "failed", "reason": str(e)} for s in selected_seats],
                        "extras": [{**a.model_dump(), "status": "failed", "reason": str(e)} for a in selected_ancillaries],
                    }
            else:
                addon_results = {
                    "seats": [{**s.model_dump(), "status": "failed", "reason": NON_GDS_ADDONS_NOTE} for s in selected_seats],
                    "extras": [{**a.model_dump(), "status": "failed", "reason": NON_GDS_ADDONS_NOTE} for a in selected_ancillaries],
                }
        # The airline's own status per flight, from the retrieve and again
        # from the seats/extras step a few seconds later (UL cancelled
        # duplicate test bookings — HX — in between). Not confirmed means the
        # booking is not "Confirmed" and the frontend must not take payment;
        # /api/payments/init checks again live in case it changes later.
        seg_codes = {**(ticket.get("segment_statuses") or {}), **(addon_results.get("segment_statuses") or {})}
        airline_problem = segment_status.booking_problem({f: (c, None) for f, c in seg_codes.items()})
        if seg_codes:
            ticket["segment_statuses"] = seg_codes
        if airline_problem:
            ticket["status"] = airline_problem["status"]
            ticket["airline_status_note"] = airline_problem["message"]
            _notify_airline_problem(locator_code, seg_codes)

        # Only items the airline CONFIRMED are charged. "pending" = requested
        # on the PNR but not yet answered by the airline: not charged, no seat
        # number recorded, and not paid at ticketing (see issue_ticket).
        confirmed_seats = [s for s in addon_results["seats"] if s["status"] == "confirmed"]
        confirmed_extras = [a for a in addon_results["extras"] if a["status"] == "confirmed"]
        ticket["addon_results"] = addon_results
        seat_request_log.record(locator_code, travelers, addon_results.get("seats"), "booking")

        seat_numbers = _format_seat_numbers([SelectedSeat(**{k: v for k, v in s.items() if k in SelectedSeat.model_fields}) for s in confirmed_seats])
        seat_charges = sum(s["price"] for s in confirmed_seats)
        if seat_numbers:
            ticket["seat_number"] = ", ".join(seat_numbers)

        ancillary_charges = sum(a["price"] for a in confirmed_extras)
        if confirmed_extras:
            ticket["ancillaries"] = [
                {k: a.get(k) for k in ("passenger_idx", "name", "price", "currency", "service_type", "offer_id", "refundable", "status")}
                for a in confirmed_extras
            ]

        seat_charges += ancillary_charges

        # The fare charged is Travelport's own AirPrice total + our markup
        # (run_booking_flow, already checked against the customer's quote),
        # plus only the seats/extras the airline confirmed. Never the
        # browser's number. Currency travels with it — for NDC, the live
        # retrieve has no Price, so its "USD" default must not be kept.
        ticket["total_fare"] = round(commit_result["charge_amount"] + seat_charges, 2)
        ticket["currency"] = commit_result["charge_currency"]

        ticket["seat_charge"] = seat_charges
        ticket["ancillary_charge"] = ancillary_charges

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
        _attach_ssr_status(ticket, commit_result.get("ssr_requests"))

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
    except services.workbench_service.PriceChangedError as e:
        raise _price_changed_response(e)
    except ValueError as e:
        friendly = friendly_travelport_error(str(e))
        if friendly != str(e):
            raise HTTPException(status_code=409, detail=friendly)
        # Any other Travelport refusal (e.g. "NAME IS REQUIRED") is not the
        # customer's to read — clear message, real error on the journey.
        logger.error(f"Confirmation failed: {e}")
        insights.mark("Travelport booking", "fail", "Airline/Travelport did not confirm the booking", raw_error=str(e))
        raise HTTPException(status_code=502, detail=_not_confirmed_detail(e))
    except httpx.HTTPStatusError as e:
        error_msg = str(e)
        status_code = e.response.status_code
        response_text = e.response.text
        logger.error(f"Confirmation failed with HTTPStatusError: {error_msg}. Details: {response_text[:300]}")
        insights.mark("Travelport booking", "fail", "Airline/Travelport did not confirm the booking",
                      raw_error=f"HTTP {status_code}: {response_text[:600]}")
        raise HTTPException(status_code=502, detail=_not_confirmed_detail(f"HTTP {status_code}: {response_text}"))
    except Exception as e:
        logger.error(f"Confirmation failed: {e}")
        insights.mark("Travelport booking", "fail", "Airline/Travelport did not confirm the booking", raw_error=str(e))
        raise HTTPException(status_code=502, detail=_not_confirmed_detail(e))
    finally:
        services.end_flow_trace_id(trace_token)
        if booked_locator is None:
            _release_booking_submission(raw_offering, travelers)


# ── STEP 9: Issue Ticket — gated on PayCorp payment confirmation ──────────────

class IssueTicketRequest(BaseModel):
    reqid: Optional[str] = Field(
        default=None,
        description="PayCorp reqid from a completed hosted-page payment. "
                     "Required: a ticket is never issued without a verified "
                     "card payment — there is no admin override."
    )


PAYMENT_REQUIRED_DETAIL = "A confirmed card payment is required before a ticket can be issued. Ticket not issued."


def _verify_and_claim_payment(
    reqid: str, expected_client_ref: str, locator_code: Optional[str] = None,
    expected_amount: Optional[float] = None, expected_currency: Optional[str] = None,
) -> dict:
    """Confirms a PayCorp reqid actually paid for THIS booking, not just that
    some payment somewhere succeeded. Closes two real gaps: (1) a successful
    reqid from one booking being replayed against a different, more expensive
    one (checked here via clientRef, which PayCorp echoes back unchanged from
    whatever /api/payments/init sent — our own booking/pending id), and (2) the
    same reqid being redeemed for issuance more than once (checked via a
    single-use DB claim, see database.claim_payment_reqid). Raises a clear
    HTTPException and claims nothing on any failure; only a reqid that passes
    every check gets marked consumed."""
    try:
        payment_result = services.complete_payment(reqid)
    except services.PayCorpError as e:
        logger.error(f"PayCorp payment check failed (reqid={reqid}): {e}")
        raise HTTPException(status_code=502, detail=str(e))

    if not payment_result.get("success"):
        insights.mark("Payment verified", "fail",
                      f"PayCorp code {payment_result.get('response_code')}: {payment_result.get('response_text')}")
        logger.warning(
            f"Ticket issuance refused — payment not successful "
            f"(code={payment_result.get('response_code')}, reqid={reqid})"
        )
        raise HTTPException(
            status_code=402,
            detail=f"Payment not successful (code={payment_result.get('response_code')}): "
                   f"{payment_result.get('response_text')}. Ticket not issued."
        )

    if (payment_result.get("client_ref") or "") != expected_client_ref:
        logger.error(
            f"PAYMENT CLIENT_REF MISMATCH: reqid={reqid} paid for client_ref="
            f"{payment_result.get('client_ref')!r}, but ticketing was requested for "
            f"{expected_client_ref!r} — refusing to issue."
        )
        raise HTTPException(
            status_code=409,
            detail="This payment does not match the booking being ticketed. Ticket not issued.",
        )

    if expected_amount is not None:
        paid_cents = payment_result.get("payment_amount")
        expected_cents = round(expected_amount * 100)
        paid_currency = (payment_result.get("currency") or "").upper()
        if paid_cents != expected_cents or (expected_currency and paid_currency != expected_currency.upper()):
            logger.error(
                f"PAYMENT AMOUNT MISMATCH: reqid={reqid} paid {paid_cents} {paid_currency} cents, "
                f"expected {expected_cents} {expected_currency} cents for {expected_client_ref!r} — refusing to issue."
            )
            raise HTTPException(
                status_code=409,
                detail="The confirmed payment amount does not match this booking's fare. Ticket not issued.",
            )

    if not database.claim_payment_reqid(reqid, expected_client_ref, locator_code):
        logger.error(f"PAYMENT REQID REPLAY: reqid={reqid} was already used for a previous ticket issuance — refusing to issue again.")
        raise HTTPException(
            status_code=409,
            detail="This payment has already been used to issue a ticket. Ticket not issued.",
        )

    logger.info(f"Payment verified for client_ref={expected_client_ref} (txnReference={payment_result.get('txn_reference')}) — issuing ticket...")
    insights.mark("Payment verified", "pass",
                  f"Paid {expected_currency or ''} {expected_amount or ''} — txn {payment_result.get('txn_reference')}".strip())
    return payment_result


@app.post("/api/bookings/{locator_code}/issue-ticket")
def issue_ticket_after_payment(locator_code: str, request: IssueTicketRequest):
    """
    STEP 9 — Issue the Travelport ticket for an already-confirmed PNR.

    The PayCorp payment (reqid) MUST have succeeded, for this booking and
    this amount — Travelport is never contacted for ticket issuance otherwise.
    There is no unpaid path: a missing reqid is refused with 402.
    """
    locator_code = locator_code.upper().strip()

    # Idempotency: a double-click, a retried request, or a race between two
    # browser tabs could otherwise call this twice for the same PNR, which
    # without this check would re-run the full Travelport buildfromlocator ->
    # formofpayment -> payment -> ticketing-commit sequence a second time
    # against an already-issued ticket. If it's already ticketed, just hand
    # back the existing ticket instead of touching Travelport again.
    existing = database.get_booking_by_locator(locator_code)
    if existing and existing.get("ticket_number"):
        logger.info(f"Issue-ticket for {locator_code} called again — already ticketed (#{existing['ticket_number']}), returning the existing ticket instead of re-issuing.")
        return {"success": True, "ticket": existing, "cached_id": existing.get("id"), "already_issued": True}

    if not request.reqid:
        logger.warning(f"Ticket issuance refused for {locator_code} — no PayCorp payment reference.")
        raise HTTPException(status_code=402, detail=PAYMENT_REQUIRED_DETAIL)
    if not existing or not existing.get("total_fare"):
        # Without the booking's own fare there is nothing to check the paid
        # amount against, so a cheaper payment could ticket a dearer booking.
        logger.warning(f"Ticket issuance refused for {locator_code} — no booking record with a fare to verify the payment against.")
        raise HTTPException(status_code=409, detail="This booking could not be matched to a payment amount. Ticket not issued — contact support.")

    payment_result = _verify_and_claim_payment(
        request.reqid, expected_client_ref=locator_code, locator_code=locator_code,
        expected_amount=existing["total_fare"], expected_currency=existing.get("currency"),
    )
    # The airline may have dropped the flight after payment started; a ticket
    # can't be issued then, so alert staff to refund instead of trying.
    problem = _live_airline_problem(locator_code)
    if problem:
        insights.mark("Ticket issued", "fail", "Airline has not confirmed / has cancelled the flight",
                      raw_error=json.dumps(problem.get("codes")))
        _notify_airline_problem(locator_code, problem["codes"], payment_result.get("txn_reference") or "paid")
        _auto_refund_for_booking(locator_code, "airline has not confirmed / cancelled the flight",
                                 payment_result.get("txn_reference") or request.reqid)
        raise HTTPException(status_code=409, detail=_paid_not_ticketed_message(locator_code))
    return _issue_and_save_ticket(locator_code, payment_result)


def _read_new_booking(locator_code: str, commit_result: dict) -> dict:
    """The booking just made, read back from Travelport. The PNR exists from
    the commit on, so a failing read must never turn it into a "not
    confirmed" booking: SQ NDC JJ2V6X (2026-11-10) — the read straight after
    the commit answered "Request cannot be unmarshalled (2999)", the customer
    was told the booking failed, retried, and a duplicate PNR was made while
    JJ2V6X stayed held at the airline. Retries the read, then falls back to
    the commit response itself, which carries the whole reservation."""
    for attempt in range(3):
        try:
            return services.retrieve_reservation(locator_code)
        except Exception as read_err:
            logger.warning(f"{locator_code}: read after booking failed (attempt {attempt + 1}/3): {read_err}")
            time.sleep(2 * (attempt + 1))
    logger.warning(f"{locator_code}: using the commit response — the booking exists at the airline")
    return services.parse_commit_response(commit_result["raw_response"], locator_code)


def _ticket_on_pnr(locator_code: str, tries: int = 3, wait: int = 5) -> dict | None:
    """The PNR read live, when it holds a ticket number (else None). A few
    reads a few seconds apart: the airline can write the ticket just after
    Travelport has answered the ticketing request with an error."""
    for attempt in range(tries):
        if attempt:
            time.sleep(wait)
        try:
            res = services.retrieve_reservation(locator_code)
        except Exception as e:
            logger.warning(f"{locator_code}: ticket re-check read failed: {e}")
            continue
        if res.get("ticket_number"):
            return res
    return None


def _issue_and_save_ticket(locator_code: str, payment_result: Optional[dict] = None) -> dict:
    """Runs after the PayCorp payment has been verified by the caller."""
    # One TraceId for every Travelport call issue_ticket() makes internally
    # (buildfromlocator -> formofpayment -> payment -> commit-with-ticketing).
    trace_token = services.start_flow_trace_id()
    try:
        # Held under the PNR's lock so the unpaid-EMD check can't see these
        # EMDs before they are recorded as ours.
        with addon_deadlines.locator_lock(locator_code):
            ticket, issue_error = None, None
            try:
                ticket = services.issue_ticket(locator_code)
            except Exception as e:
                issue_error = e
            # issue_ticket() returns the held booking when Travelport gives no
            # ticket number without an error (e.g. "NO FARES") — that is NOT
            # a ticket: handled below as paid-but-not-ticketed, never saved
            # as a success. But an error answer doesn't always mean no ticket:
            # JJ2MC1 (QR NDC, 2026-11-09) got "INTERNAL SERVER ERROR (1)" and
            # the airline issued ticket 1572141403007 anyway — the customer was
            # told it wasn't booked and a refund was filed. So the PNR is read
            # again before anything is declared failed.
            if not (ticket or {}).get("ticket_number"):
                late = _ticket_on_pnr(locator_code)
                if late:
                    logger.warning(f"{locator_code}: ticketing answered '{issue_error or 'no ticket number'}' "
                                   f"but the PNR holds ticket {late['ticket_number']} — treated as issued")
                    ticket = late
                elif issue_error:
                    raise issue_error
                else:
                    raise ValueError(ticket.get("ticket_issuance_diagnostic")
                                     or "Travelport returned no ticket number")
            database.record_emds(locator_code, [e.get("number") for e in ticket.get("emd_numbers") or []
                                                if isinstance(e, dict)], "ours_ticketing")
    except Exception as e:
        logger.error(f"Ticket issuance failed for {locator_code}: {e}")
        txn_ref = payment_result.get("txn_reference") if payment_result else None
        txn_note = f" (txnReference={txn_ref})" if txn_ref else ""
        try:
            _notify_stuck_booking(locator_code, str(e), txn_ref)
        except Exception as notify_err:
            logger.error(f"Failed to send stuck-booking notification for {locator_code} (non-fatal): {notify_err}")
        insights.mark("Ticket issued", "fail", "Travelport did not issue the ticket", raw_error=str(e))
        if payment_result:
            # Paid by card, no ticket: refund request filed for the customer;
            # the Travelport error stays in the log, not on their screen.
            logger.error(f"{locator_code}: paid{txn_note} but not ticketed — {e}")
            _auto_refund_for_booking(locator_code, str(e), txn_ref)
            raise HTTPException(status_code=502, detail=_paid_not_ticketed_message(locator_code))
        raise HTTPException(
            status_code=502,
            detail=f"Payment succeeded{txn_note} but Travelport ticket issuance failed: {str(e)}"
        )
    finally:
        services.end_flow_trace_id(trace_token)

    insights.mark("Ticket issued", "pass", f"PNR {locator_code} — ticket {ticket.get('ticket_number')}")
    insights.journey_info(locator_code=locator_code)
    if payment_result:
        ticket["payment_txn_reference"] = payment_result.get("txn_reference")
        ticket["payment_auth_code"] = payment_result.get("auth_code")

    # issue_ticket()'s internal retrieve_reservation() only knows Travelport-side
    # data — re-apply the locally-computed fields (seat selection, seat
    # charge, ancillaries, contact enrichment, adjusted total_fare) that
    # /confirm already saved for this PNR. seat_number/ancillaries are local-
    # only (seat assignment is never actually sent to the GDS), so a fresh
    # live retrieve can never carry them on its own — omitting them here
    # was exactly why a paid Seat Selection Fee showed up on the issued
    # ticket with no seat number anywhere on it.
    existing = database.get_booking_by_locator(locator_code)
    if existing:
        for key in ("seat_charge", "seat_number", "ancillaries", "ancillary_charge",
                     "email", "phone", "date_of_birth", "gender",
                     "nationality", "passport_expiry", "offer_id", "payment_method", "customer_id",
                     "offer_local_id", "offer_uuid", "offer_authority", "addon_results"):
            if existing.get(key) not in (None, "", []):
                ticket.setdefault(key, existing.get(key))
        # Seats are now really on the PNR, so the live retrieve returns one
        # (only the first, as "20C"). The per-flight label /confirm saved
        # ("Departure UL225: 20C, Return UL226: 4A") is the complete one.
        if existing.get("seat_number"):
            ticket["seat_number"] = existing["seat_number"]
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

    # Paid seats/extras are issued as EMDs in the same commit as the ticket.
    # If the ticket came out but fewer EMDs than paid seat/extra offers did,
    # the booking is flagged and staff are alerted — never shown as complete.
    emd_missing = (ticket.get("emd_expected") or 0) - len(ticket.get("emd_numbers") or [])
    ticket["emd_status"] = "pending" if (ticket.get("ticket_number") and emd_missing > 0) else (
        "issued" if ticket.get("emd_numbers") else None)
    if ticket["emd_status"] == "pending":
        txn_ref = payment_result.get("txn_reference") if payment_result else None
        try:
            _notify_stuck_booking(
                locator_code,
                f"Ticket {ticket.get('ticket_number')} issued, but {emd_missing} paid seat/extra EMD(s) were not issued by Travelport",
                txn_ref,
            )
        except Exception as notify_err:
            logger.error(f"Failed to send EMD-pending notification for {locator_code} (non-fatal): {notify_err}")

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

    # Push this ticketed booking to the George Steuart ERP as a Sales
    # Invoice (see services/erp_sync_service.py) — best-effort, same as
    # loyalty points above. The ticket is already real on Travelport by
    # this point, so an ERP/network hiccup here must never surface as a
    # booking failure to the customer; failures are recorded for the
    # /api/admin/erp-sync backfill/retry endpoints instead of being retried
    # inline. Sent in the background: the customer gets the e-ticket at once,
    # and a slow ERP can never delay it.
    _push_booking_to_erp_background(locator_code, saved)

    return {
        "success": True,
        "ticket": ticket,
        "cached_id": saved.get("id")
    }


def _push_booking_to_erp_background(locator: str, saved: dict) -> None:
    """Ticket -> ERPNext Sales Invoice on a background thread, seconds after
    ticketing, never holding up the customer's response. Result recorded on
    the booking (erp_invoice_name / erp_sync_error); never raises."""
    if not services.erp_configured():
        return

    def run():
        try:
            invoice_name = services.push_booking_to_erp(saved)
            database.mark_erp_synced(locator, invoice_name)
        except Exception as e:
            logger.warning(f"ERP sync failed for {locator} (non-fatal): {e}")
            try:
                database.mark_erp_sync_failed(locator, str(e))
            except Exception as db_err:
                logger.error(f"Could not record ERP sync failure for {locator}: {db_err}")

    threading.Thread(target=run, name=f"erp-{locator}", daemon=True).start()


# ── ERP Sync (George Steuart ERP, erp-dev.gsoptimize.lk) ───────────────────────
# Manual counterpart to the automatic push in _issue_and_save_ticket above, for
# the two cases that need one: a booking whose automatic push failed (network
# blip, ERP down) and anything ticketed before ERP_API_KEY/SECRET were ever
# configured. Both admin-only; neither ever touches Travelport.

@app.get("/api/admin/erp-sync/status")
def erp_sync_status(admin: dict = Depends(auth.get_current_admin)):
    """Lists ticketed bookings not yet pushed to the ERP, for the admin's
    ERP Sync screen to show as a worklist."""
    pending = database.get_bookings_pending_erp_sync()
    return {
        "configured": services.erp_configured(),
        "pending_count": len(pending),
        "pending": pending,
    }


@app.post("/api/admin/erp-sync/{locator_code}")
def erp_sync_one(locator_code: str, admin: dict = Depends(auth.get_current_admin)):
    """Retries (or does for the first time) the ERP push for one booking."""
    if not services.erp_configured():
        raise HTTPException(status_code=503, detail="ERP_API_KEY / ERP_API_SECRET are not configured.")
    locator_code = locator_code.upper().strip()
    booking = database.get_booking_by_locator(locator_code)
    if not booking:
        raise HTTPException(status_code=404, detail=f"No booking found for locator {locator_code}.")
    try:
        invoice_name = services.push_booking_to_erp(booking)
        database.mark_erp_synced(locator_code, invoice_name)
        return {"success": True, "invoice_name": invoice_name}
    except Exception as e:
        database.mark_erp_sync_failed(locator_code, str(e))
        raise HTTPException(status_code=502, detail=f"ERP sync failed: {e}")


@app.post("/api/admin/erp-sync/backfill")
def erp_sync_backfill(admin: dict = Depends(auth.get_current_admin)):
    """One-time (or re-run anytime) sweep: pushes every ticketed booking
    that has never been synced to the ERP. Keeps going past individual
    failures so one bad record doesn't block the rest; reports a per-locator
    result list."""
    if not services.erp_configured():
        raise HTTPException(status_code=503, detail="ERP_API_KEY / ERP_API_SECRET are not configured.")
    pending = database.get_bookings_pending_erp_sync()
    results = []
    for booking in pending:
        locator = booking.get("locator_code")
        try:
            invoice_name = services.push_booking_to_erp(booking)
            database.mark_erp_synced(locator, invoice_name)
            results.append({"locator_code": locator, "success": True, "invoice_name": invoice_name})
        except Exception as e:
            database.mark_erp_sync_failed(locator, str(e))
            results.append({"locator_code": locator, "success": False, "error": str(e)})
    return {
        "attempted": len(results),
        "succeeded": sum(1 for r in results if r["success"]),
        "failed": sum(1 for r in results if not r["success"]),
        "results": results,
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
    selected_seats: List[SelectedSeat] = []
    selected_ancillaries: List[SelectedAncillary] = []
    quoted_price: Optional[float] = Field(default=None, description="Price the customer was shown; refused if Travelport's priced fare is higher")


# Carriers whose post-commit workbench has no offer to pay against, so they
# must be ticketed with Travelport's NDC Instant Pay workflow instead.
NDC_INSTANT_PAY_CARRIERS = {"EK"}

# Airlines that sell paid seats only once the ticket is issued (Emirates):
# offered after ticketing in Manage Booking, never at booking.
PAID_SEATS_AFTER_TICKET_CARRIERS = {"EK"}

# Airlines taken off sale: hidden from search and refused at booking and
# payment, so no customer is charged for a ticket that can't be issued.
# TK: every ticket refused with 4165 "MANDATORY SSR DETAILS DOES NOT EXIST
# IN THE RESERVATION" (10 sandbox PNRs, 2026-11-02) — Travelport case open.
# FZ: Add Offer answers "504 Gateway Time-out" after 30 s on every flydubai
# itinerary since 2026-11-06 (27 tries; e.g. TraceID_5324BC4420E7,
# 2026-11-10) — Travelport case to raise. Remove the code to sell again.
PAUSED_CARRIERS = {"TK", "FZ"}
PAUSED_CARRIER_DETAIL = ("This airline isn't available for online booking right now. "
                         "You have not been charged — please choose another flight.")


def _offer_carriers(offer: dict) -> set:
    """Every marketing / operating airline code on a search offer."""
    segs = [s for leg in (offer.get("legs") or [offer]) for s in (leg.get("segments") or [])]
    return {str(s.get(k) or "").upper() for s in segs for k in ("carrier", "operating_carrier", "marketing_carrier") if s.get(k)}


def _ndc_carrier_of(raw_offering: dict) -> str:
    legs = services.workbench_service._get_leg_offerings(raw_offering)
    return str((legs[0] if legs else {}).get("id", "")).split("_")[0]


def _ndc_instant_pay(raw_offering: dict, travelers: list, selected_seats: list = None,
                      selected_ancillaries: list = None, customer_id: Optional[int] = None,
                      charged_amount: Optional[float] = None, charged_currency: Optional[str] = None) -> dict:
    try:
        ip = services.workbench_service.run_instant_pay_flow(raw_offering, travelers)
    except services.workbench_service.InstantPayError as e:
        raise HTTPException(status_code=422, detail=str(e))
    locator = ip["locator_code"]

    # The tickets are issued at this point. A failed re-read must never turn
    # into "not ticketed" + a refund (EK 1765757312231, 2026-11-06: issued,
    # then 4392 "update in progress" on the re-read) — use the commit
    # response instead.
    try:
        ticket = services.retrieve_reservation(locator)
    except Exception as e:
        logger.warning(f"{locator}: ticketed {ip['ticket_numbers']} but the re-read failed ({e}) — using the commit response")
        ticket = services.parse_commit_response(ip["raw_response"], locator)
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
    if charged_amount:
        # What the customer actually paid (Travelport's priced fare + markup,
        # fixed server-side at /api/ndc/book) — not Travelport's net price.
        ticket["total_fare"], ticket["currency"] = charged_amount, charged_currency
    elif not ticket.get("total_fare"):
        ticket["total_fare"], ticket["currency"] = ip["total_fare"], ip["currency"]

    # Travelport's Ancillary and EMD Guide: "The NDC Instant Pay workflow ...
    # does not support booking ancillaries." Seats/extras are therefore never
    # offered for these fares (see /bookings/initiate), and anything sent
    # anyway is NOT recorded or charged — it was never booked with the airline.
    if selected_seats or selected_ancillaries:
        logger.warning(
            f"{locator}: ignoring {len(selected_seats or [])} seat(s) and "
            f"{len(selected_ancillaries or [])} extra(s) — not bookable in NDC Instant Pay"
        )
    ticket["seat_charge"] = 0.0
    ticket["ancillary_charge"] = 0.0

    _attach_traveler_details(ticket, travelers)
    ticket["customer_id"] = customer_id
    saved = database.save_booking(ticket)

    # NDC Instant Pay books, pays, and tickets in one Travelport commit, so
    # unlike the GDS/regular-NDC flow it never passes through
    # _issue_and_save_ticket — this is the only other place a booking
    # actually becomes "Ticketed", so it needs the same best-effort ERP push
    # (in the background, like the GDS flow).
    _push_booking_to_erp_background(locator, saved)

    insights.mark("Ticket issued", "pass", f"PNR {locator} — ticket(s) {', '.join(n for _, n in ip['ticket_numbers'])}")
    insights.journey_info(locator_code=locator)
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
# usefully exist before payment. Its raw_offering/travelers and the amount
# to charge are saved in MySQL (ndc_pending_payments) between /api/ndc/book
# and the PayCorp return, so a backend restart can't strand a paid customer.


class NdcInstantPayCompleteRequest(BaseModel):
    pending_id: str
    reqid: str


@app.post("/api/ndc/book")
def ndc_book(request: NdcBookAndIssueRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """
    STEP 1 of the pay-before-ticket NDC flow: creates the PNR (held, not yet
    ticketed) for carriers whose post-commit workbench can pay normally.
    Emirates (Instant Pay) has no such hold — its request is stashed instead,
    and the PNR + ticket are created together, after payment, by
    /api/ndc/instant-pay/complete.
    """
    _require_switch("pnr_create_enabled")
    _journey_booking_info(request.travelers, request.raw_offering)
    _check_travelers_for_trip(request.travelers, request.raw_offering)
    if _ndc_carrier_of(request.raw_offering) in NDC_INSTANT_PAY_CARRIERS:
        # Price it with Travelport now, so the amount the customer pays is
        # fixed server-side (AirPrice total + markup) — the browser used to
        # send the payment amount itself and nothing checked it.
        trace_token = services.start_flow_trace_id()
        try:
            priced = services.workbench_service.confirm_price_details(request.raw_offering)
            amount, currency = services.workbench_service.charge_for_priced_fare(
                priced, request.quoted_price, _quoted_currency(request.raw_offering),
                len(request.travelers or []))
        except services.workbench_service.PriceChangedError as e:
            raise _price_changed_response(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=friendly_travelport_error(str(e)))
        finally:
            services.end_flow_trace_id(trace_token)
        pending_id = str(uuid.uuid4())
        database.save_ndc_pending_payment(pending_id, {
            "raw_offering": request.raw_offering,
            "travelers": [t.model_dump() for t in request.travelers],
            "selected_seats": [s.model_dump() for s in request.selected_seats],
            "selected_ancillaries": [a.model_dump() for a in request.selected_ancillaries],
            "customer_id": customer_id,
        }, amount, currency)
        insights.journey_info(amount=amount, currency=currency, fare_source="NDC")
        return {"requires_prepay": True, "pending_id": pending_id, "amount": amount, "currency": currency}

    trace_token = services.start_flow_trace_id()
    try:
        booked = confirm_booking(
            BookingConfirmRequest(
                raw_offering=request.raw_offering, travelers=request.travelers,
                selected_seats=[], payment_method="card", reference_payload_only=True,
            ),
            customer_id=customer_id,
        )
    finally:
        services.end_flow_trace_id(trace_token)
    ticket = booked["ticket"]
    return {
        "requires_prepay": False, "locator_code": ticket["locator_code"],
        "airline_pnr": ticket.get("airline_pnr"), "status": ticket.get("status"),
        "total_fare": ticket.get("total_fare"), "currency": ticket.get("currency"),
    }


def _paid_not_ticketed_message(reference: str) -> str:
    return (f"The airline did not confirm your ticket, so this flight is not booked for you. "
            f"Your payment will be refunded in full — we have already created the refund request for you "
            f"(reference {reference}), you don't need to do anything. Refunds are processed on the next working day. "
            f"Please do not pay again for this booking.")


def _auto_refund_request(reference: str, *, customer_id, name: str, email: str, phone: str,
                         travel_date: str, amount, currency: str, reason: str, payment_ref: str | None) -> None:
    """Files the customer's full-refund request automatically when they paid
    but no ticket was issued, so it sits in the next-day refund queue without
    the customer finding a form or calling anyone (best effort — never hides
    the original error)."""
    try:
        database.create_cancellation_request(
            customer_id, reference, (travel_date or datetime.now().strftime("%Y-%m-%d"))[:10],
            name or "Customer", email or "", phone or "", True,
            admin_note=(f"AUTOMATIC FULL REFUND — paid {currency} {float(amount or 0):,.2f} "
                        f"(payment {payment_ref}) but no ticket was issued: {reason}"),
        )
    except Exception as e:
        logger.error(f"{reference}: could not create the automatic refund request: {e}")


def _auto_refund_for_pending(reference: str, pending: dict, reason: str, payment_ref: str | None) -> None:
    payload = pending.get("payload") or {}
    lead = (payload.get("travelers") or [{}])[0]
    dates = sorted(str(s.get("departure_time") or "")[:10]
                   for s in _segments_of_offering(payload.get("raw_offering") or {}) if s.get("departure_time"))
    _auto_refund_request(
        reference, customer_id=payload.get("customer_id"),
        name=f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
        email=lead.get("email"), phone=lead.get("phone"), travel_date=dates[0] if dates else None,
        amount=pending.get("amount"), currency=pending.get("currency"), reason=reason, payment_ref=payment_ref,
    )


def _auto_refund_for_booking(locator_code: str, reason: str, payment_ref: str | None) -> None:
    b = database.get_booking_by_locator(locator_code) or {}
    _auto_refund_request(
        locator_code, customer_id=b.get("customer_id"), name=b.get("passenger_name"),
        email=b.get("passenger_email") or b.get("email"), phone=b.get("phone") or b.get("passenger_phone"),
        travel_date=b.get("departure_time"), amount=b.get("total_fare"), currency=b.get("currency"),
        reason=reason, payment_ref=payment_ref,
    )


@app.post("/api/ndc/instant-pay/complete")
def ndc_instant_pay_complete(request: NdcInstantPayCompleteRequest):
    """STEP 3 (Emirates only): payment confirmed — now book and ticket in one Instant Pay commit."""
    reference = f"NDC-{request.pending_id[:8]}"

    def already_handled():
        # This payment was already taken for this booking (the return page was
        # reloaded, opened twice, or the first attempt failed). Never tell a
        # customer who has paid to "search again" — they could pay twice.
        claim = database.get_payment_claim(request.pending_id)
        if not claim or claim["reqid"] != request.reqid:
            return None
        if claim["locator_code"]:
            return {"success": True, "locator_code": claim["locator_code"], "status": "Ticketed",
                    "already_completed": True, "message": None}
        raise HTTPException(status_code=502, detail=_paid_not_ticketed_message(reference))

    pending = database.get_ndc_pending_payment(request.pending_id)
    if not pending:
        done = already_handled()
        if done:
            return done
        raise HTTPException(
            status_code=404,
            detail=(f"We couldn't find this booking. If money was taken from your card, please do not pay again — "
                    f"contact us and quote reference {reference}."),
        )
    done = already_handled()
    if done:
        return done
    # The paid amount must equal the server-side amount fixed at /api/ndc/book
    # (Travelport's priced fare + markup), for this pending booking, once.
    payment_result = _verify_and_claim_payment(
        request.reqid, expected_client_ref=request.pending_id,
        expected_amount=pending["amount"], expected_currency=pending["currency"],
    )
    if not database.consume_ndc_pending_payment(request.pending_id):
        done = already_handled()
        if done:
            return done
        raise HTTPException(status_code=409, detail=f"This booking is already being completed — please do not pay again. Reference: {reference}")
    payload = pending["payload"]
    trace_token = services.start_flow_trace_id()
    try:
        result = _ndc_instant_pay(
            payload["raw_offering"], payload["travelers"],
            payload.get("selected_seats"), payload.get("selected_ancillaries"),
            customer_id=payload.get("customer_id"),
            charged_amount=pending["amount"], charged_currency=pending["currency"],
        )
    except Exception as e:
        # Paid, but Travelport didn't ticket: alert staff so the payment is
        # refunded or the ticket issued manually — never left silent. Any
        # error counts (a Travelport HTTP 500 on the commit used to escape
        # this as an unhandled crash, which the browser saw as a network error).
        reason = e.detail if isinstance(e, HTTPException) else f"{type(e).__name__}: {e}"
        logger.error(f"{reference}: paid (reqid={request.reqid}) but not ticketed — {reason}")
        insights.mark("Ticket issued", "fail", "Airline/Travelport did not issue the ticket — automatic refund request filed",
                      raw_error=str(reason))
        try:
            _notify_stuck_booking(reference, f"{reason} (payment reqid {request.reqid})", payment_result.get("txn_reference"))
        except Exception as notify_err:
            logger.error(f"Failed to send stuck-booking notification for pending {request.pending_id}: {notify_err}")
        # No ticket means a full refund — raise the refund request for the
        # customer now, so it is in the next-day refund queue without them
        # having to find a form or call anyone.
        _auto_refund_for_pending(reference, pending, reason, payment_result.get("txn_reference") or request.reqid)
        raise HTTPException(status_code=502, detail=_paid_not_ticketed_message(reference))
    finally:
        services.end_flow_trace_id(trace_token)
    try:
        # lets a reloaded return page show this ticket instead of an error
        database.set_payment_claim_locator(request.reqid, result["locator_code"])
    except Exception as e:
        logger.warning(f"Could not record PNR {result.get('locator_code')} on payment {request.reqid}: {e}")
    result["payment_txn_reference"] = payment_result.get("txn_reference")
    return result


@app.post("/api/ndc/search")
def ndc_search(request: NdcSearchRequest):
    """NDC-only search (contentSourceList: ["NDC"]); one-way or round trip."""
    _require_switch("flight_search_enabled")
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
        offers = services.parse_flight_offers(raw, legs=legs, origin=None if legs else o, destination=None if legs else d)
    except Exception as e:
        logger.error(f"NDC search failed: {e}")
        raise HTTPException(status_code=502, detail=f"Travelport NDC search error: {str(e)[:400]}")
    result = raw.get("CatalogProductOfferingsResponse", {}).get("Result", {}) or {}
    carrier_notes = [f"{e.get('SourceID')}: {e.get('Message')}" for e in result.get("Error", []) or []]
    return {"flights": offers, "count": len(offers), "content_source": "NDC", "carrier_notes": carrier_notes}


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


# ── Admin: past force-issued tickets (read-only) ──────────────────────────────
# The admin force-issue override was removed: no one, admin included, can
# issue a ticket without a verified card payment. Its audit log stays
# viewable for the tickets force-issued before then.

@app.get("/api/admin/force-issued-tickets")
def list_force_issued_tickets(_admin: dict = Depends(auth.get_current_admin)):
    return {"items": database.get_force_issued_tickets()}


# ── Share Ticket with Customer (Admin, Force Issue screen) ────────────────────
# Recovery tool for "payment succeeded but the customer never got their
# ticket" (lost email, typo'd address, spam filter, etc.). The admin re-sends
# an already-issued ticket's details by locator code -- but to guard against
# sending one customer's booking details to the WRONG person (typo, mixed-up
# PNR, or a deliberate attempt), the contact the admin types in must match
# what the customer themselves entered at booking time (passenger_email /
# phone on the booking record). A mismatch is refused outright, never sent
# partially or with a warning-only prompt, and every attempt -- matched or
# blocked -- is logged to ticket_shares for audit.

class ShareTicketRequest(BaseModel):
    channel: Literal["email", "whatsapp"]
    contact: str = Field(..., min_length=3, max_length=200)


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _phone_matches(entered: str, on_file: str) -> bool:
    """Phone numbers are free-text (see booking's "phone" field) and commonly
    written with or without a country code / leading trunk 0 (e.g. the same
    Sri Lankan number as local "0771234567" vs international "+94771234567"
    -- the leading 0 is dropped entirely when the country code is added, so
    it's not even a prefix/suffix of the other). An exact string match would
    reject this legitimate case, so instead compare only the last 9 digits
    (a typical local subscriber number) of each, ignoring country code and
    any leading trunk digit on either side -- strict enough to catch a
    genuinely different number, lenient enough to tolerate that formatting."""
    a, b = _digits_only(entered), _digits_only(on_file)
    if not a or not b:
        return False
    if a == b:
        return True
    n = min(len(a), len(b), 9)
    return n >= 7 and a[-n:] == b[-n:]


@app.post("/api/admin/bookings/{locator_code}/share-ticket")
def share_ticket_with_customer(
    locator_code: str,
    request: ShareTicketRequest,
    admin: dict = Depends(auth.get_current_admin),
):
    locator_code = locator_code.upper().strip()
    booking = database.get_booking_by_locator(locator_code)
    if not booking:
        raise HTTPException(status_code=404, detail=f"No booking found for locator '{locator_code}'.")
    if booking.get("status") != "Ticketed":
        raise HTTPException(
            status_code=400,
            detail=f"Booking {locator_code} has not been ticketed yet (status: {booking.get('status')}) — nothing to share.",
        )

    contact = request.contact.strip()
    if request.channel == "email":
        on_file = (booking.get("email") or booking.get("passenger_email") or "").strip().lower()
        matched = bool(on_file) and contact.lower() == on_file
    else:
        on_file = booking.get("phone") or ""
        matched = _phone_matches(contact, on_file)

    if not matched:
        database.log_ticket_share(
            locator_code=locator_code, admin_id=admin.get("admin_id"), admin_username=admin.get("sub"),
            channel=request.channel, contact=contact, matched=False, status="blocked_mismatch",
        )
        logger.warning(
            f"ADMIN SHARE-TICKET BLOCKED: {locator_code} by {admin.get('sub')} "
            f"(admin_id={admin.get('admin_id')}) — entered {request.channel}={contact!r} "
            f"does not match the customer's own {request.channel} on file."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"That {('email' if request.channel == 'email' else 'WhatsApp number')} doesn't match what this "
                f"customer entered during booking. For security, ticket details can't be sent to a different "
                f"contact — please verify with the customer before trying again."
            ),
        )

    if request.channel == "email":
        sent, email_error = services.send_ticket_share_email(contact, booking)
        database.log_ticket_share(
            locator_code=locator_code, admin_id=admin.get("admin_id"), admin_username=admin.get("sub"),
            channel="email", contact=contact, matched=True, status="sent" if sent else "send_failed",
        )
        if not sent:
            raise HTTPException(status_code=502, detail=f"Could not send the email: {email_error}")
        logger.info(f"ADMIN SHARE-TICKET: {locator_code} emailed to {contact} by {admin.get('sub')}")
        return {"success": True, "channel": "email", "sent_to": contact}

    # WhatsApp: no server-side WhatsApp send capability exists in this system
    # (no Twilio/Meta Business API integration) -- the match was verified
    # server-side, so the frontend is now cleared to open a wa.me deep link
    # pre-filled with the ticket details, which the admin sends themselves
    # from their own WhatsApp. wa.me needs the number in full international
    # digits (country code, no leading 0/+) -- the admin may have typed a
    # local format (e.g. "0771234567"), so the link uses the customer's own
    # on-file number (already in that format), not whatever the admin typed.
    share_text = (
        f"Tick and Book — Your E-Ticket\n"
        f"PNR: {booking.get('locator_code')}\n"
        f"Passenger: {booking.get('passenger_name') or ''}\n"
        f"Flight: {booking.get('flight_number') or ''} ({booking.get('airline') or ''})\n"
        f"Route: {booking.get('departure_airport') or ''} -> {booking.get('arrival_airport') or ''}\n"
        f"Departure: {booking.get('departure_time') or ''}\n"
        f"Fare: {booking.get('currency') or ''} {booking.get('total_fare') or ''}\n"
        f"Status: {booking.get('status') or ''}"
    )
    database.log_ticket_share(
        locator_code=locator_code, admin_id=admin.get("admin_id"), admin_username=admin.get("sub"),
        channel="whatsapp", contact=contact, matched=True, status="validated",
    )
    logger.info(f"ADMIN SHARE-TICKET: {locator_code} validated for WhatsApp to {contact} by {admin.get('sub')}")
    return {"success": True, "channel": "whatsapp", "sent_to": contact, "whatsapp_number_digits": _digits_only(on_file), "share_text": share_text}


@app.get("/api/admin/ticket-shares")
def list_ticket_shares(_admin: dict = Depends(auth.get_current_admin)):
    return {"items": database.get_ticket_shares()}


# ── STEP 8: Retrieve PNR ───────────────────────────────────────────────────────


def _merge_cached_ticket_fields(ticket: dict, cached: Optional[dict]) -> dict:
    """Fills in fields on a freshly-retrieved Travelport `ticket` dict from
    our own local `cached` DB row, for data that only ever exists locally
    and Travelport's live retrieve never returns — selected seats chief
    among them: seat assignment in this system is local/cosmetic only (no
    real GDS seat-assignment call is ever made), so a live retrieve can
    never surface a seat number on its own, no matter how it's called.
    Shared by every endpoint that calls retrieve_reservation() so a fix
    here (or a newly-added field) doesn't have to be duplicated — a
    previous divergence here was exactly why /api/ticket-status silently
    dropped seat numbers that /api/bookings/retrieve/{locator} preserved."""
    if not cached:
        return ticket
    # A cancel, void or refund done here is final even when Travelport's
    # retrieve still looks live (a voided ticket keeps its Ticket[] entry, so
    # the parse says "Ticketed") — the same rule save_booking() applies to the
    # stored status.
    if cached.get("status") in database.CANCELLED_STATUSES:
        ticket["status"] = cached["status"]
    # Seat label saved at /confirm holds every confirmed seat with its flight
    # ("Departure UL225: 20C, Return UL226: 4A"); since GDS seats are now
    # really on the PNR the live retrieve returns only the first one ("20C").
    if cached.get("seat_number"):
        ticket["seat_number"] = cached.get("seat_number")
    # Seats/extras outcome, extras charge and EMDs exist only in our record
    # (/confirm and ticketing) — without them a reopened ticket showed the
    # extras total as a "Seat Selection Fee" and lost its EMD numbers.
    for field in ["ancillary_charge", "ancillaries", "addon_results", "emd_numbers", "emd_status", "ticket_numbers", "ssr_requests"]:
        if cached.get(field) not in (None, "", []) and not ticket.get(field):
            ticket[field] = cached.get(field)
    _attach_ssr_status(ticket, None)
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
    return ticket


def _cancelled_status(cached: Optional[dict], err: Exception) -> Optional[str]:
    """Status to show when Travelport's live retrieve fails for a booking that
    is cancelled: the stored Cancelled/Voided/Refunded, or "Cancelled" when
    Travelport says so itself. Confirmed live 2026-10-29: a cancelled SQ NDC
    PNR (HNHB4H) can't be retrieved at all — "RESERVATION IS CANCELLED AND
    CANNOT BE RETRIEVED (4422)" — so Check My Ticket Status answered 502."""
    if cached and cached.get("status") in database.CANCELLED_STATUSES:
        return cached["status"]
    if "RESERVATION IS CANCELLED" in str(err).upper():
        return "Cancelled"
    return None


# ── Public booking views: personal data is only for staff or the owner ───────
# A PNR is printed on tickets, shared over WhatsApp and easy to guess, so
# anyone holding one used to get every passenger's passport number, birth
# date, phone and email back. Without an admin token or the booking's own
# customer token, those are masked or left out of the response (the stored
# record is untouched). The e-ticket itself still shows names, flights,
# fares and ticket numbers.
_PRIVATE_FIELDS_DROPPED = {
    "date_of_birth", "passport_expiry", "birth_place", "document_issue_date",
    "address_street", "address_city", "address_state_name", "address_state_value",
    "address_country", "address_postal_code", "raw_ticket_json", "raw_response",
    # Wheelchair / dietary requests are health-related.
    "ssr_requests", "special_services", "ssr_status",
}


def _mask_tail(value, keep: int = 3) -> str:
    s = str(value or "")
    return ("•" * max(len(s) - keep, 0)) + s[-keep:] if s else ""


def _mask_email(value) -> str:
    s = str(value or "")
    if "@" not in s:
        return _mask_tail(s)
    local, _, domain = s.partition("@")
    return f"{local[:1]}•••@{domain}"


def _public_booking_view(data):
    """Deep copy with personal data masked/removed, for unauthenticated callers."""
    if isinstance(data, list):
        return [_public_booking_view(v) for v in data]
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if k in _PRIVATE_FIELDS_DROPPED:
            continue
        if k == "passport_number":
            out[k] = _mask_tail(v)
        elif k in ("phone", "passenger_phone"):
            out[k] = _mask_tail(v, 3)
        elif k in ("email", "passenger_email"):
            out[k] = _mask_email(v)
        else:
            out[k] = _public_booking_view(v)
    return out


def _can_see_private(principal: Optional[dict], booking: Optional[dict]) -> bool:
    if not principal:
        return False
    if principal["role"] == "admin":
        return True
    return bool(booking) and principal.get("customer_id") is not None \
        and booking.get("customer_id") == principal.get("customer_id")


@app.get("/api/bookings/{locator_code}/live")
def live_booking_status(locator_code: str, principal: Optional[dict] = Depends(auth.get_optional_principal)):
    """
    My Bookings → "Live status": the booking straight from Travelport and the
    airline — flights with the airline's segment status, tickets with coupon
    status, seats/extras/special requests with their confirmation — never
    from our database (services/live_booking_service.py). Only the booking's
    own customer or staff.
    """
    locator_code = locator_code.upper().strip()
    if not principal:
        raise HTTPException(status_code=401, detail="Sign in to see this booking's live status.")
    if not _can_see_private(principal, database.get_booking_by_locator(locator_code)):
        raise HTTPException(status_code=403, detail="This booking isn't on your account.")
    with services.flow_trace_id() as trace_id:
        try:
            data = live_booking_service.get_live_booking(locator_code)
        except live_booking_service.LiveBookingError as e:
            if "DOES NOT EXIST" in str(e).upper():
                # 4247 — Travelport purges a PNR some time after its last flight.
                raise HTTPException(status_code=404, detail=(
                    "Travelport no longer holds this booking — bookings are removed from Travelport some time "
                    f"after the flight. Your ticket can still be opened with View / Print Ticket. (TraceId {trace_id})"))
            raise HTTPException(status_code=404, detail=f"Travelport: {e} (TraceId {trace_id})")
        except services.CancelError as e:
            raise HTTPException(status_code=502, detail=f"{e} (TraceId {trace_id})")
    data["trace_id"] = trace_id
    return data


def _attach_live_addons(ticket: dict, locator_code: str) -> None:
    """Seats, extras, special requests and their EMDs exactly as Travelport
    holds them now (live_booking_service) — what the ticket shows, not our
    record of what was asked for. Paid-later extras appear here too."""
    lb = live_booking_service.get_live_booking(locator_code, with_coupons=False)
    ticket["live_addons"] = {k: lb.get(k) or [] for k in ("seats", "extras", "special_services")}
    ticket["live_addons"]["retrieved_at"] = lb.get("retrieved_at")
    seats = [x for x in lb.get("seats") or [] if x.get("seat") and (x.get("status") or "").lower() == "confirmed"]
    if seats:
        # Same "FLIGHT: SEAT" label the ticket page already reads per flight.
        ticket["seat_number"] = ", ".join(f"{(x.get('flights') or [''])[0]}: {x['seat']}" for x in seats)
    emds: dict = {}
    for x in (lb.get("seats") or []) + (lb.get("extras") or []):
        if x.get("emd"):
            label = f"Seat {x['seat']}" if x.get("seat") else (x.get("description") or "Extra")
            emds.setdefault(x["emd"], []).append(f"{label} ({', '.join(x.get('flights') or [])})" if x.get("flights") else label)
    if emds:
        ticket["emd_numbers"] = [{"number": n, "description": "; ".join(d)} for n, d in emds.items()]
        if ticket.get("emd_status") == "pending":
            ticket["emd_status"] = None


@app.get("/api/bookings/retrieve/{locator_code}")
def retrieve_booking(locator_code: str, principal: Optional[dict] = Depends(auth.get_optional_principal),
                     live_only: bool = Query(False, description="View / Print Ticket: never fall back to our saved copy")):
    """
    STEP 8 — Retrieve a reservation's details from Travelport by PNR.
    We fetch live from Travelport to ensure latest status and PNR link,
    then update our local database cache. Passport, birth date and contact
    details are only returned to staff or the booking's own customer.
    """
    try:
        # Fetch live from Travelport
        ticket = services.retrieve_reservation(locator_code)

        # Check if we have a locally cached seat number or passport mapping
        cached = database.get_booking_by_locator(locator_code)
        _merge_cached_ticket_fields(ticket, cached)

        # Save/update the local cache
        database.save_booking(ticket)
        if live_only:
            _attach_live_addons(ticket, locator_code)
            if principal and principal.get("role") == "customer" and principal.get("customer_id"):
                ticket["extras_invoices"] = _paid_addon_invoices(locator_code, principal["customer_id"])
        return ticket if _can_see_private(principal, cached) else _public_booking_view(ticket)
    except Exception as e:
        logger.warning(f"Live retrieval failed for PNR {locator_code}: {e}")
        if live_only:
            raise HTTPException(status_code=502, detail="We couldn't get your ticket from the airline just now. "
                                                        "Please try again in a moment.")
        # Fallback to local cache if offline or error occurs -- but only when
        # the cache actually has something useful. A PNR that Travelport now
        # says doesn't exist (purged/expired on their side, or never fully
        # committed) can still have a DB row whose own flight/passenger
        # fields are just as empty, saved back when this failure mode wasn't
        # detected yet (ticket_service._parse_reservation used to silently
        # treat "RECORD LOCATOR DOES NOT EXIST" as an empty-but-valid
        # booking). Returning that row as if it were real data produced a
        # 200 OK "receipt" with every field blank and status still
        # "Confirmed" — confirmed live for PNR 7NSGYD. An empty cache row is
        # worse than no data at all, so it's treated the same as no cache.
        cached = database.get_booking_by_locator(locator_code)
        if cached and (cached.get("flight_number") or cached.get("passenger_name")):
            status = _cancelled_status(cached, e)
            if status:
                cached = {**cached, "status": status}
            return cached if _can_see_private(principal, cached) else _public_booking_view(cached)
        raise HTTPException(status_code=404, detail=f"Reservation not found: {str(e)}")


# ── Check My Ticket Status (unified TP+AD lookup) ──────────────────────────────
# One input box, no GDS picker -- the customer doesn't know (and shouldn't need
# to know) which GDS their booking is on. Our own local DB is used to figure
# out which GDS owns this PNR/ticket number (every booking made through this
# system is saved with its source) -- status/itinerary fields always come
# from a live GDS call (Travelport's retrieve_reservation() or Amadeus's
# PNR_Retrieve via services.lookup_amadeus_booking), never served from the DB
# on their own. _merge_cached_ticket_fields() backfills from the DB only the
# handful of fields a live Travelport retrieve structurally can never carry
# (seat number chief among them -- seat assignment here is local/cosmetic,
# never actually written to the GDS) — this endpoint used to skip that merge
# entirely, which is exactly why a ticket's seat number showed right after
# booking but silently disappeared the moment it was looked up again here.

@app.get("/api/ticket-status")
async def ticket_status(query: str = Query(..., min_length=3, max_length=20),
                        principal: Optional[dict] = Depends(auth.get_optional_principal)):
    q = query.strip().upper()

    # Everything that waits on MySQL or Travelport runs in a worker thread:
    # this handler is async, and a live read done directly here held the whole
    # server — every other page, login and search — until Travelport answered
    # (up to 120 s a try; the 2026-11-10 10:00 Travelport outage left the site
    # frozen). Measured on the test copy: an airport search took 7.4 s instead
    # of 2.1 s while one ticket-status lookup ran.
    tp_cached = await asyncio.to_thread(
        lambda: database.get_booking_by_locator(q) or database.get_booking_by_ticket_number(q))
    if tp_cached:
        locator = tp_cached["locator_code"]
        try:
            ticket = await asyncio.to_thread(services.retrieve_reservation, locator)
        except Exception as e:
            status = _cancelled_status(tp_cached, e)
            if not status:
                logger.error(f"Ticket-status: found {locator} locally but Travelport live retrieve failed: {e}")
                raise HTTPException(status_code=502, detail=f"Found this booking but Travelport's live lookup failed: {str(e)[:300]}")
            ticket = {**tp_cached, "status": status}
        else:
            _merge_cached_ticket_fields(ticket, tp_cached)
        ticket["source"] = "TP"
        return ticket if _can_see_private(principal, tp_cached) else _public_booking_view(ticket)

    # Phase 1: when Amadeus is switched off in Provider Control, don't call
    # the Amadeus backend at all — it isn't a booking source.
    if (await asyncio.to_thread(database.get_provider_settings)).get("search_mode", "travelport_only") not in ("both", "amadeus_only"):
        raise HTTPException(status_code=404, detail="No booking found for that PNR or ticket number.")

    # Not ours on Travelport -- ask the Amadeus backend if it's one of theirs.
    try:
        amadeus_result = await services.lookup_amadeus_booking(q)
    except services.AmadeusBookingError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail="No booking found for that PNR or ticket number.")
        logger.error(f"Ticket-status: Amadeus lookup for {q} failed: {e.message}")
        raise HTTPException(status_code=502, detail=f"Found this booking but Amadeus's live lookup failed: {e.message}")

    amadeus_result["source"] = "AD"
    return amadeus_result if (principal and principal["role"] == "admin") else _public_booking_view(amadeus_result)


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
# Two steps, staff only. /cancel/quote asks Travelport what cancelling would
# do (void a same-day GDS ticket, NDC Refund Quote, or cancel a held booking)
# without changing anything; /cancel then does it, refusing if the action or
# amount moved since the quote staff confirmed. The local record changes only
# after Travelport confirms (services/cancel_service.py) — anything that fails
# at Travelport is raised as a stuck-booking alert.

TICKETED_CANCEL_ACTIONS = ("void", "refund")
TICKETED_CANCEL_OFF = ("Void and refund are switched off (Admin Portal → Provider Control). "
                       "Nothing was changed on Travelport.")


def _ticketed_cancel_enabled() -> bool:
    return bool(database.get_provider_settings().get("ticketed_cancel_enabled", False))


def _hold_ticketed_quote(quote: dict) -> dict:
    """While the void/refund switch is off, a quote still shows what
    Travelport would do but can't be confirmed. Held-booking cancels pass."""
    if quote.get("action") in TICKETED_CANCEL_ACTIONS and quote.get("can_cancel") and not _ticketed_cancel_enabled():
        quote["can_cancel"] = False
        quote["switched_off"] = True
        quote["message"] = f"{quote.get('message', '')} {TICKETED_CANCEL_OFF}".strip()
    return quote


class CancelConfirmRequest(BaseModel):
    action: Literal["void", "refund", "cancel"]
    amount: Optional[float] = None  # the amount shown on the quote staff confirmed
    cancellation_request_id: Optional[int] = None  # B2C request to mark resolved on success


@app.post("/api/bookings/{locator_code}/cancel/quote")
def quote_booking_cancellation(locator_code: str, _admin: dict = Depends(auth.get_current_admin)):
    """Check refund: void vs refund vs cancel, and the amount, live from Travelport."""
    locator_code = locator_code.upper().strip()
    with services.flow_trace_id() as trace_id:
        try:
            quote = services.quote_cancellation(locator_code)
        except services.CancelRefused as e:
            raise HTTPException(status_code=409, detail=str(e))
        except services.CancelError as e:
            logger.error(f"Cancel quote for {locator_code} failed at {e.step}: {e}")
            raise HTTPException(status_code=502, detail=f"{e} (TraceId {trace_id})")
    quote["trace_id"] = trace_id
    return _hold_ticketed_quote(quote)


@app.post("/api/bookings/{locator_code}/cancel")
def cancel_booking(locator_code: str, body: CancelConfirmRequest, admin: dict = Depends(auth.get_current_admin)):
    """
    Confirm cancel: void / refund / cancel on Travelport, then record the
    outcome (Voided / Refunded / Cancelled + refund amount). Staff only.

    Replaces the earlier stop-gap that refused every ticketed booking with a
    409 because Cancel Workbench Items alone neither voids nor refunds a
    ticket. GDS tickets past their void period are still refused (409):
    TripServices has no automated GDS refund yet, so those are refunded in
    Smartpoint. Void and refund are also refused while the Provider Control
    switch (ticketed_cancel_enabled) is off; held-booking cancels aren't.
    """
    locator_code = locator_code.upper().strip()
    if body.action in TICKETED_CANCEL_ACTIONS and not _ticketed_cancel_enabled():
        raise HTTPException(status_code=409, detail=TICKETED_CANCEL_OFF)
    with services.flow_trace_id() as trace_id:
        try:
            result = services.cancel_travelport_booking(locator_code, body.action, body.amount)
        except services.CancelRefused as e:
            raise HTTPException(status_code=409, detail=str(e))
        except services.QuoteChanged as e:
            e.quote["trace_id"] = trace_id
            raise HTTPException(status_code=409, detail={
                "message": "Travelport's answer changed since you checked — review the new quote and confirm again.",
                "quote": _hold_ticketed_quote(e.quote),
            })
        except services.CancelError as e:
            recorded = e.done.get("status")
            logger.error(f"Cancel of {locator_code} failed at {e.step} (TraceId {trace_id}): {e}")
            _notify_cancel_failure(locator_code, f"{e} (TraceId {trace_id})", recorded)
            raise HTTPException(status_code=502, detail=(
                f"{e} (TraceId {trace_id}). "
                + (f"Saved as {recorded} — that much Travelport confirmed. " if recorded
                   else "Nothing was changed locally. ")
                + "Staff have been alerted; check the booking in Smartpoint."
            ))

    if body.cancellation_request_id:
        req = database.get_cancellation_request_by_id(body.cancellation_request_id)
        if req and req["status"] == "pending" and req["booking_locator"].upper() == locator_code:
            amount_note = f" {result['currency']} {result['amount']:,.2f}" if result.get("amount") is not None else ""
            database.resolve_cancellation_request(
                body.cancellation_request_id, "resolved", admin["admin_id"],
                f"{result['status']} on Travelport{amount_note} (TraceId {trace_id})",
            )

    return {
        "locator_code": locator_code,
        "status": result["status"],
        "amount": result["amount"],
        "currency": result["currency"],
        "details": result["details"],
        "trace_id": trace_id,
    }


# ── Invoice Data Retrieval (Travelport Live) ───────────────────────────────────

@app.get("/api/invoice/price-raw/{locator_code}")
def get_raw_price(locator_code: str, _admin: dict = Depends(auth.get_current_admin)):
    """Debug (staff only): returns raw Offer (Price + Commission + Discount) from Travelport for inspection."""
    from config.api_endpoints import TravelportEndpoints
    from services.auth_service import get_auth_headers
    pnr = locator_code.upper().strip()
    url = TravelportEndpoints.retrieve_reservation(pnr)
    headers = get_auth_headers(with_body=False)
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
    Retrieve live Travelport data for all PNRs booked/issued under the configured PCC within a date range.
    Queries the local sqlite cache for matching locators, then fetches live PNR data from Travelport.
    NO system-generated mock data. Admin-only — bulk PCC-wide report.
    """
    logger.info(f"Generating live Travelport PNR report for dates {start_date} to {end_date} under PCC {TravelportConfig.PCC}...")
    
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

    The amount is set server-side whenever client_ref is one of ours: a
    pending Emirates Instant Pay booking (amount fixed at /api/ndc/book) or
    an un-ticketed Travelport booking (its saved total_fare). The browser's
    amount is only used for anything else.
    """
    amount, currency = request.amount, request.currency
    ref = (request.client_ref or "").strip()
    if ref:
        pending = database.get_ndc_pending_payment(ref)
        addon = None if pending else (database.get_addon_payment(ref) if ref.startswith("ADD") else None)
        booking = None if (pending or addon) else database.get_booking_by_locator(ref.upper())
        if pending:
            # Go-live switches: stop before the customer pays (an NDC pending
            # payment books and tickets right after payment).
            _require_switch("pnr_create_enabled")
            _require_switch("ticketing_enabled")
            amount, currency = pending["amount"], pending["currency"]
        elif addon:
            # Manage My Booking add-on: amount fixed when the seats/extras were booked.
            if addon["status"] != "awaiting_payment":
                raise HTTPException(status_code=409, detail="This add-on has already been paid or closed.")
            # Never take payment for an item already released or no longer
            # confirmed: checked live, and marked as being paid (under the
            # PNR's lock) so the deadline sweep leaves it alone meanwhile.
            with addon_deadlines.locator_lock(addon["locator_code"]):
                try:
                    addon_deadlines.check_payable(addon["locator_code"], addon["items"], addon["supplier_total"])
                except addon_deadlines.NotPayable as e:
                    database.finish_addon_payment(addon["addon_id"], "expired", {"reason": str(e)})
                    raise HTTPException(status_code=409, detail=str(e))
                except services.CancelError as e:
                    logger.error(f"Add-on {addon['addon_id']}: live check before payment failed: {e}")
                    raise HTTPException(status_code=503, detail="We couldn't check your booking with the airline just now. "
                                                                "No payment has been taken — please try again in a moment.")
                database.set_addon_payment_started(addon["addon_id"], None)
            amount, currency = addon["amount"], addon["currency"]
        elif booking and not booking.get("ticket_number") and booking.get("total_fare"):
            _require_switch("ticketing_enabled")  # go-live switch: before the customer pays
            # Never take payment for a flight the airline has dropped — the
            # ticket could not be issued. Checked live, right now.
            problem, live_codes = _live_airline_check(ref.upper())
            if problem:
                if problem["status"] == segment_status.AIRLINE_CANCELLED:
                    _notify_airline_problem(ref.upper(), problem["codes"])
                raise HTTPException(status_code=409, detail=problem["message"])
            if {str(f)[:2].upper() for f in (live_codes or {})} & PAUSED_CARRIERS:
                raise HTTPException(status_code=409, detail=PAUSED_CARRIER_DETAIL)
            _release_if_schedule_changed(ref.upper(), live_codes)
            _release_if_fare_unticketable(ref.upper(), booking.get("fare_source"))
            # Seats/extras the airline only "requested" at booking but has
            # confirmed since are charged now (and paid with the ticket).
            try:
                if addon_billing.reconcile_held_booking(ref.upper())["confirmed"]:
                    booking = database.get_booking_by_locator(ref.upper())
            except Exception as e:
                logger.warning(f"Pending seats/extras check for {ref} failed (charging the saved total): {e}")
            amount, currency = float(booking["total_fare"]), booking.get("currency") or currency
        if (amount, currency) != (request.amount, request.currency):
            logger.warning(f"Payment init for {ref}: browser sent {request.currency} {request.amount}, "
                           f"charging the server-side {currency} {amount} instead.")
    try:
        result = services.init_payment(
            amount=amount,
            currency=currency,
            return_url=request.return_url,
            cancel_url=request.cancel_url,
            client_ref=request.client_ref,
            comment=request.comment,
        )
        if ref.startswith("ADD") and result.get("reqid"):
            database.set_addon_payment_started(ref, result["reqid"])
        return {"success": True, **result, "amount": amount, "currency": currency}
    except services.PayCorpError as e:
        logger.error(f"PayCorp init failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except httpx.HTTPStatusError as e:
        logger.error(f"PayCorp init HTTP error: {e.response.status_code} — {e.response.text}")
        raise HTTPException(status_code=502, detail=f"PayCorp gateway error: {e.response.status_code}")


# ── NDB Bank (CyberSource Secure Acceptance) pages ───────────────────────────
# Hosted Checkout takes a signed form POST, so the payment link from
# /api/payments/init opens this page, which posts the signed request to the
# bank. The bank sends the customer back by POSTing the (signed) result to the
# receipt page: the signature is checked, the result stored, and the browser
# sent to the site's return address with ?reqid=… — the same way as before,
# so ticketing still verifies the payment server-side before issuing.

@app.get("/api/payments/cybersource/redirect/{reqid}")
def cybersource_redirect(reqid: str):
    try:
        return Response(content=cybersource_service.checkout_form(reqid), media_type="text/html")
    except services.PayCorpError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/payments/cybersource/receipt")
async def cybersource_receipt(request: Request):
    form = {k: v for k, v in (await request.form()).items()}
    try:
        return RedirectResponse(await asyncio.to_thread(cybersource_service.handle_receipt, form), status_code=303)
    except services.PayCorpError as e:
        logger.error(f"NDB/CyberSource receipt refused: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/payments/complete")
def payments_complete(reqid: str = Query(..., description="reqid returned by /api/payments/init"), _admin: dict = Depends(auth.get_current_admin)):
    """
    Admin-only lookup of a hosted-page payment's final result (masked card,
    amounts, txn reference, auth code) by reqid. Nothing in this app's own
    flow calls this — ticket issuance verifies payment server-side via
    services.complete_payment() directly (see _verify_and_claim_payment),
    never through this route — so this was previously reachable by anyone
    with no authentication at all, returning full payment details for any
    reqid to anyone who had or guessed one. Gated to admin-only since it's a
    support/lookup tool, not part of the customer-facing payment flow.
    success=true iff response_code == '00'.
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
    ancillary_markup_mode: str = Field(default="percent", description="'percent' or 'fixed' -- Travelport ancillary (extra baggage/services) markup")
    ancillary_markup_percent: float = Field(default=0.0, ge=0)
    ancillary_markup_fixed: float = Field(default=0.0, ge=0)
    refund_charge_mode: str = Field(default="percent", description="'percent' or 'fixed' -- service charge deducted from a refund, on top of the airline's own fare-rule penalty")
    refund_charge_percent: float = Field(default=0.0, ge=0)
    refund_charge_fixed: float = Field(default=0.0, ge=0)


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
def put_pricing_settings(request: PricingSettingsRequest, admin: dict = Depends(auth.get_current_admin)):
    if (request.ticket_markup_mode not in ("percent", "fixed")
            or request.seat_markup_mode not in ("percent", "fixed")
            or request.amadeus_ticket_markup_mode not in ("percent", "fixed")
            or request.ancillary_markup_mode not in ("percent", "fixed")
            or request.refund_charge_mode not in ("percent", "fixed")):
        raise HTTPException(status_code=422, detail="markup_mode must be 'percent' or 'fixed'")
    # Four-eyes approval: this no longer applies immediately -- it queues for
    # a DIFFERENT admin to approve (see /api/admin/settings-change-requests).
    return database.create_settings_change_request("pricing", request.model_dump(), admin["admin_id"])


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
def put_loyalty_settings_admin(request: LoyaltySettingsRequest, admin: dict = Depends(auth.get_current_admin)):
    if request.tier_gold_threshold < request.tier_silver_threshold:
        raise HTTPException(status_code=422, detail="Gold threshold must be >= Silver threshold.")
    if request.tier_platinum_threshold < request.tier_gold_threshold:
        raise HTTPException(status_code=422, detail="Platinum threshold must be >= Gold threshold.")
    # Four-eyes approval: this no longer applies immediately -- see pricing-settings above.
    return database.create_settings_change_request("loyalty", request.model_dump(), admin["admin_id"])


# ── Settings Change Approval Queue (four-eyes for Pricing/Loyalty) ───────────

@app.get("/api/admin/settings-change-requests")
def admin_list_settings_change_requests(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    return {"requests": database.get_settings_change_requests(status_filter)}


class SettingsChangeReview(BaseModel):
    admin_note: Optional[str] = None


@app.post("/api/admin/settings-change-requests/{request_id}/approve")
def admin_approve_settings_change(request_id: int, body: SettingsChangeReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_settings_change_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    if req["proposed_by_admin_id"] == admin["admin_id"]:
        raise HTTPException(status_code=403, detail="You cannot approve your own change -- a different admin must review it.")
    data = req["proposed_data"]
    if req["setting_type"] == "pricing":
        database.update_pricing_settings(
            data["ticket_markup_mode"], data["ticket_markup_percent"], data["ticket_markup_fixed"],
            data["seat_markup_mode"], data["seat_markup_percent"], data["seat_markup_fixed"],
            data.get("markup_scope", "shared"),
            data.get("amadeus_ticket_markup_mode", "percent"), data.get("amadeus_ticket_markup_percent", 0.0), data.get("amadeus_ticket_markup_fixed", 0.0),
            data.get("ancillary_markup_mode", "percent"), data.get("ancillary_markup_percent", 0.0), data.get("ancillary_markup_fixed", 0.0),
            data.get("refund_charge_mode", "percent"), data.get("refund_charge_percent", 0.0), data.get("refund_charge_fixed", 0.0),
        )
    elif req["setting_type"] == "loyalty":
        database.update_loyalty_settings(
            data["points_per_lkr"], data["points_per_usd"],
            data["tier_silver_threshold"], data["tier_gold_threshold"], data["tier_platinum_threshold"],
        )
    else:
        raise HTTPException(status_code=500, detail=f"Unknown setting_type '{req['setting_type']}'.")
    return database.resolve_settings_change_request(request_id, "approved", admin["admin_id"], body.admin_note)


@app.post("/api/admin/settings-change-requests/{request_id}/reject")
def admin_reject_settings_change(request_id: int, body: SettingsChangeReview, admin: dict = Depends(auth.get_current_admin)):
    req = database.get_settings_change_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}.")
    if req["proposed_by_admin_id"] == admin["admin_id"]:
        raise HTTPException(status_code=403, detail="You cannot reject your own change -- a different admin must review it.")
    return database.resolve_settings_change_request(request_id, "rejected", admin["admin_id"], body.admin_note)


# ── Admin User Management ─────────────────────────────────────────────────────
# Needed so a second admin account can actually exist to approve the first
# admin's settings changes above -- previously the only way to create an
# admin was the seed_admin.py CLI script.

class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8, max_length=100)
    full_name: Optional[str] = Field(None, max_length=150)


@app.get("/api/admin/admins")
def admin_list_admins(_admin: dict = Depends(auth.get_current_admin)):
    return {"admins": database.list_admins()}


@app.post("/api/admin/admins", status_code=status.HTTP_201_CREATED)
def admin_create_admin(request: AdminCreateRequest, _admin: dict = Depends(auth.get_current_admin)):
    created = database.create_admin_if_not_exists(
        request.username.strip(), auth.hash_password(request.password), request.full_name,
    )
    if not created:
        raise HTTPException(status_code=409, detail="An admin with that username already exists.")
    return {"created": True, "username": request.username.strip()}


class LoyaltyTierBenefitCreate(BaseModel):
    tier: str = Field(..., pattern="^(Bronze|Silver|Gold|Platinum)$")
    benefit_text: str = Field(..., min_length=1, max_length=255)


class LoyaltyTierBenefitUpdate(BaseModel):
    benefit_text: str = Field(..., min_length=1, max_length=255)


@app.get("/api/admin/loyalty-tier-benefits")
def get_loyalty_tier_benefits_admin(_admin: dict = Depends(auth.get_current_admin)):
    return {"benefits": database.get_all_loyalty_tier_benefits()}


@app.post("/api/admin/loyalty-tier-benefits", status_code=status.HTTP_201_CREATED)
def create_loyalty_tier_benefit_admin(request: LoyaltyTierBenefitCreate, _admin: dict = Depends(auth.get_current_admin)):
    return database.create_loyalty_tier_benefit(request.tier, request.benefit_text.strip())


@app.put("/api/admin/loyalty-tier-benefits/{benefit_id}")
def update_loyalty_tier_benefit_admin(benefit_id: int, request: LoyaltyTierBenefitUpdate, _admin: dict = Depends(auth.get_current_admin)):
    row = database.update_loyalty_tier_benefit(benefit_id, request.benefit_text.strip())
    if not row:
        raise HTTPException(status_code=404, detail="Benefit not found.")
    return row


@app.delete("/api/admin/loyalty-tier-benefits/{benefit_id}")
def delete_loyalty_tier_benefit_admin(benefit_id: int, _admin: dict = Depends(auth.get_current_admin)):
    if not database.delete_loyalty_tier_benefit(benefit_id):
        raise HTTPException(status_code=404, detail="Benefit not found.")
    return {"deleted": True}


@app.post("/api/admin/loyalty-tier-benefits/{benefit_id}/move")
def move_loyalty_tier_benefit_admin(
    benefit_id: int,
    direction: str = Query(..., pattern="^(up|down)$"),
    _admin: dict = Depends(auth.get_current_admin),
):
    rows = database.move_loyalty_tier_benefit(benefit_id, direction)
    if rows is None:
        raise HTTPException(status_code=404, detail="Benefit not found.")
    return {"benefits": rows}


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
    """Supplier Alerts, each with its type, a one-line summary, the action to
    take, and route / travel dates split out (services/supplier_alerts.py)."""
    return {"notifications": supplier_alerts.review(database.get_provider_notifications(status_filter)),
            "kinds": {k: v[0] for k, v in supplier_alerts.KINDS.items()}}


class ProviderNotificationBulkAck(BaseModel):
    ids: List[int]
    admin_note: Optional[str] = None


@app.post("/api/admin/notifications/acknowledge-bulk")
def admin_acknowledge_notifications_bulk(body: ProviderNotificationBulkAck, admin: dict = Depends(auth.get_current_admin)):
    """Acknowledge many supplier alerts at once."""
    if not body.ids:
        raise HTTPException(status_code=422, detail="Select at least one alert.")
    if len(body.ids) > 1000:
        raise HTTPException(status_code=422, detail="At most 1000 alerts at a time.")
    return {"acknowledged": database.acknowledge_provider_notifications(body.ids, admin["admin_id"], body.admin_note)}


@app.post("/api/admin/notifications/{notification_id}/acknowledge")
def admin_acknowledge_notification(notification_id: int, body: ProviderNotificationReview, admin: dict = Depends(auth.get_current_admin)):
    note = database.get_provider_notification_by_id(notification_id)
    if not note:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return database.acknowledge_provider_notification(notification_id, admin["admin_id"], body.admin_note)


@app.get("/api/admin/stuck-bookings")
def admin_list_stuck_bookings(status_filter: Optional[str] = Query(None, alias="status"), _admin: dict = Depends(auth.get_current_admin)):
    """Ticketing Exceptions: bookings that need a person (paid but not
    ticketed, paid extra with no EMD, failed void/refund, airline problem,
    unpaid extra not removed). Each item carries its type and priority;
    open items our records show as fixed are closed first (services/
    ticketing_exceptions.py)."""
    items, closed = ticketing_exceptions.review(database.get_stuck_booking_notifications(status_filter))
    return {"notifications": items, "auto_resolved": closed,
            "kinds": {k: v[0] for k, v in ticketing_exceptions.KINDS.items()}}


class StuckBookingBulkAck(BaseModel):
    ids: List[int]
    admin_note: Optional[str] = None


@app.post("/api/admin/stuck-bookings/acknowledge-bulk")
def admin_acknowledge_stuck_bookings_bulk(body: StuckBookingBulkAck, admin: dict = Depends(auth.get_current_admin)):
    """Acknowledge many open items at once (e.g. sandbox test leftovers)."""
    if not body.ids:
        raise HTTPException(status_code=422, detail="Select at least one item.")
    if len(body.ids) > 1000:
        raise HTTPException(status_code=422, detail="At most 1000 items at a time.")
    return {"acknowledged": database.acknowledge_stuck_booking_notifications(body.ids, admin["admin_id"], body.admin_note)}


@app.post("/api/admin/stuck-bookings/{notification_id}/acknowledge")
def admin_acknowledge_stuck_booking(notification_id: int, body: ProviderNotificationReview, admin: dict = Depends(auth.get_current_admin)):
    rows = database.get_stuck_booking_notifications()
    if not any(r["id"] == notification_id for r in rows):
        raise HTTPException(status_code=404, detail="Notification not found.")
    return database.acknowledge_stuck_booking_notification(notification_id, admin["admin_id"], body.admin_note)


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


# ── Provider / module settings (go-live switches) ──────────────────────────
# search_mode lets an admin take Travelport or Amadeus out of the unified
# flight search entirely -- "travelport_only" / "amadeus_only" / "both" --
# read by /api/flights/search on every request (see search_flights above).
#
# hotels_enabled / visa_enabled / packages_enabled are the Nov 1 go-live
# switches: the launch ships Travelport flights only, with Hotels/Visa/
# Packages built but hidden from customers until each is ready. Hiding alone
# isn't enough -- the FEATURE_GATED_PREFIXES middleware below also blocks
# the underlying API routes server-side, so a disabled module can't be used
# via a direct request even while its nav tab is hidden. Flip the relevant
# flag back to true from the Admin Portal's Provider Control tab the moment
# that module is ready -- no deploy needed.

class ProviderSettingsRequest(BaseModel):
    search_mode: Literal["both", "travelport_only", "amadeus_only"]
    hotels_enabled: bool = True
    visa_enabled: bool = True
    packages_enabled: bool = True
    ticketed_cancel_enabled: bool = False  # staff void/refund (see cancel_booking)
    # Unpaid confirmed seats/extras on ticketed bookings (services/addon_deadlines.py)
    extras_pay_hours: int = Field(24, ge=1, le=168)
    extras_pay_before_departure_hours: int = Field(24, ge=0, le=168)
    # Automatic customer emails / SMS about seats/extras (services/customer_notify.py)
    notify_email_enabled: bool = True
    notify_sms_enabled: bool = True
    extras_reminder_hours: int = Field(6, ge=0, le=72)


@app.get("/api/admin/go-live-checks")
def admin_go_live_checks(_admin: dict = Depends(auth.get_current_admin)):
    """Production-readiness settings (see go_live_checks)."""
    checks = go_live_checks()
    return {"ready": all(c["ok"] for c in checks), "checks": checks}


@app.get("/api/admin/provider-settings")
def admin_get_provider_settings(_admin: dict = Depends(auth.get_current_admin)):
    return database.get_provider_settings()


@app.put("/api/admin/provider-settings")
def admin_put_provider_settings(body: ProviderSettingsRequest, _admin: dict = Depends(auth.get_current_admin)):
    if not go_live_switches.is_super_admin(_admin.get("admin_id")):
        # The void/refund switch is super-admin only (Go-live controls); an
        # ordinary admin saving this page keeps its current value.
        body.ticketed_cancel_enabled = bool(database.get_provider_settings().get("ticketed_cancel_enabled", False))
    return database.update_provider_settings(
        body.search_mode, body.hotels_enabled, body.visa_enabled, body.packages_enabled,
        body.ticketed_cancel_enabled, body.extras_pay_hours, body.extras_pay_before_departure_hours,
        body.notify_email_enabled, body.notify_sms_enabled, body.extras_reminder_hours,
    )


class GoLiveSwitchesRequest(BaseModel):
    flight_search_enabled: Optional[bool] = None
    pnr_create_enabled: Optional[bool] = None
    ticketing_enabled: Optional[bool] = None
    ticketed_cancel_enabled: Optional[bool] = None


@app.get("/api/admin/go-live-switches")
def admin_get_go_live_switches(admin: dict = Depends(auth.get_current_admin)):
    """Super-admin switches for go-live testing (services/go_live_switches.py)."""
    return {**go_live_switches.get(), "labels": go_live_switches.SWITCHES,
            "can_change": go_live_switches.is_super_admin(admin.get("admin_id"))}


@app.put("/api/admin/go-live-switches")
def admin_put_go_live_switches(body: GoLiveSwitchesRequest, admin: dict = Depends(auth.get_current_admin)):
    if not go_live_switches.is_super_admin(admin.get("admin_id")):
        raise HTTPException(status_code=403, detail="Only a super admin can change these switches.")
    values = go_live_switches.update(body.model_dump(), admin.get("sub"))
    return {**values, "labels": go_live_switches.SWITCHES, "can_change": True}


@app.get("/api/feature-flags")
def public_feature_flags():
    """Public (no-auth) read of the go-live module switches, for the
    customer frontend to decide which nav tabs to show. Server-side
    enforcement of the same flags lives in FEATURE_GATED_PREFIXES below --
    this endpoint only drives what the UI offers, not what the API accepts."""
    settings = database.get_provider_settings()
    search_mode = settings.get("search_mode", "travelport_only")
    return {
        "flights_amadeus_enabled": search_mode in ("both", "amadeus_only"),
        "hotels_enabled": settings.get("hotels_enabled", False),
        "visa_enabled": settings.get("visa_enabled", False),
        "packages_enabled": settings.get("packages_enabled", False),
    }


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
    adults_count: int = Field(default=1, ge=1, le=50)
    children_count: int = Field(default=0, ge=0, le=50)
    infants_count: int = Field(default=0, ge=0, le=50)
    preferred_date: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=1000)
    pdpa_consent: bool = Field(..., description="Must be true -- customer must explicitly agree to the PDPA data-collection disclosure before submitting")


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
    if not request.pdpa_consent:
        raise HTTPException(status_code=422, detail="You must agree to the data collection disclosure before submitting.")
    total_travelers = request.adults_count + request.children_count + request.infants_count
    return database.create_package_booking_request(
        package_id, customer_id, request.full_name.strip(), str(request.email),
        request.phone.strip(), total_travelers, request.preferred_date, request.notes,
        request.adults_count, request.children_count, request.infants_count, request.pdpa_consent,
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
    cancellation_policy: Optional[str] = Field(None, max_length=1000)


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


# ── Terminal Connection Times (B2C, admin-curated reference lookup) ──────────
# Travelport doesn't return terminal-to-terminal walk/transfer times at
# connecting airports, so this is a manually-maintained reference table
# (same pattern as Visa Requirements above) with its own standalone B2C
# lookup page -- not shown automatically in search results.

class TerminalConnectionTimeInput(BaseModel):
    country: str = Field(..., min_length=2, max_length=100)
    airport_code: str = Field(..., min_length=3, max_length=10)
    airport_name: Optional[str] = Field(None, max_length=150)
    from_terminal: str = Field(..., min_length=1, max_length=20)
    to_terminal: str = Field(..., min_length=1, max_length=20)
    estimated_minutes: int = Field(..., ge=0, le=600)
    notes: Optional[str] = Field(None, max_length=500)


@app.get("/api/terminal-connections/countries")
def list_terminal_connection_countries():
    """Public — countries that actually have documented entries, for the B2C lookup page's Country dropdown."""
    return {"countries": database.get_terminal_connection_countries()}


@app.get("/api/terminal-connections")
def check_terminal_connections(country: Optional[str] = None, airport_code: Optional[str] = None):
    """Public lookup — returns an empty list rather than 404 when nothing is documented for that country/airport, since that's an expected, normal case."""
    return {"connections": database.get_terminal_connection_times(country, airport_code)}


@app.get("/api/admin/terminal-connections")
def admin_list_terminal_connections(_admin: dict = Depends(auth.get_current_admin)):
    return {"connections": database.get_terminal_connection_times()}


@app.post("/api/admin/terminal-connections", status_code=status.HTTP_201_CREATED)
def admin_create_terminal_connection(request: TerminalConnectionTimeInput, _admin: dict = Depends(auth.get_current_admin)):
    return database.create_terminal_connection_time(request.model_dump())


@app.put("/api/admin/terminal-connections/{entry_id}")
def admin_update_terminal_connection(entry_id: int, request: TerminalConnectionTimeInput, _admin: dict = Depends(auth.get_current_admin)):
    updated = database.update_terminal_connection_time(entry_id, request.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Terminal connection entry not found.")
    return updated


@app.delete("/api/admin/terminal-connections/{entry_id}")
def admin_delete_terminal_connection(entry_id: int, _admin: dict = Depends(auth.get_current_admin)):
    deleted = database.delete_terminal_connection_time(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Terminal connection entry not found.")
    return {"message": f"Terminal connection entry {entry_id} deleted."}


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
    hotel_database.link_guest_hotel_bookings_by_email(customer["id"], customer["email"])
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
    hotel_database.link_guest_hotel_bookings_by_email(customer["id"], customer["email"])
    token = auth.create_access_token({
        "sub": customer["email"], "role": "customer", "customer_id": customer["id"],
    })
    return {
        "access_token": token, "token_type": "bearer",
        "customer": {"email": customer["email"], "full_name": customer["full_name"]},
    }


# ── Google / Microsoft Sign-In (Customer Portal) ──────────────────────────────
# See services/customer_oauth_service.py for the OAuth exchange itself.
# Both endpoints are full-page redirects (no popup/JS SDK) -- the frontend
# just points window.location at /start, and /callback lands the browser
# back on the Customer Portal with a finished access token in the URL
# (App.jsx's oauth_token handler picks it up and clears the URL).

@app.get("/api/customers/oauth/{provider}/start")
def customer_oauth_start(provider: str):
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=404, detail="Unknown sign-in provider.")
    if not services.oauth_is_configured(provider):
        return RedirectResponse(f"{AuthConfig.FRONTEND_BASE_URL}/account?oauth_error=not_configured")
    state = services.oauth_make_state()
    redirect_uri = f"{AuthConfig.OAUTH_REDIRECT_BASE}/api/customers/oauth/{provider}/callback"
    try:
        url = services.oauth_build_authorize_url(provider, redirect_uri, state)
    except services.OAuthNotConfigured:
        return RedirectResponse(f"{AuthConfig.FRONTEND_BASE_URL}/account?oauth_error=not_configured")
    return RedirectResponse(url)


@app.get("/api/customers/oauth/{provider}/callback")
def customer_oauth_callback(provider: str, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    fail_redirect = f"{AuthConfig.FRONTEND_BASE_URL}/account?oauth_error=failed"
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=404, detail="Unknown sign-in provider.")
    if error or not code or not state or not services.oauth_verify_state(state):
        logger.warning(f"Customer OAuth ({provider}) callback rejected: error={error}, has_code={bool(code)}, state_valid={bool(state and services.oauth_verify_state(state))}")
        return RedirectResponse(fail_redirect)

    redirect_uri = f"{AuthConfig.OAUTH_REDIRECT_BASE}/api/customers/oauth/{provider}/callback"
    try:
        profile = services.oauth_exchange_code_for_profile(provider, code, redirect_uri)
    except services.OAuthExchangeError as e:
        logger.error(f"Customer OAuth ({provider}) exchange failed: {e}")
        return RedirectResponse(fail_redirect)

    email = profile["email"]
    customer = database.get_customer_by_email(email)
    if not customer:
        # OAuth-only account: a random, never-disclosed password hash fills
        # the NOT NULL column -- this customer can only ever sign in via
        # Google/Microsoft (or an admin-assisted password reset), same
        # pattern as any "social login" account with no password of its own.
        placeholder_hash = auth.hash_password(uuid.uuid4().hex)
        customer = database.create_customer(email, placeholder_hash, profile.get("name") or email, None)
    # Same auto-claim as email/password login+register -- a booking made as a
    # guest under this email, before this OAuth account existed, is picked
    # up right now instead of waiting for a future email/password login.
    database.link_guest_bookings_by_email(customer["id"], customer["email"])
    hotel_database.link_guest_hotel_bookings_by_email(customer["id"], customer["email"])

    token = auth.create_access_token({
        "sub": customer["email"], "role": "customer", "customer_id": customer["id"],
    })
    params = urlencode({"oauth_token": token, "oauth_email": customer["email"], "oauth_name": customer["full_name"]})
    # In the URL fragment (#), not the query: the fragment is never sent to any
    # server or put in Referer headers, so the token stays out of logs.
    return RedirectResponse(f"{AuthConfig.FRONTEND_BASE_URL}/account#{params}")


@app.get("/api/customers/me/bookings")
def customer_my_bookings(current: dict = Depends(auth.get_current_customer)):
    bookings = database.get_bookings_for_customer(current["customer_id"])
    return {"bookings": bookings, "count": len(bookings)}


# Passengers from this customer's earlier bookings, newest first, so the
# Passenger Details form can fill them in after sign-in. Each person once
# (same name + date of birth); special requests (meal / wheelchair) are left
# out on purpose -- those are asked fresh for every trip.
_SAVED_TRAVELLER_FIELDS = (
    "first_name", "last_name", "date_of_birth", "gender",
    "passport_number", "passport_expiry", "nationality", "passport_issue_country",
    "email", "phone", "birth_place", "document_issue_date", "issued_for_geo_political_area",
    "phone_extension", "phone_area_city_code", "phone_city_code",
    "address_street", "address_city", "address_state_name", "address_state_value",
    "address_country", "address_postal_code",
)


@app.get("/api/customers/me/saved-travellers")
def customer_saved_travellers(current: dict = Depends(auth.get_current_customer)):
    seen, travellers = set(), []
    for b in database.get_bookings_for_customer(current["customer_id"]):
        for t in (b.get("travelers") if isinstance(b.get("travelers"), list) else []):
            if not isinstance(t, dict) or not t.get("first_name") or not t.get("last_name"):
                continue
            key = (str(t["first_name"]).strip().upper(), str(t["last_name"]).strip().upper(), t.get("date_of_birth") or "")
            if key in seen:
                continue
            seen.add(key)
            travellers.append({
                **{f: t.get(f) for f in _SAVED_TRAVELLER_FIELDS if t.get(f) not in (None, "")},
                "passenger_type": t.get("passenger_type") or "ADT",
            })
            if len(travellers) >= 20:
                return {"travellers": travellers}
    return {"travellers": travellers}


# Travelport's view of a booking -> the status shown in My Bookings. A
# confirmed/ticketed booking keeps its own status (Confirmed / Ticketed).
_LIVE_LIST_STATUS = {
    "airline_cancelled": segment_status.AIRLINE_CANCELLED,
    "airline_problem": segment_status.AWAITING_AIRLINE,
    "cancelled": "Cancelled",
}


def _last_departure(b: dict) -> str:
    legs = b.get("legs") if isinstance(b.get("legs"), list) else []
    return str((legs[-1].get("departure_time") if legs else None) or b.get("departure_time") or "")


@app.post("/api/customers/me/bookings/refresh-status")
def customer_refresh_booking_status(current: dict = Depends(auth.get_current_customer)):
    """
    My Bookings asks this right after loading the list: every upcoming,
    not-cancelled booking is retrieved live from Travelport (flights and the
    airline's segment status only — no ticket calls) and its list status
    comes back from that, so an airline cancellation shows in the list
    itself. Bookings still between Confirmed / Awaiting Airline / Airline
    Cancelled are saved back; ticketed and staff-cancelled ones are never
    rewritten here (database.set_airline_status).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    due = [b for b in database.get_bookings_for_customer(current["customer_id"])
           if b.get("locator_code") and b.get("status") not in database.CANCELLED_STATUSES
           and _last_departure(b)[:10] >= today]

    def check(b: dict) -> tuple[str, dict]:
        locator = b["locator_code"]
        try:
            live = live_booking_service.get_live_booking(locator, with_coupons=False)
        except Exception as e:
            # 4247: Travelport no longer holds the PNR — worth flagging on an upcoming trip.
            return locator, {"checked": False, "missing": "DOES NOT EXIST" in str(e).upper(), "error": str(e)[:200]}
        state = live.get("booking_status")
        status = _LIVE_LIST_STATUS.get(state) or (
            "Confirmed" if b.get("status") in database.AIRLINE_STATUSES else b.get("status"))
        if status in database.AIRLINE_STATUSES:
            try:
                database.set_airline_status(locator, status)
            except Exception as db_err:
                logger.error(f"Could not save live status for {locator}: {db_err}")
        return locator, {
            "checked": True, "status": status, "retrieved_at": live.get("retrieved_at"),
            "flights": {f["flight"]: f.get("airline_status_code") for f in live.get("flights") or []},
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(pool.map(check, due))
    return {"statuses": results, "checked": sum(1 for r in results.values() if r.get("checked")), "upcoming": len(due)}

# ── Booking Status / Manage My Booking (Customer Portal) ──────────────────────
# Booking Status looks a booking up by PNR or ticket number AND the
# passenger's last name, live from Travelport (services/
# manage_booking_service.py) — it does not need the booking in our database
# or on this account. A match returns a 30-minute manage token; every
# Manage My Booking call needs it, and re-reads the booking live.

class BookingStatusLookupRequest(BaseModel):
    query: str = Field(..., min_length=5, max_length=20)
    last_name: str = Field(..., min_length=1, max_length=60)


class ManageTokenRequest(BaseModel):
    manage_token: str


class ManageSpecialRequestItem(BaseModel):
    traveler_id: str
    code: str


class ManageSpecialRequestsRequest(ManageTokenRequest):
    requests: List[ManageSpecialRequestItem]


class ManageSeatItem(BaseModel):
    traveler_id: str
    flight_number: str
    seat_number: str


class ManageExtraItem(BaseModel):
    traveler_id: str
    airline_name: str
    segment_numbers: List[int] = []
    name: Optional[str] = None


class ManageSeatsExtrasRequest(ManageTokenRequest):
    seats: List[ManageSeatItem] = []
    extras: List[ManageExtraItem] = []


class AddonPaymentCompleteRequest(BaseModel):
    addon_id: str
    reqid: str


LOOKUP_NOT_FOUND = ("We couldn't find a booking with that reference and last name. Check the PNR or ticket number "
                    "and the passenger's last name as on the ticket.")
LOOKUP_UNAVAILABLE = ("The airline's booking system didn't answer just now, so we couldn't open your booking. "
                      "Your booking is not affected — please try again in a minute.")


@app.post("/api/booking-status/lookup")
def booking_status_lookup(request: BookingStatusLookupRequest, current: dict = Depends(auth.get_current_customer)):
    with services.flow_trace_id() as trace_id:
        try:
            live = manage_booking_service.lookup(request.query, request.last_name)
        except manage_booking_service.NotFound as e:
            raise HTTPException(status_code=404, detail=str(e) or LOOKUP_NOT_FOUND)
        except (manage_booking_service.Unavailable, services.CancelError) as e:
            logger.warning(f"Booking lookup {request.query}: airline system unavailable — {e} (TraceId {trace_id})")
            raise HTTPException(status_code=503, detail=LOOKUP_UNAVAILABLE)
    live["trace_id"] = trace_id
    live["manage_token"] = auth.create_manage_booking_token(live["locator_code"], current["customer_id"])
    # Seats card: requests the airline refused (never on the PNR) with the
    # reason, and each flight's seat-selection time limit when the airline has one.
    live["seat_requests"] = seat_request_log.view(live["locator_code"], live)
    live["seat_rules"] = [c for c in (seat_rules.condition_for(f.get("flight"), f.get("from"), f.get("departure"))
                                      for f in live.get("flights") or []) if c]
    # Confirmed seats/extras without payment: deadlines started, overdue ones
    # released from the PNR, the rest shown as payment due.
    try:
        if live["ticketed"]:
            check = addon_deadlines.check_booking(live["locator_code"])
            if check["released"]:
                fresh = live_booking_service.get_live_booking(live["locator_code"], with_coupons=False)
                live.update({k: fresh.get(k) for k in ("seats", "extras", "special_services", "retrieved_at")})
                live["released_extras"] = [h["description"] or h["kind"] for h in check["released"]]
            due = manage_booking_service.without_paused_extras(
                addon_billing.payable_after_ticketing(live["locator_code"]), live)
            # No "Pay" for an airline whose EMDs are paused: the items are
            # released at their deadline instead, never charged.
            if due["items"] and not manage_booking_service.emd_paused_note(live):
                live["payment_due"] = _payment_due_view(due)
        else:
            addon_billing.reconcile_held_booking(live["locator_code"])
    except Exception as e:
        logger.warning(f"Pending seats/extras check for {live['locator_code']} failed: {e}")
    try:
        live["extras_invoices"] = _paid_addon_invoices(live["locator_code"], current["customer_id"])
    except Exception as e:
        logger.warning(f"Extras invoices for {live['locator_code']} failed: {e}")
    return live


def _item_name(i: dict) -> str:
    return i.get("name") or i.get("description") or (f"Seat {i.get('seat_number')}" if i.get("seat_number") else "Extra")


def _payment_due_view(due: dict) -> dict:
    deadlines = [i["deadline_utc"] for i in due["items"] if i.get("deadline_utc")]
    return {"amount": due["amount"], "currency": due["currency"], "deadline_utc": min(deadlines) if deadlines else None,
            "items": [{"name": _item_name(i), "price": i["price"], "deadline_utc": i.get("deadline_utc")} for i in due["items"]]}


def _manage_context(token: str, current: dict) -> tuple[str, dict]:
    claims = auth.decode_manage_booking_token(token)
    if not claims or claims.get("customer_id") != current["customer_id"]:
        raise HTTPException(status_code=401, detail="Please look the booking up again — the check has expired.")
    locator = claims["locator"]
    try:
        live = live_booking_service.get_live_booking(locator, with_coupons=False)
    except (live_booking_service.LiveBookingError, services.CancelError) as e:
        raise HTTPException(status_code=502, detail=f"Travelport: {e}")
    if live.get("booking_status") not in ("confirmed", "ticketed"):
        raise HTTPException(status_code=409, detail="This booking can't be changed online — the airline hasn't confirmed every flight.")
    live["ticketed"] = bool(live.get("tickets"))
    return locator, live


@app.post("/api/manage-booking/options")
def manage_booking_options(request: ManageTokenRequest, current: dict = Depends(auth.get_current_customer)):
    locator, live = _manage_context(request.manage_token, current)
    with services.flow_trace_id() as trace_id:
        try:
            options = manage_booking_service.get_options(locator, live)
        except manage_booking_service.Refused as e:
            raise HTTPException(status_code=409, detail=str(e))
        except services.CancelError as e:
            raise HTTPException(status_code=502, detail=f"Travelport didn't answer: {e} (TraceId {trace_id})")
    options.update(trace_id=trace_id, travelers=live.get("travelers"), flights=live.get("flights"),
                   ticketed=live["ticketed"], content_source=live.get("content_source"))
    return options


@app.post("/api/manage-booking/special-requests")
def manage_booking_special_requests(request: ManageSpecialRequestsRequest, current: dict = Depends(auth.get_current_customer)):
    locator, live = _manage_context(request.manage_token, current)
    with services.flow_trace_id() as trace_id:
        try:
            rows = manage_booking_service.add_special_requests(locator, live, [r.model_dump() for r in request.requests])
        except manage_booking_service.Refused as e:
            raise HTTPException(status_code=409, detail=str(e))
        except services.CancelError as e:
            raise HTTPException(status_code=502, detail=f"Travelport didn't answer: {e} (TraceId {trace_id})")
        after = live_booking_service.get_live_booking(locator, with_coupons=False)
    return {"results": rows, "special_services": after.get("special_services"), "trace_id": trace_id}


@app.post("/api/manage-booking/seats-extras")
def manage_booking_seats_extras(request: ManageSeatsExtrasRequest, current: dict = Depends(auth.get_current_customer)):
    """
    Book seats/extras on the PNR (Travelport's live price, never the
    browser's). Free items are done. Paid items:
    - ticketed booking: a pending add-on payment is created with the amount
      fixed server-side; the customer pays by card, then EMDs are issued
      (/api/manage-booking/complete-payment);
    - held booking made here: added to the booking total, paid with the
      ticket in the normal flow.
    """
    locator, live = _manage_context(request.manage_token, current)
    if live.get("content_source") == "NDC":
        raise HTTPException(status_code=409, detail=manage_booking_service.NDC_ADDONS_REFUSED)
    cached = database.get_booking_by_locator(locator)
    if not live["ticketed"] and not cached:
        raise HTTPException(status_code=409, detail="Seats and extras for this booking have to be arranged with our team — please contact us.")
    travelers = manage_booking_service.travelers_of(live)
    idx = {t["id"]: i for i, t in enumerate(travelers)}
    seats = [{"passenger_idx": idx[s.traveler_id], "flight_number": s.flight_number, "seat_number": s.seat_number, "price": 0}
             for s in request.seats if s.traveler_id in idx]
    extras = [{"passenger_idx": idx[x.traveler_id], "airline_name": x.airline_name, "segment_numbers": x.segment_numbers,
               "name": x.name or x.airline_name, "price": 0} for x in request.extras if x.traveler_id in idx]
    if not seats and not extras:
        raise HTTPException(status_code=422, detail="Nothing to add.")

    with services.flow_trace_id() as trace_id:
        # Paid seats/extras paused for an airline whose EMDs Travelport refuses
        # (manage_booking_service.EMD_PAUSED_CARRIERS): only free items are booked.
        result = services.add_seats_and_extras_to_pnr(locator, travelers, seats, extras,
                                                      free_only=manage_booking_service.emd_paused_note(live),
                                                      free_extras_only=manage_booking_service.extras_paused_note(live))
    items = result["seats"] + result["extras"]
    for i in items:  # the Manage My Booking wording for workbench_service.customer_seat_reason()
        reason = (i.get("reason") or "").upper()
        if "ALREADY HAS A SEAT" in reason:
            i["reason"] = "This passenger already has a seat on this flight. Changing a seat online isn't available yet — please contact us."
        elif "WON'T SELL THIS SEAT" in reason:
            i["reason"] = ("The airline won't sell this seat to this passenger (for example with a wheelchair request, "
                           "or a seat it keeps for other travellers). Please choose another seat or contact us.")
        elif "DIDN'T ACCEPT THIS SEAT ONLINE" in reason:
            i["reason"] = ("The airline could not confirm this seat for your selected fare category. You have not been "
                           "charged for the seat. You can select your seat directly with the airline during check-in.")
        elif "COMMENTS MUST BE PROVIDED" in reason:
            i["reason"] = ("The airline needs details of this item (for example, what the equipment is) before it can add "
                           "it, and these can't be sent online. You have not been charged — please contact the airline to add it.")
    seat_request_log.record(locator, travelers, result["seats"], "manage")
    paid = [i for i in items if i["status"] == "confirmed" and (i.get("price") or 0) > 0]
    # "Requested" (pending) items are kept so they're billed if the airline
    # confirms them later (services/addon_billing.py) — never charged before.
    pending = [i for i in items if i["status"] == "pending" and i.get("offer_id")]
    response = {"seats": result["seats"], "extras": result["extras"], "trace_id": trace_id, "payment_required": False}
    if not paid and not pending:
        return response
    if pending:
        # Email + SMS: the request is with the airline; its answer
        # (payment_due / refused) follows from the extras sweep.
        customer_notify.notify(locator, "requested", [{"key": i["offer_id"], "name": i.get("name") or (f"Seat {i['seat_number']}" if i.get("seat_number") else "Extra"),
                                                    "price": i.get("price"), "currency": i.get("currency")} for i in pending])
        response["airline_pending"] = True
    res_after = None
    if live["ticketed"]:
        # Travelport renumbers offer ids as items come and go; its offer
        # Identifier is what the payment and the deadline track.
        try:
            res_after = addon_billing.retrieve(locator)
            states = addon_billing.offer_states(res_after)
            for i in paid + pending:
                if i["offer_id"] in states:
                    i["offer_key"] = states[i["offer_id"]]["key"]
                    # What the item is, for airlines that renumber Identifiers on every retrieve.
                    i["offer_sig"] = states[i["offer_id"]]["sig"]
        except services.CancelError as e:
            logger.warning(f"{locator}: could not read offer identifiers after adding seats/extras: {e}")

    amount = round(sum(i["price"] for i in paid), 2)
    supplier_total = round(sum(i.get("supplier_price") or 0 for i in paid), 2)
    currency = (paid or pending)[0].get("currency") or "LKR"
    if live["ticketed"]:
        if pending:
            database.create_addon_payment(
                f"ADD{uuid.uuid4().hex[:17].upper()}", locator, current["customer_id"], pending,
                [i["offer_id"] for i in pending], round(sum(i.get("price") or 0 for i in pending), 2),
                round(sum(i.get("supplier_price") or 0 for i in pending), 2), currency, status="awaiting_airline")
        if paid:
            addon_id = f"ADD{uuid.uuid4().hex[:17].upper()}"
            database.create_addon_payment(addon_id, locator, current["customer_id"], paid,
                                          [i["offer_id"] for i in paid], amount, supplier_total, currency)
            response.update(payment_required=True, addon_id=addon_id, amount=amount, currency=currency)
            try:
                check = addon_deadlines.check_booking(locator, res=res_after, release_overdue=False)
                deadlines = [h["deadline_utc"] for h in check["holds"] if h["offer_key"] in {i.get("offer_key") for i in paid}]
                if deadlines:
                    response["deadline_utc"] = min(deadlines).isoformat() + "Z"
            except Exception as e:
                logger.warning(f"{locator}: could not start payment deadlines: {e}")
        return response

    # Held booking made through us: confirmed items are charged with the
    # ticket (issue_ticket pays the confirmed offers in addon_results);
    # pending ones wait in addon_results for the airline's answer.
    raw = json.loads(cached.get("raw_ticket_json") or "{}") or dict(cached)
    addons = raw.get("addon_results") or {"seats": [], "extras": []}
    keep = paid + pending
    addons["seats"] = (addons.get("seats") or []) + [i for i in keep if i in result["seats"]]
    addons["extras"] = (addons.get("extras") or []) + [i for i in keep if i in result["extras"]]
    raw.update(addon_results=addons, total_fare=round(float(raw.get("total_fare") or 0) + amount, 2),
               seat_charge=round(float(raw.get("seat_charge") or 0) + amount, 2))
    database.save_booking(raw)
    if paid:
        response.update(added_to_booking_total=amount, currency=currency, new_total=raw["total_fare"])
    return response


@app.post("/api/manage-booking/pay-due")
def manage_booking_pay_due(request: ManageTokenRequest, current: dict = Depends(auth.get_current_customer)):
    """Ticketed booking: seats/extras the airline confirmed after they were
    requested, still without an EMD. Priced now from Travelport; creates the
    add-on payment the customer then pays (same PayCorp + EMD flow)."""
    locator, live = _manage_context(request.manage_token, current)
    if not live["ticketed"]:
        raise HTTPException(status_code=409, detail="This booking isn't ticketed yet — confirmed seats and extras are paid with the ticket.")
    paused = manage_booking_service.emd_paused_note(live)
    if paused:  # no payment taken for an EMD Travelport would refuse
        raise HTTPException(status_code=409, detail=paused)
    with services.flow_trace_id() as trace_id:
        addon_deadlines.check_booking(locator)  # overdue items are released, not offered
        with addon_deadlines.locator_lock(locator):
            due = manage_booking_service.without_paused_extras(addon_billing.payable_after_ticketing(locator), live)
            if not due["items"]:
                raise HTTPException(status_code=409, detail="Nothing is waiting for payment on this booking.")
            # An earlier add-on payment for the same items that the customer
            # left unpaid is closed, so the items can only be paid once.
            keys = {i["offer_key"] for i in due["items"]}
            for row in database.get_addon_payments_for_locator(locator):
                if row["status"] == "awaiting_payment" and keys & {i.get("offer_key") for i in row["items"]}:
                    database.finish_addon_payment(row["addon_id"], "superseded", {"reason": "replaced by a new payment"})
            addon_id = f"ADD{uuid.uuid4().hex[:17].upper()}"
            database.create_addon_payment(addon_id, locator, current["customer_id"], due["items"],
                                          [i["offer_id"] for i in due["items"]], due["amount"], due["supplier_total"], due["currency"])
    return {"payment_required": True, "addon_id": addon_id, "amount": due["amount"], "currency": due["currency"],
            "items": due["items"], "deadline_utc": _payment_due_view(due)["deadline_utc"], "trace_id": trace_id}


@app.post("/api/manage-booking/complete-payment")
def manage_booking_complete_payment(request: AddonPaymentCompleteRequest, current: dict = Depends(auth.get_current_customer)):
    """After PayCorp: verify the card payment for this add-on (amount and
    reference), then pay Travelport so the airline issues the EMDs. A failure
    after payment raises a stuck-booking alert for a refund."""
    addon = database.get_addon_payment(request.addon_id)
    if not addon or addon.get("customer_id") != current["customer_id"]:
        raise HTTPException(status_code=404, detail="Payment not found.")
    locator = addon["locator_code"]
    # So the portal can open the paid invoice and show the booking (with the
    # new extras and EMDs) straight after payment: the customer's own
    # booking reference and lead passenger last name (Booking Status lookup).
    shown = {"locator_code": locator, "last_name": _lead_last_name(locator)}
    # Under the PNR's lock: the deadline sweep can't release these items
    # between the check below and the EMDs being issued.
    with addon_deadlines.locator_lock(locator):
        addon = database.get_addon_payment(request.addon_id)
        if addon["status"] != "awaiting_payment":
            result = json.loads(addon.get("result_json") or "{}")
            return {"status": addon["status"], "result": result, "emds": result.get("emds") or [],
                    "addon_id": addon["addon_id"], **shown}
        # Checked BEFORE the PayCorp payment is completed (no money is taken
        # until then): an item released or no longer confirmed isn't charged.
        try:
            addon_deadlines.check_payable(locator, addon["items"], addon["supplier_total"], at_completion=True)
        except addon_deadlines.NotPayable as e:
            database.finish_addon_payment(addon["addon_id"], "expired", {"reason": str(e)})
            raise HTTPException(status_code=409, detail=f"{e} You have not been charged.")
        except services.CancelError as e:
            logger.error(f"Add-on {addon['addon_id']}: live check before completing payment failed: {e}")
            raise HTTPException(status_code=503, detail="We couldn't check your booking with the airline just now. "
                                                        "You have not been charged — please try again in a moment.")
        payment = _verify_and_claim_payment(request.reqid, expected_client_ref=addon["addon_id"], locator_code=locator,
                                            expected_amount=addon["amount"], expected_currency=addon["currency"])
        with services.flow_trace_id() as trace_id:
            try:
                issued = manage_booking_service.issue_emds(locator, addon["items"], addon["supplier_total"], addon["currency"])
            except (manage_booking_service.Refused, services.CancelError) as e:
                database.finish_addon_payment(addon["addon_id"], "emd_failed", {"error": str(e), "trace_id": trace_id})
                _notify_stuck_booking(locator, f"Add-on paid but EMD not issued: {e} (TraceId {trace_id})",
                                      payment.get("txn_reference"))
                raise HTTPException(status_code=502, detail="Your payment was received but the airline didn't issue the document yet. "
                                                            "Our team has been alerted and will complete it or refund you.")
            addon_deadlines.mark_paid(locator, addon["items"], issued["emds"])
    database.finish_addon_payment(addon["addon_id"], "completed", {**issued, "trace_id": trace_id,
                                                                    "payment_reference": payment.get("txn_reference")})
    # Finance: a Sales Invoice in ERPNext for these seats/extras, in the
    # background so the customer's page isn't kept waiting (best-effort,
    # the result is kept on the add-on payment).
    threading.Thread(target=_push_addon_to_erp, args=(addon["addon_id"],), name="erp-addon", daemon=True).start()
    return {"status": "completed", "emds": issued["emds"], "addon_id": addon["addon_id"], "trace_id": trace_id, **shown}


def _push_addon_to_erp(addon_id: str) -> None:
    """Paid seats/extras -> ERPNext Sales Invoice (same lines and EMDs as the
    customer's own invoice). Never raises."""
    if not services.erp_configured():
        return
    try:
        addon = database.get_addon_payment(addon_id)
        if not addon or addon["status"] != "completed" or addon.get("erp_invoice_name"):
            return
        booking = database.get_booking_by_locator(addon["locator_code"]) or {}
        customer = database.get_customer_by_id(addon["customer_id"]) if addon.get("customer_id") else None
        inv = _addon_invoice(addon, customer)
        # Seats carry their flight as flight_number (extras use segment
        # numbers), and an item the EMD wording didn't match gets the
        # payment's EMD of the same kind (seat / bag / meal / lounge).
        spare = list((json.loads(addon.get("result_json") or "{}") or {}).get("emds") or [])
        taken = {l.get("emd") for l in inv["lines"] if l.get("emd")}
        spare = [x for x in spare if x.get("number") not in taken]
        kinds = (("SEAT", ("SEAT",)), ("BAG", ("BAG", "KG", "LB")), ("MEAL", ("MEAL",)), ("LOUNGE", ("LOUNGE",)))
        for line, item in zip(inv["lines"], addon["items"]):
            line["supplier_amount"] = item.get("supplier_price")  # airline's price; markup is shown separately
            if not line.get("flights") and item.get("flight_number"):
                line["flights"] = [{"flight": item["flight_number"]}]
            if not line.get("emd"):
                text = f"{line.get('description') or ''} {line.get('airline_description') or ''}".upper()
                for _, words in kinds:
                    if any(w in text for w in words):
                        hit = next((x for x in spare if any(w in (x.get("description") or "").upper() for w in words)), None)
                        if hit:
                            line["emd"] = hit["number"]
                            spare.remove(hit)
                        break
        invoice_name = services.push_addon_to_erp(inv, booking)
        database.mark_addon_erp_synced(addon_id, invoice_name)
    except Exception as e:
        logger.warning(f"ERP sync failed for extras {addon_id} (non-fatal): {e}")
        try:
            database.mark_addon_erp_sync_failed(addon_id, str(e))
        except Exception as db_err:
            logger.error(f"Could not record ERP sync failure for extras {addon_id}: {db_err}")


def _lead_last_name(locator: str) -> str:
    """Lead passenger's last name from our booking record ('' if unknown)."""
    try:
        b = database.get_booking_by_locator(locator) or {}
        raw = json.loads(b.get("raw_ticket_json") or "{}") or {}
        t = ((b.get("travelers") or raw.get("travelers") or [{}])[0]) or {}
        return (t.get("last_name") or t.get("surname") or (b.get("passenger_name") or "").strip().split(" ")[-1] or "").strip()
    except Exception:
        return ""


# ── Invoice for seats/extras paid after booking ───────────────────────────────

def _flat_segments(raw: dict) -> list:
    """Flights of a saved booking in itinerary order (segment_numbers are
    1-based positions in this list)."""
    out = []
    for leg in raw.get("legs") or [raw]:
        out += leg.get("segments") or [leg]
    return out


def _addon_invoice(addon: dict, customer: dict | None) -> dict:
    result = json.loads(addon.get("result_json") or "{}")
    cached = database.get_booking_by_locator(addon["locator_code"]) or {}
    raw = json.loads(cached.get("raw_ticket_json") or "{}") or dict(cached)
    segs = _flat_segments(raw)
    travelers = raw.get("travelers") or []
    emds = list(result.get("emds") or [])
    # One EMD can cover several items (both bags of one payment share it),
    # and the issue answer names only the first: the live booking shows the
    # EMD on every item, so it is read from there when Travelport answers.
    live_emds: dict = {}
    try:
        for x in live_booking_service.get_live_booking(addon["locator_code"], with_coupons=False).get("extras") or []:
            if x.get("emd"):
                live_emds.setdefault((x.get("name") or x.get("description") or "").upper(), []).append(x["emd"])
    except Exception as e:
        logger.warning(f"Invoice {addon['addon_id']}: live EMDs not read: {e}")
    lines = []
    for i in addon["items"]:
        nums = [n for n in (i.get("segment_numbers") or []) if 0 < n <= len(segs)]
        flights = [segs[n - 1] for n in nums]
        t = travelers[i["passenger_idx"]] if isinstance(i.get("passenger_idx"), int) and i["passenger_idx"] < len(travelers) else {}
        # The airline's EMD carries its own wording of the item.
        airline_text = (i.get("airline_name") or i.get("description") or "").upper()
        emd = next((e for e in emds if (e.get("description") or "").upper() == airline_text), None)
        if emd:
            emds.remove(emd)
        if live_emds.get(airline_text):
            emd = {"number": live_emds[airline_text].pop(0)}
        lines.append({
            "description": _item_name(i),
            "airline_description": i.get("airline_name") or i.get("description"),
            "kind": i.get("kind"),
            "passenger": " ".join(x for x in (t.get("first_name"), t.get("last_name")) if x) or None,
            "flights": [{"flight": f.get("flight_number"), "from": f.get("departure_airport"), "to": f.get("arrival_airport"),
                         "date": (f.get("departure_time") or "")[:10]} for f in flights],
            "emd": (emd or {}).get("number"),
            "refundable": i.get("refundable"),
            "amount": round(float(i.get("price") or 0), 2),
        })
    tickets = [t.get("number") for t in raw.get("ticket_numbers") or [] if t.get("number")] or \
        ([raw["ticket_number"]] if raw.get("ticket_number") else [])
    return {
        "invoice_number": addon["addon_id"],
        "issued_at": addon["completed_at"].isoformat() if addon.get("completed_at") else None,
        "status": "Paid",
        "locator": addon["locator_code"],
        "airline_locator": raw.get("airline_pnr"),
        "tickets": tickets,
        "billed_to": {"name": (customer or {}).get("full_name"), "email": (customer or {}).get("email")},
        "lines": lines,
        "total": round(float(addon["amount"]), 2),
        "currency": addon["currency"],
        "payment": {"method": "Card", "reference": result.get("payment_reference")},
        "brand": os.getenv("BRAND_NAME", "Tick and Book"),
    }


def _paid_addon_invoices(locator: str, customer_id: int) -> list:
    return [{"invoice_number": r["addon_id"], "amount": float(r["amount"]), "currency": r["currency"],
             "paid_at": r["completed_at"].isoformat() if r.get("completed_at") else None}
            for r in database.get_addon_payments_for_locator(locator)
            if r["status"] == "completed" and r.get("customer_id") == customer_id]


@app.get("/api/manage-booking/invoice/{addon_id}")
def manage_booking_invoice(addon_id: str, current: dict = Depends(auth.get_current_customer)):
    """Detailed invoice for seats/extras the customer paid after booking."""
    addon = database.get_addon_payment(addon_id)
    if not addon or addon.get("customer_id") != current["customer_id"] or addon["status"] != "completed":
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return _addon_invoice(addon, database.get_customer_by_email(current["sub"]))


# ── Loyalty Program (Customer) ────────────────────────────────────────────────

@app.get("/api/customers/me/loyalty")
def customer_my_loyalty(current: dict = Depends(auth.get_current_customer)):
    summary = services.get_loyalty_summary(current["sub"])
    summary["recent_transactions"] = database.get_loyalty_transactions(current["sub"], limit=50)
    summary["points_by_booking"] = database.get_loyalty_points_by_locator(current["sub"])
    return summary


# ── Email Change Requests (Customer) ──────────────────────────────────────────

class EmailChangeRequestBody(BaseModel):
    new_email: EmailStr


EMAIL_CHANGE_REQUESTS_PER_YEAR = 2


@app.post("/api/customers/me/email-change-request", status_code=status.HTTP_201_CREATED)
def customer_request_email_change(request: EmailChangeRequestBody, current: dict = Depends(auth.get_current_customer)):
    if database.get_pending_email_change_request(current["customer_id"]):
        raise HTTPException(status_code=409, detail="You already have a pending email change request awaiting admin review.")
    if str(request.new_email).lower() == current["sub"].lower():
        raise HTTPException(status_code=422, detail="That is already your current email address.")
    if database.get_customer_by_email(str(request.new_email)):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    used = database.count_recent_email_change_requests(current["customer_id"], days=365)
    if used >= EMAIL_CHANGE_REQUESTS_PER_YEAR:
        raise HTTPException(
            status_code=429,
            detail=f"You've used all {EMAIL_CHANGE_REQUESTS_PER_YEAR} email change requests allowed in a 12-month period. Please contact support if this is urgent.",
        )
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
    try:
        travel_dt = datetime.strptime(request.travel_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="travel_date must be in YYYY-MM-DD format.")
    if (travel_dt - datetime.now()).total_seconds() < 48 * 3600:
        raise HTTPException(
            status_code=422,
            detail="Cancellation requests must be submitted at least 48 hours before the flight. For urgent cancellations within 48 hours, please contact our support team directly by phone.",
        )
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


@app.get("/api/customers/me/cancellation-requests")
def customer_my_cancellation_requests(current: dict = Depends(auth.get_current_customer)):
    return {"requests": database.get_cancellation_requests_for_customer(current["customer_id"])}


# ── Visa Status Check & History (Customer) ───────────────────────────────────
# Looks up the admin-curated visa_requirements table (same data the Admin
# Portal manages) by nationality + destination, plus the customer's own past
# visa consultation bookings — no new data model, just customer-scoped reads
# of tables that already exist for the admin/B2C visa consultation flow.

@app.get("/api/customers/me/visa-status")
def customer_visa_status(
    nationality: str = Query(..., min_length=2, max_length=100),
    destination: str = Query(..., min_length=2, max_length=100),
    _current: dict = Depends(auth.get_current_customer),
):
    requirement = database.get_visa_requirement(nationality.strip(), destination.strip())
    if not requirement:
        return {
            "found": False,
            "nationality": nationality.strip(),
            "destination": destination.strip(),
            "message": "We don't have this route documented yet. Please book a visa consultation for personalised guidance.",
        }
    return {"found": True, **requirement}


@app.get("/api/customers/me/visa-history")
def customer_visa_history(current: dict = Depends(auth.get_current_customer)):
    return {"consultations": database.get_visa_consultations_for_customer(current["customer_id"])}


# ═══════════════════════════════════════════════════════════════════════════
# HOTEL (STAYS) API — Travelport TripServices Stays v11
#
# Fully independent of the flight booking endpoints above: separate service
# modules (services/hotel_*.py), separate OAuth (services/hotel_auth_service.py),
# separate storage (hotel_database.py / hotel_bookings table) — no shared
# code or credentials with the Air/flight integration at all.
#
# Flow: search (property list) -> availability (bookable rooms/rates for a
# chosen property) -> optionally rules (cancellation policy) -> book-offer
# (reference-payload Create Reservation against the chosen offer).
#
# NOTE: The sandbox Travelport account is not yet provisioned for Hotel/Stays
# — every Hotel endpoint currently returns 403 at the Akamai edge (confirmed
# live, repeatedly, across search/availability/rules/property-details). These
# endpoints are wired up per the documented API contract
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
    offer_id: Optional[str] = Field(default=None, description="CatalogOffering.id from a prior Availability call, if any")
    authority: Optional[str] = Field(default=None, description="CatalogOffering.Identifier.authority from Availability (e.g. 'TVPT') — required by Travelport's Full Payload contract")


class HotelAvailabilityRequest(BaseModel):
    chain_code: str
    property_code: str
    check_in_date: str = Field(..., description="YYYY-MM-DD")
    check_out_date: str = Field(..., description="YYYY-MM-DD")
    adults: int = Field(default=1, ge=1, le=9)
    children_ages: Optional[List[int]] = Field(default=None)
    rooms: int = Field(default=1, ge=1, le=9)
    currency: Optional[str] = None


class HotelOfferBookingRequest(BaseModel):
    offer_id: str = Field(..., description="CatalogOffering.id from /api/hotels/availability")
    property_name: Optional[str] = None
    chain_code: Optional[str] = None
    property_code: Optional[str] = None
    city: Optional[str] = None
    country_code: Optional[str] = None
    check_in_date: str
    check_out_date: str
    rooms: int = Field(default=1, ge=1, le=9)
    currency: str
    total_price: float
    room_description: Optional[str] = None
    travelers: List[HotelGuestInfo]


@app.post("/api/hotels/search")
def hotel_search(request: HotelSearchRequest):
    """Search hotels by location — property-level results (see
    hotel_search_service module docstring for why rooms are always empty
    until the Availability endpoint is wired up)."""
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
        properties = hotel_search_service.parse_hotel_offers(raw, request.check_in_date, request.check_out_date)
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


@app.post("/api/hotels/availability")
def hotel_availability(request: HotelAvailabilityRequest):
    """Fetch bookable room types/rates for a property chosen from
    /api/hotels/search results — the step Search by Location can't provide.
    https://developer.travelport.com/apis/stays/availability/createhotelavailability"""
    try:
        raw = hotel_availability_service.get_availability(
            chain_code=request.chain_code,
            property_code=request.property_code,
            check_in_date=request.check_in_date,
            check_out_date=request.check_out_date,
            adults=request.adults,
            children_ages=request.children_ages,
            rooms=request.rooms,
            currency=request.currency,
        )
        offers = hotel_availability_service.parse_availability_offers(raw)
        return {"offers": offers, "count": len(offers)}
    except HotelApiError as e:
        logger.error(f"Hotel availability failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.get("/api/hotels/rules/{offer_id}")
def hotel_rules(offer_id: str):
    """Cancellation policy / terms for an Availability offer — informational
    only, not required before booking.
    https://developer.travelport.com/apis/stays/rules/buildhotelrulesfromcatalogoffering"""
    try:
        raw = hotel_rules_service.get_rules_for_offer(offer_id)
        return raw
    except HotelApiError as e:
        logger.error(f"Hotel rules failed: {e}")
        raise HTTPException(status_code=e.status_code or 502, detail=str(e))


@app.post("/api/hotels/book", status_code=status.HTTP_201_CREATED)
def hotel_book(request: HotelBookingRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """Book a hotel room — Full Payload Create Reservation."""
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
            authority=request.authority,
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


@app.post("/api/hotels/book-offer", status_code=status.HTTP_201_CREATED)
def hotel_book_offer(request: HotelOfferBookingRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """Book a hotel room — Reference Payload Create Reservation, against an
    offer_id from /api/hotels/availability (the Search -> Availability ->
    book flow). https://developer.travelport.com/apis/stays/unified-check-out/buildhotelreservation"""
    try:
        raw = hotel_booking_service.create_hotel_reservation_from_offer(
            offer_id=request.offer_id,
            total_price=request.total_price,
            currency=request.currency,
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
            "chain_code": parsed.get("chain_code") or request.chain_code or "",
            "property_code": parsed.get("property_code") or request.property_code or "",
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
        logger.error(f"Hotel booking (offer) failed: {e}")
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


# ── Admin insights: search records, booking journeys, IP watch, customer 360 ──
# Everything below reads what customers entered and what Travelport (and,
# later, Amadeus) returned — see insights.py. Nothing is generated.

class JourneyEventRequest(BaseModel):
    step: str = Field(..., max_length=60)
    status: Literal["pass", "fail"] = "pass"
    detail: Optional[str] = Field(default=None, max_length=500)
    airline: Optional[str] = Field(default=None, max_length=100)
    flight: Optional[str] = Field(default=None, max_length=80)
    fare_source: Optional[str] = Field(default=None, max_length=10)
    amount: Optional[float] = None
    currency: Optional[str] = Field(default=None, max_length=5)


@app.post("/api/journey/event")
def journey_event(request: JourneyEventRequest):
    """Steps that only happen in the browser (flight chosen, back from the
    payment page). Needs the X-Journey-Id header; anything else is ignored."""
    if not insights.current_journey() or request.step not in insights.CLIENT_STEPS:
        return {"ok": False}
    insights.journey_info(airline=request.airline, flight=request.flight, fare_source=request.fare_source,
                          amount=request.amount, currency=request.currency)
    insights.mark(request.step, request.status, request.detail)
    return {"ok": True}


# ── Card payment reconciliation (CyberSource export upload) ─────────────────
# Finance uploads the CyberSource Transactions export (JSON / CSV / .xlsx);
# every card sale is matched to our payment record, the booking or seats/extras
# payment it paid for, and its ERPNext invoice (services/payment_reconciliation).
# Read-only against bookings and ERP; each run is kept for history.

@app.post("/api/admin/payment-recon/upload")
def payment_recon_upload(file: UploadFile = File(...), admin: dict = Depends(auth.get_current_admin)):
    data = file.file.read(payment_reconciliation.MAX_FILE_BYTES + 1)
    try:
        return payment_reconciliation.run_upload(file.filename or "export", data,
                                                 admin.get("username") or admin.get("sub"))
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile) as e:
        raise HTTPException(status_code=400, detail=str(e) if isinstance(e, ValueError) else
                            "This file couldn't be read. Upload the CyberSource Transactions export as JSON, CSV or .xlsx.")


@app.get("/api/admin/payment-recon/runs")
def payment_recon_runs(admin: dict = Depends(auth.get_current_admin)):
    return {"runs": payment_reconciliation.list_runs()}


@app.get("/api/admin/payment-recon/runs/{run_id}")
def payment_recon_run(run_id: int, admin: dict = Depends(auth.get_current_admin)):
    run = payment_reconciliation.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")
    return run


@app.get("/api/admin/payment-recon/runs/{run_id}/csv")
def payment_recon_run_csv(run_id: int, admin: dict = Depends(auth.get_current_admin)):
    run = payment_reconciliation.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Reconciliation run not found.")
    return Response(content="﻿" + payment_reconciliation.run_csv(run), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="card-reconciliation-{run_id}.csv"'})


@app.get("/api/admin/insights/searches")
def admin_searches(days: int = Query(7, ge=1, le=365), ip: Optional[str] = None, route: Optional[str] = None,
                   customer_id: Optional[int] = None, _admin: dict = Depends(auth.get_current_admin)):
    return {"searches": insights.list_searches(days=days, ip=ip, route=route, customer_id=customer_id),
            "summary": insights.supplier_summary(days=days, ip=ip, route=route, customer_id=customer_id),
            "search_mode": database.get_provider_settings().get("search_mode", "travelport_only")}


@app.get("/api/admin/insights/rates-logs")
def admin_rates_logs(days: int = Query(7, ge=1, le=90), problems: bool = False,
                     supplier: Optional[str] = Query(None, pattern="^(TP|AD)$"),
                     _admin: dict = Depends(auth.get_current_admin)):
    """Airline Rates Logs: one row per customer search — each supplier's
    GDS / NDC result, the lowest fare and the supplier alerts it raised."""
    out = insights.rates_logs(days=days, problems_only=problems, supplier=supplier)
    out["search_mode"] = database.get_provider_settings().get("search_mode", "travelport_only")
    return out


@app.get("/api/admin/insights/rates-logs/{search_id}")
def admin_rates_log_detail(search_id: int, _admin: dict = Depends(auth.get_current_admin)):
    d = insights.rates_log_detail(search_id)
    if not d:
        raise HTTPException(status_code=404, detail="Search not found")
    return d


@app.get("/api/admin/insights/searches/{search_id}")
def admin_search_detail(search_id: int, _admin: dict = Depends(auth.get_current_admin)):
    row = insights.get_search(search_id)
    if not row:
        raise HTTPException(status_code=404, detail="Search not found")
    return row


def _request_from_logged_search(old: dict) -> FlightSearchRequest:
    legs = old.get("legs")
    return FlightSearchRequest(
        origin=None if legs else old["origin"], destination=None if legs else old["destination"],
        departure_date=None if legs else old["departure_date"],
        legs=[SearchLeg(**l) for l in legs] if legs else None,
        adult_count=old.get("adults") or 1, child_count=old.get("children") or 0,
        infant_count=old.get("infants") or 0, student_count=old.get("students") or 0,
        cabin_preference=None if (old.get("cabin") or "All") == "All" else old["cabin"],
    )


async def _live_recheck(search_id: int) -> tuple[dict, Optional[dict], int]:
    """(logged search, cheapest live offer now, live offer count) — the same
    search run again against the live providers."""
    old = await asyncio.to_thread(insights.get_search, search_id)
    if not old:
        raise HTTPException(status_code=404, detail="Search not found")
    result = await _search_core(_request_from_logged_search(old), kind="admin_recheck")
    flights = result["flights"]
    return old, (flights[0] if flights else None), len(flights)


def _is_cheaper(old: dict, now: Optional[dict]) -> bool:
    return bool(now and old.get("cheapest_price") and now.get("currency") == old.get("cheapest_currency")
                and now["price"] < old["cheapest_price"])


@app.post("/api/admin/insights/searches/{search_id}/recheck")
async def admin_search_recheck(search_id: int, _admin: dict = Depends(auth.get_current_admin)):
    """Runs the same search live again now and compares the cheapest fare
    with what that customer was shown."""
    old, now, count = await _live_recheck(search_id)
    return {
        "seen_price": old.get("cheapest_price"), "seen_currency": old.get("cheapest_currency"),
        "seen_airline": old.get("cheapest_airline"), "seen_at": old.get("searched_at"),
        "now_price": now.get("price") if now else None, "now_currency": now.get("currency") if now else None,
        "now_airline": now.get("airline") if now else None, "now_count": count,
        "cheaper": _is_cheaper(old, now),
    }


class FareAlertRequest(BaseModel):
    email: EmailStr


@app.post("/api/admin/insights/searches/{search_id}/fare-alert")
async def admin_send_fare_alert(search_id: int, request: FareAlertRequest,
                                admin: dict = Depends(auth.get_current_admin)):
    """Emails the customer the live fare for the route they searched — only
    if a live search right now is really cheaper than what they were shown."""
    old, now, _ = await _live_recheck(search_id)
    if not _is_cheaper(old, now):
        raise HTTPException(status_code=409, detail="The live fare is not cheaper than what the customer saw — nothing sent.")
    site = os.getenv("PUBLIC_SITE_URL", "").rstrip("/")
    route = f"{old['origin']} → {old['destination']}"
    dates = fmt_date(old["departure_date"]) + (f" – {fmt_date(old['return_date'])}" if old.get("return_date") else "")
    body = (
        f"Hello,\n\nGood news — the fare for the trip you looked at has dropped.\n\n"
        f"Route:    {route}\nDates:    {dates}\n"
        f"Now from: {now['currency']} {now['price']:,.0f} ({now.get('airline')})\n"
        f"Before:   {old['cheapest_currency']} {old['cheapest_price']:,.0f}\n\n"
        f"Fares change quickly and are confirmed only at booking."
        + (f"\nSearch and book: {site}\n" if site else "\n")
        + "\nTick and Book\n"
    )
    ok, err = await asyncio.to_thread(email_service._send, request.email,
                                      f"Lower fare: {route} from {now['currency']} {now['price']:,.0f}", body)
    await asyncio.to_thread(insights.record_message, request.email.lower(), "fare_alert",
                            f"S{search_id}", "email", ok, err, admin.get("sub"))
    if not ok:
        raise HTTPException(status_code=502, detail=f"Email not sent: {err}")
    return {"sent": True, "now_price": now["price"], "currency": now["currency"]}


@app.get("/api/admin/insights/journeys")
def admin_journeys(days: int = Query(7, ge=1, le=365), status_filter: Optional[str] = Query(None, alias="status"),
                   _admin: dict = Depends(auth.get_current_admin)):
    return {"journeys": insights.list_journeys(days=days, status=status_filter)}


@app.get("/api/admin/insights/journeys/{journey_id}")
def admin_journey_detail(journey_id: str, _admin: dict = Depends(auth.get_current_admin)):
    j = insights.get_journey(journey_id)
    if not j:
        raise HTTPException(status_code=404, detail="Journey not found")
    return j


@app.get("/api/admin/insights/alerts")
def admin_insight_alerts(_admin: dict = Depends(auth.get_current_admin)):
    return insights.alerts_since(None)


@app.post("/api/admin/insights/alerts/seen")
def admin_insight_alerts_seen(_admin: dict = Depends(auth.get_current_admin)):
    insights.mark_failures_seen()
    return {"ok": True}


@app.get("/api/admin/insights/ips")
def admin_ip_watch(days: int = Query(7, ge=1, le=90), _admin: dict = Depends(auth.get_current_admin)):
    return {"ips": insights.ip_watch(days=days)}


class BlockIpRequest(BaseModel):
    ip: str = Field(..., min_length=3, max_length=45)
    reason: Optional[str] = Field(default=None, max_length=300)


@app.post("/api/admin/insights/ips/block")
def admin_block_ip(request: BlockIpRequest, admin: dict = Depends(auth.get_current_admin)):
    insights.block_ip(request.ip.strip(), request.reason or "", admin.get("sub"))
    return {"ok": True}


@app.post("/api/admin/insights/ips/unblock")
def admin_unblock_ip(request: BlockIpRequest, _admin: dict = Depends(auth.get_current_admin)):
    insights.unblock_ip(request.ip.strip())
    return {"ok": True}


@app.get("/api/admin/insights/customers")
def admin_customers_360(q: Optional[str] = None, _admin: dict = Depends(auth.get_current_admin)):
    return {"customers": insights.customers_360(q)}


@app.get("/api/admin/insights/customer")
def admin_customer_detail(email: str, _admin: dict = Depends(auth.get_current_admin)):
    return insights.customer_detail(email)


# Automatic daily reminders — OFF unless AUTO_REMINDERS=true. Birthday wishes
# are marketing: under Sri Lanka's PDPA only send them to customers who have
# agreed to receive messages. Each reminder goes once (customer_messages).
AUTO_REMINDERS = os.getenv("AUTO_REMINDERS", "false").strip().lower() in ("1", "true", "yes")


@app.get("/api/admin/insights/reminders")
def admin_reminders(birthday_days: int = Query(30, ge=1, le=366), passport_months: int = Query(6, ge=1, le=24),
                    _admin: dict = Depends(auth.get_current_admin)):
    return {"birthdays": insights.upcoming_birthdays(birthday_days),
            "passports": insights.expiring_passports(passport_months),
            "auto_enabled": AUTO_REMINDERS}


@app.get("/api/admin/insights/routes")
def admin_route_report(days: int = Query(30, ge=1, le=365), _admin: dict = Depends(auth.get_current_admin)):
    return {"routes": insights.route_report(days)}


class ReminderSendRequest(BaseModel):
    kind: Literal["birthday", "passport"]
    email: EmailStr
    channel: Literal["email", "sms"] = "email"


def _reminder_text(kind: str, row: dict) -> tuple[str, str, str]:
    """(subject, email body, sms text) built only from the traveller's own details."""
    first = (row.get("name") or "").split(" ")[0].title() or "there"
    if kind == "birthday":
        return ("Happy birthday from Tick and Book",
                f"Dear {first},\n\nHappy birthday from all of us at Tick and Book! "
                f"We hope your year ahead is full of great journeys.\n\nTick and Book\n",
                f"Happy birthday {first}! Wishing you a year of great journeys - Tick and Book")
    exp = fmt_date(row.get("passport_expiry"))
    when = "has expired" if row.get("expired") else f"expires on {exp}"
    return ("Your passport is due for renewal",
            f"Dear {first},\n\nOur records show the passport ending {row.get('passport_last4')} that you travelled "
            f"with {when}. Many countries require at least 6 months' validity on arrival, so renewing early "
            f"avoids problems at check-in.\n\nTick and Book\n",
            f"Tick and Book: your passport ending {row.get('passport_last4')} {when}. "
            f"Renew early - many countries need 6 months' validity.")


def _send_reminder(kind: str, row: dict, channel: str, by: str) -> tuple[bool, Optional[str]]:
    subject, body, sms = _reminder_text(kind, row)
    ref = row["birthday"][:4] if kind == "birthday" else row["passport_expiry"]
    if channel == "sms":
        if not row.get("phone"):
            ok, err = False, "No phone number on record"
        else:
            ok, err = sms_service.send_sms(row["phone"], sms)
    else:
        ok, err = email_service._send(row["email"], subject, body)
    insights.record_message(row["email"], kind, ref, channel, ok, err, by)
    return ok, err


@app.post("/api/admin/insights/reminders/send")
def admin_send_reminder(request: ReminderSendRequest, admin: dict = Depends(auth.get_current_admin)):
    rows = insights.upcoming_birthdays(366) if request.kind == "birthday" else insights.expiring_passports(24)
    row = next((r for r in rows if r["email"] == request.email.lower()), None)
    if not row:
        raise HTTPException(status_code=404, detail="No matching traveller for this reminder")
    ok, err = _send_reminder(request.kind, row, request.channel, admin.get("sub"))
    if not ok:
        raise HTTPException(status_code=502, detail=f"Not sent: {err}")
    return {"sent": True}


def _auto_reminder_pass() -> None:
    for row in insights.upcoming_birthdays(0):
        if row["email"] and not row.get("sent_at"):
            _send_reminder("birthday", row, "email", "auto")
    for row in insights.expiring_passports(6):
        if row["email"] and not row.get("sent_at") and row["days_left"] in (180, 90, 30):
            _send_reminder("passport", row, "email", "auto")


@app.on_event("startup")
def _start_auto_reminders():
    if not AUTO_REMINDERS:
        return
    import threading

    def loop():
        while True:
            try:
                _auto_reminder_pass()
            except Exception as e:
                logger.error(f"Automatic reminders failed: {e}")
            time.sleep(6 * 3600)
    threading.Thread(target=loop, name="auto-reminders", daemon=True).start()
