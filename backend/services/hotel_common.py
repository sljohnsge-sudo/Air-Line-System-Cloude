"""
services/hotel_common.py
=========================
Shared helpers for the Hotel (Stays) service modules — kept separate from
the Air services. Reuses the existing OAuth token cache in auth_service
(read-only import; OAuth is account-wide, not product-scoped) but builds
Hotel-specific request headers, which differ from the Air header set.
"""

import logging
from services.auth_service import get_access_token
from config.hotel_config import HotelConfig

logger = logging.getLogger(__name__)


def get_hotel_headers() -> dict:
    """Headers for Travelport Hotel (Stays) v11/v12 API calls.
    Confirmed against the live interactive spec at
    https://developer.travelport.com/apis/stays (Unified Check Out sample cURL).
    """
    token = get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "XAUTH_TRAVELPORT_ACCESSGROUP": HotelConfig.ACCESS_GROUP,
        "TVP-PCC-Core": f"{HotelConfig.PCC}_1G",
        "TraceId": HotelConfig.generate_trace_id(),
        "TVP-Correlation-Id": HotelConfig.generate_correlation_id(),
    }


class HotelApiError(Exception):
    """Raised for any non-2xx response from a Hotel/Stays API call."""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
