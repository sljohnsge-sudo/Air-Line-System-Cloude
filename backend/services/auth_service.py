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
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints

logger = logging.getLogger(__name__)

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

    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
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
        "XAUTH_TRAVELPORT_ACCESSGROUP": TravelportConfig.ACCESS_GROUP,
        "TVP-PCC-Core": f"{TravelportConfig.PCC}_1G",
        "taxBreakDown": "true",
        "Content-Version": "11",
        "TraceId": TravelportConfig.generate_trace_id(),
    }
    return headers


def invalidate_token():
    """Force-invalidate the cached token (call if a 401 is received)."""
    global _cached_token, _token_expiry
    _cached_token = None
    _token_expiry = 0.0
    logger.warning("Travelport access token invalidated.")
