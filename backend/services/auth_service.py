"""
services/auth_service.py
========================
STEP 1 — OAuth 2.0 Token Management
Bearer access token management. Travelport tokens are valid for 24h and are
meant to be reused across calls, so one token is kept in this process's
memory only (never on disk, never logged, never sent to the browser) and
refreshed 5 minutes before expiry or after a 401.
To update auth logic: modify only this file.
"""

import base64
import json
import threading
import time
import httpx
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


_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_expiry: float = 0.0
_REFRESH_MARGIN_SECONDS = 300


def _token_expiry(token: str, expires_in) -> float:
    """Expiry (epoch seconds) from the JWT's own exp claim, falling back to expires_in."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return time.time() + float(expires_in or 3600)


def get_access_token() -> str:
    """
    Returns a Bearer access token, reusing one in memory until 5 minutes
    before its expiry (Travelport tokens last 24h and are meant to be reused
    across calls). Held only in this process's memory — never written to
    disk, never logged, never sent to the browser.

    Returns:
        str: Bearer token string (without 'Bearer ' prefix)

    Raises:
        httpx.HTTPStatusError: if OAuth server returns an error
        Exception: on network failures
    """
    global _cached_token, _cached_expiry
    with _token_lock:
        if _cached_token and time.time() < _cached_expiry - _REFRESH_MARGIN_SECONDS:
            return _cached_token
        _cached_token, _cached_expiry = _request_new_token()
        return _cached_token


def _request_new_token() -> tuple[str, float]:
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

    access_token = token_data["access_token"]
    logger.info(f"New token obtained. Expires in {token_data.get('expires_in')}s.")
    return access_token, _token_expiry(access_token, token_data.get("expires_in"))


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
    """Drop the in-memory token (called after a 401) so the next call re-authenticates."""
    global _cached_token, _cached_expiry
    with _token_lock:
        _cached_token, _cached_expiry = None, 0.0
    logger.warning("Travelport returned 401 — cached token dropped; a fresh one will be requested.")
