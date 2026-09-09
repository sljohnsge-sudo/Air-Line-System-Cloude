"""
services/auth_service.py
========================
STEP 1 — OAuth 2.0 Token Management
Handles obtaining and caching the Bearer access token from Travelport.

Token is valid for 24 hours. This service automatically refreshes when expired.
To update auth logic: modify only this file.
"""

import httpx
import time
import logging
import contextvars
from contextlib import contextmanager
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from utils import tp_logger

logger = logging.getLogger(__name__)

# ── Per-flow TraceId ──────────────────────────────────────────────────────────
# Travelport's own guidance: TraceId exists to correlate the multiple linked
# API calls of ONE flow (e.g. create workbench -> add offer -> add travelers
# -> commit, all for a single booking) — not to be freshly randomized on
# every individual HTTP call, which is what get_auth_headers() used to do
# unconditionally. flow_trace_id() is wrapped around each multi-call
# orchestrator (run_booking_flow, issue_ticket, cancel_reservation, the
# /bookings/initiate handler, etc.); get_auth_headers() picks it up from here
# automatically, so no call site needs to pass anything through. Idempotent —
# nesting (an orchestrator called from within an already-wrapped endpoint)
# reuses the outer trace_id rather than generating a new inner one.
_flow_trace_id: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("_flow_trace_id", default=None)


@contextmanager
def flow_trace_id():
    if _flow_trace_id.get() is not None:
        yield _flow_trace_id.get()
        return
    trace_id = TravelportConfig.generate_trace_id()
    token = _flow_trace_id.set(trace_id)
    try:
        yield trace_id
    finally:
        _flow_trace_id.reset(token)


# Plain start/end pair equivalent to flow_trace_id() above, for call sites
# where wrapping a big existing try/except/finally block in a `with` would
# mean re-indenting a large, delicate function body. Same idempotent
# semantics: end_flow_trace_id() is always safe to call, including with the
# None token returned when a flow was already in progress (nothing to reset).
def start_flow_trace_id():
    if _flow_trace_id.get() is not None:
        return None
    return _flow_trace_id.set(TravelportConfig.generate_trace_id())


def end_flow_trace_id(token) -> None:
    if token is not None:
        _flow_trace_id.reset(token)

# ── In-memory token cache ──────────────────────────────────────────────────────
_cached_token: str | None = None
_token_expiry: float = 0.0          # Unix timestamp when token expires


def get_access_token() -> str:
    """
    Returns a valid Bearer access token for Travelport API calls.
    Automatically refreshes if the current token has expired.

    Returns:
        str: Bearer token string (without 'Bearer ' prefix)

    Raises:
        httpx.HTTPStatusError: if OAuth server returns an error
        Exception: on network failures
    """
    global _cached_token, _token_expiry

    # Return cached token if still valid (with 60s safety buffer)
    if _cached_token and time.time() < (_token_expiry - 60):
        logger.debug("Using cached Travelport access token.")
        return _cached_token

    logger.info("Requesting new Travelport access token...")

    payload = {
        "grant_type": "password",
        "username": TravelportConfig.USERNAME,
        "password": TravelportConfig.PASSWORD,
        "client_id": TravelportConfig.CLIENT_ID,
        "client_secret": TravelportConfig.CLIENT_SECRET,
    }

    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(
            TravelportEndpoints.OAUTH_TOKEN,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        response.raise_for_status()
        token_data = response.json()

    _cached_token = token_data["access_token"]
    # expires_in is in seconds; store absolute expiry time
    expires_in = token_data.get("expires_in", 86400)
    _token_expiry = time.time() + expires_in

    logger.info(f"New token obtained. Expires in {expires_in}s.")
    return _cached_token


def get_auth_headers(session_id: str | None = None) -> dict:
    """
    Returns HTTP headers with a valid Bearer token.
    Call this before every Travelport API request.

    Per Travelport's Common Flights API Headers guidance and direct
    certification feedback on our submitted logs:
      - Accept-Encoding is mandatory (Travelport blocks production traffic
        without it) and Cache-Control is recommended — both added below.
      - Accept-Version alongside Content-Version is required for Search,
        Price, Book, Seats, and Ticket operations — added below (was
        previously missing; only Content-Version was sent).
      - Only ONE of XAUTH_TRAVELPORT_ACCESSGROUP / TVP-PCC-Core should be
        sent, not both — and if both are sent, Travelport uses the access
        group anyway, so TVP-PCC-Core was dead weight. Dropped in favor of
        XAUTH_TRAVELPORT_ACCESSGROUP, which (unlike TVP-PCC-Core) is
        supported on every endpoint, not just a documented subset.
      - TraceId must correlate one flow's linked calls, not be unique per
        individual request — see flow_trace_id() above, which this reads
        from automatically.

    Args:
        session_id (str|None): Deprecated (no longer used in Travelport v11 headers)

    Returns:
        dict: Headers including Authorization, Content-Type, and agency context.
    """
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "XAUTH_TRAVELPORT_ACCESSGROUP": TravelportConfig.ACCESS_GROUP,
        "taxBreakDown": "true",
        "Accept-Version": "11",
        "Content-Version": "11",
        "TraceId": _flow_trace_id.get() or TravelportConfig.generate_trace_id(),
    }
    return headers


def invalidate_token():
    """Force-invalidate the cached token (call if a 401 is received)."""
    global _cached_token, _token_expiry
    _cached_token = None
    _token_expiry = 0.0
    logger.warning("Travelport access token invalidated.")
