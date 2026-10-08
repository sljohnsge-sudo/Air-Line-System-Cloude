"""
services/hotel_auth_service.py
================================
OAuth 2.0 token management for Travelport Stays (Hotel) —
https://developer.travelport.com/apis/stays/authentication/paths/~1oauth~1token/post.md

Deliberately independent of services/auth_service.py (Air): its own token
cache and its own request to HotelConfig.AUTH_URL using the HOTEL_TP_*
credentials. Nothing here is shared with, or touches, the Air/flight side.

To update auth logic: modify only this file.
"""

import base64
import json
import threading
import time
import httpx
import logging
from config.hotel_config import HotelConfig
from utils import tp_logger

logger = logging.getLogger(__name__)

_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_expiry: float = 0.0
_REFRESH_MARGIN_SECONDS = 300


def _token_expiry(token: str) -> float:
    """Expiry (epoch seconds) from the JWT's own exp claim.

    Travelport Stays' token response only documents an `access_token` field
    (no `expires_in` — see the endpoint's Response schema), so expiry is
    read from the JWT payload itself; 1h is a fallback only if that fails.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return time.time() + 3600


def get_hotel_access_token() -> str:
    """
    Returns a Bearer access token for Travelport Stays, reusing one in memory
    until 5 minutes before its expiry. Held only in this process's memory —
    never written to disk, never logged, never sent to the browser.

    Returns:
        str: Bearer token string (without 'Bearer ' prefix)

    Raises:
        httpx.HTTPStatusError: if the OAuth server returns an error
        Exception: on network failures
    """
    global _cached_token, _cached_expiry
    with _token_lock:
        if _cached_token and time.time() < _cached_expiry - _REFRESH_MARGIN_SECONDS:
            return _cached_token
        _cached_token, _cached_expiry = _request_new_hotel_token()
        return _cached_token


def _request_new_hotel_token() -> tuple[str, float]:
    logger.info("Requesting new Travelport Stays (Hotel) access token...")

    payload = {
        "grant_type": "password",
        "username": HotelConfig.USERNAME,
        "password": HotelConfig.PASSWORD,
        "client_id": HotelConfig.CLIENT_ID,
        "client_secret": HotelConfig.CLIENT_SECRET,
    }

    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(
            HotelConfig.AUTH_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        response.raise_for_status()
        token_data = response.json()

    access_token = token_data["access_token"]
    logger.info("New Hotel token obtained.")
    return access_token, _token_expiry(access_token)


def invalidate_hotel_token():
    """Drop the in-memory token (called after a 401) so the next call re-authenticates."""
    global _cached_token, _cached_expiry
    with _token_lock:
        _cached_token, _cached_expiry = None, 0.0
    logger.warning("Travelport Stays returned 401 — cached Hotel token dropped; a fresh one will be requested.")
