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
        resp = await client.post(f"{AMADEUS_BACKEND_URL}/api/bookings/{locator}/issue-ticket")
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
        resp = await client.get(f"{AMADEUS_BACKEND_URL}/api/bookings/lookup", params={"query": query})
    if resp.status_code >= 400:
        raise AmadeusBookingError(_extract_error(resp), resp.status_code)
    return resp.json()


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        return data.get("error") or data.get("detail") or resp.text
    except Exception:
        return resp.text or f"Amadeus backend returned HTTP {resp.status_code}"
