"""
services/amadeus_booking_proxy.py
===================================
Proxies booking/ticketing calls from the unified frontend (:5173) to the
sibling Amadeus backend's own REST API (:8002), exactly like
amadeus_aggregator.py does for search. The Amadeus backend's CORS is
deliberately scoped to its own standalone frontend (localhost:5175) only --
rather than widen that, the browser talks to THIS backend (already open to
:5173), which makes the server-to-server call instead.

Amadeus ticket issuance is currently blocked by errorCode 2011 "ENTRY NOT
AUTHORISED" (an unresolved Amadeus office-authority gate, confirmed live --
not a code defect). confirm_booking() reliably succeeds (PNR + fare
confirmed); issue_ticket() will very likely raise until Amadeus grants that
authority. Both are surfaced as-is -- the frontend is responsible for
explaining a failed issue_ticket() as "PNR confirmed, ticketing pending"
rather than a generic error.
"""

import os
import httpx

AMADEUS_BACKEND_URL = os.getenv("AMADEUS_BACKEND_URL", "http://localhost:8002")
# Shared key the Amadeus backend requires on ticket issue, extras purchase,
# EMD issue, the booking list and the lookup (12/11/2026).
AMADEUS_INTERNAL_KEY = os.getenv("AMADEUS_INTERNAL_KEY", "").strip()


def _internal_headers() -> dict:
    return {"X-Internal-Key": AMADEUS_INTERNAL_KEY} if AMADEUS_INTERNAL_KEY else {}


class AmadeusBookingError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def confirm_booking(segments: list[dict], travelers: list[dict], contact_email: str, contact_phone: str) -> dict:
    payload = {
        "segments": segments,
        "travelers": travelers,
        "contactEmail": contact_email,
        "contactPhone": contact_phone,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{AMADEUS_BACKEND_URL}/api/bookings/confirm", json=payload)
    if resp.status_code >= 400:
        raise AmadeusBookingError(_extract_error(resp), resp.status_code)
    return resp.json()


async def issue_ticket(locator: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{AMADEUS_BACKEND_URL}/api/bookings/{locator}/issue-ticket", headers=_internal_headers())
    if resp.status_code >= 400:
        raise AmadeusBookingError(_extract_error(resp), resp.status_code)
    return resp.json()


async def lookup_booking(query: str) -> dict:
    """"Check My Ticket Status" -- asks the Amadeus backend whether this
    PNR/ticket number is one of ITS bookings (local-index check on that
    side), and if so returns the live PNR_Retrieve answer. Raises
    AmadeusBookingError(status_code=404) when it isn't an Amadeus booking --
    the caller (main.py's /api/ticket-status) treats that as "not Amadeus
    either" after already ruling out Travelport, not as a real failure."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{AMADEUS_BACKEND_URL}/api/bookings/lookup", params={"query": query}, headers=_internal_headers())
    if resp.status_code >= 400:
        raise AmadeusBookingError(_extract_error(resp), resp.status_code)
    return resp.json()


# ── Staff: Amadeus bookings list, cancel, void, refund (12/11/2026) ──────
# Admin > Amadeus Bookings. Every action is behind its own staff switch
# (go_live_switches.AMADEUS_SWITCHES), checked in main.py before calling.

async def _staff_call(method: str, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, f"{AMADEUS_BACKEND_URL}{path}", json=payload, headers=_internal_headers())
    except httpx.ConnectError:
        raise AmadeusBookingError("The Amadeus system is not running, so Amadeus bookings can't be reached right now.", 503)
    except httpx.TimeoutException:
        raise AmadeusBookingError("Amadeus took too long to answer. Check the booking before trying again.", 504)
    if resp.status_code >= 400:
        raise AmadeusBookingError(_extract_error(resp), resp.status_code)
    return resp.json()


async def list_bookings() -> list:
    return await _staff_call("GET", "/api/bookings/history", timeout=30)


async def servicing_view(locator: str) -> dict:
    return await _staff_call("GET", f"/api/bookings/{locator}/servicing", timeout=60)


async def cancel_booking(locator: str, staff: str, confirm_emds: bool = False) -> dict:
    return await _staff_call("POST", f"/api/bookings/{locator}/cancel", {"staff": staff, "confirmEmds": confirm_emds})


async def void_booking(locator: str, staff: str) -> dict:
    return await _staff_call("POST", f"/api/bookings/{locator}/void", {"staff": staff})


async def refund_quote(locator: str, documents: list[str], fees: dict) -> dict:
    return await _staff_call("POST", f"/api/bookings/{locator}/refund-quote", {"documents": documents, "fees": fees})


async def refund_booking(locator: str, staff: str, documents: list[str], fees: dict) -> dict:
    return await _staff_call("POST", f"/api/bookings/{locator}/refund", {"staff": staff, "documents": documents, "fees": fees})


# ── Customer booking, payment and ticket (13/11/2026) ─────────────────
# Used by main.py's customer Amadeus flow (hold -> pay -> ticket). Plain
# (non-async) calls, because the payment code that uses them is plain too.
# The browser never calls the Amadeus backend itself any more.

def _call_sync(method: str, path: str, payload: dict | None = None, timeout: float = 120,
               internal: bool = True, params: dict | None = None) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, f"{AMADEUS_BACKEND_URL}{path}", json=payload, params=params,
                                  headers=_internal_headers() if internal else {})
    except httpx.ConnectError:
        raise AmadeusBookingError("The Amadeus system is not running, so Amadeus bookings can't be reached right now.", 503)
    except httpx.TimeoutException:
        raise AmadeusBookingError("Amadeus took too long to answer.", 504)
    if resp.status_code >= 400:
        raise AmadeusBookingError(_extract_error(resp), resp.status_code)
    return resp.json()


def hold_booking_sync(segments: list[dict], travelers: list[dict], contact_email: str, contact_phone: str) -> dict:
    """Books the flights and stores the fare (PNR + TST) -- no ticket. Every
    passenger field (date of birth, passport) goes through as given."""
    return _call_sync("POST", "/api/bookings/confirm", {
        "segments": segments, "travelers": travelers,
        "contactEmail": contact_email, "contactPhone": contact_phone,
    }, timeout=150, internal=False)


def servicing_view_sync(locator: str) -> dict:
    return _call_sync("GET", f"/api/bookings/{locator}/servicing", timeout=60)


def issue_ticket_sync(locator: str) -> dict:
    return _call_sync("POST", f"/api/bookings/{locator}/issue-ticket", timeout=150)


def lookup_booking_sync(query: str) -> dict:
    return _call_sync("GET", "/api/bookings/lookup", params={"query": query}, timeout=60)


def fare_rules_sync(payload: dict) -> dict:
    return _call_sync("POST", "/api/flights/fare-rules", payload, timeout=90, internal=False)


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        return data.get("error") or data.get("detail") or resp.text
    except Exception:
        return resp.text or f"Amadeus backend returned HTTP {resp.status_code}"
